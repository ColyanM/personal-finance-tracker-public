import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from scripts.paths import PRIVATE_DATA_DIR, PROJECT_ROOT, private_data_dir_is_safe
except ModuleNotFoundError:
    from paths import PRIVATE_DATA_DIR, PROJECT_ROOT, private_data_dir_is_safe


SECRET_DIR = PRIVATE_DATA_DIR / "secrets"
POWERSHELL_PATH = (
    Path(os.getenv("SystemRoot", "C:\\Windows"))
    / "System32"
    / "WindowsPowerShell"
    / "v1.0"
    / "powershell.exe"
)
SECRET_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def get_secret_path(secret_name, secret_dir=None):  #Gets a safe encrypted file path for one secret
    if not SECRET_NAME_PATTERN.fullmatch(secret_name):
        raise ValueError("Secret name can only use lowercase letters, numbers, and hyphens")

    if secret_dir is None:
        secret_dir = SECRET_DIR

    return secret_dir / f"{secret_name}.txt"


def has_secret(secret_name, secret_dir=None):  #Checks an encrypted secret exists without reading it
    secret_path = get_secret_path(secret_name, secret_dir)
    return secret_path.is_file() and secret_path.stat().st_size > 0


def powershell_quote(value):  #Keeps a file path safe inside a PowerShell command
    return str(value).replace("'", "''")


def run_powershell(command, capture_output=False, input_text=None):  #Runs the Windows encryption command
    try:
        return subprocess.run(
            [POWERSHELL_PATH, "-NoProfile", "-Command", command],
            check=True,
            capture_output=capture_output,
            input=input_text,
            text=True,
        )
    except FileNotFoundError as error:
        raise OSError("Windows PowerShell is required for encrypted secrets") from error
    except subprocess.CalledProcessError as error:
        raise OSError("Windows could not process the encrypted secret") from error


def set_secret(secret_name, prompt="Enter secret"):  #Prompts without showing or placing the secret in command history
    if not private_data_dir_is_safe(SECRET_DIR, PROJECT_ROOT):
        raise ValueError("Secret folder must be outside the project and OneDrive")

    secret_path = get_secret_path(secret_name)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    quoted_path = powershell_quote(secret_path)
    quoted_prompt = powershell_quote(prompt)
    command = (
        f"$secret = Read-Host '{quoted_prompt}' -AsSecureString; "
        "if ($secret.Length -eq 0) { throw 'Secret cannot be empty' }; "
        "$encrypted = ConvertFrom-SecureString $secret; "
        f"Set-Content -LiteralPath '{quoted_path}' -Value $encrypted -NoNewline"
    )
    run_powershell(command)


def set_secret_value(secret_name, secret_value):  #Encrypts an API token without adding it to command history
    if not isinstance(secret_value, str) or not secret_value:
        raise ValueError("Secret value cannot be empty")

    if not private_data_dir_is_safe(SECRET_DIR, PROJECT_ROOT):
        raise ValueError("Secret folder must be outside the project and OneDrive")

    secret_path = get_secret_path(secret_name)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    quoted_path = powershell_quote(secret_path)
    command = (
        "$plain = [Console]::In.ReadToEnd(); "
        "$secure = ConvertTo-SecureString $plain -AsPlainText -Force; "
        "$encrypted = ConvertFrom-SecureString $secure; "
        f"Set-Content -LiteralPath '{quoted_path}' -Value $encrypted -NoNewline"
    )
    run_powershell(command, input_text=secret_value)


def get_secret(secret_name):  #Decrypts one secret for the current Windows account
    secret_path = get_secret_path(secret_name)
    if not secret_path.exists():
        return None

    quoted_path = powershell_quote(secret_path)
    command = (
        f"$encrypted = Get-Content -LiteralPath '{quoted_path}' -Raw; "
        "$secure = ConvertTo-SecureString $encrypted; "
        "$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure); "
        "try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) } "
        "finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }"
    )
    result = run_powershell(command, capture_output=True)
    return result.stdout.rstrip("\r\n")


def delete_secret(secret_name):  #Deletes an encrypted secret without reading it
    secret_path = get_secret_path(secret_name)
    if secret_path.exists():
        secret_path.unlink()
        return True

    return False


def get_secret_status():  #Checks encrypted secret files without decrypting them
    if not SECRET_DIR.exists():
        return []

    return [
        (secret_path.stem, secret_path.stem.replace("-", " ").title(), True)
        for secret_path in sorted(SECRET_DIR.glob("*.txt"))
        if SECRET_NAME_PATTERN.fullmatch(secret_path.stem)
        and secret_path.stat().st_size > 0
    ]


def print_status():  #Shows setup without exposing secret values
    print(f"Encrypted secret folder: {SECRET_DIR}")
    secrets = get_secret_status()

    if not secrets:
        print("- No bank secrets saved")
        return

    for _, label, _ in secrets:
        print(f"- {label}: configured")


def read_arguments():  #Reads the encrypted secret command
    parser = argparse.ArgumentParser(description="Manage encrypted bank tokens")
    parser.add_argument("command", choices=["status"])
    return parser.parse_args()


def main():  #Runs the selected encrypted secret command
    arguments = read_arguments()

    try:
        if arguments.command == "status":
            print_status()
    except (OSError, ValueError) as error:
        print(f"Could not manage encrypted secrets: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
