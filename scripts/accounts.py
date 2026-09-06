import argparse
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation

try:
    from scripts.common import (
        check_database,
        convert_minor_units,
        format_minor,
        get_latest_rate,
        money_to_minor,
        parse_active,
    )
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import (
        check_database,
        convert_minor_units,
        format_minor,
        get_latest_rate,
        money_to_minor,
        parse_active,
    )
    from paths import DATABASE_PATH

SUPPORTED_CURRENCIES = {"NZD", "USD"}  #Currencies currently supported by the app


def make_manual_account_id(name):  #Makes a simple repeatable account ID
    account_id = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")  #Makes a simple ID from the account name

    if not account_id:
        raise ValueError("The account name must contain at least one letter or number")

    return f"manual-{account_id}"  #Keeps the same ID when the same account is added again


def add_account(
    name,
    currency,
    balance,
    provider="manual",
    provider_account_id=None,
    institution=None,
    account_type=None,
    balance_type="asset",
    database_path=DATABASE_PATH,
):  #Adds one account row to SQLite
    check_database(database_path)
    currency = currency.upper()
    provider = provider.lower().strip()
    name = name.strip()
    balance_type = balance_type.lower().strip()

    if not name:
        raise ValueError("The account name cannot be empty")

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Currency must be NZD or USD")

    if not provider:
        raise ValueError("Provider cannot be empty")

    if balance_type not in {"asset", "liability"}:
        raise ValueError("Balance type must be asset or liability")

    if provider_account_id is None:
        if provider != "manual":
            raise ValueError("A provider account ID is required for non-manual accounts")
        provider_account_id = make_manual_account_id(name)  #Manual accounts do not come with bank IDs

    balance_minor = money_to_minor(balance)  #Saves money as cents so totals stay accurate
    balance_as_of = date.today().isoformat()

    with closing(sqlite3.connect(database_path)) as connection:  #Closes the database cleanly on Windows
        connection.execute(
            """
            INSERT INTO accounts (
                provider,
                provider_account_id,
                display_name,
                institution,
                account_type,
                balance_type,
                native_currency,
                current_balance_minor,
                balance_as_of
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider,
                provider_account_id,
                name,
                institution,
                account_type,
                balance_type,
                currency,
                balance_minor,
                balance_as_of,
            ),
        )
        connection.commit()

    return provider_account_id


def provider_account_exists(provider, provider_account_id, database_path=DATABASE_PATH):  #Checks for an already saved bank account without displaying its private ID
    check_database(database_path)
    provider = provider.lower().strip()

    with closing(sqlite3.connect(database_path)) as connection:
        match = connection.execute(
            """
            SELECT 1
            FROM accounts
            WHERE provider = ?
              AND provider_account_id = ?
            LIMIT 1
            """,
            (provider, provider_account_id),
        ).fetchone()

    return match is not None


def update_account(
    name,
    balance=None,
    institution=None,
    account_type=None,
    balance_type=None,
    is_active=None,
    provider="manual",
    database_path=DATABASE_PATH,
):  #Updates one manual account without touching the rest
    check_database(database_path)
    provider = provider.lower().strip()
    updates = []  #Builds only the fields selected for change
    values = []

    if balance is not None:
        updates.append("current_balance_minor = ?")
        values.append(money_to_minor(balance))
        updates.append("balance_as_of = ?")  #Balance date changes when the balance changes
        values.append(date.today().isoformat())

    if institution is not None:
        updates.append("institution = ?")
        values.append(institution)

    if account_type is not None:
        updates.append("account_type = ?")
        values.append(account_type)

    if balance_type is not None:
        balance_type = balance_type.lower().strip()
        if balance_type not in {"asset", "liability"}:
            raise ValueError("Balance type must be asset or liability")
        updates.append("balance_type = ?")
        values.append(balance_type)

    if is_active is not None:
        updates.append("is_active = ?")
        values.append(1 if is_active else 0)

    if not updates:
        raise ValueError("Choose at least one account value to update")

    updates.append("updated_at = CURRENT_TIMESTAMP")  #Tracks when the account was last changed
    values.extend([name, provider])

    with closing(sqlite3.connect(database_path)) as connection:
        result = connection.execute(
            f"""
            UPDATE accounts
            SET {", ".join(updates)}
            WHERE display_name = ?
              AND provider = ?
            """,
            values,
        )
        connection.commit()

    if result.rowcount == 0:
        raise ValueError(f'Account not found: "{name}"')


def rename_account(account_id, new_name, database_path=DATABASE_PATH):  #Changes the account name without changing bank IDs
    check_database(database_path)
    new_name = new_name.strip()

    if not new_name:
        raise ValueError("Account name cannot be empty")

    with closing(sqlite3.connect(database_path)) as connection:
        result = connection.execute(
            """
            UPDATE accounts
            SET display_name = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_name, account_id),
        )
        connection.commit()

    if result.rowcount == 0:
        raise ValueError("Account not found")


def save_balance_snapshot(
    connection,
    source,
    account_id,
    account_name,
    balance_date,
    currency,
    balance_minor,
):  #Adds one balance-history point for the net worth graph
    account_name = snapshot_account_name(account_id, account_name)
    connection.execute(
        """
        DELETE FROM account_balance_history
        WHERE source = ?
          AND account_id = ?
          AND balance_date = ?
          AND currency = ?
        """,
        (source, account_id, balance_date, currency),
    )
    connection.execute(
        """
        INSERT INTO account_balance_history (
            source,
            account_name,
            account_id,
            balance_date,
            currency,
            balance_minor
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, account_name, balance_date, currency)
        DO UPDATE SET
            account_id = excluded.account_id,
            balance_minor = excluded.balance_minor,
            updated_at = CURRENT_TIMESTAMP
        """,
        (source, account_name, account_id, balance_date, currency, balance_minor),
    )


def snapshot_account_name(account_id, account_name):  #Prevents duplicate display names from overwriting linked balance history
    account_name = account_name.strip()
    if account_id is None:
        return account_name

    return f"{account_name} #{account_id}"


def list_accounts(database_path=DATABASE_PATH):  #Gets accounts ready for display
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            """
            SELECT
                display_name,
                provider,
                institution,
                account_type,
                native_currency,
                current_balance_minor,
                balance_as_of,
                is_active,
                provider_account_id,
                balance_type,
                id
            FROM accounts
            ORDER BY is_active DESC, display_name
            """
        ).fetchall()


def set_loan_details(name, interest_rate, payment, payment_day, database_path=DATABASE_PATH):  #Saves loan details Plaid does not provide
    check_database(database_path)
    try:
        rate = Decimal(str(interest_rate))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Interest rate must be a number") from error

    if not rate.is_finite() or rate < 0 or rate > 100:
        raise ValueError("Interest rate must be between 0 and 100")
    if payment_day < 1 or payment_day > 31:
        raise ValueError("Payment day must be between 1 and 31")

    payment_minor = money_to_minor(payment)
    if payment_minor <= 0:
        raise ValueError("Payment must be greater than zero")

    with closing(sqlite3.connect(database_path)) as connection:
        matches = connection.execute(
            """
            SELECT id, account_type
            FROM accounts
            WHERE display_name = ?
              AND balance_type = 'liability'
              AND is_active = 1
            """,
            (name,),
        ).fetchall()
        account_type = (matches[0][1] or "").lower() if len(matches) == 1 else ""
        if len(matches) != 1 or "credit" in account_type or "card" in account_type:
            raise ValueError(f'One active loan account was not found: "{name}"')

        connection.execute(
            """
            UPDATE accounts
            SET manual_interest_rate = ?,
                manual_payment_minor = ?,
                manual_payment_day = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (format(rate, "f"), payment_minor, payment_day, matches[0][0]),
        )
        connection.commit()


def get_net_worth(report_currency="NZD", database_path=DATABASE_PATH):  #Totals active accounts in one currency
    check_database(database_path)
    report_currency = report_currency.upper()

    if report_currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Currency must be NZD or USD")

    accounts = []
    total_minor = 0

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT display_name, native_currency, current_balance_minor, balance_type
            FROM accounts
            WHERE is_active = 1
            ORDER BY display_name
            """
        ).fetchall()

        asset_total_minor = 0
        liability_total_minor = 0

        for name, native_currency, native_balance_minor, balance_type in rows:
            rate, rate_date, rate_source = get_latest_rate(
                connection,
                native_currency,
                report_currency,
            )
            report_balance_minor = convert_minor_units(native_balance_minor, rate)
            if balance_type == "liability":
                liability_total_minor += report_balance_minor
                total_minor -= report_balance_minor
            else:
                asset_total_minor += report_balance_minor
                total_minor += report_balance_minor
            accounts.append(
                {
                    "name": name,
                    "native_currency": native_currency,
                    "native_balance_minor": native_balance_minor,
                    "report_balance_minor": report_balance_minor,
                    "balance_type": balance_type,
                    "rate": rate,
                    "rate_date": rate_date,
                    "rate_source": rate_source,
                }
            )

    return {
        "currency": report_currency,
        "total_minor": total_minor,
        "asset_total_minor": asset_total_minor,
        "liability_total_minor": liability_total_minor,
        "accounts": accounts,
    }


def read_arguments():
    parser = argparse.ArgumentParser(description="Manage bank accounts")  #Keeps this script runnable from PowerShell
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new account")
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--provider", default="manual")
    add_parser.add_argument("--provider-account-id")
    add_parser.add_argument("--institution")
    add_parser.add_argument("--account-type")
    add_parser.add_argument("--balance-type", default="asset", choices=["asset", "liability"])
    add_parser.add_argument("--currency", required=True, choices=["NZD", "USD"])
    add_parser.add_argument("--balance", required=True)

    update_parser = subparsers.add_parser("update", help="Update an existing account")
    update_parser.add_argument("--name", required=True)
    update_parser.add_argument("--provider", default="manual")
    update_parser.add_argument("--balance")
    update_parser.add_argument("--institution")
    update_parser.add_argument("--account-type")
    update_parser.add_argument("--balance-type", choices=["asset", "liability"])
    update_parser.add_argument("--active", type=parse_active)

    subparsers.add_parser("list", help="List all accounts")

    net_worth_parser = subparsers.add_parser("net-worth", help="Show current net worth")
    net_worth_parser.add_argument("--currency", default="NZD", choices=["NZD", "USD"])

    loan_parser = subparsers.add_parser("loan-details", help="Save details Plaid does not provide")
    loan_parser.add_argument("--name", required=True)
    loan_parser.add_argument("--rate", required=True)
    loan_parser.add_argument("--payment", required=True)
    loan_parser.add_argument("--payment-day", required=True, type=int)

    return parser.parse_args()


def print_accounts(accounts):  #Prints accounts in a simple readable format
    if not accounts:
        print("No accounts found")
        return

    for account in accounts:
        name = account[0]
        provider = account[1]
        institution = account[2] or "-"
        account_type = account[3] or "-"
        currency = account[4]
        balance = format_minor(account[5])
        balance_as_of = account[6] or "-"
        status = "active" if account[7] else "inactive"
        balance_type = account[9]

        print(f"{name} | {balance} {currency} | {institution} | {account_type} | {balance_type}")  #First line is the human useful stuff
        print(f"  {provider} | {status} | as of {balance_as_of}")


def print_net_worth(summary):  #Prints active accounts and the total net worth
    accounts = summary["accounts"]
    report_currency = summary["currency"]

    if not accounts:
        print("No active accounts found")
        return

    for account in accounts:
        native_balance = format_minor(account["native_balance_minor"])
        report_balance = format_minor(account["report_balance_minor"])
        print(
            f"{account['name']} | {native_balance} {account['native_currency']} | "
            f"{report_balance} {report_currency}"
        )

    print(f"Net worth: {format_minor(summary['total_minor'])} {report_currency}")


def main():  #Runs the right account command from the arguments
    arguments = read_arguments()

    try:
        if arguments.command == "add":
            add_account(
                name=arguments.name,
                currency=arguments.currency,
                balance=arguments.balance,
                provider=arguments.provider,
                provider_account_id=arguments.provider_account_id,
                institution=arguments.institution,
                account_type=arguments.account_type,
                balance_type=arguments.balance_type,
            )
            print(f'Added account: "{arguments.name}"')
        elif arguments.command == "update":
            update_account(
                name=arguments.name,
                balance=arguments.balance,
                institution=arguments.institution,
                account_type=arguments.account_type,
                balance_type=arguments.balance_type,
                is_active=arguments.active,
                provider=arguments.provider,
            )
            print(f'Updated account: "{arguments.name}"')
        elif arguments.command == "list":
            print_accounts(list_accounts())
        elif arguments.command == "net-worth":
            print_net_worth(get_net_worth(arguments.currency))
        elif arguments.command == "loan-details":
            set_loan_details(
                arguments.name,
                arguments.rate,
                arguments.payment,
                arguments.payment_day,
            )
            print(f'Saved loan details: "{arguments.name}"')
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage accounts: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
