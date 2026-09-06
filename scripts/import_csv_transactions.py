import argparse
import csv
import hashlib
import sqlite3
import sys
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

try:
    from scripts.common import (
        check_database,
        convert_minor_units,
        get_latest_rate,
        money_to_minor,
    )
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import (
        check_database,
        convert_minor_units,
        get_latest_rate,
        money_to_minor,
    )
    from paths import DATABASE_PATH

SUPPORTED_CURRENCIES = {"NZD", "USD"}  #Keeps imports limited to currencies this app supports
TRANSACTION_STATUSES = {"pending", "posted"}  #Keeps the pending/final charge choices simple
REQUIRED_COLUMNS = {"account_name", "posted_date", "description", "amount", "currency"}  #Minimum CSV fields needed


def make_transaction_id(row):  #Builds an ID when the CSV does not provide one
    original_values = [
        row["account_name"].strip(),
        row["posted_date"].strip(),
        row["amount"].strip(),
        row["currency"].strip().upper(),
        row["description"].strip(),
    ]
    original_text = "|".join(original_values)
    transaction_hash = hashlib.sha256(original_text.encode("utf-8")).hexdigest()
    return f"csv-{transaction_hash[:24]}"  #Makes the same ID if the same row is imported again


def validate_date(value, field_name):  #Makes sure dates are stored in one format
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must use YYYY-MM-DD") from error


def get_account(connection, account_name):  #Finds the active account named in the CSV
    accounts = connection.execute(  #Transactions are linked by account name in the CSV
        """
        SELECT id, display_name
        FROM accounts
        WHERE display_name = ?
          AND is_active = 1
        """,
        (account_name,),
    ).fetchall()

    if not accounts:
        raise ValueError(f'Active account not found: "{account_name}"')

    if len(accounts) > 1:
        raise ValueError(f'More than one active account is named: "{account_name}"')

    return accounts[0][0]


def get_category_id(connection, category_name):  #Finds the active category named in the CSV
    category = connection.execute(  #Only active categories can be used on new imports
        """
        SELECT id
        FROM categories
        WHERE name = ?
          AND is_active = 1
        """,
        (category_name,),
    ).fetchone()

    if category is None:
        raise ValueError(
            f'Active category not found: "{category_name}". '
            "Run python scripts/categories.py seed or add the category first"
        )

    return category[0]


def get_fixed_amounts(connection, amount_minor, currency):  #Works out both NZD and USD values once
    if currency == "NZD":
        rate_to_nzd = Decimal("1")
        rate_to_usd, rate_date, rate_source = get_latest_rate(
            connection, "NZD", "USD"
        )
        amount_nzd_minor = amount_minor
        amount_usd_minor = convert_minor_units(amount_minor, rate_to_usd)
    else:
        rate_to_usd = Decimal("1")
        rate_to_nzd, rate_date, rate_source = get_latest_rate(
            connection, "USD", "NZD"
        )
        amount_usd_minor = amount_minor
        amount_nzd_minor = convert_minor_units(amount_minor, rate_to_nzd)

    return (  #Saves both currency values now so old transactions do not change later
        str(rate_to_nzd),
        str(rate_to_usd),
        rate_source,
        rate_date,
        amount_nzd_minor,
        amount_usd_minor,
    )


def prepare_row(connection, row):  #Cleans and validates one CSV row before saving it
    account_name = row["account_name"].strip()  #Cleans CSV text before storing it
    posted_date = row["posted_date"].strip()
    description = row["description"].strip()
    currency = row["currency"].strip().upper()
    status = (row.get("status") or "posted").strip().lower()
    category_name = (row.get("category") or "").strip()
    transaction_id = (row.get("transaction_id") or "").strip() or make_transaction_id(row)  #Missing IDs get a stable generated ID
    pending_transaction_id = (row.get("pending_transaction_id") or "").strip() or None
    authorized_date = (row.get("authorized_date") or "").strip() or None
    merchant = (row.get("merchant") or "").strip() or None
    notes = (row.get("notes") or "").strip() or None

    if not account_name or not posted_date or not description:
        raise ValueError("Account name, posted date, and description cannot be empty")

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Currency must be NZD or USD")

    if status not in TRANSACTION_STATUSES:
        raise ValueError("Status must be pending or posted")

    validate_date(posted_date, "posted_date")

    if authorized_date is not None:
        validate_date(authorized_date, "authorized_date")

    if status == "pending" and pending_transaction_id is None:
        pending_transaction_id = transaction_id  #Keeps the pending ID for matching the final charge later

    account_id = get_account(connection, account_name)
    amount_minor = money_to_minor(row["amount"].strip())
    fixed_amounts = get_fixed_amounts(connection, amount_minor, currency)  #Locks in the exchange rate now
    category_id = None

    if category_name:
        category_id = get_category_id(connection, category_name)

    return {  #Keeps the cleaned row together so insert and update code can share it
        "provider_transaction_id": transaction_id,
        "pending_provider_transaction_id": pending_transaction_id,
        "account_id": account_id,
        "posted_date": posted_date,
        "authorized_date": authorized_date,
        "description_raw": description,
        "merchant_clean": merchant,
        "category_id": category_id,
        "category_was_provided": bool(category_name),
        "original_currency": currency,
        "original_amount_minor": amount_minor,
        "fx_rate_to_nzd": fixed_amounts[0],
        "fx_rate_to_usd": fixed_amounts[1],
        "fx_rate_source": fixed_amounts[2],
        "fx_rate_date": fixed_amounts[3],
        "amount_nzd_minor_fixed": fixed_amounts[4],
        "amount_usd_minor_fixed": fixed_amounts[5],
        "notes": notes,
        "transaction_status": status,
    }


def find_existing_transaction(connection, transaction_id):  #Checks if this exact CSV transaction was already saved
    return connection.execute(  #Checks duplicates before trying to insert
        """
        SELECT id, transaction_status, pending_provider_transaction_id
        FROM transactions
        WHERE provider = 'manual_csv'
          AND provider_transaction_id = ?
        """,
        (transaction_id,),
    ).fetchone()


def find_pending_transaction(connection, pending_transaction_id):  #Looks for a matching pending charge to replace
    if pending_transaction_id is None:
        return None

    return connection.execute(  #Only matches an exact pending ID so similar purchases stay separate
        """
        SELECT id
        FROM transactions
        WHERE provider = 'manual_csv'
          AND pending_provider_transaction_id = ?
          AND transaction_status = 'pending'
        """,
        (pending_transaction_id,),
    ).fetchone()


def insert_transaction(
    connection,
    transaction,
    uncategorized_id,
    provider="manual_csv",
):  #Saves a brand new imported transaction
    category_id = transaction["category_id"] or uncategorized_id  #Blank categories start as Uncategorized

    connection.execute(
        """
        INSERT INTO transactions (
            provider,
            provider_transaction_id,
            pending_provider_transaction_id,
            account_id,
            posted_date,
            authorized_date,
            description_raw,
            merchant_clean,
            category_id,
            original_currency,
            original_amount_minor,
            fx_rate_to_nzd,
            fx_rate_to_usd,
            fx_rate_source,
            fx_rate_date,
            amount_nzd_minor_fixed,
            amount_usd_minor_fixed,
            notes,
            transaction_status,
            review_status
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            provider,
            transaction["provider_transaction_id"],
            transaction["pending_provider_transaction_id"],
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
            transaction["notes"],
            transaction["transaction_status"],
            "not_reviewed",
        ),
    )


def replace_pending_transaction(connection, pending_id, transaction):  #Turns a pending charge into its final posted charge
    category_id = transaction["category_id"] if transaction["category_was_provided"] else None  #Keeps the pending category unless the final row gives one
    pending_row = connection.execute(
        """
        SELECT original_amount_minor, review_status, reviewed_at
        FROM transactions
        WHERE id = ?
        """,
        (pending_id,),
    ).fetchone()
    review_status = "not_reviewed"
    reviewed_at = None
    if pending_row and pending_row[0] == transaction["original_amount_minor"]:
        review_status = pending_row[1]  #Keeps review done when the final amount did not change
        reviewed_at = pending_row[2]

    connection.execute(  #Updates the pending row so the same charge is not stored twice
        """
        UPDATE transactions
        SET provider_transaction_id = ?,
            account_id = ?,
            posted_date = ?,
            authorized_date = ?,
            description_raw = ?,
            merchant_clean = COALESCE(?, merchant_clean),
            category_id = COALESCE(?, category_id),
            original_currency = ?,
            original_amount_minor = ?,
            fx_rate_to_nzd = ?,
            fx_rate_to_usd = ?,
            fx_rate_source = ?,
            fx_rate_date = ?,
            amount_nzd_minor_fixed = ?,
            amount_usd_minor_fixed = ?,
            notes = COALESCE(?, notes),
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
            transaction["notes"],
            review_status,
            reviewed_at,
            pending_id,
        ),
    )


def import_transactions(csv_path, database_path=DATABASE_PATH):  #Imports a CSV and returns added matched skipped counts
    check_database(database_path)
    added_count = 0  #New transactions saved
    matched_count = 0  #Pending rows changed into posted rows
    skipped_count = 0  #Duplicates ignored

    with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)  #Uses column names instead of column positions

        if reader.fieldnames is None:
            raise ValueError("The CSV file does not contain column headers")

        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"The CSV file is missing required columns: {missing}")

        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")  #Makes SQLite enforce account and category links
            uncategorized_id = get_category_id(connection, "Uncategorized")

            for row_number, row in enumerate(reader, start=2):  #Starts at 2 because row 1 is the header
                try:
                    transaction = prepare_row(connection, row)

                    existing = find_existing_transaction(  #Checks duplicate IDs before looking for pending matches
                        connection, transaction["provider_transaction_id"]
                    )

                    if existing:
                        if (
                            existing[1] == "pending"
                            and transaction["transaction_status"] == "posted"
                        ):
                            replace_pending_transaction(connection, existing[0], transaction)  #Some providers keep the same ID after posting
                            matched_count += 1
                        else:
                            skipped_count += 1
                        continue

                    pending = None  #Other providers give the posted charge a new ID
                    if transaction["transaction_status"] == "posted":
                        pending = find_pending_transaction(
                            connection,
                            transaction["pending_provider_transaction_id"],
                        )

                    if pending:
                        replace_pending_transaction(connection, pending[0], transaction)
                        matched_count += 1
                    else:
                        insert_transaction(connection, transaction, uncategorized_id)
                        added_count += 1
                except (KeyError, ValueError, sqlite3.Error) as error:
                    raise ValueError(f"CSV row {row_number}: {error}") from error

            connection.commit()  #Saves all rows together after the file is valid

    return added_count, matched_count, skipped_count


def read_arguments():  #Reads the CSV path from PowerShell
    parser = argparse.ArgumentParser(description="Import manual transactions from CSV")
    parser.add_argument("csv_path", type=Path)
    return parser.parse_args()


def main():  #Runs the import and prints the summary
    arguments = read_arguments()

    try:
        added, matched, skipped = import_transactions(arguments.csv_path)
    except (FileNotFoundError, OSError, ValueError, csv.Error, sqlite3.Error) as error:
        print(f"Could not import transactions: {error}", file=sys.stderr)
        return 1

    print(f"Added transactions: {added}")
    print(f"Pending charges changed to posted: {matched}")
    print(f"Duplicates skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
