import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation

try:
    from scripts.accounts import add_account, provider_account_exists, save_balance_snapshot
    from scripts.common import check_database, money_to_minor
    from scripts.database_backup import create_backup
    from scripts.paths import BACKUP_DIR, DATABASE_PATH
    from scripts.plaid_api import fetch_accounts
    from scripts.plaid_environment import (
        PRODUCTION_PROVIDER,
        SANDBOX_PROVIDER,
        get_plaid_config,
    )
    from scripts.plaid_items import get_saved_items
except ModuleNotFoundError:
    from accounts import add_account, provider_account_exists, save_balance_snapshot
    from common import check_database, money_to_minor
    from database_backup import create_backup
    from paths import BACKUP_DIR, DATABASE_PATH
    from plaid_api import fetch_accounts
    from plaid_environment import PRODUCTION_PROVIDER, SANDBOX_PROVIDER, get_plaid_config
    from plaid_items import get_saved_items


def clean_text(value, fallback):  #Keeps account text on one short safe line
    if not isinstance(value, str):
        return fallback

    cleaned = " ".join(value.split())[:80]
    return cleaned or fallback


def format_balance(value, currency):  #Formats a Plaid balance without storing it
    if value is None:
        return "Not available"

    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "Not available"

    return f"{amount:,.2f} {currency}"


def get_balance_type(account):  #Credit cards and loans reduce net worth
    plaid_type = clean_text(account.get("type"), "unknown").lower()
    return "liability" if plaid_type in {"credit", "loan"} else "asset"


def get_safe_accounts(accounts):  #Removes IDs, masks, metadata, and other private fields
    default_institution = get_plaid_config()["label"]
    safe_accounts = []

    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError("Plaid returned an invalid account")

        balances = account.get("balances")
        if not isinstance(balances, dict):
            balances = {}

        currency = balances.get("iso_currency_code")
        if not isinstance(currency, str) or not currency:
            currency = "USD"

        safe_accounts.append(
            {
                "name": clean_text(account.get("name"), "Unnamed account"),
                "type": clean_text(account.get("type"), "unknown"),
                "subtype": clean_text(account.get("subtype"), "unknown"),
                "institution": clean_text(
                    account.get("_connection_label"),
                    default_institution,
                ),
                "currency": currency,
                "current": balances.get("current"),
                "available": balances.get("available"),
                "balance_type": get_balance_type(account),
            }
        )

    return safe_accounts


def print_accounts(accounts, heading="Plaid accounts"):  #Prints safe fields only
    print(heading)

    if not accounts:
        print("- No accounts found")
        return

    for position, account in enumerate(accounts, start=1):
        currency = account["currency"]
        current = format_balance(account["current"], currency)
        available = format_balance(account["available"], currency)
        print(
            f"{position}. {account['institution']} | {account['name']} | {account['type']} | "
            f"{account['subtype']} | {account['balance_type']} | "
            f"current {current} | available {available}"
        )


def parse_selection(value, account_count):  #Checks a comma separated account selection
    selections = []

    for part in value.split(","):
        try:
            position = int(part.strip())
        except ValueError as error:
            raise ValueError("Account selections must be numbers") from error

        if position < 1 or position > account_count:
            raise ValueError(f"Account selection must be between 1 and {account_count}")

        index = position - 1
        if index not in selections:
            selections.append(index)

    if not selections:
        raise ValueError("Select at least one account")

    return selections


def get_linked_accounts():  #Fetches accounts from every encrypted Plaid item
    items = get_saved_items()
    if not items:
        raise ValueError("Run python scripts\\plaid_link.py connect first")

    accounts = []
    for item in items:
        for account in fetch_accounts(item["access_token"]):
            if not isinstance(account, dict):
                raise ValueError("Plaid returned an invalid account")
            account = account.copy()
            account["_connection_label"] = item["label"]
            accounts.append(account)
    return accounts


def get_account_to_save(account):  #Checks Plaid data before it reaches SQLite
    config = get_plaid_config()
    account_id = account.get("account_id")
    name = clean_text(account.get("name"), "Unnamed account")
    balances = account.get("balances")

    if not isinstance(account_id, str) or not account_id.strip():
        raise ValueError(f'"{name}" is missing its Plaid account ID')

    if not isinstance(balances, dict):
        raise ValueError(f'"{name}" does not have valid balance details')

    currency = clean_text(balances.get("iso_currency_code"), "").upper()
    if currency != "USD":
        raise ValueError(f'"{name}" does not use the expected USD currency')

    balance_value = balances.get("current")
    if balance_value is None:
        balance_value = balances.get("available")

    try:
        balance = Decimal(str(balance_value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f'"{name}" does not have a valid balance') from error

    if not balance.is_finite():
        raise ValueError(f'"{name}" does not have a valid balance')

    account_type = clean_text(account.get("subtype"), "unknown")
    if account_type == "unknown":
        account_type = clean_text(account.get("type"), "unknown")

    return {
        "name": name,
        "currency": currency,
        "balance": str(balance),
        "provider": config["provider"],
        "provider_account_id": account_id.strip(),
        "institution": clean_text(account.get("_connection_label"), config["label"]),
        "account_type": account_type,
        "balance_type": get_balance_type(account),
    }


def save_selected_accounts(
    accounts,
    selections,
    confirmation,
    database_path=DATABASE_PATH,
    backup_dir=BACKUP_DIR,
):  #Saves only selected Plaid accounts after a private backup
    if confirmation != "SAVE":
        raise ValueError("Save cancelled because confirmation was not SAVE")

    accounts_to_save = [get_account_to_save(accounts[index]) for index in selections]
    new_accounts = []
    skipped = []

    for account in accounts_to_save:
        if provider_account_exists(
            account["provider"],
            account["provider_account_id"],
            database_path,
        ):
            skipped.append(account["name"])
        else:
            new_accounts.append(account)

    if not new_accounts:
        return [], skipped, None

    backup_path = create_backup(
        database_path,
        backup_dir,
        label="before-plaid-account-save",
    )

    for account in new_accounts:
        add_account(database_path=database_path, **account)

    return [account["name"] for account in new_accounts], skipped, backup_path


def refresh_saved_balances(
    accounts,
    confirmation,
    database_path=DATABASE_PATH,
    backup_dir=BACKUP_DIR,
    make_backup=True,
):  #Refreshes balances only for Plaid accounts already saved
    if confirmation != "REFRESH":
        raise ValueError("Refresh confirmation must be REFRESH")

    provider = get_plaid_config()["provider"]
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        saved_accounts = {
            row[0]: {"id": row[1], "name": row[2]}
            for row in connection.execute(
                """
                SELECT provider_account_id, id, display_name
                FROM accounts
                WHERE provider = ?
                  AND is_active = 1
                """,
                (provider,),
            )
        }

    saved_ids = set(saved_accounts)
    if not saved_ids:
        raise ValueError("Save at least one Plaid account before refreshing balances")

    accounts_to_refresh = []

    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError("Plaid returned an invalid account")
        if account.get("account_id") in saved_ids:
            accounts_to_refresh.append(get_account_to_save(account))

    if not accounts_to_refresh:
        raise ValueError("Plaid did not return any saved accounts")

    backup_path = None
    if make_backup:
        backup_path = create_backup(
            database_path,
            backup_dir,
            label="before-plaid-balance-refresh",
        )
    balance_date = date.today().isoformat()
    updated = 0

    with closing(sqlite3.connect(database_path)) as connection:
        for account in accounts_to_refresh:
            balance_minor = money_to_minor(account["balance"])
            result = connection.execute(
                """
                UPDATE accounts
                SET current_balance_minor = ?,
                    balance_as_of = ?,
                    account_type = ?,
                    balance_type = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider = ?
                  AND provider_account_id = ?
                  AND is_active = 1
                """,
                (
                    balance_minor,
                    balance_date,
                    account["account_type"],
                    account["balance_type"],
                    provider,
                    account["provider_account_id"],
                ),
            )
            updated += result.rowcount
            if result.rowcount:
                saved_account = saved_accounts[account["provider_account_id"]]
                save_balance_snapshot(
                    connection,
                    provider,
                    saved_account["id"],
                    saved_account["name"],
                    balance_date,
                    account["currency"],
                    balance_minor,
                )

        connection.commit()

    return updated, backup_path


def get_production_verification(accounts, database_path=DATABASE_PATH):  #Checks real data without printing private IDs
    check_database(database_path)
    linked_balances = {}
    for account in accounts:
        ready = get_account_to_save(account)
        linked_balances[ready["provider_account_id"]] = money_to_minor(ready["balance"])

    with closing(sqlite3.connect(database_path)) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version < 11:
            raise ValueError("Run python scripts\\db_init.py before Production verification")

        saved_accounts = connection.execute(
            """
            SELECT provider_account_id, current_balance_minor
            FROM accounts
            WHERE provider = ?
              AND is_active = 1
            """,
            (PRODUCTION_PROVIDER,),
        ).fetchall()
        transaction_counts = connection.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN transaction_status = 'posted' THEN 1 ELSE 0 END),
                   SUM(CASE WHEN transaction_status = 'pending' THEN 1 ELSE 0 END)
            FROM transactions
            WHERE provider = ?
            """,
            (PRODUCTION_PROVIDER,),
        ).fetchone()
        sandbox_accounts = connection.execute(
            "SELECT COUNT(*) FROM accounts WHERE provider = ?",
            (SANDBOX_PROVIDER,),
        ).fetchone()[0]
        sandbox_transactions = connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE provider = ?",
            (SANDBOX_PROVIDER,),
        ).fetchone()[0]

    matched_accounts = sum(1 for account_id, _ in saved_accounts if account_id in linked_balances)
    matching_balances = sum(
        1
        for account_id, saved_balance in saved_accounts
        if linked_balances.get(account_id) == saved_balance
    )
    return {
        "linked_accounts": len(linked_balances),
        "saved_accounts": len(saved_accounts),
        "matched_accounts": matched_accounts,
        "matching_balances": matching_balances,
        "transactions": transaction_counts[0],
        "posted": transaction_counts[1] or 0,
        "pending": transaction_counts[2] or 0,
        "sandbox_accounts": sandbox_accounts,
        "sandbox_transactions": sandbox_transactions,
    }


def print_production_verification(summary):  #Prints counts without account IDs or transaction details
    print("Plaid Production verification")
    print(f"- Linked accounts: {summary['linked_accounts']}")
    print(f"- Saved accounts: {summary['saved_accounts']}")
    print(f"- Accounts matched to Plaid: {summary['matched_accounts']}")
    print(f"- Balances matching Plaid: {summary['matching_balances']}")
    print(f"- Transactions: {summary['transactions']}")
    print(f"- Posted: {summary['posted']}")
    print(f"- Pending: {summary['pending']}")
    print(f"- Sandbox accounts remaining: {summary['sandbox_accounts']}")
    print(f"- Sandbox transactions remaining: {summary['sandbox_transactions']}")


def remove_sandbox_data(
    accounts,
    confirmation,
    database_path=DATABASE_PATH,
    backup_dir=BACKUP_DIR,
):  #Removes only verified Sandbox rows after creating a private backup
    if confirmation != "REMOVE_SANDBOX_DATA":
        raise ValueError("Sandbox removal confirmation must be REMOVE_SANDBOX_DATA")

    summary = get_production_verification(accounts, database_path)
    if summary["saved_accounts"] < 1:
        raise ValueError("Save at least one Production account before removing Sandbox data")
    if summary["matched_accounts"] != summary["saved_accounts"]:
        raise ValueError("Every saved Production account must still match Plaid")
    if summary["matching_balances"] != summary["saved_accounts"]:
        raise ValueError("Refresh Production balances before removing Sandbox data")
    if summary["transactions"] < 1:
        raise ValueError("Import Production transactions before removing Sandbox data")

    if summary["sandbox_accounts"] == 0 and summary["sandbox_transactions"] == 0:
        return 0, 0, None

    backup_path = create_backup(database_path, backup_dir, label="before-sandbox-removal")
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        removed_transactions = connection.execute(
            "DELETE FROM transactions WHERE provider = ?",
            (SANDBOX_PROVIDER,),
        ).rowcount
        removed_accounts = connection.execute(
            "DELETE FROM accounts WHERE provider = ?",
            (SANDBOX_PROVIDER,),
        ).rowcount
        if removed_accounts != summary["sandbox_accounts"]:
            raise ValueError("Sandbox account count changed during removal")
        if removed_transactions != summary["sandbox_transactions"]:
            raise ValueError("Sandbox transaction count changed during removal")
        connection.commit()

    return removed_accounts, removed_transactions, backup_path


def read_arguments():  #Reads the Plaid account command
    parser = argparse.ArgumentParser(description="Manage Plaid accounts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preview", help="Show safe account details")
    select_parser = subparsers.add_parser("select", help="Check an account selection")
    select_parser.add_argument("--accounts", required=True)
    save_parser = subparsers.add_parser("save", help="Save selected accounts")
    save_parser.add_argument("--accounts", required=True)
    save_parser.add_argument("--confirm", required=True)
    refresh_parser = subparsers.add_parser("refresh", help="Refresh saved balances")
    refresh_parser.add_argument("--confirm", required=True)
    subparsers.add_parser("verify-production", help="Check saved Production data")
    cleanup_parser = subparsers.add_parser(
        "remove-sandbox",
        help="Remove verified Sandbox rows",
    )
    cleanup_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main():  #Runs the selected Plaid account command
    arguments = read_arguments()

    try:
        raw_accounts = get_linked_accounts()
        accounts = get_safe_accounts(raw_accounts)

        if arguments.command == "preview":
            print_accounts(accounts)
        elif arguments.command == "select":
            selections = parse_selection(arguments.accounts, len(accounts))
            selected_accounts = [accounts[index] for index in selections]
            print_accounts(selected_accounts, "Selected Plaid accounts")
            print("Preview only. Nothing was saved")
        elif arguments.command == "save":
            selections = parse_selection(arguments.accounts, len(accounts))
            selected_accounts = [accounts[index] for index in selections]
            print_accounts(selected_accounts, "Plaid accounts to save")
            saved, skipped, backup_path = save_selected_accounts(
                raw_accounts,
                selections,
                arguments.confirm,
            )
            print(f"Accounts saved: {len(saved)}")
            print(f"Accounts already saved: {len(skipped)}")
            if backup_path:
                print(f"Backup created: {backup_path.name}")
        elif arguments.command == "refresh":
            updated, backup_path = refresh_saved_balances(
                raw_accounts,
                arguments.confirm,
            )
            print(f"Plaid account balances updated: {updated}")
            print(f"Backup created: {backup_path.name}")
        elif arguments.command == "verify-production":
            print_production_verification(
                get_production_verification(raw_accounts)
            )
        elif arguments.command == "remove-sandbox":
            removed_accounts, removed_transactions, backup_path = remove_sandbox_data(
                raw_accounts,
                arguments.confirm,
            )
            print(f"Sandbox accounts removed: {removed_accounts}")
            print(f"Sandbox transactions removed: {removed_transactions}")
            if backup_path:
                print(f"Backup created: {backup_path.name}")
    except (OSError, ValueError) as error:
        print(f"Could not manage Plaid accounts: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
