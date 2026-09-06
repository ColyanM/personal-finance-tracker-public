import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation

try:
    from scripts.accounts import add_account, provider_account_exists, save_balance_snapshot
    from scripts.akahu_api import fetch_page, get_tokens
    from scripts.common import check_database, money_to_minor, safe_text
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from accounts import add_account, provider_account_exists, save_balance_snapshot
    from akahu_api import fetch_page, get_tokens
    from common import check_database, money_to_minor, safe_text
    from paths import DATABASE_PATH


ACCOUNTS_PATH = "/v1/accounts"


def fetch_accounts(app_id_token, user_access_token):  #Makes one fixed read-only request
    return fetch_page(ACCOUNTS_PATH, app_id_token, user_access_token)["items"]


def is_bnz_account(account):  #Keeps the preview limited to the configured BNZ connection
    if not isinstance(account, dict):
        return False

    connection = account.get("connection")
    if not isinstance(connection, dict):
        return False

    connection_name = connection.get("name", "").upper()
    return "BNZ" in connection_name or "BANK OF NEW ZEALAND" in connection_name


def get_bnz_accounts(accounts):  #Keeps the BNZ fields needed for previewing and saving
    bnz_accounts = []

    for account in accounts:
        if not is_bnz_account(account):
            continue

        connection = account["connection"]
        balance = account.get("balance")
        if not isinstance(balance, dict):
            balance = {}

        bnz_accounts.append(
            {
                "provider_account_id": account.get("_id"),
                "name": account.get("name") or "Unnamed account",
                "provider": connection.get("name") or "BNZ",
                "type": account.get("type") or "UNKNOWN",
                "status": account.get("status") or "UNKNOWN",
                "currency": balance.get("currency") or "UNKNOWN",
                "current": balance.get("current"),
            }
        )

    return bnz_accounts


def format_balance(value):  #Formats the preview balance without changing it
    if value is None:
        return "unavailable"

    try:
        return f"{Decimal(str(value)):,.2f}"
    except (InvalidOperation, TypeError, ValueError):
        return "unavailable"


def print_account(account, prefix="-"):  #Prints one account without its private ID
    balance = format_balance(account["current"])
    print(
        f"{prefix} {safe_text(account['name'], 'Unnamed account')} | "
        f"{safe_text(account['provider'], 'BNZ')} | "
        f"{safe_text(account['type'], 'UNKNOWN')} | "
        f"{safe_text(account['status'], 'UNKNOWN')} | "
        f"{balance} {safe_text(account['currency'], 'UNKNOWN')}"
    )


def print_preview(accounts):  #Prints safe account details without IDs or account numbers
    bnz_accounts = get_bnz_accounts(accounts)

    if not bnz_accounts:
        print("No connected BNZ accounts found")
        return

    print("Connected BNZ accounts:")
    for account in bnz_accounts:
        print_account(account)

    hidden_count = len(accounts) - len(bnz_accounts)
    if hidden_count:
        print(f"Other connected accounts hidden: {hidden_count}")

    print()
    print("Preview only. Nothing was saved.")


def parse_selection(value, account_count):  #Turns numbered choices into account positions
    selections = []

    for item in value.split(","):
        item = item.strip()
        if not item:
            continue

        try:
            number = int(item)
        except ValueError as error:
            raise ValueError("Enter account numbers separated by commas") from error

        if number < 1 or number > account_count:
            raise ValueError(f"Account number must be between 1 and {account_count}")

        position = number - 1
        if position not in selections:
            selections.append(position)

    if not selections:
        raise ValueError("Choose at least one account")

    return selections


def get_account_to_save(account):  #Checks bank data before it reaches SQLite
    account_id = account.get("provider_account_id")
    name = safe_text(account.get("name"), "Unnamed account")
    currency = safe_text(account.get("currency"), "UNKNOWN").upper()
    status = safe_text(account.get("status"), "UNKNOWN").upper()

    if not isinstance(account_id, str) or not account_id.strip():
        raise ValueError(f'"{name}" is missing its Akahu account ID')

    if currency != "NZD":
        raise ValueError(f'"{name}" does not use the expected NZD currency')

    if status != "ACTIVE":
        raise ValueError(f'"{name}" is not active')

    try:
        balance = Decimal(str(account.get("current")))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f'"{name}" does not have a valid current balance') from error

    if not balance.is_finite():
        raise ValueError(f'"{name}" does not have a valid current balance')

    return {
        "name": name,
        "currency": currency,
        "balance": str(balance),
        "provider": "akahu-bnz",
        "provider_account_id": account_id,
        "institution": safe_text(account.get("provider"), "BNZ"),
        "account_type": safe_text(account.get("type"), "UNKNOWN"),
    }


def save_selected_accounts(
    accounts,
    selections,
    confirmation,
    database_path=DATABASE_PATH,
):  #Saves only the accounts that were selected and confirmed
    if confirmation != "SAVE":
        raise ValueError("Save cancelled because confirmation was not SAVE")

    accounts_to_save = [get_account_to_save(accounts[position]) for position in selections]
    saved = []
    skipped = []

    for account in accounts_to_save:
        if provider_account_exists(
            account["provider"],
            account["provider_account_id"],
            database_path,
        ):
            skipped.append(account["name"])
            continue

        add_account(database_path=database_path, **account)
        saved.append(account["name"])

    return saved, skipped


def refresh_saved_account_balances(accounts, database_path=DATABASE_PATH):  #Updates balances only for previously saved BNZ accounts
    check_database(database_path)
    balance_as_of = date.today().isoformat()
    updated = 0

    with closing(sqlite3.connect(database_path)) as connection:
        saved_accounts = {  #Keeps new connected accounts from being added automatically
            row[0]: {"id": row[1], "name": row[2]}
            for row in connection.execute(
                """
                SELECT provider_account_id, id, display_name
                FROM accounts
                WHERE provider = 'akahu-bnz'
                  AND is_active = 1
                """
            )
        }

        for account in get_bnz_accounts(accounts):
            saved_row = saved_accounts.get(account.get("provider_account_id"))
            if not saved_row:
                continue

            saved_account = get_account_to_save(account)
            balance_minor = money_to_minor(saved_account["balance"])
            result = connection.execute(
                """
                UPDATE accounts
                SET current_balance_minor = ?,
                    balance_as_of = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider = ?
                  AND provider_account_id = ?
                  AND is_active = 1
                """,
                (
                    balance_minor,
                    balance_as_of,
                    saved_account["provider"],
                    saved_account["provider_account_id"],
                ),
            )
            updated += result.rowcount
            if result.rowcount:
                save_balance_snapshot(
                    connection,
                    saved_account["provider"],
                    saved_row["id"],
                    saved_row["name"],
                    balance_as_of,
                    saved_account["currency"],
                    balance_minor,
                )

        connection.commit()

    return updated


def choose_accounts_to_save(accounts):  #Asks before writing any BNZ account to SQLite
    bnz_accounts = get_bnz_accounts(accounts)

    if not bnz_accounts:
        print("No connected BNZ accounts found")
        return

    print("Choose the BNZ accounts to save:")
    for number, account in enumerate(bnz_accounts, start=1):
        print_account(account, str(number))

    selections = parse_selection(
        input("Enter account numbers separated by commas: "),
        len(bnz_accounts),
    )

    print()
    print("Selected accounts:")
    for position in selections:
        print(f"- {safe_text(bnz_accounts[position]['name'], 'Unnamed account')}")

    confirmation = input("Type SAVE to store these accounts in the private database: ")
    saved, skipped = save_selected_accounts(bnz_accounts, selections, confirmation)

    print(f"Accounts saved: {len(saved)}")
    print(f"Accounts already saved: {len(skipped)}")


def read_arguments():  #Reads the Akahu account command
    parser = argparse.ArgumentParser(description="Preview or save connected BNZ accounts")
    parser.add_argument("command", choices=["preview", "save"])
    return parser.parse_args()


def main():  #Runs the selected BNZ account command
    arguments = read_arguments()

    try:
        accounts = fetch_accounts(*get_tokens())

        if arguments.command == "preview":
            print_preview(accounts)
        elif arguments.command == "save":
            choose_accounts_to_save(accounts)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage Akahu accounts: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
