import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta

try:
    from scripts.common import check_database, format_minor
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import check_database, format_minor
    from paths import DATABASE_PATH

SUPPORTED_CURRENCIES = {"NZD", "USD"}  #These are the currencies saved on each transaction
PERIODS = {"week", "month", "year"}  #Supported summary periods


def get_amount_column(currency):  #Chooses the fixed amount column for the requested currency
    currency = currency.upper()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Currency must be NZD or USD")

    if currency == "NZD":
        return "amount_nzd_minor_fixed"

    return "amount_usd_minor_fixed"


def transaction_lines_cte(amount_column):  #Uses split categories when a transaction has been split
    return f"""
    WITH transaction_lines AS (
        SELECT
            t.posted_date,
            c.group_name,
            c.name,
            c.is_income,
            c.is_transfer,
            t.{amount_column} AS amount_minor
        FROM transactions t
        JOIN categories c ON c.id = t.category_id
        WHERE NOT EXISTS (
            SELECT 1
            FROM transaction_splits s
            WHERE s.transaction_id = t.id
        )

        UNION ALL

        SELECT
            t.posted_date,
            c.group_name,
            c.name,
            c.is_income,
            c.is_transfer,
            s.{amount_column} AS amount_minor
        FROM transactions t
        JOIN transaction_splits s ON s.transaction_id = t.id
        JOIN categories c ON c.id = s.category_id
    )
    """


def get_period_dates(period, today=None):  #Works out the date range for week month or year
    if period not in PERIODS:
        raise ValueError("Period must be week, month, or year")

    today = today or date.today()

    if period == "week":
        start_date = today - timedelta(days=today.weekday())  #Monday is the start of the week
        end_date = start_date + timedelta(days=6)  #Sunday is the end of the week
    elif period == "month":
        start_date = today.replace(day=1)

        if today.month == 12:
            next_month = date(today.year + 1, 1, 1)
        else:
            next_month = date(today.year, today.month + 1, 1)

        end_date = next_month - timedelta(days=1)
    else:
        start_date = date(today.year, 1, 1)
        end_date = date(today.year, 12, 31)

    return start_date.isoformat(), end_date.isoformat()


def get_spending_by_category(
    connection,
    amount_column,
    start_date,
    end_date,
    include_investments=False,
):  #Gets spending totals without income or transfers
    investment_filter = ""
    if not include_investments:
        investment_filter = "AND group_name != 'Investments'"  #Investments stay out unless explicitly requested

    rows = connection.execute(
        f"""
        {transaction_lines_cte(amount_column)}
        SELECT
            group_name,
            name,
            SUM(-amount_minor) AS amount_minor
        FROM transaction_lines
        WHERE posted_date >= ?
          AND posted_date <= ?
          AND is_income = 0
          AND is_transfer = 0
          {investment_filter}
        GROUP BY group_name, name
        HAVING amount_minor != 0
        ORDER BY amount_minor DESC, group_name, name
        """,
        (start_date, end_date),
    ).fetchall()

    return [
        {"group_name": row[0], "category_name": row[1], "amount_minor": row[2]}
        for row in rows
    ]


def get_income_by_category(
    connection,
    amount_column,
    start_date,
    end_date,
):  #Gets income totals separately from spending
    rows = connection.execute(
        f"""
        {transaction_lines_cte(amount_column)}
        SELECT
            group_name,
            name,
            SUM(amount_minor) AS amount_minor
        FROM transaction_lines
        WHERE posted_date >= ?
          AND posted_date <= ?
          AND is_income = 1
          AND is_transfer = 0
        GROUP BY group_name, name
        HAVING amount_minor != 0
        ORDER BY amount_minor DESC, group_name, name
        """,
        (start_date, end_date),
    ).fetchall()

    return [
        {"group_name": row[0], "category_name": row[1], "amount_minor": row[2]}
        for row in rows
    ]


def total_minor(rows):  #Adds the category rows into one total
    return sum(row["amount_minor"] for row in rows)


def get_spending_summary(
    period,
    currency="NZD",
    include_investments=False,
    today=None,
    database_path=DATABASE_PATH,
):  #Builds the summary data used by the command line
    start_date, end_date = get_period_dates(period, today)
    return get_spending_summary_between(
        start_date,
        end_date,
        currency=currency,
        include_investments=include_investments,
        period=period,
        database_path=database_path,
    )


def get_spending_summary_between(
    start_date,
    end_date,
    currency="NZD",
    include_investments=False,
    period="custom",
    database_path=DATABASE_PATH,
):  #Builds summary totals for a custom date range
    check_database(database_path)
    currency = currency.upper()
    amount_column = get_amount_column(currency)

    with closing(sqlite3.connect(database_path)) as connection:
        spending_rows = get_spending_by_category(
            connection,
            amount_column,
            start_date,
            end_date,
            include_investments=include_investments,
        )
        income_rows = get_income_by_category(
            connection,
            amount_column,
            start_date,
            end_date,
        )

    spending_total = total_minor(spending_rows)
    income_total = total_minor(income_rows)

    return {  #Keeps the summary in one simple dictionary
        "period": period,
        "start_date": start_date,
        "end_date": end_date,
        "currency": currency,
        "include_investments": include_investments,
        "spending_total_minor": spending_total,
        "income_total_minor": income_total,
        "net_minor": income_total - spending_total,
        "spending_by_category": spending_rows,
        "income_by_category": income_rows,
    }


def get_daily_spending_totals_between(
    start_date,
    end_date,
    currency="NZD",
    include_investments=False,
    database_path=DATABASE_PATH,
):  #Gets all daily spending totals for a range in one database query
    check_database(database_path)
    currency = currency.upper()
    amount_column = get_amount_column(currency)
    investment_filter = ""
    if not include_investments:
        investment_filter = "AND group_name != 'Investments'"

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            f"""
            {transaction_lines_cte(amount_column)}
            SELECT
                posted_date,
                SUM(-amount_minor) AS amount_minor
            FROM transaction_lines
            WHERE posted_date >= ?
              AND posted_date <= ?
              AND is_income = 0
              AND is_transfer = 0
              {investment_filter}
            GROUP BY posted_date
            ORDER BY posted_date
            """,
            (start_date, end_date),
        ).fetchall()

    return {row[0]: row[1] for row in rows}


def print_category_rows(rows, currency):  #Prints one section of category totals
    if not rows:
        print("- None")
        return

    for row in rows:
        amount = format_minor(row["amount_minor"])
        print(f"- {row['category_name']} ({row['group_name']}): {amount} {currency}")


def print_summary(summary):  #Prints the summary in a PowerShell friendly format
    currency = summary["currency"]

    print(
        f"{summary['period'].title()} summary: "
        f"{summary['start_date']} to {summary['end_date']}"
    )
    print(f"Currency: {currency}")
    print(f"Spending: {format_minor(summary['spending_total_minor'])} {currency}")
    print(f"Income: {format_minor(summary['income_total_minor'])} {currency}")
    print(f"Net: {format_minor(summary['net_minor'])} {currency}")

    if not summary["include_investments"]:
        print("Investments: excluded")
    else:
        print("Investments: included")

    print("\nSpending by category")
    print_category_rows(summary["spending_by_category"], currency)

    print("\nIncome by category")
    print_category_rows(summary["income_by_category"], currency)


def parse_as_of(value):  #Supports testing summaries against a specific date
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--as-of must use YYYY-MM-DD") from error


def read_arguments():  #Reads the summary period and display options
    parser = argparse.ArgumentParser(description="Show spending summaries")
    parser.add_argument("period", choices=sorted(PERIODS))
    parser.add_argument("--currency", default="NZD", choices=["NZD", "USD"])
    parser.add_argument(
        "--include-investments",
        action="store_true",
        help="Include investment contributions in spending totals",
    )
    parser.add_argument(
        "--as-of",
        type=parse_as_of,
        help="Use this date for the current week, month, or year",
    )
    return parser.parse_args()


def main():  #Runs the requested summary command
    arguments = read_arguments()

    try:
        summary = get_spending_summary(
            period=arguments.period,
            currency=arguments.currency,
            include_investments=arguments.include_investments,
            today=arguments.as_of,
        )
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(f"Could not build summary: {error}", file=sys.stderr)
        return 1

    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
