import argparse
import http.client
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date
from decimal import Decimal, InvalidOperation

try:
    from scripts.common import check_database, get_latest_rate
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import check_database, get_latest_rate
    from paths import DATABASE_PATH


FX_HOST = "api.frankfurter.dev"
FX_PATH = "/v2/rate/NZD/USD"
FX_SOURCE = "frankfurter"
MAX_RESPONSE_BYTES = 10_000
MAX_HISTORY_RESPONSE_BYTES = 2_000_000


def fetch_latest_nzd_usd_rate():  #Gets one current public rate without sending private data
    connection = http.client.HTTPSConnection(FX_HOST, timeout=15)

    try:
        connection.request("GET", FX_PATH, headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, http.client.HTTPException) as error:
        raise OSError("Could not securely reach the exchange rate service") from error
    finally:
        connection.close()

    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Exchange rate response was unexpectedly large")

    if response.status != 200:
        raise ValueError(f"Exchange rate request failed with status {response.status}")

    try:
        result = json.loads(body)
        rate_date = date.fromisoformat(result["date"])
        rate = Decimal(str(result["rate"]))
    except (KeyError, TypeError, ValueError, InvalidOperation, json.JSONDecodeError) as error:
        raise ValueError("Exchange rate service returned an invalid response") from error

    if result.get("base") != "NZD" or result.get("quote") != "USD":
        raise ValueError("Exchange rate service returned the wrong currency pair")

    if rate_date > date.today() or not rate.is_finite() or rate <= 0:
        raise ValueError("Exchange rate service returned an invalid rate")

    return rate, rate_date.isoformat()


def save_rate_pair(nzd_to_usd, rate_date, database_path=DATABASE_PATH):  #Saves the current rate and its matching reverse rate
    check_database(database_path)
    usd_to_nzd = Decimal("1") / nzd_to_usd
    rates = [
        ("NZD", "USD", str(nzd_to_usd)),
        ("USD", "NZD", str(usd_to_nzd)),
    ]

    with closing(sqlite3.connect(database_path)) as connection:
        connection.executemany(
            """
            INSERT INTO fx_rates (
                base_currency,
                quote_currency,
                rate,
                rate_date,
                source
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (base_currency, quote_currency, rate_date, source)
            DO UPDATE SET
                rate = excluded.rate,
                fetched_at = CURRENT_TIMESTAMP
            """,
            [
                (base, quote, rate, rate_date, FX_SOURCE)
                for base, quote, rate in rates
            ],
        )
        connection.commit()

    return usd_to_nzd


def save_usd_rate_pairs(rates, database_path=DATABASE_PATH):  #Saves historic USD to NZD rates and their reverse rates
    check_database(database_path)
    saved_rows = []

    for rate_date, usd_to_nzd in rates:
        nzd_to_usd = Decimal("1") / usd_to_nzd
        saved_rows.extend(
            [
                ("USD", "NZD", str(usd_to_nzd), rate_date, FX_SOURCE),
                ("NZD", "USD", str(nzd_to_usd), rate_date, FX_SOURCE),
            ]
        )

    with closing(sqlite3.connect(database_path)) as connection:
        connection.executemany(
            """
            INSERT INTO fx_rates (
                base_currency,
                quote_currency,
                rate,
                rate_date,
                source
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (base_currency, quote_currency, rate_date, source)
            DO UPDATE SET
                rate = excluded.rate,
                fetched_at = CURRENT_TIMESTAMP
            """,
            saved_rows,
        )
        connection.commit()

    return len(rates)


def validate_rate_date(value, label):  #Checks command line dates before calling the public rate API
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} must use YYYY-MM-DD") from error

    if parsed > date.today():
        raise ValueError(f"{label} cannot be in the future")

    return parsed


def parse_historical_usd_nzd_rates(result, start, end):  #Accepts the public API history format
    saved_rates = []

    if isinstance(result, dict) and isinstance(result.get("rates"), dict):
        rows = [
            {"date": rate_date, "base": "USD", "quote": "NZD", "rate": quote_values.get("NZD")}
            for rate_date, quote_values in result["rates"].items()
            if isinstance(quote_values, dict)
        ]
    elif isinstance(result, list):
        rows = result
    else:
        raise ValueError("Exchange rate service returned invalid history")

    for row in sorted(rows, key=lambda item: str(item.get("date", ""))):
        try:
            parsed_date = validate_rate_date(str(row["date"]), "Rate date")
            rate = Decimal(str(row["rate"]))
        except (KeyError, TypeError, InvalidOperation, ValueError) as error:
            raise ValueError("Exchange rate service returned an invalid USD/NZD rate") from error

        if row.get("base") != "USD" or row.get("quote") != "NZD":
            raise ValueError("Exchange rate service returned the wrong currency pair")

        if parsed_date < start or parsed_date > end or not rate.is_finite() or rate <= 0:
            raise ValueError("Exchange rate service returned an out of range rate")

        saved_rates.append((parsed_date.isoformat(), rate))

    if not saved_rates:
        raise ValueError("Exchange rate service returned no rates")

    return saved_rates


def fetch_historical_usd_nzd_rates(start_date, end_date):  #Gets daily USD to NZD rates for old imports
    start = validate_rate_date(start_date, "Start date")
    end = validate_rate_date(end_date, "End date")

    if start > end:
        raise ValueError("Start date must be before end date")

    path = f"/v2/rates?from={start.isoformat()}&to={end.isoformat()}&base=USD&quotes=NZD"
    connection = http.client.HTTPSConnection(FX_HOST, timeout=30)

    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(MAX_HISTORY_RESPONSE_BYTES + 1)
    except (OSError, http.client.HTTPException) as error:
        raise OSError("Could not securely reach the exchange rate service") from error
    finally:
        connection.close()

    if len(body) > MAX_HISTORY_RESPONSE_BYTES:
        raise ValueError("Exchange rate history response was unexpectedly large")

    if response.status != 200:
        raise ValueError(f"Exchange rate history request failed with status {response.status}")

    try:
        result = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError("Exchange rate service returned invalid history") from error

    return parse_historical_usd_nzd_rates(result, start, end)


def refresh_exchange_rates(database_path=DATABASE_PATH):  #Fetches and saves the latest NZD and USD rates
    nzd_to_usd, rate_date = fetch_latest_nzd_usd_rate()
    usd_to_nzd = save_rate_pair(nzd_to_usd, rate_date, database_path)
    return rate_date, nzd_to_usd, usd_to_nzd


def get_saved_rate_pair(database_path=DATABASE_PATH):  #Uses the latest saved public rate when the public API is down
    check_database(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        nzd_to_usd, rate_date, source = get_latest_rate(connection, "NZD", "USD")
        usd_to_nzd, _, _ = get_latest_rate(connection, "USD", "NZD")

    return rate_date, nzd_to_usd, usd_to_nzd, source


def refresh_exchange_rates_or_saved(database_path=DATABASE_PATH):  #Lets bank refresh continue if public rates are down
    try:
        rate_date, nzd_to_usd, usd_to_nzd = refresh_exchange_rates(database_path)
        return rate_date, nzd_to_usd, usd_to_nzd, ""
    except (OSError, ValueError) as error:
        rate_date, nzd_to_usd, usd_to_nzd, source = get_saved_rate_pair(database_path)
        warning = f" using saved FX rate from {rate_date} ({source}) because latest failed: {error}"
        return rate_date, nzd_to_usd, usd_to_nzd, warning


def read_arguments():  #Keeps latest refresh as the default command
    parser = argparse.ArgumentParser(description="Refresh public NZD and USD exchange rates")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("latest", help="Refresh the latest exchange rate")

    historical_parser = subparsers.add_parser("historical", help="Backfill old USD/NZD rates")
    historical_parser.add_argument("--start", required=True)
    historical_parser.add_argument("--end", required=True)

    return parser.parse_args()


def main():  #Refreshes rates without needing any bank tokens
    arguments = read_arguments()
    command = arguments.command or "latest"

    try:
        if command == "latest":
            rate_date, nzd_to_usd, usd_to_nzd = refresh_exchange_rates()
            print(f"Exchange rate date: {rate_date}")
            print(f"NZD -> USD: {nzd_to_usd}")
            print(f"USD -> NZD: {usd_to_nzd}")
        elif command == "historical":
            rates = fetch_historical_usd_nzd_rates(arguments.start, arguments.end)
            saved_count = save_usd_rate_pairs(rates)
            print(f"Historical rate dates saved: {saved_count}")
            print(f"Date range: {arguments.start} to {arguments.end}")
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error) as error:
        print(f"Could not refresh exchange rates: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
