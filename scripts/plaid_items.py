import argparse
import json
import sqlite3
import sys
from contextlib import closing

try:
    from scripts.database_backup import create_backup
    from scripts.paths import BACKUP_DIR, DATABASE_PATH
    from scripts.plaid_api import PlaidRequestError, fetch_accounts, remove_item
    from scripts.plaid_environment import get_plaid_config
    from scripts.secret_store import (
        delete_secret,
        get_secret,
        get_secret_status,
        has_secret,
        set_secret_value,
    )
except ModuleNotFoundError:
    from database_backup import create_backup
    from paths import BACKUP_DIR, DATABASE_PATH
    from plaid_api import PlaidRequestError, fetch_accounts, remove_item
    from plaid_environment import get_plaid_config
    from secret_store import (
        delete_secret,
        get_secret,
        get_secret_status,
        has_secret,
        set_secret_value,
    )


def clean_label(value):  #Keeps connection names short and safe to display
    default_label = get_plaid_config()["label"]

    if not isinstance(value, str):
        return default_label

    label = " ".join(value.split())[:80]
    return label or default_label


def describe_item_error(error, item, position):  #Names one failed item without exposing IDs
    label = clean_label(item.get("label"))
    prefix = f"Plaid connection {position} ({label})"
    if error.error_code == "ITEM_LOGIN_REQUIRED":
        return (
            f"{prefix} needs bank sign-in. Run "
            f"python scripts\\plaid_link.py update --connection {position} "
            "on this laptop"
        )

    return f"{prefix}: {error}"


def get_item_secret_names():  #Finds Plaid item files without decrypting them
    config = get_plaid_config()
    item_secret = config["item_secret"]
    item_prefix = config["item_prefix"]
    names = [
        name
        for name, _, _ in get_secret_status()
        if name == item_secret or name.startswith(item_prefix)
    ]
    return sorted(names, key=lambda name: (name != item_secret, name))


def get_saved_items():  #Decrypts each Plaid item only while a command is running
    config = get_plaid_config()
    items = []

    for position, secret_name in enumerate(get_item_secret_names(), start=1):
        saved_value = get_secret(secret_name)

        try:
            saved_item = json.loads(saved_value or "")
        except json.JSONDecodeError as error:
            raise ValueError(f"An encrypted {config['label']} item is invalid") from error

        access_token = saved_item.get("access_token") if isinstance(saved_item, dict) else None
        item_id = saved_item.get("item_id") if isinstance(saved_item, dict) else None
        if not isinstance(access_token, str) or not access_token:
            raise ValueError(f"An encrypted {config['label']} access token is missing")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"An encrypted {config['label']} item ID is missing")

        items.append(
            {
                "secret_name": secret_name,
                "label": clean_label(
                    saved_item.get("label") or f"{config['label']} {position}"
                ),
                "access_token": access_token,
                "item_id": item_id,
            }
        )

    return items


def get_saved_item(position):  #Selects one encrypted connection by its displayed number
    items = get_saved_items()
    if position < 1 or position > len(items):
        raise ValueError("Plaid connection number was not found")

    return items[position - 1]


def get_next_secret_name():  #Keeps the existing first item and numbers later items
    config = get_plaid_config()
    item_secret = config["item_secret"]
    item_prefix = config["item_prefix"]

    if not has_secret(item_secret):
        return item_secret

    position = 2
    while has_secret(f"{item_prefix}{position}"):
        position += 1
    return f"{item_prefix}{position}"


def save_item(access_token, item_id, label):  #Encrypts one new item without changing older items
    config = get_plaid_config()

    for item in get_saved_items():
        if item["item_id"] == item_id:
            raise ValueError(f"This {config['label']} connection is already saved")

    saved_item = json.dumps(
        {
            "access_token": access_token,
            "item_id": item_id,
            "label": clean_label(label),
        }
    )
    set_secret_value(get_next_secret_name(), saved_item)


def disconnect_item(
    position,
    confirmation,
    database_path=DATABASE_PATH,
    backup_dir=BACKUP_DIR,
):  #Revokes one item and deactivates only its saved accounts
    if confirmation != "DISCONNECT":
        raise ValueError("Disconnect confirmation must be DISCONNECT")

    item = get_saved_item(position)
    provider = get_plaid_config()["provider"]
    item_was_already_removed = False
    try:
        accounts = fetch_accounts(item["access_token"])
    except PlaidRequestError as error:
        if error.error_code != "ITEM_NOT_FOUND":
            raise
        accounts = []
        item_was_already_removed = True

    account_ids = [
        account.get("account_id")
        for account in accounts
        if isinstance(account, dict) and isinstance(account.get("account_id"), str)
    ]
    backup_path = create_backup(database_path, backup_dir, label="before-plaid-disconnect")
    if not item_was_already_removed:
        remove_item(item["access_token"])

    deactivated = 0
    with closing(sqlite3.connect(database_path)) as connection:
        for account_id in account_ids:
            result = connection.execute(
                """
                UPDATE accounts
                SET is_active = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE provider = ?
                  AND provider_account_id = ?
                """,
                (provider, account_id),
            )
            deactivated += result.rowcount
        connection.commit()

    if not delete_secret(item["secret_name"]):
        raise OSError("Encrypted Plaid connection could not be removed")
    return item["label"], deactivated, backup_path


def print_items(items):  #Shows connection names without IDs or tokens
    if not items:
        print(f"No {get_plaid_config()['label']} connections saved")
        return

    for position, item in enumerate(items, start=1):
        print(f"{position}. {item['label']}")


def read_arguments():  #Reads the Plaid connection command
    parser = argparse.ArgumentParser(description="Manage Plaid connections")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List encrypted connections")
    disconnect_parser = subparsers.add_parser("disconnect", help="Revoke one connection")
    disconnect_parser.add_argument("--connection", required=True, type=int)
    disconnect_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main():  #Runs the selected Plaid connection command
    arguments = read_arguments()

    try:
        if arguments.command == "list":
            print_items(get_saved_items())
        else:
            label, deactivated, backup_path = disconnect_item(
                arguments.connection,
                arguments.confirm,
            )
            print(f"Disconnected: {label}")
            print(f"Accounts deactivated: {deactivated}")
            print(f"Backup created: {backup_path.name}")
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage Plaid connections: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
