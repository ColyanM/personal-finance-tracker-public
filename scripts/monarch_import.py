import argparse
import csv
import hashlib
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

try:
    from scripts.common import (
        check_database,
        convert_minor_units,
        get_rate_for_date,
        money_to_minor,
        safe_text,
    )
    from scripts.database_backup import BACKUP_DIR, create_backup
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import (
        check_database,
        convert_minor_units,
        get_rate_for_date,
        money_to_minor,
        safe_text,
    )
    from database_backup import BACKUP_DIR, create_backup
    from paths import DATABASE_PATH

SUPPORTED_CURRENCIES = {"NZD", "USD"}  #Monarch exports need an explicit currency when importing
BALANCE_COLUMNS = {"Date", "Balance", "Account"}
TRANSACTION_COLUMNS = {"Date", "Merchant", "Category", "Account", "Amount"}
PROVIDER = "monarch-history"
PLAID_PROVIDERS = ("plaid-production-us", "plaid-sandbox-us", "plaid-us")

CATEGORY_RENAMES = {  #Fictional examples for aligning older exports with the starter categories
    "subscription": "Subscriptions",
    "bus and train": "Public transport",
    "vehicle incident": "Auto incident",
    "car loan": "Car loan",
    "household help": "Household services",
    "eating out": "Eating out",
    "gas": "Petrol",
    "hotel": "Hotels",
    "property income": "Other income",
    "moving": "Moving expenses",
    "personal spending": "Personal spending",
    "travel reimbursement": "Reimbursement",
    "lawn": "Home",
    "lpg": "Home gas",
    "mortgage": "Mortgage",
    "paychecks": "Other income",
    "phone bill": "Mobile phone",
    "power": "Electric",
    "reimbursement": "Reimbursement",
    "telecommunications": "Telecommunications",
    "student loans": "Student loans",
    "training": "General",
    "water": "Water",
    "pet food": "Pet food",
    "account transfer": "Account transfer",
    "transfer": "Account transfer",
    "other income": "Other income",
}

PRIMARY_PAYCHECK_MERCHANTS = {
    "example employer one",
    "example retirement contribution",
}
SECONDARY_PAYCHECK_MERCHANTS = {
    "example employer two",
    "example benefits provider",
}
OTHER_INCOME_MERCHANTS = {"interest", "interest income"}


def validate_date(value, field_name):  #Keeps imported dates in one SQLite-friendly format
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ValueError(f"{field_name} must use YYYY-MM-DD") from error


def read_csv_rows(csv_path, required_columns):  #Reads one Monarch CSV using its header names
    with csv_path.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.DictReader(csv_file)
        if reader.fieldnames is None:
            raise ValueError(f"{csv_path.name} does not contain column headers")

        missing = required_columns - set(reader.fieldnames)
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"{csv_path.name} is missing columns: {missing_text}")

        return list(reader)


def make_stable_id(parts):  #Makes the same ID every time the same Monarch row is imported
    text = "|".join(str(part or "").strip() for part in parts)
    row_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"monarch-{row_hash[:28]}"


def get_account_lookup(connection):  #Matches Monarch names to existing accounts when possible
    rows = connection.execute(
        """
        SELECT id, display_name, native_currency
        FROM accounts
        """
    ).fetchall()
    return {name: {"id": account_id, "currency": currency} for account_id, name, currency in rows}


def get_or_create_history_account(connection, account_name, currency):  #Stores old transaction accounts without making them active
    lookup = get_account_lookup(connection)
    if account_name in lookup:
        return lookup[account_name]["id"], lookup[account_name]["currency"]

    provider_account_id = make_stable_id([account_name])
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
            balance_as_of,
            is_active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider, provider_account_id)
        DO UPDATE SET
            display_name = excluded.display_name,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            PROVIDER,
            provider_account_id,
            account_name,
            "Monarch",
            "historical",
            "asset",
            currency,
            0,
            date.today().isoformat(),
            0,
        ),
    )
    account_id = connection.execute(
        """
        SELECT id
        FROM accounts
        WHERE provider = ?
          AND provider_account_id = ?
        """,
        (PROVIDER, provider_account_id),
    ).fetchone()[0]
    return account_id, currency


def get_category_lookup(connection):  #Matches categories without caring about upper/lower case
    rows = connection.execute(
        """
        SELECT id, name
        FROM categories
        WHERE is_active = 1
        """
    ).fetchall()
    return {name.lower(): category_id for category_id, name in rows}


def get_mapped_category_name(category_name, merchant_name):  #Handles the few Monarch categories that need merchant context
    cleaned = safe_text(category_name, "Uncategorized")
    merchant = safe_text(merchant_name, "").lower()

    if merchant in OTHER_INCOME_MERCHANTS:
        return "Other income"

    if "example savings transfer" in merchant:
        return "Account transfer"

    if merchant == "example employer reimbursement" and cleaned.lower() == "general":
        return "Primary paycheck"

    if cleaned.lower() == "paychecks":
        if merchant in PRIMARY_PAYCHECK_MERCHANTS:
            return "Primary paycheck"
        if merchant in SECONDARY_PAYCHECK_MERCHANTS:
            return "Secondary paycheck"

    mapped_name = CATEGORY_RENAMES.get(cleaned.lower(), cleaned)
    return mapped_name


def get_category_id(category_lookup, category_name, merchant_name, missing_categories):  #Falls back to Uncategorized if an old name is not configured
    cleaned = safe_text(category_name, "Uncategorized")
    mapped_name = get_mapped_category_name(cleaned, merchant_name)
    category_id = category_lookup.get(mapped_name.lower())

    if category_id is not None:
        return category_id

    missing_categories.add(cleaned)
    return category_lookup["uncategorized"]


def fixed_amounts(connection, amount_minor, currency, transaction_date):  #Stores both values using the transaction date rate
    if currency == "NZD":
        rate_to_nzd = "1"
        rate_to_usd, rate_date, rate_source = get_rate_for_date(connection, "NZD", "USD", transaction_date)
        amount_nzd_minor = amount_minor
        amount_usd_minor = convert_minor_units(amount_minor, rate_to_usd)
    else:
        rate_to_usd = "1"
        rate_to_nzd, rate_date, rate_source = get_rate_for_date(connection, "USD", "NZD", transaction_date)
        amount_usd_minor = amount_minor
        amount_nzd_minor = convert_minor_units(amount_minor, rate_to_nzd)

    return (
        str(rate_to_nzd),
        str(rate_to_usd),
        f"monarch_export_using_{rate_source}",
        rate_date,
        amount_nzd_minor,
        amount_usd_minor,
    )


def import_balances(balance_path, currency, database_path=DATABASE_PATH):  #Saves balance history without adding old accounts to the accounts page
    check_database(database_path)
    rows = read_csv_rows(balance_path, BALANCE_COLUMNS)
    saved_count = 0
    account_names = set()

    with closing(sqlite3.connect(database_path)) as connection:
        lookup = get_account_lookup(connection)

        for row_number, row in enumerate(rows, start=2):
            try:
                account_name = safe_text(row["Account"], "Unknown account")
                balance_date = validate_date(row["Date"].strip(), "Date")
                balance_minor = money_to_minor(row["Balance"].strip())
                account_id = lookup.get(account_name, {}).get("id")
                account_names.add(account_name)

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
                    ON CONFLICT (source, account_name, balance_date, currency)
                    DO UPDATE SET
                        account_id = excluded.account_id,
                        balance_minor = excluded.balance_minor,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (PROVIDER, account_name, account_id, balance_date, currency, balance_minor),
                )
                saved_count += 1
            except (KeyError, ValueError, sqlite3.Error) as error:
                raise ValueError(f"Balance row {row_number}: {error}") from error

        connection.commit()

    return {"balance_rows": saved_count, "balance_accounts": len(account_names)}


def import_transactions(transaction_path, currency, database_path=DATABASE_PATH):  #Saves Monarch history as already-reviewed posted transactions
    check_database(database_path)
    rows = read_csv_rows(transaction_path, TRANSACTION_COLUMNS)
    added_count = 0
    skipped_count = 0
    history_accounts = set()
    missing_categories = set()

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        category_lookup = get_category_lookup(connection)
        if "uncategorized" not in category_lookup:
            raise ValueError("Uncategorized category is missing. Run python scripts\\categories.py seed first")

        for row_number, row in enumerate(rows, start=2):
            try:
                account_name = safe_text(row["Account"], "Unknown account")
                posted_date = validate_date(row["Date"].strip(), "Date")
                merchant = safe_text(row["Merchant"], "Unknown merchant")
                original_statement = safe_text(row.get("Original Statement"), merchant)
                notes = safe_text(row.get("Notes"), "")
                tags = safe_text(row.get("Tags"), "")
                owner = safe_text(row.get("Owner"), "")
                amount_minor = money_to_minor(row["Amount"].strip())
                transaction_id = make_stable_id(
                    [posted_date, account_name, merchant, original_statement, amount_minor]
                )
                account_id, account_currency = get_or_create_history_account(connection, account_name, currency)
                history_accounts.add(account_name)
                category_id = get_category_id(category_lookup, row["Category"], merchant, missing_categories)
                used_currency = account_currency if account_currency in SUPPORTED_CURRENCIES else currency
                fixed = fixed_amounts(connection, amount_minor, used_currency, posted_date)
                note_parts = [part for part in [notes, tags, owner] if part]
                saved_notes = " | ".join(note_parts) or None

                result = connection.execute(
                    """
                    INSERT INTO transactions (
                        provider,
                        provider_transaction_id,
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
                        review_status,
                        reviewed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (provider, provider_transaction_id) DO NOTHING
                    """,
                    (
                        PROVIDER,
                        transaction_id,
                        account_id,
                        posted_date,
                        posted_date,
                        original_statement,
                        merchant,
                        category_id,
                        used_currency,
                        amount_minor,
                        fixed[0],
                        fixed[1],
                        fixed[2],
                        fixed[3],
                        fixed[4],
                        fixed[5],
                        saved_notes,
                        "posted",
                        "reviewed",
                    ),
                )
                if result.rowcount:
                    added_count += 1
                else:
                    skipped_count += 1
            except (KeyError, ValueError, sqlite3.Error) as error:
                raise ValueError(f"Transaction row {row_number}: {error}") from error

        connection.commit()

    return {
        "transactions_added": added_count,
        "transactions_skipped": skipped_count,
        "transaction_accounts": len(history_accounts),
        "missing_categories": sorted(missing_categories),
    }


def preview_file(csv_path, required_columns):  #Shows row count, date range, and account count without saving anything
    rows = read_csv_rows(csv_path, required_columns)
    dates = []
    accounts = set()

    for row in rows:
        if row.get("Date"):
            dates.append(validate_date(row["Date"].strip(), "Date"))
        if row.get("Account"):
            accounts.add(safe_text(row["Account"], "Unknown account"))

    return {
        "rows": len(rows),
        "accounts": len(accounts),
        "first_date": min(dates) if dates else "-",
        "last_date": max(dates) if dates else "-",
    }


def preview_missing_categories(transaction_path, database_path=DATABASE_PATH):  #Checks category names without importing transactions
    rows = read_csv_rows(transaction_path, TRANSACTION_COLUMNS)
    missing_categories = set()

    with closing(sqlite3.connect(database_path)) as connection:
        category_lookup = get_category_lookup(connection)

        for row in rows:
            category_name = safe_text(row["Category"], "Uncategorized")
            merchant_name = safe_text(row.get("Merchant"), "")
            mapped_name = get_mapped_category_name(category_name, merchant_name)
            if mapped_name.lower() not in category_lookup:
                missing_categories.add(category_name)

    return sorted(missing_categories)


def clear_monarch_history(database_path=DATABASE_PATH):  #Deletes only prior Monarch imports so a clean import can be rerun
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        deleted_transactions = connection.execute(
            "DELETE FROM transactions WHERE provider = ?",
            (PROVIDER,),
        ).rowcount
        deleted_balances = connection.execute(
            "DELETE FROM account_balance_history WHERE source = ?",
            (PROVIDER,),
        ).rowcount
        deleted_accounts = connection.execute(
            "DELETE FROM accounts WHERE provider = ?",
            (PROVIDER,),
        ).rowcount
        connection.commit()

    return deleted_transactions, deleted_balances, deleted_accounts


def count_transactions(database_path=DATABASE_PATH):  #Shows how many transaction rows would be removed
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]


def clear_all_transactions(
    confirmation,
    database_path=DATABASE_PATH,
    backup_dir=BACKUP_DIR,
):  #Clears transaction history but keeps accounts, balances, budgets, rules, and secrets
    if confirmation != "CLEAR_TRANSACTIONS":
        raise ValueError("Clear confirmation must be CLEAR_TRANSACTIONS")

    check_database(database_path)
    found_count = count_transactions(database_path)
    backup_path = None

    if found_count:
        backup_path = create_backup(
            database_path,
            backup_dir,
            label="before-clear-transactions",
        )

    with closing(sqlite3.connect(database_path)) as connection:
        deleted_count = connection.execute("DELETE FROM transactions").rowcount
        connection.commit()

    return deleted_count, backup_path


def count_plaid_transactions(database_path=DATABASE_PATH):  #Checks only local Plaid transactions so accounts and balances stay saved
    check_database(database_path)
    placeholders = ", ".join("?" for _ in PLAID_PROVIDERS)

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            f"""
            SELECT COUNT(*)
            FROM transactions
            WHERE provider IN ({placeholders})
            """,
            PLAID_PROVIDERS,
        ).fetchone()[0]


def clear_plaid_transactions(
    confirmation,
    database_path=DATABASE_PATH,
    backup_dir=BACKUP_DIR,
):  #Clears only duplicate-prone Plaid transactions before loading Monarch history
    if confirmation != "CLEAR_PLAID_TRANSACTIONS":
        raise ValueError("Clear confirmation must be CLEAR_PLAID_TRANSACTIONS")

    check_database(database_path)
    found_count = count_plaid_transactions(database_path)
    backup_path = None

    if found_count:
        backup_path = create_backup(
            database_path,
            backup_dir,
            label="before-clear-plaid-transactions",
        )

    with closing(sqlite3.connect(database_path)) as connection:
        placeholders = ", ".join("?" for _ in PLAID_PROVIDERS)
        deleted_count = connection.execute(
            f"""
            DELETE FROM transactions
            WHERE provider IN ({placeholders})
            """,
            PLAID_PROVIDERS,
        ).rowcount
        connection.commit()

    return deleted_count, backup_path


def read_arguments():  #Keeps the importer explicit to prevent accidental history writes
    parser = argparse.ArgumentParser(description="Import Monarch history into Finance Hub")
    parser.add_argument(
        "command",
        choices=["preview", "import", "clear", "clear-plaid-transactions", "clear-transactions"],
    )
    parser.add_argument("--balances", type=Path)
    parser.add_argument("--transactions", type=Path)
    parser.add_argument("--currency", choices=sorted(SUPPORTED_CURRENCIES))
    parser.add_argument("--confirm")
    return parser.parse_args()


def main():  #Runs preview or import from PowerShell
    arguments = read_arguments()

    if arguments.command == "clear":
        if arguments.confirm != "CLEAR":
            print("Add --confirm CLEAR before deleting Monarch history", file=sys.stderr)
            return 1

        try:
            transactions, balances, accounts = clear_monarch_history()
        except (FileNotFoundError, sqlite3.Error) as error:
            print(f"Could not clear Monarch history: {error}", file=sys.stderr)
            return 1

        print(f"Monarch transactions deleted: {transactions}")
        print(f"Monarch balance rows deleted: {balances}")
        print(f"Monarch helper accounts deleted: {accounts}")
        return 0

    if arguments.command == "clear-plaid-transactions":
        try:
            found_count = count_plaid_transactions()
            if arguments.confirm != "CLEAR_PLAID_TRANSACTIONS":
                print(f"Plaid transactions found: {found_count}")
                print("No changes made")
                print("Add --confirm CLEAR_PLAID_TRANSACTIONS to delete only Plaid transactions")
                return 0

            deleted_count, backup_path = clear_plaid_transactions(arguments.confirm)
        except (FileNotFoundError, ValueError, sqlite3.Error) as error:
            print(f"Could not clear Plaid transactions: {error}", file=sys.stderr)
            return 1

        if backup_path is not None:
            print(f"Backup created: {backup_path.name}")
        print(f"Plaid transactions deleted: {deleted_count}")
        print("Plaid accounts, balances, loan details, and investment details were kept")
        return 0

    if arguments.command == "clear-transactions":
        try:
            found_count = count_transactions()
            if arguments.confirm != "CLEAR_TRANSACTIONS":
                print(f"Transactions found: {found_count}")
                print("No changes made")
                print("Add --confirm CLEAR_TRANSACTIONS to delete all transaction rows")
                return 0

            deleted_count, backup_path = clear_all_transactions(arguments.confirm)
        except (FileNotFoundError, ValueError, sqlite3.Error) as error:
            print(f"Could not clear transactions: {error}", file=sys.stderr)
            return 1

        if backup_path is not None:
            print(f"Backup created: {backup_path.name}")
        print(f"Transactions deleted: {deleted_count}")
        print("Accounts, balances, budgets, rules, and bank connections were kept")
        return 0

    if not arguments.balances and not arguments.transactions:
        print("Choose --balances, --transactions, or both", file=sys.stderr)
        return 1

    if arguments.currency not in SUPPORTED_CURRENCIES:
        print("Choose --currency USD or --currency NZD", file=sys.stderr)
        return 1

    try:
        check_database(DATABASE_PATH)
        if arguments.command == "preview":
            if arguments.balances:
                summary = preview_file(arguments.balances, BALANCE_COLUMNS)
                print(
                    f"Balance rows: {summary['rows']} | accounts: {summary['accounts']} | "
                    f"{summary['first_date']} to {summary['last_date']}"
                )
            if arguments.transactions:
                summary = preview_file(arguments.transactions, TRANSACTION_COLUMNS)
                print(
                    f"Transaction rows: {summary['rows']} | accounts: {summary['accounts']} | "
                    f"{summary['first_date']} to {summary['last_date']}"
                )
                missing_categories = preview_missing_categories(arguments.transactions)
                if missing_categories:
                    print("Categories that would import as Uncategorized:")
                    for category in missing_categories[:20]:
                        print(f"- {category}")
                    if len(missing_categories) > 20:
                        print(f"- and {len(missing_categories) - 20} more")
            print(f"Import currency if saved: {arguments.currency}")
            return 0

        if arguments.confirm != "IMPORT":
            raise ValueError("Add --confirm IMPORT before writing Monarch history")

        if arguments.balances:
            summary = import_balances(arguments.balances, arguments.currency)
            print(f"Balance rows saved: {summary['balance_rows']}")
            print(f"Balance accounts found: {summary['balance_accounts']}")

        if arguments.transactions:
            summary = import_transactions(arguments.transactions, arguments.currency)
            print(f"Transactions added: {summary['transactions_added']}")
            print(f"Duplicates skipped: {summary['transactions_skipped']}")
            print(f"Transaction accounts found: {summary['transaction_accounts']}")
            if summary["missing_categories"]:
                print("Categories saved as Uncategorized:")
                for category in summary["missing_categories"][:20]:
                    print(f"- {category}")
                if len(summary["missing_categories"]) > 20:
                    print(f"- and {len(summary['missing_categories']) - 20} more")
    except (FileNotFoundError, OSError, ValueError, csv.Error, sqlite3.Error) as error:
        print(f"Could not import Monarch history: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
