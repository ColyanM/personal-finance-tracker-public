import argparse
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

try:
    from scripts.automation import finish_run, start_run
    from scripts.category_rules import apply_rules, get_matching_category_id
    from scripts.common import (
        check_database,
        get_refresh_days,
        imported_transaction_exists,
        money_to_minor,
        safe_text,
    )
    from scripts.database_backup import create_backup
    from scripts.fx_refresh import refresh_exchange_rates_or_saved
    from scripts.import_csv_transactions import (
        get_category_id,
        get_fixed_amounts,
        insert_transaction,
        replace_pending_transaction,
    )
    from scripts.paths import BACKUP_DIR, DATABASE_PATH
    from scripts.plaid_accounts import refresh_saved_balances
    from scripts.plaid_api import PlaidRequestError, fetch_accounts, fetch_transactions
    from scripts.plaid_details import refresh_investments
    from scripts.plaid_environment import get_plaid_config
    from scripts.plaid_items import describe_item_error, get_saved_items
    from scripts.pre_bank_review import require_safe_review
except ModuleNotFoundError:
    from automation import finish_run, start_run
    from category_rules import apply_rules, get_matching_category_id
    from common import (
        check_database,
        get_refresh_days,
        imported_transaction_exists,
        money_to_minor,
        safe_text,
    )
    from database_backup import create_backup
    from fx_refresh import refresh_exchange_rates_or_saved
    from import_csv_transactions import (
        get_category_id,
        get_fixed_amounts,
        insert_transaction,
        replace_pending_transaction,
    )
    from paths import BACKUP_DIR, DATABASE_PATH
    from plaid_accounts import refresh_saved_balances
    from plaid_api import PlaidRequestError, fetch_accounts, fetch_transactions
    from plaid_details import refresh_investments
    from plaid_environment import get_plaid_config
    from plaid_items import describe_item_error, get_saved_items
    from pre_bank_review import require_safe_review


def get_date_range(days, today=None):  #Keeps each Plaid request to a small recent range
    if days < 1 or days > 90:
        raise ValueError("Days must be between 1 and 90")

    today = today or date.today()
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def get_saved_accounts(database_path=DATABASE_PATH):  #Gets private IDs without printing them
    provider = get_plaid_config()["provider"]
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT id, provider_account_id, display_name, native_currency
            FROM accounts
            WHERE provider = ?
              AND is_active = 1
            ORDER BY display_name
            """,
            (provider,),
        ).fetchall()

    return {
        provider_id: {
            "database_id": database_id,
            "name": name,
            "currency": currency,
        }
        for database_id, provider_id, name, currency in rows
    }


def get_monarch_transaction_cutoff(connection):  #Stops Plaid duplicating the Monarch history baseline
    row = connection.execute(
        """
        SELECT MAX(posted_date)
        FROM transactions
        WHERE provider = 'monarch-history'
        """
    ).fetchone()
    return row[0] if row and row[0] else None


def get_plaid_refresh_days(days, database_path=DATABASE_PATH):  #Uses the last saved row so refreshes do not miss gaps
    if days is not None:
        get_date_range(days)
        return days

    provider = get_plaid_config()["provider"]
    check_database(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        return get_refresh_days(
            connection,
            provider,
            "monarch-history",
            default_days=30,
        )


def is_covered_by_monarch(connection, transaction, cutoff_date):  #Allows late same-day rows if Monarch missed them
    if not cutoff_date or transaction["posted_date"] > cutoff_date:
        return False

    return imported_transaction_exists(connection, "monarch-history", transaction)


def get_transaction_date(value, label):  #Checks dates before saving them
    cleaned = safe_text(value, "")[:10]

    try:
        date.fromisoformat(cleaned)
    except ValueError as error:
        raise ValueError(f"Plaid transaction has an invalid {label}") from error

    return cleaned


def get_transaction_amount(value):  #Changes Plaid spending into the app's negative amount format
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Plaid transaction has an invalid amount") from error

    if not amount.is_finite():
        raise ValueError("Plaid transaction has an invalid amount")

    return -amount


def prepare_transaction(connection, transaction, account):  #Checks one Plaid row before SQLite
    transaction_id = transaction.get("transaction_id")
    if not isinstance(transaction_id, str) or not transaction_id.strip():
        raise ValueError("Plaid transaction is missing its ID")

    pending = transaction.get("pending")
    if not isinstance(pending, bool):
        raise ValueError("Plaid transaction has an invalid status")

    currency = safe_text(transaction.get("iso_currency_code"), "").upper()
    if currency != account["currency"] or currency != "USD":
        raise ValueError("Plaid transactions must use the saved USD currency")

    posted_date = get_transaction_date(transaction.get("date"), "date")
    authorized_value = transaction.get("authorized_date")
    authorized_date = None
    if authorized_value:
        authorized_date = get_transaction_date(authorized_value, "authorized date")

    description = safe_text(transaction.get("name"), "Unknown transaction")[:200]
    merchant = safe_text(transaction.get("merchant_name"), "")[:200] or None
    amount_minor = money_to_minor(str(get_transaction_amount(transaction.get("amount"))))
    fixed_amounts = get_fixed_amounts(connection, amount_minor, currency)
    pending_id = transaction_id.strip() if pending else transaction.get("pending_transaction_id")
    if not isinstance(pending_id, str) or not pending_id.strip():
        pending_id = None

    return {
        "provider_transaction_id": transaction_id.strip(),
        "pending_provider_transaction_id": pending_id,
        "account_id": account["database_id"],
        "posted_date": posted_date,
        "authorized_date": authorized_date,
        "description_raw": description,
        "merchant_clean": merchant,
        "category_id": get_matching_category_id(connection, description, merchant),
        "category_was_provided": False,
        "original_currency": currency,
        "original_amount_minor": amount_minor,
        "fx_rate_to_nzd": fixed_amounts[0],
        "fx_rate_to_usd": fixed_amounts[1],
        "fx_rate_source": fixed_amounts[2],
        "fx_rate_date": fixed_amounts[3],
        "amount_nzd_minor_fixed": fixed_amounts[4],
        "amount_usd_minor_fixed": fixed_amounts[5],
        "notes": None,
        "transaction_status": "pending" if pending else "posted",
    }


def prepare_transactions(connection, transactions, saved_accounts):  #Keeps only saved Plaid accounts
    prepared = []
    seen_ids = set()
    monarch_cutoff = get_monarch_transaction_cutoff(connection)

    for transaction in transactions:
        if not isinstance(transaction, dict):
            raise ValueError("Plaid returned an invalid transaction")

        account = saved_accounts.get(transaction.get("account_id"))
        if account is None:
            continue

        ready = prepare_transaction(connection, transaction, account)
        if is_covered_by_monarch(connection, ready, monarch_cutoff):
            continue

        transaction_id = ready["provider_transaction_id"]
        if transaction_id not in seen_ids:
            prepared.append(ready)
            seen_ids.add(transaction_id)

    return prepared


def update_pending_transaction(connection, transaction):  #Updates a pending charge without losing its review
    provider = get_plaid_config()["provider"]
    connection.execute(
        """
        UPDATE transactions
        SET account_id = ?,
            posted_date = ?,
            authorized_date = ?,
            description_raw = ?,
            merchant_clean = ?,
            original_amount_minor = ?,
            fx_rate_to_nzd = ?,
            fx_rate_to_usd = ?,
            fx_rate_source = ?,
            fx_rate_date = ?,
            amount_nzd_minor_fixed = ?,
            amount_usd_minor_fixed = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE provider = ?
          AND provider_transaction_id = ?
          AND transaction_status = 'pending'
        """,
        (
            transaction["account_id"],
            transaction["posted_date"],
            transaction["authorized_date"],
            transaction["description_raw"],
            transaction["merchant_clean"],
            transaction["original_amount_minor"],
            transaction["fx_rate_to_nzd"],
            transaction["fx_rate_to_usd"],
            transaction["fx_rate_source"],
            transaction["fx_rate_date"],
            transaction["amount_nzd_minor_fixed"],
            transaction["amount_usd_minor_fixed"],
            provider,
            transaction["provider_transaction_id"],
        ),
    )


def save_fetched_transactions(
    transactions,
    confirmation,
    database_path=DATABASE_PATH,
    refreshed_account_ids=None,
):  #Saves final charges and keeps only current pending charges
    if confirmation != "SAVE":
        raise ValueError("Save cancelled because confirmation was not SAVE")

    provider = get_plaid_config()["provider"]
    saved_accounts = get_saved_accounts(database_path)
    if not saved_accounts:
        raise ValueError("Save at least one Plaid account before saving transactions")

    added = 0
    matched = 0
    skipped = 0
    removed_pending = 0

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        uncategorized_id = get_category_id(connection, "Uncategorized")
        prepared = prepare_transactions(connection, transactions, saved_accounts)
        posted_rows = [row for row in prepared if row["transaction_status"] == "posted"]
        pending_rows = [row for row in prepared if row["transaction_status"] == "pending"]

        for transaction in posted_rows:
            existing = connection.execute(
                """
                SELECT id, transaction_status
                FROM transactions
                WHERE provider = ?
                  AND provider_transaction_id = ?
                """,
                (provider, transaction["provider_transaction_id"]),
            ).fetchone()

            if existing:
                if existing[1] == "pending":
                    replace_pending_transaction(connection, existing[0], transaction)
                    matched += 1
                else:
                    skipped += 1  #Keeps the exchange rate locked for saved final charges
                continue

            pending_id = transaction["pending_provider_transaction_id"]
            pending_match = None
            if pending_id:
                pending_match = connection.execute(
                    """
                    SELECT id
                    FROM transactions
                    WHERE provider = ?
                      AND transaction_status = 'pending'
                      AND (
                          provider_transaction_id = ?
                          OR pending_provider_transaction_id = ?
                      )
                    """,
                    (provider, pending_id, pending_id),
                ).fetchone()

            if pending_match:
                replace_pending_transaction(connection, pending_match[0], transaction)
                matched += 1
            else:
                insert_transaction(connection, transaction, uncategorized_id, provider)
                added += 1

        current_pending_ids = {
            transaction["provider_transaction_id"] for transaction in pending_rows
        }
        saved_pending = connection.execute(
            """
            SELECT id, provider_transaction_id, account_id
            FROM transactions
            WHERE provider = ?
              AND transaction_status = 'pending'
            """,
            (provider,),
        ).fetchall()

        refreshed_database_ids = None
        if refreshed_account_ids is not None:
            refreshed_database_ids = {
                saved_accounts[provider_account_id]["database_id"]
                for provider_account_id in refreshed_account_ids
                if provider_account_id in saved_accounts
            }

        for transaction_id, provider_id, account_id in saved_pending:
            if (
                refreshed_database_ids is not None
                and account_id not in refreshed_database_ids
            ):
                continue
            if provider_id not in current_pending_ids:
                connection.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
                removed_pending += 1

        for transaction in pending_rows:
            existing = connection.execute(
                """
                SELECT transaction_status
                FROM transactions
                WHERE provider = ?
                  AND provider_transaction_id = ?
                """,
                (provider, transaction["provider_transaction_id"]),
            ).fetchone()

            if existing and existing[0] == "pending":
                update_pending_transaction(connection, transaction)
            elif existing:
                skipped += 1
            else:
                insert_transaction(connection, transaction, uncategorized_id, provider)

        connection.commit()

    return added, matched, skipped, len(pending_rows), removed_pending


def get_recent_transactions(days, database_path=DATABASE_PATH):  #Fetches saved accounts from every Plaid item
    saved_accounts = get_saved_accounts(database_path)
    if not saved_accounts:
        raise ValueError("Save at least one Plaid account before fetching transactions")

    days = get_plaid_refresh_days(days, database_path)
    start_date, end_date = get_date_range(days)
    items = get_saved_items()
    if not items:
        raise ValueError("Run python scripts\\plaid_link.py connect first")

    linked_accounts = []
    transactions = []
    detail_requests = []
    refreshed_account_ids = set()
    item_failures = []
    failure_codes = []
    successful_connections = 0
    for position, item in enumerate(items, start=1):
        try:
            fetched_accounts = fetch_accounts(item["access_token"])
            account_ids = [
                account.get("account_id")
                for account in fetched_accounts
                if isinstance(account, dict) and account.get("account_id") in saved_accounts
            ]
            fetched_transactions = []
            if account_ids:
                fetched_transactions = fetch_transactions(
                    item["access_token"],
                    start_date,
                    end_date,
                    account_ids,
                )
        except PlaidRequestError as error:
            item_failures.append(describe_item_error(error, item, position))
            failure_codes.append(error.error_code)
            continue

        successful_connections += 1
        linked_accounts.extend(fetched_accounts)
        transactions.extend(fetched_transactions)
        refreshed_account_ids.update(account_ids)
        if account_ids:
            detail_requests.append(
                {
                    "access_token": item["access_token"],
                    "account_ids": account_ids,
                    "label": item["label"],
                    "position": position,
                }
            )

    if successful_connections == 0:
        error_code = failure_codes[0] if len(set(failure_codes)) == 1 else None
        raise PlaidRequestError(
            "No US connections could be refreshed: " + " | ".join(item_failures),
            error_code,
        )

    return (
        linked_accounts,
        transactions,
        detail_requests,
        refreshed_account_ids,
        item_failures,
    )


def refresh_plaid(
    days,
    confirmation,
    database_path=DATABASE_PATH,
    backup_dir=BACKUP_DIR,
):  #Runs one backed up Plaid and exchange rate refresh
    if confirmation != "REFRESH":
        raise ValueError("Refresh confirmation must be REFRESH")

    days = get_plaid_refresh_days(days, database_path)
    require_safe_review("plaid", database_path, backup_dir)
    backup_path = create_backup(database_path, backup_dir, label="before-plaid-refresh")
    connection_mode = get_plaid_config()["name"]
    run_id = start_run(f"plaid_{connection_mode}_refresh", database_path)

    try:
        rate_date, _, _, fx_warning = refresh_exchange_rates_or_saved(database_path)
        (
            linked_accounts,
            transactions,
            item_accounts,
            refreshed_account_ids,
            item_failures,
        ) = get_recent_transactions(days, database_path)
        accounts_updated, _ = refresh_saved_balances(
            linked_accounts,
            "REFRESH",
            database_path,
            backup_dir,
            make_backup=False,
        )
        result = save_fetched_transactions(
            transactions,
            "SAVE",
            database_path,
            refreshed_account_ids=refreshed_account_ids,
        )
        position_count, investment_failures = refresh_investments(
            item_accounts,
            database_path,
        )
        item_failures.extend(investment_failures)
        categorized = apply_rules(database_path)
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error):
        finish_run(run_id, "failed", "Plaid refresh failed", database_path)
        raise

    details = (
        f"FX date: {rate_date}{fx_warning}, accounts updated: {accounts_updated}, "
        f"days checked: {days}, "
        f"posted added: {result[0]}, pending matched: {result[1]}, "
        f"current pending: {result[3]}, "
        f"investment positions: {position_count}, categorized: {categorized}"
    )
    if item_failures:
        details += ", partial refresh: " + " | ".join(item_failures)
    finish_run(
        run_id,
        "failed" if item_failures else "success",
        details,
        database_path,
    )
    return (
        backup_path,
        rate_date,
        accounts_updated,
        result,
        categorized,
        position_count,
        fx_warning,
        tuple(item_failures),
    )


def preview_transactions(days, database_path=DATABASE_PATH):  #Shows counts without exposing transaction details
    days = get_plaid_refresh_days(days, database_path)
    _, transactions, _, _, item_failures = get_recent_transactions(days, database_path)
    posted = sum(1 for item in transactions if isinstance(item, dict) and not item.get("pending"))
    pending = sum(1 for item in transactions if isinstance(item, dict) and item.get("pending"))
    print(f"Recent Plaid transactions: {len(transactions)}")
    print(f"Days checked: {days}")
    print(f"Posted: {posted}")
    print(f"Pending: {pending}")
    for failure in item_failures:
        print(f"Warning: {failure}")
    print("Preview only. Nothing was saved")


def read_arguments():  #Reads the Plaid transaction command
    parser = argparse.ArgumentParser(description="Manage Plaid transactions")
    subparsers = parser.add_subparsers(dest="command", required=True)
    preview_parser = subparsers.add_parser("preview", help="Show safe transaction counts")
    preview_parser.add_argument("--days", type=int)
    refresh_parser = subparsers.add_parser("refresh", help="Refresh accounts and transactions")
    refresh_parser.add_argument("--days", type=int)
    refresh_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main():  #Runs the selected Plaid transaction command
    arguments = read_arguments()

    try:
        if arguments.command == "preview":
            preview_transactions(arguments.days)
        else:
            result = refresh_plaid(arguments.days, arguments.confirm)
            print(f"Backup created: {result[0].name}")
            print(f"Exchange rate used: {result[1]}")
            print(f"Account balances updated: {result[2]}")
            print(f"Posted transactions added: {result[3][0]}")
            print(f"Pending charges matched to final charges: {result[3][1]}")
            print(f"Current pending charges: {result[3][3]}")
            print(f"Existing transactions categorized: {result[4]}")
            print(f"Investment positions saved: {result[5]}")
            if len(result) > 7 and result[7]:
                for failure in result[7]:
                    print(f"Warning: {failure}", file=sys.stderr)
                return 1
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage Plaid transactions: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
