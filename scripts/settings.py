import sqlite3
from contextlib import closing

try:
    from scripts.common import check_database
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import check_database
    from paths import DATABASE_PATH


DEFAULT_SETTINGS = {
    "display_currency": "NZD",
    "daily_alert_enabled": "false",
    "daily_alert_send_if_empty": "false",
    "daily_alert_time": "07:00",
    "daily_alert_currency": "NZD",
    "daily_alert_hours": "24",
    "daily_alert_max_rows": "20",
}

SETTING_OPTIONS = {
    "display_currency": {"NZD", "USD"},
    "daily_alert_enabled": {"true", "false"},
    "daily_alert_send_if_empty": {"true", "false"},
    "daily_alert_currency": {"NZD", "USD"},
}

SETTING_RANGES = {
    "daily_alert_hours": (1, 168),
    "daily_alert_max_rows": (1, 50),
}


def clean_time(value):  #Keeps the alert time in simple 24 hour format
    pieces = value.strip().split(":")
    if len(pieces) != 2:
        raise ValueError("Alert time must be HH:MM")

    hour = int(pieces[0])
    minute = int(pieces[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Alert time must be between 00:00 and 23:59")

    return f"{hour:02d}:{minute:02d}"


def clean_number_setting(key, value):  #Checks number settings before SQLite
    minimum, maximum = SETTING_RANGES[key]
    number = int(value)
    if number < minimum or number > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")

    return str(number)


def clean_setting(key, value):  #Checks one setting before it reaches SQLite
    if key not in SETTING_OPTIONS and key not in SETTING_RANGES and key != "daily_alert_time":
        raise ValueError(f"Unknown setting: {key}")

    value = value.strip()
    if key == "daily_alert_time":
        return clean_time(value)
    if key in SETTING_RANGES:
        return clean_number_setting(key, value)
    if key not in {"display_currency", "daily_alert_currency"}:
        value = value.lower()
    else:
        value = value.upper()

    if value not in SETTING_OPTIONS[key]:
        raise ValueError(f"Invalid value for {key}: {value}")

    return value


def get_settings(database_path=DATABASE_PATH):  #Gets settings with safe defaults for missing rows
    check_database(database_path)
    settings = DEFAULT_SETTINGS.copy()

    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT key, value
            FROM app_settings
            ORDER BY key
            """
        ).fetchall()

    settings.update(rows)
    return settings


def update_settings(changes, database_path=DATABASE_PATH):  #Saves only the supplied settings changes
    check_database(database_path)
    cleaned = {
        key: clean_setting(key, value)
        for key, value in changes.items()
    }

    if not cleaned:
        raise ValueError("Choose at least one setting to update")

    with closing(sqlite3.connect(database_path)) as connection:
        connection.executemany(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT (key)
            DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            cleaned.items(),
        )
        connection.commit()
