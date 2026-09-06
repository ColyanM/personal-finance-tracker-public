import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from scripts.remote_access import (
        CONFIG_PATH,
        load_tailscale_settings,
        remove_tailscale_settings,
        save_tailscale_settings,
    )
except ModuleNotFoundError:
    from remote_access import (
        CONFIG_PATH,
        load_tailscale_settings,
        remove_tailscale_settings,
        save_tailscale_settings,
    )


ENABLE_CONFIRMATION = "ENABLE_PRIVATE_ACCESS"
DISABLE_CONFIRMATION = "DISABLE_PRIVATE_ACCESS"
LOCAL_TARGET = "http://127.0.0.1:8000"
SERVE_ARGUMENTS = ["serve", "--bg", "--https=443", LOCAL_TARGET]


def tailscale_candidates():  #Checks PATH and standard Windows installation folders
    candidates = []
    for command_name in ("tailscale.exe", "tailscale"):
        command_path = shutil.which(command_name)
        if command_path:
            candidates.append(Path(command_path))

    for environment_name in ("ProgramFiles", "LOCALAPPDATA"):
        base_path = os.getenv(environment_name)
        if base_path:
            candidates.append(Path(base_path) / "Tailscale" / "tailscale.exe")

    return candidates


def find_tailscale_executable():  #Finds the signed-in Windows Tailscale CLI
    for candidate in tailscale_candidates():
        if candidate.is_file():
            return candidate

    raise FileNotFoundError(
        "Tailscale is not installed. Install it from https://tailscale.com/download/windows"
    )


def run_tailscale(arguments, capture_output=True):  #Runs list-form CLI arguments without a shell
    command = [str(find_tailscale_executable()), *arguments]
    return subprocess.run(
        command,
        check=True,
        capture_output=capture_output,
        text=True,
    )


def parse_json_output(completed_process, description):  #Turns CLI JSON into a checked object
    try:
        data = json.loads(completed_process.stdout)
    except (AttributeError, json.JSONDecodeError) as error:
        raise ValueError(f"Tailscale returned invalid {description} JSON") from error

    if not isinstance(data, dict):
        raise ValueError(f"Tailscale returned invalid {description} data")
    return data


def get_tailscale_status():  #Reads the connected node and current account identity
    return parse_json_output(
        run_tailscale(["status", "--json"]),
        "status",
    )


def get_serve_status():  #Reads private Serve and public Funnel state together
    return parse_json_output(
        run_tailscale(["serve", "status", "--json"]),
        "Serve status",
    )


def get_node_access(status, user_login=None):  #Derives the exact node URL and owning login
    if status.get("BackendState") != "Running":
        raise ValueError("Tailscale is not connected")

    self_status = status.get("Self")
    if not isinstance(self_status, dict):
        raise ValueError("Tailscale status is missing this device")

    dns_name = str(self_status.get("DNSName", "")).rstrip(".").lower()
    if not dns_name:
        raise ValueError("Tailscale MagicDNS name is unavailable")

    if user_login is None:
        user_id = str(self_status.get("UserID", ""))
        users = status.get("User", {})
        user = users.get(user_id, {}) if isinstance(users, dict) else {}
        user_login = user.get("LoginName") if isinstance(user, dict) else None

    if not user_login:
        raise ValueError("Tailscale user login is unavailable; pass --user-login")

    return f"https://{dns_name}", user_login


def value_is_enabled(value):  #Recognizes enabled Funnel entries in Serve JSON
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        return any(value_is_enabled(item) for item in value.values())
    if isinstance(value, list):
        return any(value_is_enabled(item) for item in value)
    return False


def funnel_is_enabled(data):  #Fails closed when any listener is public through Funnel
    if isinstance(data, dict):
        for key, value in data.items():
            if key.casefold() == "allowfunnel" and value_is_enabled(value):
                return True
            if funnel_is_enabled(value):
                return True
    elif isinstance(data, list):
        return any(funnel_is_enabled(item) for item in data)

    return False


def configuration_contains_target(data, target=LOCAL_TARGET):  #Confirms Serve proxies only to the local app target
    if isinstance(data, str):
        return data.rstrip("/") == target.rstrip("/")
    if isinstance(data, dict):
        return any(configuration_contains_target(value, target) for value in data.values())
    if isinstance(data, list):
        return any(configuration_contains_target(value, target) for value in data)
    return False


def require_no_funnel(serve_status):  #Never combines personal finance access with a public listener
    if funnel_is_enabled(serve_status):
        raise ValueError(
            "Tailscale Funnel is active on this device. Disable it with "
            "'tailscale funnel reset' after reviewing any existing public mappings."
        )


def print_plan(user_login=None):  #Shows the exact private setup without changing the laptop
    status = get_tailscale_status()
    origin, user_login = get_node_access(status, user_login)
    serve_status = get_serve_status()
    require_no_funnel(serve_status)

    print(f"Private URL: {origin}")
    print(f"Allowed Tailscale login: {user_login}")
    print(f"Local target: {LOCAL_TARGET}")
    print(f"Configuration file: {CONFIG_PATH}")
    print("Serve command:")
    print(subprocess.list2cmdline(["tailscale", *SERVE_ARGUMENTS]))
    print("Funnel will not be enabled")


def enable_access(arguments):  #Enables persistent private Serve and pins its authenticated identity
    if arguments.confirm != ENABLE_CONFIRMATION:
        raise ValueError(f"Add --confirm {ENABLE_CONFIRMATION} to enable access")

    status = get_tailscale_status()
    origin, user_login = get_node_access(status, arguments.user_login)
    require_no_funnel(get_serve_status())

    run_tailscale(SERVE_ARGUMENTS, capture_output=False)
    serve_status = get_serve_status()
    require_no_funnel(serve_status)
    if not configuration_contains_target(serve_status):
        raise ValueError("Tailscale Serve did not report the Finance Hub localhost target")

    settings = save_tailscale_settings(origin, user_login)
    print(f"Private Tailscale access configured: {settings.origin}")
    print(f"Allowed login: {settings.user_login}")
    print(f"Saved outside Git: {CONFIG_PATH}")
    print("Restart Finance Hub so it loads this configuration")


def show_status():  #Reports app configuration and live Tailscale state without changing it
    settings = load_tailscale_settings()
    if settings is None:
        print("Finance Hub remote access: disabled")
    else:
        print(f"Finance Hub private URL: {settings.origin}")
        print(f"Allowed login: {settings.user_login}")

    status = get_tailscale_status()
    origin, user_login = get_node_access(status)
    print(f"Tailscale node: {origin}")
    print(f"Signed-in login: {user_login}")

    serve_status = get_serve_status()
    print(f"Finance Hub target active: {configuration_contains_target(serve_status)}")
    print(f"Public Funnel active: {funnel_is_enabled(serve_status)}")
    if funnel_is_enabled(serve_status):
        raise ValueError("Public Funnel access is active")


def disable_access(arguments):  #Disables only the HTTPS listener created for Finance Hub
    if arguments.confirm != DISABLE_CONFIRMATION:
        raise ValueError(f"Add --confirm {DISABLE_CONFIRMATION} to disable access")

    run_tailscale([*SERVE_ARGUMENTS, "off"], capture_output=False)
    removed = remove_tailscale_settings()
    print("Private Tailscale Serve listener disabled")
    print("Finance Hub access configuration removed" if removed else "Finance Hub was already local-only")


def read_arguments():  #Reads the private remote-access helper command
    parser = argparse.ArgumentParser(
        description="Configure private Finance Hub access through Tailscale Serve"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Preview the exact private URL and command")
    plan_parser.add_argument("--user-login", help="Override the login if Tailscale does not report it")

    enable_parser = subparsers.add_parser("enable", help="Enable private Tailscale Serve access")
    enable_parser.add_argument("--user-login", help="Override the login if Tailscale does not report it")
    enable_parser.add_argument("--confirm", required=True)

    subparsers.add_parser("status", help="Check the private URL and public Funnel state")

    disable_parser = subparsers.add_parser("disable", help="Disable Finance Hub Tailscale Serve")
    disable_parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main():  #Runs the private access helper
    arguments = read_arguments()
    try:
        if arguments.command == "plan":
            print_plan(arguments.user_login)
        elif arguments.command == "enable":
            enable_access(arguments)
        elif arguments.command == "status":
            show_status()
        elif arguments.command == "disable":
            disable_access(arguments)
    except (FileNotFoundError, OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Could not configure private Tailscale access: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
