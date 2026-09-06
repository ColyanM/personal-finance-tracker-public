import argparse
import sqlite3
import sys
from contextlib import closing

try:
    from scripts.common import check_database
    from scripts.paths import DATABASE_PATH, PRIVATE_DATA_DIR
    from scripts.secret_store import get_secret_status, has_secret
except ModuleNotFoundError:
    from common import check_database
    from paths import DATABASE_PATH, PRIVATE_DATA_DIR
    from secret_store import get_secret_status, has_secret


EMAIL_SECRETS = (
    "daily-alert-email-sender",
    "daily-alert-email-password",
    "daily-alert-email-recipients",
)
DAILY_TASK_RUNNER = PRIVATE_DATA_DIR / "finance_hub_daily_task.cmd"


def get_setup_status():  #Checks which future automation settings exist
    secret_status = [
        (label, is_configured)
        for _, label, is_configured in get_secret_status()
    ]
    email_ready = all(has_secret(secret_name) for secret_name in EMAIL_SECRETS)
    secret_status.append(("Daily email alert", email_ready))
    secret_status.append(("Daily task runner", DAILY_TASK_RUNNER.is_file()))
    return secret_status


def start_run(job_name, database_path=DATABASE_PATH):  #Starts one automation history row
    check_database(database_path)
    job_name = job_name.strip()

    if not job_name:
        raise ValueError("Job name cannot be empty")

    with closing(sqlite3.connect(database_path)) as connection:
        result = connection.execute(
            """
            INSERT INTO automation_runs (job_name, status)
            VALUES (?, 'running')
            """,
            (job_name,),
        )
        connection.commit()
        return result.lastrowid


def finish_run(run_id, status, details=None, database_path=DATABASE_PATH):  #Finishes a running history row
    check_database(database_path)

    if status not in {"success", "failed"}:
        raise ValueError("Finished status must be success or failed")

    with closing(sqlite3.connect(database_path)) as connection:
        result = connection.execute(
            """
            UPDATE automation_runs
            SET status = ?,
                finished_at = CURRENT_TIMESTAMP,
                details = ?
            WHERE id = ?
              AND status = 'running'
            """,
            (status, details, run_id),
        )
        connection.commit()

    if result.rowcount == 0:
        raise ValueError(f"Running automation record not found: {run_id}")


def list_runs(limit=10, database_path=DATABASE_PATH):  #Gets the newest automation history rows
    check_database(database_path)

    if limit < 1:
        raise ValueError("History limit must be at least 1")

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            """
            SELECT id, job_name, status, started_at, finished_at, COALESCE(details, '')
            FROM automation_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def print_status():  #Prints setup without exposing secret values
    print("Automation setup:")
    for label, is_configured in get_setup_status():
        status = "configured" if is_configured else "not configured"
        print(f"- {label}: {status}")


def print_history(runs):  #Prints recent automation attempts
    if not runs:
        print("No automation runs found")
        return

    for run_id, job_name, status, started_at, finished_at, details in runs:
        finished_text = finished_at or "not finished"
        print(f"{run_id} | {job_name} | {status} | {started_at} | {finished_text}")
        if details:
            print(f"  {details}")


def read_arguments():  #Reads the automation check command
    parser = argparse.ArgumentParser(description="Check future automation setup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Check which future settings exist")

    history_parser = subparsers.add_parser("history", help="Show recent automation runs")
    history_parser.add_argument("--limit", type=int, default=10)
    return parser.parse_args()


def main():  #Runs the selected automation check
    arguments = read_arguments()

    try:
        if arguments.command == "status":
            print_status()
        elif arguments.command == "history":
            print_history(list_runs(limit=arguments.limit))
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(f"Could not check automation: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
