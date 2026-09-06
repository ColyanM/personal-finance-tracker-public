import argparse
import csv
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

try:
    from scripts.common import check_database, format_minor, money_to_minor
    from scripts.paths import DATABASE_PATH, EXPORT_DIR, PROJECT_ROOT, private_data_dir_is_safe
except ModuleNotFoundError:
    from common import check_database, format_minor, money_to_minor
    from paths import DATABASE_PATH, EXPORT_DIR, PROJECT_ROOT, private_data_dir_is_safe

EXPORT_COLUMNS = [  #Keeps the exported CSV columns in one easy place
    "id",
    "posted_date",
    "account",
    "description",
    "category",
    "amount",
    "currency",
    "amount_nzd",
    "amount_usd",
    "transaction_status",
    "review_status",
    "notes",
]


def check_export_folder(export_folder):  #Keeps exported transaction data out of Git and OneDrive
    if not private_data_dir_is_safe(export_folder, PROJECT_ROOT):
        raise ValueError("Export folder must be outside the project and OneDrive")


def default_export_name():  #Makes a dated file name when none is supplied
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"finance_hub-transactions-{timestamp}.csv"


def get_export_path(output_path=None, export_dir=EXPORT_DIR):  #Chooses a safe export path
    check_export_folder(export_dir)

    if output_path is None:
        return export_dir / default_export_name()

    output_path = Path(output_path)
    if output_path.name != output_path.as_posix():
        export_folder = output_path.parent
        check_export_folder(export_folder)
        return output_path

    return export_dir / output_path.name


def list_exports(export_dir=EXPORT_DIR):  #Lists newest transaction exports first
    check_export_folder(export_dir)
    if not export_dir.exists():
        return []

    return sorted(
        export_dir.glob("*.csv"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )


def cleanup_exports(keep, confirmation, export_dir=EXPORT_DIR):  #Deletes older exports only after confirmation
    if confirmation != "DELETE_OLD_EXPORTS":
        raise ValueError("Export cleanup confirmation must be DELETE_OLD_EXPORTS")

    if keep < 1:
        raise ValueError("Keep at least one export")

    exports = list_exports(export_dir)
    deleted = []
    for export_path in exports[keep:]:
        export_path.unlink()
        deleted.append(export_path.name)

    return deleted


def get_category_id(connection, category_name):  #Finds an active category by name
    category = connection.execute(  #Edits can only use active categories
        """
        SELECT id
        FROM categories
        WHERE name = ?
          AND is_active = 1
        """,
        (category_name,),
    ).fetchone()

    if category is None:
        raise ValueError(f'Active category not found: "{category_name}"')

    return category[0]


def get_uncategorized_id(connection):  #Gets the holding category used for unknown spending
    return get_category_id(connection, "Uncategorized")


def list_transactions(
    account=None,
    category=None,
    transaction_status=None,
    review_status=None,
    start=None,
    end=None,
    search=None,
    limit=None,
    database_path=DATABASE_PATH,
):  #Lists transactions using the supplied filters
    check_database(database_path)
    where_sql, values = build_transaction_filter_sql(
        account,
        category,
        transaction_status,
        review_status,
        start,
        end,
        search,
    )
    limit_sql = ""
    if limit is not None:
        limit = int(limit)
        if limit < 1:
            raise ValueError("Transaction limit must be at least 1")
        limit_sql = "LIMIT ?"
        values.append(limit)

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(  #Shows the account and category names instead of only IDs
            f"""
            SELECT
                t.id,
                t.posted_date,
                a.display_name,
                COALESCE(t.merchant_clean, t.description_raw),
                c.name,
                t.original_amount_minor,
                t.original_currency,
                t.amount_nzd_minor_fixed,
                t.amount_usd_minor_fixed,
                t.transaction_status,
                t.review_status,
                COALESCE(t.notes, '')
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            {where_sql}
            ORDER BY t.posted_date DESC, t.id DESC
            {limit_sql}
            """,
            values,
        ).fetchall()


def count_transactions(
    account=None,
    category=None,
    transaction_status=None,
    review_status=None,
    start=None,
    end=None,
    search=None,
    database_path=DATABASE_PATH,
):  #Counts rows with the same filters without loading every transaction
    check_database(database_path)
    where_sql, values = build_transaction_filter_sql(
        account,
        category,
        transaction_status,
        review_status,
        start,
        end,
        search,
    )

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            f"""
            SELECT COUNT(*)
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            {where_sql}
            """,
            values,
        ).fetchone()[0]


def build_transaction_filter_sql(
    account=None,
    category=None,
    transaction_status=None,
    review_status=None,
    start=None,
    end=None,
    search=None,
):  #Builds the shared filters for list and count
    filters = []  #Builds the WHERE clause from the supplied filters
    values = []

    if account:
        filters.append("a.display_name = ?")
        values.append(account)

    if category:
        filters.append(
            """
            (
                c.name = ?
                OR EXISTS (
                    SELECT 1
                    FROM transaction_splits s
                    JOIN categories split_category ON split_category.id = s.category_id
                    WHERE s.transaction_id = t.id
                      AND split_category.name = ?
                )
            )
            """
        )
        values.extend([category, category])

    if transaction_status:
        filters.append("t.transaction_status = ?")
        values.append(transaction_status)

    if review_status:
        filters.append("t.review_status = ?")
        values.append(review_status)

    if start:
        filters.append("t.posted_date >= ?")
        values.append(start)

    if end:
        filters.append("t.posted_date <= ?")
        values.append(end)

    if search:
        filters.append(
            "(lower(COALESCE(t.merchant_clean, '')) LIKE ? OR lower(t.description_raw) LIKE ?)"
        )
        search_text = f"%{search.lower()}%"
        values.extend([search_text, search_text])

    where_sql = ""  #No WHERE clause is needed when no filters are used
    if filters:
        where_sql = "WHERE " + " AND ".join(filters)  #Keeps filtering simple without a separate query for each option

    return where_sql, values


def export_transactions(
    output_path=None,
    account=None,
    category=None,
    transaction_status=None,
    review_status=None,
    start=None,
    end=None,
    search=None,
    database_path=DATABASE_PATH,
    export_dir=EXPORT_DIR,
):  #Exports transactions to a CSV for later use
    output_path = get_export_path(output_path, export_dir)
    transactions = list_transactions(
        account=account,
        category=category,
        transaction_status=transaction_status,
        review_status=review_status,
        start=start,
        end=end,
        search=search,
        database_path=database_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)  #Creates the folder if it is not there yet

    with output_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()

        for transaction in transactions:
            writer.writerow(
                {
                    "id": transaction[0],
                    "posted_date": transaction[1],
                    "account": transaction[2],
                    "description": transaction[3],
                    "category": transaction[4],
                    "amount": format_minor(transaction[5]),
                    "currency": transaction[6],
                    "amount_nzd": format_minor(transaction[7]),
                    "amount_usd": format_minor(transaction[8]),
                    "transaction_status": transaction[9],
                    "review_status": transaction[10],
                    "notes": transaction[11],
                }
            )

    return len(transactions), output_path


def get_transaction(connection, transaction_id):  #Gets one transaction row or raises a clear error
    transaction = connection.execute(  #Used before edits and manual matching
        """
        SELECT *
        FROM transactions
        WHERE id = ?
        """,
        (transaction_id,),
    ).fetchone()

    if transaction is None:
        raise ValueError(f"Transaction not found: {transaction_id}")

    return transaction


def list_transaction_splits(transaction_ids, database_path=DATABASE_PATH):  #Gets split lines for the rows currently on screen
    if not transaction_ids:
        return {}

    check_database(database_path)
    placeholders = ",".join("?" for _ in transaction_ids)

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            f"""
            SELECT
                s.transaction_id,
                c.name,
                s.amount_nzd_minor_fixed,
                s.amount_usd_minor_fixed,
                s.split_order
            FROM transaction_splits s
            JOIN categories c ON c.id = s.category_id
            WHERE s.transaction_id IN ({placeholders})
            ORDER BY s.transaction_id, s.split_order
            """,
            transaction_ids,
        ).fetchall()

    split_map = {}
    for transaction_id, category, amount_nzd, amount_usd, split_order in rows:
        split_map.setdefault(transaction_id, []).append(
            {
                "category": category,
                "amount_nzd_minor": amount_nzd,
                "amount_usd_minor": amount_usd,
                "split_order": split_order,
            }
        )

    return split_map


def split_display_amounts(total_minor, split_lines, currency):  #Checks split rows match the bank amount
    total_abs = abs(total_minor)
    amounts = [abs(money_to_minor(split["amount"])) for split in split_lines]

    if total_abs == 0:
        raise ValueError("A zero amount transaction cannot be split")

    if any(amount <= 0 for amount in amounts):
        raise ValueError("Split amounts must be more than zero")

    if sum(amounts) != total_abs:
        raise ValueError(
            f"Split amounts must add up to {format_minor(total_abs)} {currency}"
        )

    sign = -1 if total_minor < 0 else 1
    return [amount * sign for amount in amounts]


def matching_currency_splits(total_minor, display_total_minor, display_amounts):  #Keeps the other currency tied to the same split percentages
    total_abs = abs(total_minor)
    display_abs = abs(display_total_minor)
    if display_abs == 0:
        raise ValueError("A zero amount transaction cannot be split")

    sign = -1 if total_minor < 0 else 1
    converted = []
    used_abs = 0
    for display_amount in display_amounts[:-1]:
        amount_abs = int(
            (Decimal(total_abs) * Decimal(abs(display_amount)) / Decimal(display_abs)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        converted.append(amount_abs * sign)
        used_abs += amount_abs

    converted.append((total_abs - used_abs) * sign)
    return converted


def clean_split_lines(split_lines):  #Normalizes split rows from the API before saving
    cleaned = []
    for split in split_lines:
        category = (split.get("category") or "").strip()
        amount = str(split.get("amount") or "").strip()
        cleaned.append({"category": category, "amount": amount})

    if len(cleaned) < 2:
        raise ValueError("Add at least two split categories")

    categories = [split["category"] for split in cleaned]
    if any(not category for category in categories):
        raise ValueError("Choose a category for every split")
    if len(set(categories)) != len(categories):
        raise ValueError("Choose each split category only once")

    return cleaned


def save_transaction_split(
    transaction_id,
    currency,
    split_lines=None,
    first_category=None,
    first_amount=None,
    second_category=None,
    second_amount=None,
    database_path=DATABASE_PATH,
):  #Splits one bank transaction into multiple budget categories
    check_database(database_path)
    currency = currency.upper()
    if currency not in {"NZD", "USD"}:
        raise ValueError("Split currency must be NZD or USD")

    if split_lines is None:
        split_lines = [
            {"category": first_category, "amount": first_amount},
            {"category": second_category, "amount": second_amount},
        ]
    split_lines = clean_split_lines(split_lines)

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        transaction = get_transaction(connection, transaction_id)
        column_names = [row[1] for row in connection.execute("PRAGMA table_info(transactions)").fetchall()]
        transaction_row = dict(zip(column_names, transaction))

        nzd_total = transaction_row["amount_nzd_minor_fixed"]
        usd_total = transaction_row["amount_usd_minor_fixed"]
        if currency == "NZD":
            nzd_amounts = split_display_amounts(nzd_total, split_lines, currency)
            usd_amounts = matching_currency_splits(usd_total, nzd_total, nzd_amounts)
        else:
            usd_amounts = split_display_amounts(usd_total, split_lines, currency)
            nzd_amounts = matching_currency_splits(nzd_total, usd_total, usd_amounts)

        category_ids = [get_category_id(connection, split["category"]) for split in split_lines]

        connection.execute("DELETE FROM transaction_splits WHERE transaction_id = ?", (transaction_id,))
        for split_order, (category_id, amount_nzd, amount_usd) in enumerate(
            zip(category_ids, nzd_amounts, usd_amounts),
            start=1,
        ):
            connection.execute(
                """
                INSERT INTO transaction_splits (
                    transaction_id,
                    split_order,
                    category_id,
                    amount_nzd_minor_fixed,
                    amount_usd_minor_fixed
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (transaction_id, split_order, category_id, amount_nzd, amount_usd),
            )
        connection.commit()


def clear_transaction_split(transaction_id, database_path=DATABASE_PATH):  #Removes a split and goes back to the transaction category
    check_database(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        result = connection.execute(
            "DELETE FROM transaction_splits WHERE transaction_id = ?",
            (transaction_id,),
        )
        connection.commit()

    if result.rowcount == 0:
        raise ValueError(f"Transaction split not found: {transaction_id}")


def set_review_status(transaction_id, review_status, database_path=DATABASE_PATH):  #Marks a transaction reviewed or not reviewed
    check_database(database_path)
    reviewed_at = datetime.now().isoformat(timespec="seconds")  #Stores when the transaction was reviewed

    if review_status == "not_reviewed":
        reviewed_at = None

    with closing(sqlite3.connect(database_path)) as connection:
        result = connection.execute(  #Reviewed transactions keep the time they were checked
            """
            UPDATE transactions
            SET review_status = ?,
                reviewed_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (review_status, reviewed_at, transaction_id),
        )
        connection.commit()

    if result.rowcount == 0:
        raise ValueError(f"Transaction not found: {transaction_id}")


def edit_transaction(
    transaction_id,
    category=None,
    merchant=None,
    notes=None,
    database_path=DATABASE_PATH,
):  #Changes transaction fields that can be edited manually
    check_database(database_path)
    updates = []  #Only changes fields included in the command
    values = []

    with closing(sqlite3.connect(database_path)) as connection:
        if category is not None:
            updates.append("category_id = ?")
            values.append(get_category_id(connection, category))  #Stores the ID while accepting a category name

        if merchant is not None:
            updates.append("merchant_clean = ?")
            values.append(merchant)

        if notes is not None:
            updates.append("notes = ?")
            values.append(notes)

        if not updates:
            raise ValueError("Choose at least one value to edit")

        updates.append("updated_at = CURRENT_TIMESTAMP")  #Tracks the latest transaction change
        values.append(transaction_id)
        result = connection.execute(
            f"""
            UPDATE transactions
            SET {", ".join(updates)}
            WHERE id = ?
            """,
            values,
        )
        connection.commit()

    if result.rowcount == 0:
        raise ValueError(f"Transaction not found: {transaction_id}")


def manually_match_pending(pending_id, posted_id, database_path=DATABASE_PATH):  #Merges two rows when they are the same charge
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        pending = get_transaction(connection, pending_id)
        posted = get_transaction(connection, posted_id)

        column_names = [  #Turns SQLite rows into dictionaries so the match code is easier to read
            row[1]
            for row in connection.execute("PRAGMA table_info(transactions)").fetchall()
        ]
        pending_row = dict(zip(column_names, pending))
        posted_row = dict(zip(column_names, posted))

        if pending_row["transaction_status"] != "pending":
            raise ValueError("The pending transaction must have status pending")

        if posted_row["transaction_status"] != "posted":
            raise ValueError("The posted transaction must have status posted")

        uncategorized_id = get_uncategorized_id(connection)
        category_id = posted_row["category_id"]
        if category_id == uncategorized_id:
            category_id = pending_row["category_id"]  #Keeps the pending category if the posted row is Uncategorized

        pending_provider_transaction_id = (  #Keeps the original pending ID for future duplicate checks
            pending_row["pending_provider_transaction_id"]
            or pending_row["provider_transaction_id"]
        )
        amount_did_not_change = pending_row["original_amount_minor"] == posted_row["original_amount_minor"]
        review_status = pending_row["review_status"] if amount_did_not_change else "not_reviewed"
        reviewed_at = pending_row["reviewed_at"] if amount_did_not_change else None

        connection.execute("DELETE FROM transactions WHERE id = ?", (posted_id,))  #Frees the posted ID before the pending row takes it
        connection.execute(  #Keeps one row after manually matching a pending and posted charge
            """
            UPDATE transactions
            SET provider_transaction_id = ?,
                pending_provider_transaction_id = ?,
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
                notes = COALESCE(?, notes),
                transaction_status = 'posted',
                review_status = ?,
                reviewed_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                posted_row["provider_transaction_id"],
                pending_provider_transaction_id,
                posted_row["account_id"],
                posted_row["posted_date"],
                posted_row["authorized_date"],
                posted_row["description_raw"],
                posted_row["merchant_clean"],
                category_id,
                posted_row["original_currency"],
                posted_row["original_amount_minor"],
                posted_row["fx_rate_to_nzd"],
                posted_row["fx_rate_to_usd"],
                posted_row["fx_rate_source"],
                posted_row["fx_rate_date"],
                posted_row["amount_nzd_minor_fixed"],
                posted_row["amount_usd_minor_fixed"],
                posted_row["notes"],
                review_status,
                reviewed_at,
                pending_id,
            ),
        )
        connection.commit()


def print_transactions(transactions):  #Prints transactions in a simple readable format
    if not transactions:
        print("No transactions found")
        return

    for transaction in transactions:
        print(  #First line is the quick transaction summary
            f"{transaction[0]} | {transaction[1]} | {transaction[2]} | "
            f"{transaction[3]} | {format_minor(transaction[5])} {transaction[6]} | "
            f"{transaction[4]} | {transaction[9]} | {transaction[10]}"
        )
        print(
            f"  NZD {format_minor(transaction[7])} | "
            f"USD {format_minor(transaction[8])} | {transaction[11]}"
        )


def print_exports(exports):  #Prints export file names without reading the CSV contents
    if not exports:
        print("No transaction exports found")
        return

    for export_path in exports:
        print(export_path.name)


def add_transaction_filters(parser):  #Adds the same filters to list and export
    parser.add_argument("--account")
    parser.add_argument("--category")
    parser.add_argument("--status", choices=["pending", "posted"])
    parser.add_argument("--review", choices=["not_reviewed", "reviewed"])
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--search")


def read_arguments():  #Reads the transaction command and filters
    parser = argparse.ArgumentParser(description="Manage transactions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List transactions")
    add_transaction_filters(list_parser)

    export_parser = subparsers.add_parser("export", help="Export transactions to CSV")
    export_parser.add_argument("--output", type=Path)
    add_transaction_filters(export_parser)

    subparsers.add_parser("exports", help="List transaction exports")

    cleanup_exports_parser = subparsers.add_parser("cleanup-exports", help="Delete older transaction exports")
    cleanup_exports_parser.add_argument("--keep", type=int, default=10)
    cleanup_exports_parser.add_argument("--confirm", required=True)

    subparsers.add_parser("pending", help="Show pending transactions")
    subparsers.add_parser("not-reviewed", help="Show transactions that still need review")

    reviewed_parser = subparsers.add_parser("mark-reviewed", help="Mark a transaction reviewed")
    reviewed_parser.add_argument("--id", required=True, type=int)

    not_reviewed_parser = subparsers.add_parser(
        "mark-not-reviewed",
        help="Mark a transaction not reviewed",
    )
    not_reviewed_parser.add_argument("--id", required=True, type=int)

    edit_parser = subparsers.add_parser("edit", help="Edit transaction details")
    edit_parser.add_argument("--id", required=True, type=int)
    edit_parser.add_argument("--category")
    edit_parser.add_argument("--merchant")
    edit_parser.add_argument("--notes")

    match_parser = subparsers.add_parser(
        "match-pending",
        help="Merge a posted charge into a pending charge",
    )
    match_parser.add_argument("--pending-id", required=True, type=int)
    match_parser.add_argument("--posted-id", required=True, type=int)

    return parser.parse_args()


def main():  #Runs the right transaction command from the arguments
    arguments = read_arguments()

    try:
        if arguments.command == "list":
            transactions = list_transactions(
                account=arguments.account,
                category=arguments.category,
                transaction_status=arguments.status,
                review_status=arguments.review,
                start=arguments.start,
                end=arguments.end,
                search=arguments.search,
            )
            print_transactions(transactions)
        elif arguments.command == "export":
            exported_count, output_path = export_transactions(
                arguments.output,
                account=arguments.account,
                category=arguments.category,
                transaction_status=arguments.status,
                review_status=arguments.review,
                start=arguments.start,
                end=arguments.end,
                search=arguments.search,
            )
            print(f"Exported transactions: {exported_count}")
            print(f"Export file: {output_path}")
        elif arguments.command == "exports":
            print_exports(list_exports())
        elif arguments.command == "cleanup-exports":
            deleted = cleanup_exports(arguments.keep, arguments.confirm)
            print(f"Old exports deleted: {len(deleted)}")
        elif arguments.command == "pending":
            print_transactions(list_transactions(transaction_status="pending"))
        elif arguments.command == "not-reviewed":
            print_transactions(list_transactions(review_status="not_reviewed"))
        elif arguments.command == "mark-reviewed":
            set_review_status(arguments.id, "reviewed")
            print(f"Marked transaction {arguments.id} as reviewed")
        elif arguments.command == "mark-not-reviewed":
            set_review_status(arguments.id, "not_reviewed")
            print(f"Marked transaction {arguments.id} as not reviewed")
        elif arguments.command == "edit":
            edit_transaction(
                arguments.id,
                category=arguments.category,
                merchant=arguments.merchant,
                notes=arguments.notes,
            )
            print(f"Updated transaction {arguments.id}")
        elif arguments.command == "match-pending":
            manually_match_pending(arguments.pending_id, arguments.posted_id)
            print(
                f"Matched pending transaction {arguments.pending_id} "
                f"to posted transaction {arguments.posted_id}"
            )
    except (FileNotFoundError, OSError, ValueError, csv.Error, sqlite3.Error) as error:
        print(f"Could not manage transactions: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
