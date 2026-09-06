import argparse
import subprocess
import sys
from pathlib import Path

try:
    from scripts.paths import PRIVATE_DATA_DIR, PROJECT_ROOT, private_data_dir_is_safe
    from scripts.settings import get_settings
except ModuleNotFoundError:
    from paths import PRIVATE_DATA_DIR, PROJECT_ROOT, private_data_dir_is_safe
    from settings import get_settings


TASK_NAME = "Finance Hub Daily Refresh"
RUNNER_FILE = PRIVATE_DATA_DIR / "finance_hub_daily_task.cmd"
DAILY_SCRIPT = PROJECT_ROOT / "scripts" / "daily_automation.py"
CREATE_CONFIRMATION = "CREATE"
REMOVE_CONFIRMATION = "REMOVE"
SUPPORTED_CURRENCIES = {"NZD", "USD"}


def clean_time(task_time):  #Keeps Task Scheduler time in HH:MM format
    pieces = task_time.split(":")
    if len(pieces) != 2:
        raise ValueError("Time must be HH:MM")

    hour = int(pieces[0])
    minute = int(pieces[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Time must be between 00:00 and 23:59")

    return f"{hour:02d}:{minute:02d}"


def clean_currency(currency):  #Uses the same display currencies as the app
    currency = currency.upper().strip()
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Currency must be NZD or USD")

    return currency


def quote_batch_path(path):  #Quotes paths for the private runner batch file
    return f'"{Path(path)}"'


def build_runner_text(arguments, python_path=None):  #Builds the private command that Task Scheduler runs
    python_path = Path(python_path or sys.executable)
    command_parts = [
        quote_batch_path(python_path),
        quote_batch_path(DAILY_SCRIPT),
        "run",
        "--confirm",
        "RUN_DAILY",
    ]

    if arguments.currency is not None:
        command_parts.extend(["--currency", clean_currency(arguments.currency)])
    if arguments.hours is not None:
        command_parts.extend(["--hours", str(arguments.hours)])
    if arguments.max_rows is not None:
        command_parts.extend(["--max-rows", str(arguments.max_rows)])
    if arguments.bnz_days is not None:
        command_parts.extend(["--bnz-days", str(arguments.bnz_days)])
    if arguments.plaid_days is not None:
        command_parts.extend(["--plaid-days", str(arguments.plaid_days)])
    if arguments.skip_bnz:
        command_parts.append("--skip-bnz")
    if arguments.skip_plaid:
        command_parts.append("--skip-plaid")
    if arguments.skip_email:
        command_parts.append("--skip-email")
    if arguments.send_if_empty:
        command_parts.append("--send-if-empty")

    lines = [
        "@echo off",
        f'cd /d "{PROJECT_ROOT}"',
    ]

    lines.append(" ".join(command_parts))
    return "\r\n".join(lines) + "\r\n"


def apply_task_settings(arguments):  #Uses the Settings page time unless the command overrides it
    settings = get_settings()
    if arguments.time is None:
        arguments.time = settings["daily_alert_time"]

    return arguments


def save_runner(arguments):  #Writes the runner outside the repo so Git never sees it
    if not private_data_dir_is_safe(PRIVATE_DATA_DIR, PROJECT_ROOT):
        raise ValueError("Private data folder must stay outside the project")

    RUNNER_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUNNER_FILE.write_text(build_runner_text(arguments), encoding="utf-8")
    return RUNNER_FILE


def build_task_command(task_time, runner_path=RUNNER_FILE):  #Builds the Windows Task Scheduler command safely
    task_time = clean_time(task_time)
    task_action = f'cmd.exe /c ""{runner_path}""'
    return [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        task_action,
        "/SC",
        "DAILY",
        "/ST",
        task_time,
        "/F",
    ]


def printable_command(command):  #Prints a command that can be pasted into cmd if needed
    return subprocess.list2cmdline(command)


def print_preview(arguments):  #Shows the runner and task command without installing anything
    runner_path = RUNNER_FILE
    print(f"Runner file: {runner_path}")
    print("Runner contents:")
    print(build_runner_text(arguments).replace("\r\n", "\n").rstrip())
    print("")
    print("Task Scheduler command:")
    print(printable_command(build_task_command(arguments.time, runner_path)))


def install_task(arguments):  #Creates or updates the Windows scheduled task
    if arguments.confirm != CREATE_CONFIRMATION:
        raise ValueError(f"Add --confirm {CREATE_CONFIRMATION} to create the task")

    runner_path = save_runner(arguments)
    subprocess.run(build_task_command(arguments.time, runner_path), check=True)
    print(f"Scheduled task created: {TASK_NAME}")
    print(f"Runner file: {runner_path}")


def show_status():  #Asks Windows if the task exists
    subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"],
        check=True,
    )


def remove_task(arguments):  #Removes the scheduled task and the private runner file
    if arguments.confirm != REMOVE_CONFIRMATION:
        raise ValueError(f"Add --confirm {REMOVE_CONFIRMATION} to remove the task")

    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=True)
    if RUNNER_FILE.exists():
        RUNNER_FILE.unlink()
    print(f"Scheduled task removed: {TASK_NAME}")


def add_daily_options(parser):  #Keeps preview and install options the same
    parser.add_argument("--time", help="Daily run time in HH:MM")
    parser.add_argument("--currency")
    parser.add_argument("--hours", type=int)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--bnz-days", type=int)
    parser.add_argument("--plaid-days", type=int)
    parser.add_argument("--skip-bnz", action="store_true")
    parser.add_argument("--skip-plaid", action="store_true")
    parser.add_argument("--skip-email", action="store_true")
    parser.add_argument("--send-if-empty", action="store_true")


def read_arguments():  #Reads the Task Scheduler helper command
    parser = argparse.ArgumentParser(description="Set up the Finance Hub daily task")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preview_parser = subparsers.add_parser("preview", help="Show the scheduled task")
    add_daily_options(preview_parser)

    install_parser = subparsers.add_parser("install", help="Create the scheduled task")
    add_daily_options(install_parser)
    install_parser.add_argument("--confirm", required=True)

    subparsers.add_parser("status", help="Show the scheduled task status")

    remove_parser = subparsers.add_parser("remove", help="Remove the scheduled task")
    remove_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main():  #Runs the Task Scheduler helper
    arguments = read_arguments()

    try:
        if arguments.command == "preview":
            arguments = apply_task_settings(arguments)
            print_preview(arguments)
        elif arguments.command == "install":
            arguments = apply_task_settings(arguments)
            install_task(arguments)
        elif arguments.command == "status":
            show_status()
        elif arguments.command == "remove":
            remove_task(arguments)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Could not manage scheduled task: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
