import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta

try:
    from scripts.common import (
        check_database,
        convert_minor_units,
        format_minor,
        get_latest_rate,
        money_to_minor,
    )
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import (
        check_database,
        convert_minor_units,
        format_minor,
        get_latest_rate,
        money_to_minor,
    )
    from paths import DATABASE_PATH

SUPPORTED_CURRENCIES = {"NZD", "USD"}  #Currencies supported by the budget workflow
SUPPORTED_BUDGET_VIEWS = {"week", "month"}  #Budgets can repeat by week or by month


def budget_money_to_minor(amount):  #Turns a typed budget amount into cents
    try:
        amount_minor = money_to_minor(amount)
    except ValueError as error:
        raise ValueError(f"Invalid budget amount: {amount}") from error

    if amount_minor < 0:
        raise ValueError("Budget amount cannot be negative")

    return amount_minor


def validate_week_start(week_start):  #Checks weekly Mondays or monthly first days
    try:
        week_start_date = date.fromisoformat(week_start)
    except ValueError as error:
        raise ValueError("Week start must use YYYY-MM-DD") from error

    if week_start_date.weekday() != 0 and week_start_date.day != 1:
        raise ValueError("Budget date must be a Monday or the first day of a month")

    return week_start_date.isoformat()


def clean_currency(currency):  #Keeps currency input consistent
    currency = currency.upper()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Currency must be NZD or USD")

    return currency


def clean_budget_view(budget_view):  #Keeps budget inheritance separate for weeks and months
    return budget_view if budget_view in SUPPORTED_BUDGET_VIEWS else "week"


def budget_date_filter(alias, budget_view):  #Finds only weekly Mondays or monthly first days
    if clean_budget_view(budget_view) == "month":
        return f"strftime('%d', {alias}.week_start_date) = '01'"

    return f"strftime('%w', {alias}.week_start_date) = '1'"


def budget_period_end_sql(alias, budget_view):  #Gets the last day for actual spending in a period
    if clean_budget_view(budget_view) == "month":
        return f"date({alias}.period_start_date, '+1 month', '-1 day')"

    return f"date({alias}.period_start_date, '+6 days')"


def transaction_lines_cte():  #Uses split categories for budget actuals
    return """
        transaction_lines AS (
            SELECT
                t.posted_date,
                t.category_id,
                t.amount_nzd_minor_fixed,
                t.amount_usd_minor_fixed
            FROM transactions t
            WHERE NOT EXISTS (
                SELECT 1
                FROM transaction_splits s
                WHERE s.transaction_id = t.id
            )

            UNION ALL

            SELECT
                t.posted_date,
                s.category_id,
                s.amount_nzd_minor_fixed,
                s.amount_usd_minor_fixed
            FROM transactions t
            JOIN transaction_splits s ON s.transaction_id = t.id
        )
    """


def parse_true_false(value):  #Turns true or false text from PowerShell into a boolean
    value = value.lower()

    if value == "true":
        return True

    if value == "false":
        return False

    raise argparse.ArgumentTypeError("Rollover must be true or false")


def get_budget_category_id(connection, category_name):  #Finds an active income or expense category
    category = connection.execute(
        """
        SELECT id
        FROM categories
        WHERE name = ?
          AND is_active = 1
          AND is_transfer = 0
        """,
        (category_name,),
    ).fetchone()

    if category is None:
        raise ValueError(f'Active budget category not found: "{category_name}"')

    return category[0]


def add_budget(
    category,
    week_start,
    amount,
    currency="NZD",
    rollover_enabled=False,
    database_path=DATABASE_PATH,
):  #Adds one weekly budget row
    check_database(database_path)
    week_start = validate_week_start(week_start)
    currency = clean_currency(currency)
    amount_minor = budget_money_to_minor(amount)

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        category_id = get_budget_category_id(connection, category)
        existing_budget = connection.execute(
            """
            SELECT id
            FROM weekly_budgets
            WHERE category_id = ?
              AND week_start_date = ?
              AND budget_currency = ?
            """,
            (category_id, week_start, currency),
        ).fetchone()

        if existing_budget is not None:
            raise ValueError(
                f'Budget already exists for "{category}" in {week_start} {currency}'
            )

        result = connection.execute(
            """
            INSERT INTO weekly_budgets (
                category_id,
                week_start_date,
                budget_currency,
                budget_amount_minor,
                rollover_enabled
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                category_id,
                week_start,
                currency,
                amount_minor,
                1 if rollover_enabled else 0,
            ),
        )
        connection.commit()

    return result.lastrowid


def update_budget(
    category,
    week_start,
    amount=None,
    currency="NZD",
    rollover_enabled=None,
    database_path=DATABASE_PATH,
):  #Updates the amount for an existing weekly budget
    check_database(database_path)
    week_start = validate_week_start(week_start)
    currency = clean_currency(currency)
    updates = []
    values = []

    if amount is not None:
        updates.append("budget_amount_minor = ?")
        values.append(budget_money_to_minor(amount))

    if rollover_enabled is not None:
        updates.append("rollover_enabled = ?")
        values.append(1 if rollover_enabled else 0)

    if not updates:
        raise ValueError("Choose an amount or rollover setting to update")

    with closing(sqlite3.connect(database_path)) as connection:
        category_id = get_budget_category_id(connection, category)
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.extend([category_id, week_start, currency])
        result = connection.execute(
            f"""
            UPDATE weekly_budgets
            SET {", ".join(updates)}
            WHERE category_id = ?
              AND week_start_date = ?
              AND budget_currency = ?
            """,
            values,
        )
        connection.commit()

    if result.rowcount == 0:
        raise ValueError(f'Budget not found for "{category}" in {week_start} {currency}')


def delete_budget(category, week_start, database_path=DATABASE_PATH):  #Clears a category budget for one week
    check_database(database_path)
    week_start = validate_week_start(week_start)

    with closing(sqlite3.connect(database_path)) as connection:
        category_id = get_budget_category_id(connection, category)
        connection.execute(
            """
            DELETE FROM weekly_budgets
            WHERE category_id = ?
              AND week_start_date = ?
            """,
            (category_id, week_start),
        )
        connection.commit()


def list_budgets(week_start=None, database_path=DATABASE_PATH):  #Gets saved budgets ready for display
    check_database(database_path)
    filters = []
    values = []

    if week_start is not None:
        filters.append("wb.week_start_date = ?")
        values.append(validate_week_start(week_start))

    where_sql = ""
    if filters:
        where_sql = "WHERE " + " AND ".join(filters)

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            f"""
            SELECT
                wb.week_start_date,
                c.group_name,
                c.name,
                wb.budget_currency,
                wb.budget_amount_minor,
                wb.rollover_enabled,
                c.is_income
            FROM weekly_budgets wb
            JOIN categories c ON c.id = wb.category_id
            {where_sql}
            ORDER BY wb.week_start_date DESC, c.group_name, c.name, wb.budget_currency
            """,
            values,
        ).fetchall()


def has_prior_budget(category, week_start, budget_view="week", database_path=DATABASE_PATH):  #Checks if zero should stop an older budget
    check_database(database_path)
    week_start = validate_week_start(week_start)
    budget_view = clean_budget_view(budget_view)
    date_filter = budget_date_filter("wb", budget_view)

    with closing(sqlite3.connect(database_path)) as connection:
        category_id = get_budget_category_id(connection, category)
        row = connection.execute(
            f"""
            SELECT 1
            FROM weekly_budgets wb
            WHERE wb.category_id = ?
              AND wb.week_start_date < ?
              AND {date_filter}
            LIMIT 1
            """,
            (category_id, week_start),
        ).fetchone()

    return row is not None


def get_planned_income_total(week_start, currency="NZD", database_path=DATABASE_PATH, budget_view="week"):  #Totals planned income for the period
    check_database(database_path)
    week_start = validate_week_start(week_start)
    currency = clean_currency(currency)
    budget_view = clean_budget_view(budget_view)
    date_filter = budget_date_filter("wb", budget_view)

    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            f"""
            WITH ranked_budgets AS (
                SELECT
                    wb.category_id,
                    wb.budget_currency,
                    wb.budget_amount_minor,
                    c.is_income,
                    ROW_NUMBER() OVER (
                        PARTITION BY wb.category_id
                        ORDER BY wb.week_start_date DESC, wb.updated_at DESC, wb.id DESC
                    ) AS budget_rank
                FROM weekly_budgets wb
                JOIN categories c ON c.id = wb.category_id
                WHERE wb.week_start_date <= ?
                  AND {date_filter}
            )
            SELECT COALESCE(SUM(budget_amount_minor), 0)
            FROM ranked_budgets
            WHERE budget_rank = 1
              AND budget_currency = ?
              AND is_income = 1
            """,
            (week_start, currency),
        ).fetchone()

    return row[0]


def get_budget_progress(
    week_start=None,
    database_path=DATABASE_PATH,
    display_currency=None,
    budget_view="week",
):  #Compares income budgets to income and expense budgets to spending
    check_database(database_path)
    filters = []
    values = []
    actual_sql = """
        CASE
            WHEN c.is_income = 1 AND wb.budget_currency = 'NZD'
                THEN tl.amount_nzd_minor_fixed
            WHEN c.is_income = 1
                THEN tl.amount_usd_minor_fixed
            WHEN wb.budget_currency = 'NZD'
                THEN -tl.amount_nzd_minor_fixed
            ELSE -tl.amount_usd_minor_fixed
        END
    """

    if display_currency is not None:
        display_currency = clean_currency(display_currency)
        amount_column = f"tl.amount_{display_currency.lower()}_minor_fixed"
        actual_sql = f"""
            CASE
                WHEN c.is_income = 1 THEN {amount_column}
                ELSE -{amount_column}
            END
        """

    if week_start is not None:
        week_start = validate_week_start(week_start)
        budget_view = clean_budget_view(budget_view)
        date_filter = budget_date_filter("wb", budget_view)
        period_end = budget_period_end_sql("eb", budget_view)
        effective_actual_sql = """
            CASE
                WHEN eb.is_income = 1 AND eb.budget_currency = 'NZD'
                    THEN tl.amount_nzd_minor_fixed
                WHEN eb.is_income = 1
                    THEN tl.amount_usd_minor_fixed
                WHEN eb.budget_currency = 'NZD'
                    THEN -tl.amount_nzd_minor_fixed
                ELSE -tl.amount_usd_minor_fixed
            END
        """

        if display_currency is not None:
            amount_column = f"tl.amount_{display_currency.lower()}_minor_fixed"
            effective_actual_sql = f"""
                CASE
                    WHEN eb.is_income = 1 THEN {amount_column}
                    ELSE -{amount_column}
                END
            """

        with closing(sqlite3.connect(database_path)) as connection:
            rows = connection.execute(
                f"""
                WITH {transaction_lines_cte()},
                ranked_budgets AS (
                    SELECT
                        ? AS period_start_date,
                        wb.week_start_date AS budget_source_date,
                        wb.category_id,
                        c.group_name,
                        c.name,
                        wb.budget_currency,
                        wb.budget_amount_minor,
                        wb.rollover_enabled,
                        c.is_income,
                        ROW_NUMBER() OVER (
                            PARTITION BY wb.category_id
                            ORDER BY wb.week_start_date DESC, wb.updated_at DESC, wb.id DESC
                        ) AS budget_rank
                    FROM weekly_budgets wb
                    JOIN categories c ON c.id = wb.category_id
                    WHERE wb.week_start_date <= ?
                      AND {date_filter}
                ),
                effective_budgets AS (
                    SELECT *
                    FROM ranked_budgets
                    WHERE budget_rank = 1
                )
                SELECT
                    eb.period_start_date,
                    eb.group_name,
                    eb.name,
                    eb.budget_currency,
                    eb.budget_amount_minor,
                    eb.rollover_enabled,
                    eb.is_income,
                    COALESCE(wr.rollover_minor, 0) AS rollover_minor,
                    COALESCE(
                        SUM({effective_actual_sql}),
                        0
                    ) AS actual_minor
                FROM effective_budgets eb
                LEFT JOIN (
                    SELECT
                        category_id,
                        to_week_start_date,
                        currency,
                        SUM(rollover_amount_minor) AS rollover_minor
                    FROM weekly_budget_rollovers
                    GROUP BY category_id, to_week_start_date, currency
                ) wr
                    ON wr.category_id = eb.category_id
                   AND wr.to_week_start_date = eb.period_start_date
                   AND wr.currency = eb.budget_currency
                LEFT JOIN transaction_lines tl
                    ON tl.category_id = eb.category_id
                   AND tl.posted_date >= eb.period_start_date
                   AND tl.posted_date <= {period_end}
                GROUP BY
                    eb.period_start_date,
                    eb.group_name,
                    eb.name,
                    eb.budget_currency,
                    eb.budget_amount_minor,
                    eb.rollover_enabled,
                    eb.is_income,
                    wr.rollover_minor
                ORDER BY eb.group_name, eb.name, eb.budget_currency
                """,
                (week_start, week_start),
            ).fetchall()

            progress = []
            for row in rows:
                currency = row[3]
                budget = row[4]
                rollover = row[7]

                if display_currency is not None:
                    rate, _, _ = get_latest_rate(connection, currency, display_currency)  #Plans use the current rate but actuals stay fixed
                    currency = display_currency
                    budget = convert_minor_units(budget, rate)
                    rollover = convert_minor_units(rollover, rate)

                progress.append(
                    (
                        row[0],
                        row[1],
                        row[2],
                        currency,
                        budget,
                        row[5],
                        row[6],
                        rollover,
                        row[8],
                        budget + rollover - row[8],
                    )
                )

        return progress

    if week_start is not None:
        filters.append("wb.week_start_date = ?")
        values.append(validate_week_start(week_start))

    where_sql = ""
    if filters:
        where_sql = "WHERE " + " AND ".join(filters)

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            f"""
            WITH {transaction_lines_cte()}
            SELECT
                wb.week_start_date,
                c.group_name,
                c.name,
                wb.budget_currency,
                wb.budget_amount_minor,
                wb.rollover_enabled,
                c.is_income,
                COALESCE(wr.rollover_minor, 0) AS rollover_minor,
                COALESCE(
                    SUM({actual_sql}),
                    0
                ) AS actual_minor
            FROM weekly_budgets wb
            JOIN categories c ON c.id = wb.category_id
            LEFT JOIN (
                SELECT
                    category_id,
                    to_week_start_date,
                    currency,
                    SUM(rollover_amount_minor) AS rollover_minor
                FROM weekly_budget_rollovers
                GROUP BY category_id, to_week_start_date, currency
            ) wr
                ON wr.category_id = wb.category_id
               AND wr.to_week_start_date = wb.week_start_date
               AND wr.currency = wb.budget_currency
            LEFT JOIN transaction_lines tl
                ON tl.category_id = wb.category_id
               AND tl.posted_date >= wb.week_start_date
               AND tl.posted_date <= date(wb.week_start_date, '+6 days')
            {where_sql}
            GROUP BY
                wb.week_start_date,
                c.group_name,
                c.name,
                wb.budget_currency,
                wb.budget_amount_minor,
                wb.rollover_enabled,
                c.is_income,
                wr.rollover_minor
            ORDER BY wb.week_start_date DESC, c.group_name, c.name, wb.budget_currency
            """,
            values,
        ).fetchall()

        progress = []
        for row in rows:
            currency = row[3]
            budget = row[4]
            rollover = row[7]

            if display_currency is not None:
                rate, _, _ = get_latest_rate(connection, currency, display_currency)  #Plans use the current rate but actuals stay fixed
                currency = display_currency
                budget = convert_minor_units(budget, rate)
                rollover = convert_minor_units(rollover, rate)

            progress.append(
                (
                    row[0],
                    row[1],
                    row[2],
                    currency,
                    budget,
                    row[5],
                    row[6],
                    rollover,
                    row[8],
                    budget + rollover - row[8],
                )
            )

    return progress


def get_rollover_preview(week_start, database_path=DATABASE_PATH):  #Shows rollover amounts without saving anything
    week_start = validate_week_start(week_start)
    next_week_start = (date.fromisoformat(week_start) + timedelta(days=7)).isoformat()
    progress_rows = get_budget_progress(week_start=week_start, database_path=database_path)
    preview_rows = []

    for row in progress_rows:
        _, group_name, category, currency, _, rollover_enabled, is_income, _, _, remaining = row
        if rollover_enabled and not is_income and remaining > 0:
            preview_rows.append(
                (
                    week_start,
                    next_week_start,
                    group_name,
                    category,
                    currency,
                    remaining,
                )
            )

    return preview_rows


def save_rollovers(week_start, database_path=DATABASE_PATH):  #Saves the rollover preview for the next week
    week_start = validate_week_start(week_start)
    preview_rows = get_rollover_preview(week_start, database_path=database_path)  #Keeps save matching the preview

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")  #Keeps rollover categories linked to real categories
        connection.execute(  #Clears old saved rollovers from this same week first
            """
            DELETE FROM weekly_budget_rollovers
            WHERE from_week_start_date = ?
            """,
            (week_start,),
        )

        for row in preview_rows:
            from_week, to_week, _, category, currency, amount_minor = row
            category_id = get_budget_category_id(connection, category)
            connection.execute(
                """
                INSERT INTO weekly_budget_rollovers (
                    category_id,
                    from_week_start_date,
                    to_week_start_date,
                    currency,
                    rollover_amount_minor
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (category_id, from_week, to_week, currency, amount_minor),
            )

        connection.commit()

    return preview_rows


def print_budgets(budgets):  #Prints budgets in a simple readable format
    if not budgets:
        print("No weekly budgets found")
        return

    for week_start, group_name, category, currency, amount_minor, rollover_enabled, is_income in budgets:
        amount = format_minor(amount_minor)
        rollover = "rollover on" if rollover_enabled else "rollover off"
        budget_type = "income" if is_income else "expense"
        print(f"{week_start} | {category} ({group_name}) | {budget_type} | {amount} {currency} | {rollover}")


def print_budget_progress(progress_rows):  #Prints budget progress in a simple readable format
    if not progress_rows:
        print("No weekly budgets found")
        return

    for row in progress_rows:
        week_start, group_name, category, currency, budget, rollover_enabled, is_income, saved_rollover, actual, remaining = row
        budget_text = format_minor(budget)
        saved_rollover_text = format_minor(saved_rollover)
        actual_text = format_minor(actual)
        remaining_text = format_minor(abs(remaining))
        rollover = "rollover on" if rollover_enabled else "rollover off"
        budget_type = "income" if is_income else "expense"
        actual_label = "received" if is_income else "spent"
        status = "left" if remaining >= 0 else "over"
        print(
            f"{week_start} | {category} ({group_name}) | "
            f"{budget_type} | "
            f"budget {budget_text} {currency} | rollover {saved_rollover_text} {currency} | "
            f"{actual_label} {actual_text} {currency} | "
            f"{status} {remaining_text} {currency} | {rollover}"
        )


def print_rollover_preview(preview_rows):  #Prints rollover amounts without changing the database
    if not preview_rows:
        print("No rollover amounts found")
        return

    for week_start, next_week_start, group_name, category, currency, amount_minor in preview_rows:
        amount = format_minor(amount_minor)
        print(
            f"{category} ({group_name}) | {amount} {currency} | "
            f"{week_start} to {next_week_start}"
        )


def read_arguments():  #Reads the budget command and options
    parser = argparse.ArgumentParser(description="Manage weekly budgets")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a weekly budget")
    add_parser.add_argument("--category", required=True)
    add_parser.add_argument("--week", required=True)
    add_parser.add_argument("--amount", required=True)
    add_parser.add_argument("--currency", default="NZD", choices=["NZD", "USD"])
    add_parser.add_argument("--rollover", type=parse_true_false, default=False)

    update_parser = subparsers.add_parser("update", help="Update a weekly budget")
    update_parser.add_argument("--category", required=True)
    update_parser.add_argument("--week", required=True)
    update_parser.add_argument("--amount")
    update_parser.add_argument("--currency", default="NZD", choices=["NZD", "USD"])
    update_parser.add_argument("--rollover", type=parse_true_false)

    list_parser = subparsers.add_parser("list", help="List weekly budgets")
    list_parser.add_argument("--week")

    progress_parser = subparsers.add_parser("progress", help="Show weekly budget progress")
    progress_parser.add_argument("--week")

    rollover_preview_parser = subparsers.add_parser(
        "rollover-preview",
        help="Preview rollover amounts without saving them",
    )
    rollover_preview_parser.add_argument("--week", required=True)

    rollover_save_parser = subparsers.add_parser(
        "rollover-save",
        help="Save rollover amounts for the next week",
    )
    rollover_save_parser.add_argument("--week", required=True)

    return parser.parse_args()


def main():  #Runs the right budget command from the arguments
    arguments = read_arguments()

    try:
        if arguments.command == "add":
            budget_id = add_budget(
                category=arguments.category,
                week_start=arguments.week,
                amount=arguments.amount,
                currency=arguments.currency,
                rollover_enabled=arguments.rollover,
            )
            print(f"Added weekly budget {budget_id}")
        elif arguments.command == "update":
            update_budget(
                category=arguments.category,
                week_start=arguments.week,
                amount=arguments.amount,
                currency=arguments.currency,
                rollover_enabled=arguments.rollover,
            )
            print(f'Updated weekly budget for "{arguments.category}"')
        elif arguments.command == "list":
            print_budgets(list_budgets(week_start=arguments.week))
        elif arguments.command == "progress":
            print_budget_progress(get_budget_progress(week_start=arguments.week))
        elif arguments.command == "rollover-preview":
            print_rollover_preview(get_rollover_preview(week_start=arguments.week))
        elif arguments.command == "rollover-save":
            saved_rows = save_rollovers(week_start=arguments.week)
            print_rollover_preview(saved_rows)
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage budgets: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
