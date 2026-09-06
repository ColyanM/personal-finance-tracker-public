import argparse
import sqlite3
import sys

try:
    from scripts.akahu_accounts import fetch_accounts, refresh_saved_account_balances
    from scripts.akahu_api import get_tokens
    from scripts.akahu_transactions import (
        fetch_recent_transactions,
        get_date_query,
        save_fetched_transactions,
    )
    from scripts.automation import finish_run, start_run
    from scripts.category_rules import apply_rules
    from scripts.common import check_database, get_refresh_days
    from scripts.database_backup import create_backup
    from scripts.fx_refresh import refresh_exchange_rates_or_saved
    from scripts.paths import BACKUP_DIR, DATABASE_PATH
    from scripts.pre_bank_review import require_safe_review
except ModuleNotFoundError:
    from akahu_accounts import fetch_accounts, refresh_saved_account_balances
    from akahu_api import get_tokens
    from akahu_transactions import (
        fetch_recent_transactions,
        get_date_query,
        save_fetched_transactions,
    )
    from automation import finish_run, start_run
    from category_rules import apply_rules
    from common import check_database, get_refresh_days
    from database_backup import create_backup
    from fx_refresh import refresh_exchange_rates_or_saved
    from paths import BACKUP_DIR, DATABASE_PATH
    from pre_bank_review import require_safe_review


REFRESH_JOB_NAME = "bnz_refresh"


def get_bnz_refresh_days(days, database_path=DATABASE_PATH):  #Uses the BNZ CSV cutoff or latest Akahu row
    if days is not None:
        get_date_query(days)
        return days

    check_database(database_path)
    with sqlite3.connect(database_path) as connection:
        return get_refresh_days(
            connection,
            "akahu-bnz",
            "bnz-csv-history",
            default_days=7,
        )


def refresh_bnz(
    days,
    confirmation,
    database_path=DATABASE_PATH,
    backup_dir=BACKUP_DIR,
):  #Backs up and safely refreshes BNZ transactions
    if confirmation != "REFRESH":
        raise ValueError("Refresh confirmation must be REFRESH")

    days = get_bnz_refresh_days(days, database_path)
    require_safe_review("bnz", database_path, backup_dir)
    backup_path = create_backup(database_path, backup_dir, label="before-bnz-refresh")
    run_id = start_run(REFRESH_JOB_NAME, database_path)

    try:
        rate_date, _, _, fx_warning = refresh_exchange_rates_or_saved(database_path)
        accounts_updated = refresh_saved_account_balances(
            fetch_accounts(*get_tokens()),
            database_path,
        )
        posted, pending = fetch_recent_transactions(days)
        added, updated, pending_saved = save_fetched_transactions(
            posted,
            pending,
            "SAVE",
            database_path,
        )
        categorized = apply_rules(database_path)
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error):
        finish_run(run_id, "failed", "BNZ refresh failed", database_path)
        raise

    details = (
        f"FX date: {rate_date}{fx_warning}, accounts updated: {accounts_updated}, "
        f"days checked: {days}, "
        f"posted added: {added}, "
        f"posted updated: {updated}, "
        f"pending saved: {pending_saved}, categorized: {categorized}"
    )
    finish_run(run_id, "success", details, database_path)
    return backup_path, rate_date, accounts_updated, added, updated, pending_saved, categorized, fx_warning


def read_arguments():  #Reads the safe BNZ refresh options
    parser = argparse.ArgumentParser(description="Safely refresh BNZ transactions")
    parser.add_argument("--days", type=int)
    parser.add_argument("--confirm", required=True, help="Type REFRESH to continue")
    return parser.parse_args()


def main():  #Runs one complete BNZ refresh
    arguments = read_arguments()

    try:
        result = refresh_bnz(arguments.days, arguments.confirm)
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error) as error:
        print(f"Could not refresh BNZ transactions: {error}", file=sys.stderr)
        return 1

    print(f"Backup created: {result[0].name}")
    print(f"Exchange rate used: {result[1]}")
    print(f"Account balances updated: {result[2]}")
    print(f"Posted transactions added: {result[3]}")
    print(f"Posted transactions updated: {result[4]}")
    print(f"Current pending transactions saved: {result[5]}")
    print(f"Existing transactions categorized: {result[6]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
