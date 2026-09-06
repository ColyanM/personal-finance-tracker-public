import argparse
import csv
import hashlib
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

try:
    from scripts.common import check_database, convert_minor_units, get_rate_for_date, money_to_minor, safe_text
    from scripts.database_backup import BACKUP_DIR, create_backup
    from scripts.import_csv_transactions import get_category_id
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import check_database, convert_minor_units, get_rate_for_date, money_to_minor, safe_text
    from database_backup import BACKUP_DIR, create_backup
    from import_csv_transactions import get_category_id
    from paths import DATABASE_PATH


BNZ_CSV_PROVIDER = "bnz-csv-history"
BNZ_LIVE_PROVIDER = "akahu-bnz"
BNZ_PROVIDERS = (BNZ_CSV_PROVIDER, BNZ_LIVE_PROVIDER)
REQUIRED_COLUMNS = {"Date", "Amount", "Payee", "Particulars", "Code", "Reference", "Tran Type"}
FILE_ACCOUNT_NAMES = {
    "everyday": "Everyday",
    "savings": "Savings",
}
CONFIRMED_PAYEE_RULES = {  #Fictional examples that demonstrate confirmed CSV mappings
    "example property manager": "Rent",
    "example gym": "Gym membership",
    "example fuel station": "Petrol",
    "example parking": "Parking",
    "example subscription": "Subscriptions",
    "interest": "Other income",
    "example restaurant": "Eating out",
    "example transit": "Public transport",
    "example transfer": "Account transfer",
    "example employer two": "Secondary paycheck",
    "example electric utility": "Electric",
    "example recreation provider": "Recreation",
    "example employer one": "Primary paycheck",
    "example medical provider": "Doctor",
    "example hobby shop": "Hobbies",
    "cash withdrawal": "General",
}


def parse_bnz_date(value):  #Turns BNZ dd/mm/yy dates into SQLite dates
    try:
        return datetime.strptime(value.strip(), "%d/%m/%y").date().isoformat()
    except ValueError as error:
        raise ValueError("Date must use the BNZ dd/mm/yy format") from error


def get_account_name(csv_path):  #Uses the file name instead of importing account numbers
    filename = csv_path.name.lower()

    for key, account_name in FILE_ACCOUNT_NAMES.items():
        if key in filename:
            return account_name

    raise ValueError(f"Could not tell which BNZ account this file belongs to: {csv_path.name}")


def read_rows(csv_path):  #Reads one BNZ CSV and keeps only useful transaction fields
    account_name = get_account_name(csv_path)

    with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            raise ValueError(f"{csv_path.name} does not contain column headers")

        missing = REQUIRED_COLUMNS - set(reader.fieldnames)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"{csv_path.name} is missing columns: {missing_text}")

        rows = []
        for row_number, row in enumerate(reader, start=2):
            rows.append(prepare_csv_row(row, row_number, account_name))

    return rows


def clean_description(row):  #Builds a useful label without saving account numbers
    parts = [
        safe_text(row.get("Payee"), ""),
        safe_text(row.get("Particulars"), ""),
        safe_text(row.get("Code"), ""),
        safe_text(row.get("Reference"), ""),
        safe_text(row.get("Tran Type"), ""),
    ]
    return " | ".join(part for part in parts if part) or "BNZ transaction"


def confirmed_category(row):  #Matches repeat BNZ transactions represented by configured examples
    payee = safe_text(row.get("Payee"), "").lower()
    details = clean_description(row).lower()

    if payee == "example payee":
        if "meal split" in details:
            return "Eating out"
        if "general split" in details:
            return "General"
        if "equipment rental" in details:
            return "Hobbies"

    return CONFIRMED_PAYEE_RULES.get(payee)


def prepare_csv_row(row, row_number, account_name):  #Cleans one BNZ row before database work
    amount_text = safe_text(row.get("Amount"), "0").replace(",", "")
    try:
        Decimal(amount_text)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"CSV row {row_number}: Amount is not valid") from error

    transaction_date = parse_bnz_date(safe_text(row.get("Date"), ""))
    payee = safe_text(row.get("Payee"), "BNZ transaction")
    description = clean_description(row)
    transaction_id = make_transaction_id(account_name, row_number, transaction_date, amount_text, description)

    return {
        "account_name": account_name,
        "row_number": row_number,
        "posted_date": transaction_date,
        "description": description,
        "merchant": payee,
        "amount": amount_text,
        "transaction_id": transaction_id,
        "is_transfer": False,
        "category_name": confirmed_category(row),
    }


def make_transaction_id(account_name, row_number, transaction_date, amount_text, description):  #Makes repeat imports stable
    values = [account_name, row_number, transaction_date, amount_text, description]
    row_hash = hashlib.sha256("|".join(str(value) for value in values).encode("utf-8")).hexdigest()
    return f"bnz-csv-{row_hash[:28]}"


def mark_internal_transfers(rows):  #Marks matching Everyday and Savings movements as transfers
    unmatched = {}

    for row in sorted(rows, key=lambda item: (item["posted_date"], item["account_name"], item["row_number"])):
        amount = Decimal(row["amount"])
        key = (row["posted_date"], abs(amount))
        matches = unmatched.setdefault(key, [])
        other = next(
            (
                item
                for item in matches
                if item["account_name"] != row["account_name"] and Decimal(item["amount"]) == -amount
            ),
            None,
        )

        if other:
            other["is_transfer"] = True
            row["is_transfer"] = True
            matches.remove(other)
        else:
            matches.append(row)

    return rows


def get_account_ids(connection):  #Finds the two active BNZ accounts used by the CSV importer
    rows = connection.execute(
        """
        SELECT id, display_name
        FROM accounts
        WHERE provider = ?
          AND display_name IN (?, ?)
          AND is_active = 1
        """,
        (BNZ_LIVE_PROVIDER, "Everyday", "Savings"),
    ).fetchall()
    account_ids = {name: account_id for account_id, name in rows}

    for account_name in FILE_ACCOUNT_NAMES.values():
        if account_name not in account_ids:
            raise ValueError(f'Active BNZ account not found: "{account_name}"')

    return account_ids


def fixed_amounts(connection, amount_minor, transaction_date):  #Locks the USD value using the saved date rate
    rate_to_nzd = Decimal("1")
    rate_to_usd, rate_date, rate_source = get_rate_for_date(connection, "NZD", "USD", transaction_date)
    amount_nzd_minor = amount_minor
    amount_usd_minor = convert_minor_units(amount_minor, rate_to_usd)

    return (
        str(rate_to_nzd),
        str(rate_to_usd),
        f"bnz_csv_using_{rate_source}",
        rate_date,
        amount_nzd_minor,
        amount_usd_minor,
    )


def build_transaction(connection, row, account_ids, uncategorized_id, transfer_id):  #Turns a cleaned row into a database row
    amount_minor = money_to_minor(row["amount"])
    fixed = fixed_amounts(connection, amount_minor, row["posted_date"])
    category_name = "Account transfer" if row["is_transfer"] else row["category_name"]
    category_id = transfer_id if row["is_transfer"] else uncategorized_id

    if category_name and not row["is_transfer"]:
        category_id = get_category_id(connection, category_name)

    return {
        "provider_transaction_id": row["transaction_id"],
        "account_id": account_ids[row["account_name"]],
        "posted_date": row["posted_date"],
        "description_raw": row["description"],
        "merchant_clean": row["merchant"],
        "category_id": category_id,
        "original_amount_minor": amount_minor,
        "fx_rate_to_nzd": fixed[0],
        "fx_rate_to_usd": fixed[1],
        "fx_rate_source": fixed[2],
        "fx_rate_date": fixed[3],
        "amount_nzd_minor_fixed": fixed[4],
        "amount_usd_minor_fixed": fixed[5],
        "review_status": "reviewed" if category_name else "not_reviewed",
    }


def insert_bnz_transaction(connection, transaction):  #Saves one BNZ CSV row without extra private fields
    reviewed_at = "CURRENT_TIMESTAMP" if transaction["review_status"] == "reviewed" else "NULL"
    connection.execute(
        f"""
        INSERT INTO transactions (
            provider,
            provider_transaction_id,
            account_id,
            posted_date,
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
            transaction_status,
            review_status,
            reviewed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, {reviewed_at})
        ON CONFLICT (provider, provider_transaction_id) DO NOTHING
        """,
        (
            BNZ_CSV_PROVIDER,
            transaction["provider_transaction_id"],
            transaction["account_id"],
            transaction["posted_date"],
            transaction["description_raw"],
            transaction["merchant_clean"],
            transaction["category_id"],
            "NZD",
            transaction["original_amount_minor"],
            transaction["fx_rate_to_nzd"],
            transaction["fx_rate_to_usd"],
            transaction["fx_rate_source"],
            transaction["fx_rate_date"],
            transaction["amount_nzd_minor_fixed"],
            transaction["amount_usd_minor_fixed"],
            "posted",
            transaction["review_status"],
        ),
    )


def load_csv_rows(everyday_path=None, savings_path=None):  #Loads whichever BNZ CSV files were selected
    rows = []

    for csv_path in [everyday_path, savings_path]:
        if csv_path:
            rows.extend(read_rows(csv_path))

    if not rows:
        raise ValueError("Choose --everyday, --savings, or both")

    return mark_internal_transfers(rows)


def preview_import(everyday_path=None, savings_path=None):  #Shows what would import without writing
    rows = load_csv_rows(everyday_path, savings_path)
    account_counts = {}
    transfer_count = 0
    confirmed_count = 0

    for row in rows:
        account_counts[row["account_name"]] = account_counts.get(row["account_name"], 0) + 1
        if row["is_transfer"]:
            transfer_count += 1
        if row["category_name"]:
            confirmed_count += 1

    print(f"BNZ CSV rows: {len(rows)}")
    for account_name, count in sorted(account_counts.items()):
        print(f"{account_name}: {count}")
    print(f"Confirmed category rows: {confirmed_count}")
    print(f"Matched internal transfers: {transfer_count}")
    print("Import currency if saved: NZD")


def import_bnz_csv(everyday_path=None, savings_path=None, confirmation=None, database_path=DATABASE_PATH):  #Imports reviewed transfers and uncategorized spending
    if confirmation != "IMPORT":
        raise ValueError("Add --confirm IMPORT before writing BNZ CSV history")

    rows = load_csv_rows(everyday_path, savings_path)
    check_database(database_path)
    added = 0
    skipped = 0
    transfers = 0

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        account_ids = get_account_ids(connection)
        uncategorized_id = get_category_id(connection, "Uncategorized")
        transfer_id = get_category_id(connection, "Account transfer")

        for row in rows:
            transaction = build_transaction(connection, row, account_ids, uncategorized_id, transfer_id)
            before = connection.total_changes
            insert_bnz_transaction(connection, transaction)
            if connection.total_changes > before:
                added += 1
                if row["is_transfer"]:
                    transfers += 1
            else:
                skipped += 1

        connection.commit()

    return {"added": added, "skipped": skipped, "transfers": transfers}


def count_bnz_transactions(database_path=DATABASE_PATH):  #Counts local BNZ transaction rows only
    check_database(database_path)
    placeholders = ", ".join("?" for _ in BNZ_PROVIDERS)

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            f"SELECT COUNT(*) FROM transactions WHERE provider IN ({placeholders})",
            BNZ_PROVIDERS,
        ).fetchone()[0]


def clear_bnz_transactions(confirmation, database_path=DATABASE_PATH, backup_dir=BACKUP_DIR):  #Clears old BNZ rows without touching Monarch or Plaid
    if confirmation != "CLEAR_BNZ_TRANSACTIONS":
        raise ValueError("Clear confirmation must be CLEAR_BNZ_TRANSACTIONS")

    check_database(database_path)
    found_count = count_bnz_transactions(database_path)
    backup_path = None

    if found_count:
        backup_path = create_backup(database_path, backup_dir, label="before-clear-bnz-transactions")

    with closing(sqlite3.connect(database_path)) as connection:
        placeholders = ", ".join("?" for _ in BNZ_PROVIDERS)
        deleted = connection.execute(
            f"DELETE FROM transactions WHERE provider IN ({placeholders})",
            BNZ_PROVIDERS,
        ).rowcount
        connection.commit()

    return deleted, backup_path


def read_arguments():  #Reads the BNZ CSV command
    parser = argparse.ArgumentParser(description="Import BNZ transaction CSV history")
    parser.add_argument("command", choices=["preview", "import", "clear"])
    parser.add_argument("--everyday", type=Path)
    parser.add_argument("--savings", type=Path)
    parser.add_argument("--confirm")
    return parser.parse_args()


def main():  #Runs the selected BNZ CSV command
    arguments = read_arguments()

    try:
        if arguments.command == "preview":
            preview_import(arguments.everyday, arguments.savings)
        elif arguments.command == "import":
            result = import_bnz_csv(
                arguments.everyday,
                arguments.savings,
                arguments.confirm,
            )
            print(f"BNZ transactions added: {result['added']}")
            print(f"Duplicates skipped: {result['skipped']}")
            print(f"Matched transfers reviewed: {result['transfers']}")
        elif arguments.command == "clear":
            found_count = count_bnz_transactions()
            if arguments.confirm != "CLEAR_BNZ_TRANSACTIONS":
                print(f"BNZ transactions found: {found_count}")
                print("No changes made")
                print("Add --confirm CLEAR_BNZ_TRANSACTIONS to delete only BNZ transaction rows")
                return 0

            deleted, backup_path = clear_bnz_transactions(arguments.confirm)
            if backup_path is not None:
                print(f"Backup created: {backup_path.name}")
            print(f"BNZ transactions deleted: {deleted}")
            print("Monarch, Plaid, accounts, balances, budgets, and rules were kept")
    except (FileNotFoundError, OSError, ValueError, csv.Error, sqlite3.Error) as error:
        print(f"Could not import BNZ CSV history: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
