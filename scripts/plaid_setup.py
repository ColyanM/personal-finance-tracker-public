import argparse
import sys

try:
    from scripts.plaid_api import (
        LINK_EXTRA_PRODUCTS,
        LINK_PRODUCTS,
        check_credentials,
        get_client_user_id,
    )
    from scripts.plaid_environment import get_plaid_config
    from scripts.plaid_items import get_item_secret_names
    from scripts.secret_store import delete_secret, has_secret, set_secret
except ModuleNotFoundError:
    from plaid_api import (
        LINK_EXTRA_PRODUCTS,
        LINK_PRODUCTS,
        check_credentials,
        get_client_user_id,
    )
    from plaid_environment import get_plaid_config
    from plaid_items import get_item_secret_names
    from secret_store import delete_secret, has_secret, set_secret


def get_credential_secrets():  #Gets the separate Plaid Production secret names
    config = get_plaid_config()
    return (
        (config["client_id_name"], config["client_id_label"]),
        (config["secret_name"], config["secret_label"]),
    )


def get_setup_status():  #Checks the selected Plaid setup without decrypting credentials
    config = get_plaid_config()
    status = [
        (label, has_secret(secret_name))
        for secret_name, label in get_credential_secrets()
    ]
    status.append(("Private Plaid user ID", has_secret(config["user_id_name"])))
    status.append((f"{config['label']} Items", bool(get_item_secret_names())))
    return status


def save_credentials():  #Saves only missing credentials through hidden Windows prompts
    config = get_plaid_config()
    missing = [
        (secret_name, label)
        for secret_name, label in get_credential_secrets()
        if not has_secret(secret_name)
    ]

    user_id_is_missing = not has_secret(config["user_id_name"])
    if not missing and not user_id_is_missing:
        raise ValueError("Plaid credentials are already configured")

    for secret_name, label in missing:
        set_secret(secret_name, f"Enter {label}")
        print(f"{label}: configured")

    if user_id_is_missing:
        get_client_user_id()
        print("Private Plaid user ID: configured")


def remove_local_values():  #Removes encrypted Plaid Production values
    if get_item_secret_names():
        raise ValueError("Disconnect each Plaid connection before removing credentials")

    config = get_plaid_config()
    removed = 0

    secret_names = [name for name, _ in get_credential_secrets()]
    secret_names.append(config["user_id_name"])
    for secret_name in secret_names:
        if delete_secret(secret_name):
            removed += 1

    return removed


def print_production_review():  #Shows what must be ready before real accounts are linked
    config = get_plaid_config()
    print("Plaid Production review")
    setup_checks = get_setup_status()[:3]
    for label, is_configured in setup_checks:
        status = "ready" if is_configured else "not ready"
        print(f"- {label}: {status}")

    requested = ["accounts", *LINK_PRODUCTS, *LINK_EXTRA_PRODUCTS]
    print(f"- Data access: {', '.join(requested)}")
    print("- Not requested: identity, bank account numbers, payments, transfers")
    print("- Link page: local browser on 127.0.0.1")
    print("Confirm Production and OAuth access in the Plaid Dashboard before connecting")

    if not all(is_configured for _, is_configured in setup_checks):
        raise ValueError("Complete Plaid Production setup before connecting")


def print_status():  #Shows Plaid setup without exposing credential values
    print(f"Plaid connection mode: {get_plaid_config()['name']}")

    for label, is_configured in get_setup_status():
        status = "configured" if is_configured else "not configured"
        print(f"{label}: {status}")


def read_arguments():  #Reads the Plaid setup command
    parser = argparse.ArgumentParser(description="Manage encrypted Plaid credentials")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("setup", help="Save Plaid Production credentials")
    subparsers.add_parser("status", help="Check Plaid Production setup")
    subparsers.add_parser("check", help="Check saved credentials against Plaid")
    subparsers.add_parser("review", help="Review Production access before linking")

    remove_parser = subparsers.add_parser("remove", help="Remove selected Plaid credentials")
    remove_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main():  #Runs the selected Plaid setup command
    arguments = read_arguments()

    try:
        if arguments.command == "setup":
            save_credentials()
        elif arguments.command == "status":
            print_status()
        elif arguments.command == "check":
            check_credentials()
            print(f"{get_plaid_config()['label']} credentials are working")
        elif arguments.command == "review":
            print_production_review()
        elif arguments.command == "remove":
            if arguments.confirm != "REMOVE":
                raise ValueError("Remove confirmation must be REMOVE")
            print(f"Removed local encrypted Plaid values: {remove_local_values()}")
    except (OSError, ValueError) as error:
        print(f"Could not manage Plaid setup: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
