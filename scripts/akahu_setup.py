import argparse
import sys

try:
    from scripts.secret_store import delete_secret, has_secret, set_secret
except ModuleNotFoundError:
    from secret_store import delete_secret, has_secret, set_secret


AKAHU_SECRETS = (
    ("akahu-app-id-token", "Akahu App ID Token"),
    ("akahu-user-access-token", "Akahu User Access Token"),
)


def get_setup_status():  #Checks Akahu setup without decrypting either token
    return [
        (label, has_secret(secret_name))
        for secret_name, label in AKAHU_SECRETS
    ]


def save_tokens():  #Saves both Akahu tokens through hidden Windows prompts
    if any(is_configured for _, is_configured in get_setup_status()):
        raise ValueError("Remove existing Akahu tokens before running setup again")

    for secret_name, label in AKAHU_SECRETS:
        set_secret(secret_name, f"Enter {label}")
        print(f"{label}: configured")


def remove_tokens():  #Removes both encrypted Akahu token files
    removed = 0

    for secret_name, _ in AKAHU_SECRETS:
        if delete_secret(secret_name):
            removed += 1

    return removed


def print_status():  #Shows setup without exposing token values
    for label, is_configured in get_setup_status():
        status = "configured" if is_configured else "not configured"
        print(f"{label}: {status}")


def read_arguments():  #Reads the Akahu setup command
    parser = argparse.ArgumentParser(description="Manage encrypted Akahu tokens")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("setup", help="Save both Akahu tokens")
    subparsers.add_parser("status", help="Check Akahu token setup")

    remove_parser = subparsers.add_parser("remove", help="Remove both Akahu tokens")
    remove_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main():  #Runs the selected Akahu setup command
    arguments = read_arguments()

    try:
        if arguments.command == "setup":
            save_tokens()
        elif arguments.command == "status":
            print_status()
        elif arguments.command == "remove":
            if arguments.confirm != "REMOVE":
                raise ValueError("Remove confirmation must be REMOVE")
            print(f"Removed encrypted Akahu tokens: {remove_tokens()}")
    except (OSError, ValueError) as error:
        print(f"Could not manage Akahu setup: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
