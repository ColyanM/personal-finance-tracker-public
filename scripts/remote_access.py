import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

try:
    from scripts.paths import PRIVATE_DATA_DIR
except ModuleNotFoundError:
    from paths import PRIVATE_DATA_DIR


CONFIG_VERSION = 1
CONFIG_PATH = PRIVATE_DATA_DIR / "tailscale_access.json"
ORIGIN_ENVIRONMENT_VARIABLE = "FINANCE_HUB_TAILSCALE_ORIGIN"
USER_ENVIRONMENT_VARIABLE = "FINANCE_HUB_TAILSCALE_USER"
TAILSCALE_HOST_SUFFIX = ".ts.net"
HOST_LABEL_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class TailscaleAccessSettings:
    origin: str
    host: str
    user_login: str


def clean_tailscale_origin(origin):  #Accepts one exact private Tailscale HTTPS origin
    if not isinstance(origin, str) or not origin.strip():
        raise ValueError("Tailscale origin is required")

    candidate = origin.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as error:
        raise ValueError("Tailscale origin is invalid") from error

    hostname = (parsed.hostname or "").lower()
    if parsed.scheme.lower() != "https":
        raise ValueError("Tailscale origin must use https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Tailscale origin must not contain credentials")
    if port is not None:
        raise ValueError("Tailscale origin must use the default HTTPS port")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Tailscale origin must not contain a path, query, or fragment")
    if not hostname.endswith(TAILSCALE_HOST_SUFFIX):
        raise ValueError("Tailscale origin hostname must end in .ts.net")

    labels = hostname.split(".")
    if len(labels) < 4 or not all(HOST_LABEL_PATTERN.fullmatch(label) for label in labels):
        raise ValueError("Tailscale origin hostname is invalid")

    return f"https://{hostname}"


def clean_tailscale_user(user_login):  #Pins remote access to one Tailscale login
    if not isinstance(user_login, str) or not user_login.strip():
        raise ValueError("Tailscale user login is required")

    user_login = user_login.strip()
    if len(user_login) > 320 or any(ord(character) < 32 for character in user_login):
        raise ValueError("Tailscale user login is invalid")

    return user_login


def build_tailscale_settings(origin, user_login):  #Builds one validated remote-access record
    origin = clean_tailscale_origin(origin)
    return TailscaleAccessSettings(
        origin=origin,
        host=urlsplit(origin).hostname,
        user_login=clean_tailscale_user(user_login),
    )


def settings_from_data(data):  #Validates the private JSON configuration
    if not isinstance(data, dict):
        raise ValueError("Tailscale access configuration must be a JSON object")
    if data.get("version") != CONFIG_VERSION:
        raise ValueError("Unsupported Tailscale access configuration version")
    if data.get("mode") != "tailscale-serve":
        raise ValueError("Remote access mode must be tailscale-serve")

    return build_tailscale_settings(data.get("origin"), data.get("user_login"))


def load_tailscale_settings(config_path=CONFIG_PATH, environment=None):  #Loads an optional fail-closed configuration
    environment = os.environ if environment is None else environment
    environment_origin = environment.get(ORIGIN_ENVIRONMENT_VARIABLE, "").strip()
    environment_user = environment.get(USER_ENVIRONMENT_VARIABLE, "").strip()

    if environment_origin or environment_user:
        if not environment_origin or not environment_user:
            raise ValueError(
                f"Set both {ORIGIN_ENVIRONMENT_VARIABLE} and {USER_ENVIRONMENT_VARIABLE}"
            )
        return build_tailscale_settings(environment_origin, environment_user)

    config_path = Path(config_path)
    if not config_path.exists():
        return None

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError("Tailscale access configuration is not valid JSON") from error

    return settings_from_data(data)


def save_tailscale_settings(origin, user_login, config_path=CONFIG_PATH):  #Saves non-secret access settings outside Git
    settings = build_tailscale_settings(origin, user_login)
    config_path = Path(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = config_path.with_suffix(f"{config_path.suffix}.tmp")
    data = {
        "version": CONFIG_VERSION,
        "mode": "tailscale-serve",
        "origin": settings.origin,
        "user_login": settings.user_login,
    }
    temporary_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(config_path)
    return settings


def remove_tailscale_settings(config_path=CONFIG_PATH):  #Removes only Finance Hub's local access record
    config_path = Path(config_path)
    if config_path.exists():
        config_path.unlink()
        return True

    return False
