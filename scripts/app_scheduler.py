import argparse
import subprocess
import sys
from pathlib import Path

try:
    from scripts.paths import PRIVATE_DATA_DIR, PROJECT_ROOT, private_data_dir_is_safe
except ModuleNotFoundError:
    from paths import PRIVATE_DATA_DIR, PROJECT_ROOT, private_data_dir_is_safe


TASK_NAME = "Finance Hub Web App"
RUNNER_FILE = PRIVATE_DATA_DIR / "finance_hub_web_app.cmd"
LOG_FILE = PRIVATE_DATA_DIR / "finance_hub_web_app.log"
APP_SCRIPT = PROJECT_ROOT / "app.py"
CREATE_CONFIRMATION = "CREATE"
REMOVE_CONFIRMATION = "REMOVE"
POWERSHELL_PATH = Path(
    "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
)


def quote_batch_path(path):  #Quotes paths for the private Windows runner
    return f'"{Path(path)}"'


def get_python_path():  #Keeps the installed task on the same user environment
    virtual_environment_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if virtual_environment_python.exists():
        return virtual_environment_python
    return Path(sys.executable)


def build_runner_text(python_path=None):  #Starts the loopback app and keeps logs outside Git
    python_path = Path(python_path or get_python_path())
    command = " ".join(
        [
            quote_batch_path(python_path),
            quote_batch_path(APP_SCRIPT),
            ">>",
            quote_batch_path(LOG_FILE),
            "2>&1",
        ]
    )
    return "\r\n".join(
        [
            "@echo off",
            f'cd /d "{PROJECT_ROOT}"',
            command,
            "",
        ]
    )


def save_runner(python_path=None):  #Writes no credentials and stays in the current user's private folder
    if not private_data_dir_is_safe(PRIVATE_DATA_DIR, PROJECT_ROOT):
        raise ValueError("Private data folder must stay outside the project")

    RUNNER_FILE.parent.mkdir(parents=True, exist_ok=True)
    RUNNER_FILE.write_text(build_runner_text(python_path), encoding="utf-8")
    return RUNNER_FILE


def build_task_command(runner_path=RUNNER_FILE):  #Runs at this user's logon with limited privileges
    task_action = f'cmd.exe /c ""{runner_path}""'
    return [
        "schtasks",
        "/Create",
        "/TN",
        TASK_NAME,
        "/TR",
        task_action,
        "/SC",
        "ONLOGON",
        "/DELAY",
        "0000:15",
        "/RL",
        "LIMITED",
        "/F",
    ]


def build_task_settings_command():  #Removes Windows' three-day limit and restarts after crashes
    settings_script = (
        "$settings = New-ScheduledTaskSettingsSet "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) "
        "-MultipleInstances IgnoreNew "
        "-RestartCount 3 "
        "-RestartInterval (New-TimeSpan -Minutes 1) "
        "-StartWhenAvailable; "
        f"Set-ScheduledTask -TaskName '{TASK_NAME}' -Settings $settings | Out-Null"
    )
    return [
        str(POWERSHELL_PATH),
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        settings_script,
    ]


def print_preview():  #Shows the files and Windows task before creating them
    print(f"Runner file: {RUNNER_FILE}")
    print(f"Log file: {LOG_FILE}")
    print("Runner contents:")
    print(build_runner_text().replace("\r\n", "\n").rstrip())
    print("")
    print("Task Scheduler command:")
    print(subprocess.list2cmdline(build_task_command()))
    print("Task reliability command:")
    print(subprocess.list2cmdline(build_task_settings_command()))
    print("The task runs as the Windows user who installs it, never as SYSTEM")


def install_task(arguments):  #Creates the current-user logon task and optionally starts it now
    if arguments.confirm != CREATE_CONFIRMATION:
        raise ValueError(f"Add --confirm {CREATE_CONFIRMATION} to create the task")

    runner_path = save_runner()
    subprocess.run(build_task_command(runner_path), check=True)
    subprocess.run(build_task_settings_command(), check=True)
    print(f"Scheduled task created: {TASK_NAME}")
    print(f"Runner file: {runner_path}")
    if arguments.start_now:
        subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], check=True)
        print("Finance Hub start requested")


def show_status():  #Asks Windows if the web app task exists or is running
    subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
        check=True,
    )
    print(f"Runner file exists: {RUNNER_FILE.exists()}")
    print(f"Log file: {LOG_FILE}")


def start_task():  #Starts the configured app without opening a terminal window
    subprocess.run(["schtasks", "/Run", "/TN", TASK_NAME], check=True)
    print("Finance Hub start requested")


def stop_task():  #Stops the task-owned app process
    subprocess.run(["schtasks", "/End", "/TN", TASK_NAME], check=True)
    print("Finance Hub task stopped")


def remove_task(arguments):  #Removes autostart but keeps its diagnostic log
    if arguments.confirm != REMOVE_CONFIRMATION:
        raise ValueError(f"Add --confirm {REMOVE_CONFIRMATION} to remove the task")

    subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=True)
    if RUNNER_FILE.exists():
        RUNNER_FILE.unlink()
    print(f"Scheduled task removed: {TASK_NAME}")
    print(f"Log retained: {LOG_FILE}")


def read_arguments():  #Reads the web-app Task Scheduler helper command
    parser = argparse.ArgumentParser(description="Start Finance Hub automatically at Windows logon")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preview", help="Show the task without changing Windows")

    install_parser = subparsers.add_parser("install", help="Create the current-user logon task")
    install_parser.add_argument("--confirm", required=True)
    install_parser.add_argument("--start-now", action="store_true")

    subparsers.add_parser("status", help="Show task status")
    subparsers.add_parser("start", help="Start Finance Hub now")
    subparsers.add_parser("stop", help="Stop the task-owned Finance Hub process")

    remove_parser = subparsers.add_parser("remove", help="Remove the logon task")
    remove_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main():  #Runs the web-app Task Scheduler helper
    arguments = read_arguments()
    try:
        if arguments.command == "preview":
            print_preview()
        elif arguments.command == "install":
            install_task(arguments)
        elif arguments.command == "status":
            show_status()
        elif arguments.command == "start":
            start_task()
        elif arguments.command == "stop":
            stop_task()
        elif arguments.command == "remove":
            remove_task(arguments)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Could not manage Finance Hub startup: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
