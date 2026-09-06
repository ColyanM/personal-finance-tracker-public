import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

try:
    from scripts.akahu_accounts import format_balance
    from scripts.akahu_api import fetch_page, get_tokens
    from scripts.automation import finish_run, start_run
    from scripts.category_rules import get_matching_category_id
    from scripts.common import check_database, imported_transaction_exists, money_to_minor, safe_text
    from scripts.import_csv_transactions import (
        get_category_id,
        get_fixed_amounts,
        insert_transaction,
    )
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from akahu_accounts import format_balance
    from akahu_api import fetch_page, get_tokens
    from automation import finish_run, start_run
    from category_rules import get_matching_category_id
    from common import check_database, imported_transaction_exists, money_to_minor, safe_text
    from import_csv_transactions import (
        get_category_id,
        get_fixed_amounts,
        insert_transaction,
    )
    from paths import DATABASE_PATH


POSTED_PATH = "/v1/transactions"
PENDING_PATH = "/v1/transactions/pending"
MAX_PREVIEW_ITEMS = 1_000
DISPLAY_LIMIT = 50
IMPORT_JOB_NAME = "bnz_transaction_import"


def get_saved_bnz_accounts(database_path=DATABASE_PATH):  #Gets the private IDs needed to filter the preview
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        accounts = connection.execute(
            """
            SELECT id, provider_account_id, display_name, native_currency
            FROM accounts
            WHERE provider = 'akahu-bnz'
              AND is_active = 1
            ORDER BY display_name
            """
        ).fetchall()

    return {
        provider_account_id: {
            "database_id": database_id,
            "name": name,
            "currency": currency,
        }
        for database_id, provider_account_id, name, currency in accounts
    }


def get_bnz_csv_cutoff(connection):  #Keeps future Akahu refreshes from duplicating CSV history
    row = connection.execute(
        """
        SELECT MAX(posted_date)
        FROM transactions
        WHERE provider = 'bnz-csv-history'
        """
    ).fetchone()
    return row[0] if row and row[0] else None


def is_covered_by_bnz_csv(connection, transaction, cutoff_date):  #Lets late same-day Akahu rows through
    if not cutoff_date or transaction["posted_date"] > cutoff_date:
        return False

    return imported_transaction_exists(connection, "bnz-csv-history", transaction)


def get_date_query(days, today=None):  #Limits posted transactions to a recent range
    if days < 1 or days > 90:
        raise ValueError("Days must be between 1 and 90")

    today = today or date.today()
    return {
        "start": f"{(today - timedelta(days=days)).isoformat()}T23:59:59.999Z",
        "end": f"{today.isoformat()}T23:59:59.999Z",
    }


def fetch_transactions(path, app_id_token, user_access_token, query=None):  #Follows Akahu pages without writing anything
    if path not in {POSTED_PATH, PENDING_PATH}:
        raise ValueError("Akahu transaction path is not approved")

    transactions = []
    page_query = dict(query or {})

    while True:
        result = fetch_page(path, app_id_token, user_access_token, page_query)
        transactions.extend(result["items"])

        if len(transactions) > MAX_PREVIEW_ITEMS:
            raise ValueError("Transaction preview is too large. Use a shorter date range")

        cursor = result.get("cursor")
        next_cursor = cursor.get("next") if isinstance(cursor, dict) else None

        if next_cursor is None:
            return transactions

        if not isinstance(next_cursor, str) or not next_cursor:
            raise ValueError("Akahu returned an unexpected transaction cursor")

        page_query["cursor"] = next_cursor


def fetch_recent_transactions(days):  #Fetches recent posted and current pending transactions
    app_id_token, user_access_token = get_tokens()
    posted = fetch_transactions(
        POSTED_PATH,
        app_id_token,
        user_access_token,
        get_date_query(days),
    )
    pending = fetch_transactions(PENDING_PATH, app_id_token, user_access_token)
    return posted, pending


def get_description(transaction):  #Uses the merchant name without exposing merchant metadata
    merchant = transaction.get("merchant")
    if isinstance(merchant, dict):
        merchant_name = safe_text(merchant.get("name"), "")
        if merchant_name:
            return merchant_name

    return safe_text(transaction.get("description"), "Unknown transaction")


def get_amount(value):  #Rejects invalid amounts before they reach the preview
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    return amount if amount.is_finite() else None


def get_date(value):  #Keeps the transaction date in the format used by SQLite
    transaction_date = safe_text(value, "")[:10]

    try:
        date.fromisoformat(transaction_date)
    except ValueError as error:
        raise ValueError("Akahu transaction has an invalid date") from error

    return transaction_date


def make_pending_id(account_id, transaction_date, description, amount, position):  #Makes a temporary ID for one pending charge
    values = [account_id, transaction_date, description, str(amount), position]
    private_hash = hashlib.sha256(json.dumps(values).encode("utf-8")).hexdigest()
    return f"akahu-pending-{private_hash[:24]}"


def prepare_transaction(
    connection,
    transaction,
    account,
    status,
    position,
):  #Checks one Akahu transaction before saving it
    transaction_date = get_date(transaction.get("date"))
    description = safe_text(transaction.get("description"), "Unknown transaction")
    amount = get_amount(transaction.get("amount"))

    if amount is None:
        raise ValueError("Akahu transaction has an invalid amount")

    if account["currency"] != "NZD":
        raise ValueError("Akahu BNZ transactions must use NZD")

    if status == "posted":
        transaction_id = transaction.get("_id")
        if not isinstance(transaction_id, str) or not transaction_id.strip():
            raise ValueError("Posted Akahu transaction is missing its ID")
        pending_id = None
    else:
        transaction_id = make_pending_id(
            transaction.get("_account"),
            transaction_date,
            description,
            amount,
            position,
        )
        pending_id = transaction_id

    amount_minor = money_to_minor(str(amount))
    fixed_amounts = get_fixed_amounts(
        connection,
        amount_minor,
        account["currency"],
    )
    merchant = transaction.get("merchant")
    merchant_name = None
    if isinstance(merchant, dict):
        merchant_name = safe_text(merchant.get("name"), "") or None
    category_id = get_matching_category_id(connection, description, merchant_name)

    return {
        "provider_transaction_id": transaction_id,
        "pending_provider_transaction_id": pending_id,
        "account_id": account["database_id"],
        "posted_date": transaction_date,
        "authorized_date": None,
        "description_raw": description,
        "merchant_clean": merchant_name,
        "category_id": category_id,
        "original_currency": account["currency"],
        "original_amount_minor": amount_minor,
        "fx_rate_to_nzd": fixed_amounts[0],
        "fx_rate_to_usd": fixed_amounts[1],
        "fx_rate_source": fixed_amounts[2],
        "fx_rate_date": fixed_amounts[3],
        "amount_nzd_minor_fixed": fixed_amounts[4],
        "amount_usd_minor_fixed": fixed_amounts[5],
        "notes": None,
        "transaction_status": status,
    }


def prepare_transactions(connection, transactions, saved_accounts, status):  #Keeps only transactions for saved BNZ accounts
    prepared = []
    posted_ids = set()
    csv_cutoff = get_bnz_csv_cutoff(connection) if status == "posted" else None

    for position, transaction in enumerate(transactions, start=1):
        if not isinstance(transaction, dict):
            continue

        account = saved_accounts.get(transaction.get("_account"))
        if account is None:
            continue

        ready = prepare_transaction(connection, transaction, account, status, position)

        if status == "posted":
            if is_covered_by_bnz_csv(connection, ready, csv_cutoff):
                continue

            transaction_id = ready["provider_transaction_id"]
            if transaction_id in posted_ids:
                continue
            posted_ids.add(transaction_id)

        prepared.append(ready)

    return prepared


def find_pending_match(connection, transaction):  #Finds the likely pending row for a new posted BNZ charge
    return connection.execute(
        """
        SELECT id, original_amount_minor, review_status, reviewed_at, category_id
        FROM transactions
        WHERE provider = 'akahu-bnz'
          AND transaction_status = 'pending'
          AND account_id = ?
          AND description_raw = ?
          AND ABS(julianday(posted_date) - julianday(?)) <= 14
        ORDER BY ABS(julianday(posted_date) - julianday(?)), id DESC
        LIMIT 1
        """,
        (
            transaction["account_id"],
            transaction["description_raw"],
            transaction["posted_date"],
            transaction["posted_date"],
        ),
    ).fetchone()


def replace_pending_with_posted(connection, pending_row, transaction):  #Keeps review only when the amount stayed the same
    pending_id, pending_amount, pending_review, pending_reviewed_at, pending_category_id = pending_row
    review_status = pending_review if pending_amount == transaction["original_amount_minor"] else "not_reviewed"
    reviewed_at = pending_reviewed_at if pending_amount == transaction["original_amount_minor"] else None
    category_id = transaction["category_id"] or pending_category_id

    connection.execute(
        """
        UPDATE transactions
        SET provider_transaction_id = ?,
            pending_provider_transaction_id = NULL,
            account_id = ?,
            posted_date = ?,
            authorized_date = ?,
            description_raw = ?,
            merchant_clean = COALESCE(?, merchant_clean),
            category_id = ?,
            original_currency = ?,
            original_amount_minor = ?,
            fx_rate_to_nzd = ?,
            fx_rate_to_usd = ?,
            fx_rate_source = ?,
            fx_rate_date = ?,
            amount_nzd_minor_fixed = ?,
            amount_usd_minor_fixed = ?,
            transaction_status = 'posted',
            review_status = ?,
            reviewed_at = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            transaction["provider_transaction_id"],
            transaction["account_id"],
            transaction["posted_date"],
            transaction["authorized_date"],
            transaction["description_raw"],
            transaction["merchant_clean"],
            category_id,
            transaction["original_currency"],
            transaction["original_amount_minor"],
            transaction["fx_rate_to_nzd"],
            transaction["fx_rate_to_usd"],
            transaction["fx_rate_source"],
            transaction["fx_rate_date"],
            transaction["amount_nzd_minor_fixed"],
            transaction["amount_usd_minor_fixed"],
            review_status,
            reviewed_at,
            pending_id,
        ),
    )


def update_posted_transaction(connection, transaction):  #Updates bank fields without losing saved reviews or categories
    connection.execute(
        """
        UPDATE transactions
        SET account_id = ?,
            posted_date = ?,
            authorized_date = ?,
            description_raw = ?,
            merchant_clean = ?,
            original_currency = ?,
            original_amount_minor = ?,
            fx_rate_to_nzd = ?,
            fx_rate_to_usd = ?,
            fx_rate_source = ?,
            fx_rate_date = ?,
            amount_nzd_minor_fixed = ?,
            amount_usd_minor_fixed = ?,
            transaction_status = 'posted',
            pending_provider_transaction_id = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE provider = 'akahu-bnz'
          AND provider_transaction_id = ?
        """,
        (
            transaction["account_id"],
            transaction["posted_date"],
            transaction["authorized_date"],
            transaction["description_raw"],
            transaction["merchant_clean"],
            transaction["original_currency"],
            transaction["original_amount_minor"],
            transaction["fx_rate_to_nzd"],
            transaction["fx_rate_to_usd"],
            transaction["fx_rate_source"],
            transaction["fx_rate_date"],
            transaction["amount_nzd_minor_fixed"],
            transaction["amount_usd_minor_fixed"],
            transaction["provider_transaction_id"],
        ),
    )


def save_fetched_transactions(
    posted,
    pending,
    confirmation,
    database_path=DATABASE_PATH,
):  #Saves posted rows and rebuilds pending rows in one SQLite transaction
    if confirmation != "SAVE":
        raise ValueError("Save cancelled because confirmation was not SAVE")

    saved_accounts = get_saved_bnz_accounts(database_path)
    if not saved_accounts:
        raise ValueError("Save at least one BNZ account before saving transactions")

    posted_added = 0
    posted_updated = 0

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        uncategorized_id = get_category_id(connection, "Uncategorized")
        posted_rows = prepare_transactions(connection, posted, saved_accounts, "posted")
        pending_rows = prepare_transactions(connection, pending, saved_accounts, "pending")

        for transaction in posted_rows:
            existing = connection.execute(
                """
                SELECT id
                FROM transactions
                WHERE provider = 'akahu-bnz'
                  AND provider_transaction_id = ?
                """,
                (transaction["provider_transaction_id"],),
            ).fetchone()

            if existing:
                update_posted_transaction(connection, transaction)
                posted_updated += 1
            else:
                pending_match = find_pending_match(connection, transaction)
                if pending_match:
                    replace_pending_with_posted(connection, pending_match, transaction)
                else:
                    insert_transaction(
                        connection,
                        transaction,
                        uncategorized_id,
                        provider="akahu-bnz",
                    )
                posted_added += 1

        connection.execute(
            """
            DELETE FROM transactions
            WHERE provider = 'akahu-bnz'
              AND transaction_status = 'pending'
            """
        )  #Akahu recommends rebuilding pending data because it can change

        for transaction in pending_rows:
            insert_transaction(
                connection,
                transaction,
                uncategorized_id,
                provider="akahu-bnz",
            )

        connection.commit()

    return posted_added, posted_updated, len(pending_rows)


def get_transaction_preview(posted, pending, saved_accounts):  #Keeps only safe fields from saved BNZ accounts
    preview = []

    for status, transactions in (("posted", posted), ("pending", pending)):
        for transaction in transactions:
            if not isinstance(transaction, dict):
                continue

            account = saved_accounts.get(transaction.get("_account"))
            if account is None:
                continue

            preview.append(
                {
                    "date": safe_text(transaction.get("date"), "unknown date")[:10],
                    "account": account["name"],
                    "description": get_description(transaction),
                    "amount": get_amount(transaction.get("amount")),
                    "currency": account["currency"],
                    "type": safe_text(transaction.get("type"), "UNKNOWN"),
                    "status": status,
                }
            )

    return sorted(preview, key=lambda item: item["date"], reverse=True)


def print_preview(posted, pending, saved_accounts):  #Prints transactions without IDs or private metadata
    preview = get_transaction_preview(posted, pending, saved_accounts)

    if not preview:
        print("No recent BNZ transactions found for saved accounts")
        print()
        print("Preview only. Nothing was saved.")
        return

    print("Recent BNZ transactions:")
    for transaction in preview[:DISPLAY_LIMIT]:
        amount = format_balance(transaction["amount"])
        print(
            f"- {transaction['date']} | {transaction['status']} | "
            f"{safe_text(transaction['account'], 'Unnamed account')} | "
            f"{safe_text(transaction['description'], 'Unknown transaction')} | "
            f"{amount} {transaction['currency']} | {transaction['type']}"
        )

    if len(preview) > DISPLAY_LIMIT:
        print(f"Showing newest {DISPLAY_LIMIT} of {len(preview)} transactions")

    print()
    print("Preview only. Nothing was saved.")


def preview_transactions(days, database_path=DATABASE_PATH):  #Fetches both posted and pending transaction previews
    saved_accounts = get_saved_bnz_accounts(database_path)
    if not saved_accounts:
        raise ValueError("Save at least one BNZ account before previewing transactions")

    posted, pending = fetch_recent_transactions(days)
    print_preview(posted, pending, saved_accounts)


def save_transactions(days, database_path=DATABASE_PATH):  #Fetches and confirms transactions before writing
    run_id = start_run(IMPORT_JOB_NAME, database_path)

    try:
        saved_accounts = get_saved_bnz_accounts(database_path)
        if not saved_accounts:
            raise ValueError("Save at least one BNZ account before saving transactions")

        posted, pending = fetch_recent_transactions(days)
        print_preview(posted, pending, saved_accounts)
        confirmation = input("Type SAVE to store these transactions in the private database: ")
        result = save_fetched_transactions(posted, pending, confirmation, database_path)
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error):
        finish_run(run_id, "failed", "BNZ transaction import failed", database_path)
        raise

    details = (
        f"Posted added: {result[0]}, posted updated: {result[1]}, "
        f"pending saved: {result[2]}"
    )
    finish_run(run_id, "success", details, database_path)

    print(f"Posted transactions added: {result[0]}")
    print(f"Posted transactions updated: {result[1]}")
    print(f"Current pending transactions saved: {result[2]}")


def read_arguments():  #Reads the transaction preview or save command
    parser = argparse.ArgumentParser(description="Preview or save recent BNZ transactions")
    parser.add_argument("command", choices=["preview", "save"])
    parser.add_argument("--days", type=int, default=7)
    return parser.parse_args()


def main():  #Runs the selected BNZ transaction command
    arguments = read_arguments()

    try:
        if arguments.command == "preview":
            preview_transactions(arguments.days)
        elif arguments.command == "save":
            save_transactions(arguments.days)
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage Akahu transactions: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
