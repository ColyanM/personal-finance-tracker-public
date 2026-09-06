import argparse
import sqlite3
import sys
from contextlib import closing

try:
    from scripts.akahu_setup import AKAHU_SECRETS
    from scripts.database_backup import check_integrity, list_backups
    from scripts.db_init import CURRENT_SCHEMA_VERSION
    from scripts.paths import BACKUP_DIR, DATABASE_PATH, PROJECT_ROOT, private_data_dir_is_safe
    from scripts.plaid_environment import get_plaid_config
    from scripts.secret_store import POWERSHELL_PATH, SECRET_DIR, has_secret
    from scripts.security_check import run_checks
except ModuleNotFoundError:
    from akahu_setup import AKAHU_SECRETS
    from database_backup import check_integrity, list_backups
    from db_init import CURRENT_SCHEMA_VERSION
    from paths import BACKUP_DIR, DATABASE_PATH, PROJECT_ROOT, private_data_dir_is_safe
    from plaid_environment import get_plaid_config
    from secret_store import POWERSHELL_PATH, SECRET_DIR, has_secret
    from security_check import run_checks


SUPPORTED_PROVIDERS = {"bnz", "plaid"}


def clean_provider(provider):  #Keeps every safety review explicit about the bank it will access
    provider = (provider or "").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        choices = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(f"Bank provider must be one of: {choices}")

    return provider


def get_database_check(database_path):  #Checks the live finance database before bank access
    try:
        check_integrity(database_path)
        database_uri = f"{database_path.resolve().as_uri()}?mode=ro"

        with closing(sqlite3.connect(database_uri, uri=True)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]

        if version != CURRENT_SCHEMA_VERSION:
            details = (
                f"Run python scripts\\db_init.py "
                f"(found version {version}, need {CURRENT_SCHEMA_VERSION})"
            )
            return "Database ready", False, details

        details = f"Integrity and schema version {version} are ready"
        return "Database ready", True, details
    except (OSError, ValueError, sqlite3.Error) as error:
        return "Database ready", False, str(error)


def get_backup_check(backup_dir):  #Checks the newest private backup can be opened
    try:
        backups = list_backups(backup_dir)
        if not backups:
            return (
                "Verified backup",
                False,
                "Run python scripts\\database_backup.py backup first",
            )

        check_integrity(backups[0])
        return "Verified backup", True, f"Newest backup is valid: {backups[0].name}"
    except (OSError, ValueError, sqlite3.Error) as error:
        return "Verified backup", False, str(error)


def get_akahu_token_check(secret_dir):  #Checks both encrypted token files exist without reading them
    missing = [
        label
        for secret_name, label in AKAHU_SECRETS
        if not has_secret(secret_name, secret_dir)
    ]

    if missing:
        return "Akahu tokens", False, f"Missing: {', '.join(missing)}"

    return "Akahu tokens", True, "Both encrypted tokens are configured"


def get_plaid_credential_check(secret_dir):  #Checks Plaid Production without requiring Akahu
    config = get_plaid_config()
    required_secrets = (
        (config["client_id_name"], config["client_id_label"]),
        (config["secret_name"], config["secret_label"]),
        (config["user_id_name"], "Private Plaid user ID"),
    )
    missing = [
        label
        for secret_name, label in required_secrets
        if not has_secret(secret_name, secret_dir)
    ]
    check_name = f"{config['label']} credentials"

    if missing:
        return check_name, False, f"Missing: {', '.join(missing)}"

    return check_name, True, "Encrypted credentials are configured"


def get_provider_credential_check(provider, secret_dir):  #Routes the review to one provider's encrypted values
    provider = clean_provider(provider)
    if provider == "bnz":
        return get_akahu_token_check(secret_dir)

    return get_plaid_credential_check(secret_dir)


def run_review(
    provider,
    project_root=PROJECT_ROOT,
    database_path=DATABASE_PATH,
    backup_dir=BACKUP_DIR,
    secret_dir=SECRET_DIR,
    powershell_path=POWERSHELL_PATH,
):  #Builds the complete pre-bank security review
    provider = clean_provider(provider)
    checks = [
        (f"Baseline: {name}", passed, details)
        for name, passed, details in run_checks(project_root)
    ]
    checks.append(get_database_check(database_path))
    checks.append(get_backup_check(backup_dir))
    checks.append(get_provider_credential_check(provider, secret_dir))
    checks.append(
        (
            "Encrypted secret location",
            private_data_dir_is_safe(secret_dir, project_root),
            f"Using {secret_dir}",
        )
    )
    checks.append(
        (
            "Trusted PowerShell",
            powershell_path.is_file(),
            f"Using {powershell_path}",
        )
    )
    return checks


def require_safe_review(provider, database_path=DATABASE_PATH, backup_dir=BACKUP_DIR):  #Stops bank access when a security check fails
    checks = run_review(
        provider,
        database_path=database_path,
        backup_dir=backup_dir,
    )
    failed = [name for name, passed, _ in checks if not passed]

    if failed:
        message = f"Pre-bank review failed: {', '.join(failed)}"
        if "Verified backup" in failed:
            message += ". Run python scripts\\database_backup.py backup first"
        raise ValueError(message)


def read_arguments():  #Requires callers to name the bank whose credentials should be checked
    parser = argparse.ArgumentParser(description="Review safety before bank access")
    parser.add_argument("--provider", required=True, choices=sorted(SUPPORTED_PROVIDERS))
    return parser.parse_args()


def main():  #Prints whether bank connection work can safely begin
    arguments = read_arguments()
    try:
        checks = run_review(arguments.provider)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Could not run pre-bank security review: {error}", file=sys.stderr)
        return 1

    for name, passed, details in checks:
        result = "PASS" if passed else "FAIL"
        print(f"[{result}] {name}: {details}")

    if all(passed for _, passed, _ in checks):
        print("Pre-bank security review passed")
        return 0

    print("Pre-bank security review failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
