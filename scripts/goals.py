import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import date

try:
    from scripts.common import check_database, format_minor, money_to_minor
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import check_database, format_minor, money_to_minor
    from paths import DATABASE_PATH


SUPPORTED_CURRENCIES = {"NZD", "USD"}


def clean_name(name):  #Keeps goal names short enough for the page
    name = str(name or "").strip()
    if not name:
        raise ValueError("Goal name is required")
    if len(name) > 80:
        raise ValueError("Goal name must be 80 characters or less")
    return name


def clean_currency(currency):  #Only allows the currencies used by Finance Hub
    currency = str(currency or "").upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Currency must be NZD or USD")
    return currency


def clean_amount(amount, label, allow_zero):  #Turns a typed goal amount into cents
    amount_minor = money_to_minor(amount)
    if amount_minor < 0 or (amount_minor == 0 and not allow_zero):
        rule = "zero or more" if allow_zero else "more than zero"
        raise ValueError(f"{label} must be {rule}")
    return amount_minor


def clean_target_date(target_date):  #Checks the optional YYYY-MM-DD target date
    target_date = str(target_date or "").strip()
    if not target_date:
        return None

    try:
        return date.fromisoformat(target_date).isoformat()
    except ValueError as error:
        raise ValueError("Target date must use YYYY-MM-DD") from error


def save_goal(
    name,
    target_amount,
    saved_amount,
    currency,
    target_date=None,
    database_path=DATABASE_PATH,
):  #Adds a goal or updates the goal with the same name
    check_database(database_path)
    name = clean_name(name)
    currency = clean_currency(currency)
    target_minor = clean_amount(target_amount, "Target amount", False)
    saved_minor = clean_amount(saved_amount, "Current amount", True)
    target_date = clean_target_date(target_date)

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute(
            """
            INSERT INTO goals (
                name,
                currency,
                target_amount_minor,
                saved_amount_minor,
                target_date
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                currency = excluded.currency,
                target_amount_minor = excluded.target_amount_minor,
                saved_amount_minor = excluded.saved_amount_minor,
                target_date = excluded.target_date,
                is_active = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (name, currency, target_minor, saved_minor, target_date),
        )
        connection.commit()


def list_goals(active_only=True, database_path=DATABASE_PATH):  #Gets goals ready for the page
    check_database(database_path)
    where_sql = "WHERE is_active = 1" if active_only else ""

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            f"""
            SELECT
                id,
                name,
                currency,
                target_amount_minor,
                saved_amount_minor,
                target_date,
                is_active
            FROM goals
            {where_sql}
            ORDER BY target_date IS NULL, target_date, name COLLATE NOCASE
            """
        ).fetchall()


def deactivate_goal(name, database_path=DATABASE_PATH):  #Hides a goal without deleting its history
    check_database(database_path)
    name = clean_name(name)

    with closing(sqlite3.connect(database_path)) as connection:
        result = connection.execute(
            """
            UPDATE goals
            SET is_active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE name = ? COLLATE NOCASE
              AND is_active = 1
            """,
            (name,),
        )
        if result.rowcount == 0:
            raise ValueError(f'Active goal not found: "{name}"')
        connection.commit()


def print_goals(goals):  #Prints a short list for PowerShell
    if not goals:
        print("No goals found")
        return

    for _, name, currency, target, saved, target_date, is_active in goals:
        status = "active" if is_active else "inactive"
        date_text = target_date or "no target date"
        print(
            f"{name}: {format_minor(saved)} of {format_minor(target)} "
            f"{currency} | {date_text} | {status}"
        )


def read_arguments():  #Reads the goal command from PowerShell
    parser = argparse.ArgumentParser(description="Manage savings goals")
    subparsers = parser.add_subparsers(dest="command", required=True)

    save_parser = subparsers.add_parser("save", help="Add or update a goal")
    save_parser.add_argument("--name", required=True)
    save_parser.add_argument("--target", required=True)
    save_parser.add_argument("--saved", default="0")
    save_parser.add_argument("--currency", choices=sorted(SUPPORTED_CURRENCIES), required=True)
    save_parser.add_argument("--date")

    list_parser = subparsers.add_parser("list", help="List saved goals")
    list_parser.add_argument("--all", action="store_true")

    deactivate_parser = subparsers.add_parser("deactivate", help="Hide an active goal")
    deactivate_parser.add_argument("--name", required=True)
    return parser.parse_args()


def main():  #Runs the selected goal command
    arguments = read_arguments()

    try:
        if arguments.command == "save":
            save_goal(
                arguments.name,
                arguments.target,
                arguments.saved,
                arguments.currency,
                arguments.date,
            )
            print(f'Goal saved: "{clean_name(arguments.name)}"')
        elif arguments.command == "deactivate":
            deactivate_goal(arguments.name)
            print(f'Goal deactivated: "{clean_name(arguments.name)}"')
        else:
            print_goals(list_goals(active_only=not arguments.all))
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage goals: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
