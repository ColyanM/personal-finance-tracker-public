import sqlite3
import sys
from contextlib import closing
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from scripts.database_backup import check_integrity
    from scripts.db_init import CURRENT_SCHEMA_VERSION
    from scripts.paths import DATABASE_PATH, PRIVATE_DATA_DIR, PROJECT_ROOT, private_data_dir_is_safe
    from scripts.remote_access import load_tailscale_settings
    from scripts.security_check import run_checks as run_security_checks
    from scripts.settings import get_settings
except ModuleNotFoundError:
    from database_backup import check_integrity
    from db_init import CURRENT_SCHEMA_VERSION
    from paths import DATABASE_PATH, PRIVATE_DATA_DIR, PROJECT_ROOT, private_data_dir_is_safe
    from remote_access import load_tailscale_settings
    from security_check import run_checks as run_security_checks
    from settings import get_settings


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
DAILY_AUTOMATION_SCRIPT = PROJECT_ROOT / "scripts" / "daily_automation.py"
TASK_SCHEDULER_SCRIPT = PROJECT_ROOT / "scripts" / "task_scheduler.py"
APP_SCHEDULER_SCRIPT = PROJECT_ROOT / "scripts" / "app_scheduler.py"
TAILSCALE_ACCESS_SCRIPT = PROJECT_ROOT / "scripts" / "tailscale_access.py"
NZ_TIMEZONE = "Pacific/Auckland"


def get_database_check():  #Checks the live database without changing it
    try:
        check_integrity(DATABASE_PATH)
        database_uri = f"{DATABASE_PATH.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(database_uri, uri=True)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]

        if version != CURRENT_SCHEMA_VERSION:
            return "Database", False, f"Run python scripts\\db_init.py first, found schema {version}"

        return "Database", True, f"Schema version {version} and integrity are ok"
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error) as error:
        return "Database", False, str(error)


def get_react_build_check():  #Makes sure python app.py has a built front end to serve
    if not FRONTEND_INDEX.exists():
        return "React build", False, "Run cd frontend then npm run build"

    assets_folder = FRONTEND_DIST / "assets"
    has_javascript = assets_folder.exists() and any(assets_folder.glob("*.js"))
    has_css = assets_folder.exists() and any(assets_folder.glob("*.css"))
    if not has_javascript or not has_css:
        return "React build", False, "Build folder is missing JS or CSS assets"

    return "React build", True, "Built files are ready"


def get_settings_check():  #Checks app settings can be read safely
    try:
        settings = get_settings(DATABASE_PATH)
        currency = settings["display_currency"]
        alert_status = "on" if settings["daily_alert_enabled"] == "true" else "off"
        return "Settings", True, f"Display currency {currency}, daily email {alert_status}"
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        return "Settings", False, str(error)


def get_private_folder_check():  #Checks private data stays outside the repo
    safe = private_data_dir_is_safe(PRIVATE_DATA_DIR, PROJECT_ROOT)
    details = f"Using {PRIVATE_DATA_DIR}"
    return "Private data folder", safe, details


def get_daily_automation_check():  #Checks the optional daily job files are present
    missing = [
        path.name
        for path in (DAILY_AUTOMATION_SCRIPT, TASK_SCHEDULER_SCRIPT)
        if not path.exists()
    ]
    if missing:
        return "Daily automation files", False, f"Missing: {', '.join(missing)}"

    return "Daily automation files", True, "Plan, run, and scheduler helpers are present"


def get_private_access_check():  #Validates optional Tailscale settings without requiring remote access
    missing = [
        path.name
        for path in (APP_SCHEDULER_SCRIPT, TAILSCALE_ACCESS_SCRIPT)
        if not path.exists()
    ]
    if missing:
        return "Private phone access", False, f"Missing: {', '.join(missing)}"

    try:
        settings = load_tailscale_settings()
    except (OSError, ValueError) as error:
        return "Private phone access", False, str(error)

    if settings is None:
        return "Private phone access", True, "Disabled; app remains local-only"

    return "Private phone access", True, f"Configured for {settings.origin}"


def get_timezone_check():  #Checks Windows has the IANA data used by daily automation
    try:
        ZoneInfo(NZ_TIMEZONE)
    except ZoneInfoNotFoundError:
        return (
            "Timezone data",
            False,
            "Run python -m pip install -r requirements.txt",
        )

    return "Timezone data", True, f"{NZ_TIMEZONE} is available"


def run_final_checks():  #Runs the finished app checks without touching bank data
    checks = [
        (f"Security: {name}", passed, details)
        for name, passed, details in run_security_checks(PROJECT_ROOT)
    ]
    checks.extend(
        [
            get_database_check(),
            get_react_build_check(),
            get_settings_check(),
            get_private_folder_check(),
            get_daily_automation_check(),
            get_private_access_check(),
            get_timezone_check(),
        ]
    )
    return checks


def main():  #Prints one final local readiness report
    checks = run_final_checks()
    for name, passed, details in checks:
        result = "PASS" if passed else "FAIL"
        print(f"[{result}] {name}: {details}")

    if all(passed for _, passed, _ in checks):
        print("Finance Hub final check passed")
        return 0

    print("Finance Hub final check failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
