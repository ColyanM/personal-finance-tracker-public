import argparse
import sqlite3
import sys
from contextlib import closing

try:
    from scripts.common import check_database
    from scripts.paths import DATABASE_PATH
    from scripts.transactions import get_category_id, get_uncategorized_id
except ModuleNotFoundError:
    from common import check_database
    from paths import DATABASE_PATH
    from transactions import get_category_id, get_uncategorized_id


STARTING_RULES = [
    ("Credit Card Payment", "Credit card payment", 10),
    ("Example Card Payment", "Credit card payment", 10),
    ("Savings INTERNET XFR", "Account transfer", 20),
    ("Everyday INTERNET XFR", "Account transfer", 20),
    ("To Checking", "Account transfer", 20),
    ("From Savings", "Account transfer", 20),
    ("Account transfer", "Account transfer", 20),
    ("Student Loan Payment", "Student loans", 30),
    ("Example Gym", "Gym membership", 100),
    ("Example Parking", "Parking", 100),
    ("Example Transit", "Public transport", 100),
    ("Example Subscription", "Subscriptions", 100),
    ("Interest Income", "Other income", 100),
    ("Example Employer One", "Primary paycheck", 100),
    ("Example Employer Two", "Secondary paycheck", 100),
    ("Example Fuel Station", "Petrol", 100),
    ("Example Grocery Store", "Groceries", 100),
    ("Example Restaurant", "Eating out", 100),
    ("Example Property Manager", "Rent", 100),
    ("Example Electric Utility", "Electric", 100),
    ("Example Gas Utility", "Home gas", 100),
    ("Example Recreation Provider", "Recreation", 100),
    ("Example Medical Provider", "Doctor", 100),
    ("Example Hobby Shop", "Hobbies", 100),
    ("Cash Withdrawal", "General", 100),
    ("Example Airline", "Flights", 100),
    ("Example Hotel", "Hotels", 100),
    ("Example Tour Operator", "Excursions", 100),
    ("Example Car Rental", "Vacation transportation", 100),
]  #Specific payment and transfer rules stay first so they do not count as spending


def add_rule(
    match_text,
    category,
    priority=100,
    database_path=DATABASE_PATH,
):  #Adds one simple text matching rule
    check_database(database_path)
    match_text = match_text.strip()

    if not match_text:
        raise ValueError("Rule text cannot be empty")

    if priority < 1:
        raise ValueError("Rule priority must be at least 1")

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        category_id = get_category_id(connection, category)
        result = connection.execute(
            """
            INSERT INTO category_rules (match_text, category_id, priority)
            VALUES (?, ?, ?)
            ON CONFLICT (match_text) DO UPDATE SET
                category_id = excluded.category_id,
                priority = excluded.priority,
                is_active = 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (match_text, category_id, priority),
        )
        connection.commit()
        return result.lastrowid


def seed_rules(database_path=DATABASE_PATH):  #Adds starter rules without replacing existing changes
    check_database(database_path)
    saved_count = 0

    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")

        for match_text, category, priority in STARTING_RULES:
            category_id = get_category_id(connection, category)
            result = connection.execute(
                """
                INSERT INTO category_rules (match_text, category_id, priority)
                VALUES (?, ?, ?)
                ON CONFLICT (match_text) DO NOTHING
                """,
                (match_text, category_id, priority),
            )
            if result.rowcount:
                saved_count += 1

        connection.commit()

    return saved_count, len(STARTING_RULES)


def list_rules(database_path=DATABASE_PATH):  #Lists rules without showing any bank details
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            """
            SELECT cr.id, cr.match_text, c.name, cr.priority, cr.is_active
            FROM category_rules cr
            JOIN categories c ON c.id = cr.category_id
            ORDER BY cr.priority, cr.id
            """
        ).fetchall()


def deactivate_rule(rule_id, database_path=DATABASE_PATH):  #Turns off a rule without deleting it
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        result = connection.execute(
            """
            UPDATE category_rules
            SET is_active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (rule_id,),
        )
        connection.commit()

    if result.rowcount == 0:
        raise ValueError(f"Category rule not found: {rule_id}")


def clean_rule_text(value):  #Makes matching less picky about spaces and uppercase letters
    return " ".join((value or "").lower().split())


def get_matching_category_id(connection, description, merchant=None):  #Uses the first active rule that matches
    search_text = clean_rule_text(f"{description or ''} {merchant or ''}")
    rules = connection.execute(
        """
        SELECT cr.category_id, cr.match_text
        FROM category_rules cr
        JOIN categories c ON c.id = cr.category_id
        WHERE cr.is_active = 1
          AND c.is_active = 1
        ORDER BY cr.priority, cr.id
        """
    ).fetchall()

    for category_id, match_text in rules:
        if clean_rule_text(match_text) in search_text:
            return category_id

    return None


def apply_rules(database_path=DATABASE_PATH):  #Categorizes existing Uncategorized transactions only
    check_database(database_path)
    updated_count = 0

    with closing(sqlite3.connect(database_path)) as connection:
        uncategorized_id = get_uncategorized_id(connection)
        transactions = connection.execute(
            """
            SELECT id, description_raw, merchant_clean
            FROM transactions
            WHERE category_id = ?
            ORDER BY id
            """,
            (uncategorized_id,),
        ).fetchall()

        for transaction_id, description, merchant in transactions:
            category_id = get_matching_category_id(connection, description, merchant)
            if category_id is None:
                continue

            connection.execute(
                """
                UPDATE transactions
                SET category_id = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND category_id = ?
                """,
                (category_id, transaction_id, uncategorized_id),
            )
            updated_count += 1

        connection.commit()

    return updated_count


def cleanup_summary(database_path=DATABASE_PATH, limit=10, include_rule_matches=True):  #Shows what is left to clean up without changing data
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        uncategorized_id = get_uncategorized_id(connection)
        needs_review = connection.execute(
            """
            SELECT COUNT(*)
            FROM transactions
            WHERE review_status = 'not_reviewed'
            """
        ).fetchone()[0]
        uncategorized = connection.execute(
            """
            SELECT COUNT(*)
            FROM transactions
            WHERE category_id = ?
            """,
            (uncategorized_id,),
        ).fetchone()[0]
        can_apply = 0

        if include_rule_matches:
            transactions = connection.execute(
                """
                SELECT id, description_raw, merchant_clean
                FROM transactions
                WHERE category_id = ?
                ORDER BY id
                """,
                (uncategorized_id,),
            ).fetchall()

            for _, description, merchant in transactions:
                if get_matching_category_id(connection, description, merchant):
                    can_apply += 1

        top_rows = connection.execute(
            """
            SELECT
                COALESCE(NULLIF(merchant_clean, ''), description_raw) AS label,
                COUNT(*) AS row_count,
                SUM(ABS(amount_nzd_minor_fixed)) AS amount_nzd,
                MAX(posted_date) AS last_seen
            FROM transactions
            WHERE category_id = ?
            GROUP BY label
            ORDER BY row_count DESC, amount_nzd DESC
            LIMIT ?
            """,
            (uncategorized_id, limit),
        ).fetchall()

    return {
        "needs_review": needs_review,
        "uncategorized": uncategorized,
        "can_apply": can_apply,
        "top_uncategorized": [
            {
                "label": label,
                "count": row_count,
                "amount_nzd_minor": amount_nzd or 0,
                "last_seen": last_seen,
            }
            for label, row_count, amount_nzd, last_seen in top_rows
        ],
    }


def print_cleanup_summary(summary):  #Prints the cleanup summary for PowerShell
    print(f"Needs review: {summary['needs_review']}")
    print(f"Uncategorized: {summary['uncategorized']}")
    print(f"Can be categorized by active rules: {summary['can_apply']}")

    if not summary["top_uncategorized"]:
        print("No Uncategorized transactions found")
        return

    print("\nTop Uncategorized groups")
    for row in summary["top_uncategorized"]:
        amount = row["amount_nzd_minor"] / 100
        print(
            f"- {row['label']} | {row['count']} rows | "
            f"${amount:,.2f} NZD | last {row['last_seen']}"
        )


def print_rules(rules):  #Prints the saved rule list
    if not rules:
        print("No category rules found")
        return

    for rule_id, match_text, category, priority, is_active in rules:
        status = "active" if is_active else "inactive"
        print(f"{rule_id} | {match_text} -> {category} | priority {priority} | {status}")


def read_arguments():  #Reads the category rule command
    parser = argparse.ArgumentParser(description="Manage simple transaction category rules")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("seed", help="Add the starting category rules")
    subparsers.add_parser("list", help="List category rules")
    subparsers.add_parser("apply", help="Apply rules to Uncategorized transactions")
    summary_parser = subparsers.add_parser("summary", help="Show remaining cleanup work")
    summary_parser.add_argument("--limit", type=int, default=10)

    add_parser = subparsers.add_parser("add", help="Add a category rule")
    add_parser.add_argument("--text", required=True)
    add_parser.add_argument("--category", required=True)
    add_parser.add_argument("--priority", type=int, default=100)

    deactivate_parser = subparsers.add_parser("deactivate", help="Deactivate a category rule")
    deactivate_parser.add_argument("--id", type=int, required=True)
    return parser.parse_args()


def main():  #Runs the selected category rule command
    arguments = read_arguments()

    try:
        if arguments.command == "seed":
            saved, total = seed_rules()
            print(f"Saved {saved} of {total} starting category rules")
        elif arguments.command == "list":
            print_rules(list_rules())
        elif arguments.command == "apply":
            print(f"Transactions categorized: {apply_rules()}")
        elif arguments.command == "summary":
            print_cleanup_summary(cleanup_summary(limit=arguments.limit))
        elif arguments.command == "add":
            add_rule(arguments.text, arguments.category, arguments.priority)
            print("Saved category rule")
        elif arguments.command == "deactivate":
            deactivate_rule(arguments.id)
            print(f"Deactivated category rule: {arguments.id}")
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage category rules: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
