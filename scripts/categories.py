import argparse
import json
import sqlite3
import sys
from contextlib import closing

try:
    from scripts.common import check_database, parse_active
    from scripts.paths import DATABASE_PATH, SEED_DATA_DIR
except ModuleNotFoundError:
    from common import check_database, parse_active
    from paths import DATABASE_PATH, SEED_DATA_DIR

SEED_PATH = SEED_DATA_DIR / "categories_seed.json"  #Keeps the starter categories easy to edit
CATEGORY_TYPES = {"expense", "income", "transfer"}
CATEGORY_GROUP_ORDER = (  #Keeps dropdowns in a useful default order
    "Income",
    "Fixed Expenses",
    "Variable Expenses",
    "Debts",
    "Pets",
    "Investments",
    "Travel",
    "Transfers",
    "Holding Categories",
)


def category_group_rank(group_name):  #Puts the standard groups above groups added later
    try:
        return CATEGORY_GROUP_ORDER.index(group_name)
    except ValueError:
        return len(CATEGORY_GROUP_ORDER)


def get_type_flags(category_type):  #Turns category type text into database flags
    category_type = category_type.lower()

    if category_type not in CATEGORY_TYPES:
        raise ValueError("Category type must be expense, income, or transfer")

    return category_type == "income", category_type == "transfer"  #SQLite stores category type as two simple flags


def get_type_name(is_income, is_transfer):  #Turns database flags back into text
    if is_income:
        return "income"

    if is_transfer:
        return "transfer"

    return "expense"


def add_category(
    name,
    group_name,
    category_type="expense",
    database_path=DATABASE_PATH,
):  #Adds one category that can be used on transactions
    check_database(database_path)
    name = name.strip()
    group_name = group_name.strip()
    is_income, is_transfer = get_type_flags(category_type)  #Converts expense income transfer into database flags

    if not name:
        raise ValueError("Category name cannot be empty")

    if not group_name:
        raise ValueError("Category group cannot be empty")

    with closing(sqlite3.connect(database_path)) as connection:  #Closes the database cleanly on Windows
        connection.execute(
            """
            INSERT INTO categories (
                name,
                group_name,
                is_income,
                is_transfer
            )
            VALUES (?, ?, ?, ?)
            """,
            (name, group_name, is_income, is_transfer),
        )
        connection.commit()


def seed_categories(database_path=DATABASE_PATH, seed_path=SEED_PATH):  #Loads the starter category list
    check_database(database_path)

    with seed_path.open(encoding="utf-8") as seed_file:
        categories = json.load(seed_file)

    if not isinstance(categories, list) or not categories:
        raise ValueError("The category seed file must contain a list of categories")

    added_count = 0  #Counts only the categories that were actually inserted

    with closing(sqlite3.connect(database_path)) as connection:
        for category in categories:
            if not {"name", "group_name", "type"}.issubset(category):
                raise ValueError("Every seed category needs a name, group_name, and type")

            is_income, is_transfer = get_type_flags(category["type"])
            result = connection.execute(  #Keeps existing category changes when seeding again
                """
                INSERT INTO categories (
                    name,
                    group_name,
                    is_income,
                    is_transfer
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT (name) DO NOTHING
                """,
                (
                    category["name"].strip(),
                    category["group_name"].strip(),
                    is_income,
                    is_transfer,
                ),
            )
            added_count += result.rowcount

        connection.commit()

    return added_count, len(categories)


def update_category(
    name,
    new_name=None,
    group_name=None,
    category_type=None,
    is_active=None,
    database_path=DATABASE_PATH,
):  #Changes category details without deleting old transaction links
    check_database(database_path)
    updates = []  #Builds only the fields selected for change
    values = []

    if new_name is not None:
        updates.append("name = ?")
        values.append(new_name.strip())

    if group_name is not None:
        updates.append("group_name = ?")
        values.append(group_name.strip())

    if category_type is not None:
        is_income, is_transfer = get_type_flags(category_type)
        updates.extend(["is_income = ?", "is_transfer = ?"])  #Changing type updates both flags together
        values.extend([is_income, is_transfer])

    if is_active is not None:
        updates.append("is_active = ?")
        values.append(1 if is_active else 0)

    if not updates:
        raise ValueError("Choose at least one category value to update")

    updates.append("updated_at = CURRENT_TIMESTAMP")  #Tracks when the category was last changed
    values.append(name)

    with closing(sqlite3.connect(database_path)) as connection:
        result = connection.execute(
            f"""
            UPDATE categories
            SET {", ".join(updates)}
            WHERE name = ?
            """,
            values,
        )
        connection.commit()

    if result.rowcount == 0:
        raise ValueError(f'Category not found: "{name}"')


def list_categories(database_path=DATABASE_PATH):  #Gets categories ready for display
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT name, group_name, is_income, is_transfer, is_active
            FROM categories
            ORDER BY name
            """
        ).fetchall()

    return sorted(rows, key=lambda row: (category_group_rank(row[1]), row[1], row[0]))


def read_arguments():
    parser = argparse.ArgumentParser(description="Manage transaction categories")  #Keeps this script runnable from PowerShell
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("seed", help="Add the starting categories")
    subparsers.add_parser("list", help="List all categories")

    add_parser = subparsers.add_parser("add", help="Add a new category")
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--group", required=True)
    add_parser.add_argument(
        "--type",
        default="expense",
        choices=["expense", "income", "transfer"],
    )

    update_parser = subparsers.add_parser("update", help="Update a category")
    update_parser.add_argument("--name", required=True)
    update_parser.add_argument("--new-name")
    update_parser.add_argument("--group")
    update_parser.add_argument("--type", choices=["expense", "income", "transfer"])
    update_parser.add_argument("--active", type=parse_active)

    deactivate_parser = subparsers.add_parser(
        "deactivate",
        help="Hide a category without deleting it",
    )
    deactivate_parser.add_argument("--name", required=True)

    return parser.parse_args()


def print_categories(categories):  #Prints categories grouped so they are easier to scan
    if not categories:
        print("No categories found")
        return

    current_group = None

    for name, group_name, is_income, is_transfer, is_active in categories:
        if group_name != current_group:
            current_group = group_name
            print(f"\n{current_group}")  #Prints each group once so the list is easier to read

        category_type = get_type_name(is_income, is_transfer)
        status = "active" if is_active else "inactive"
        print(f"- {name} | {category_type} | {status}")


def main():  #Runs the right category command from the arguments
    arguments = read_arguments()

    try:
        if arguments.command == "seed":
            added_count, total_count = seed_categories()
            print(f"Added {added_count} of {total_count} starting categories")
        elif arguments.command == "list":
            print_categories(list_categories())
        elif arguments.command == "add":
            add_category(
                name=arguments.name,
                group_name=arguments.group,
                category_type=arguments.type,
            )
            print(f'Added category: "{arguments.name}"')
        elif arguments.command == "update":
            update_category(
                name=arguments.name,
                new_name=arguments.new_name,
                group_name=arguments.group,
                category_type=arguments.type,
                is_active=arguments.active,
            )
            print(f'Updated category: "{arguments.name}"')
        elif arguments.command == "deactivate":
            update_category(name=arguments.name, is_active=False)
            print(f'Deactivated category: "{arguments.name}"')
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as error:
        print(f"Could not manage categories: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
