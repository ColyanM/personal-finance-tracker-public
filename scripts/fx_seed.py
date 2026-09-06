import argparse
import json
import sqlite3
import sys
from contextlib import closing
from decimal import Decimal, InvalidOperation

try:
    from scripts.common import (
        check_database,
        convert_minor_units,
        format_minor,
        money_to_minor,
    )
    from scripts.paths import DATABASE_PATH, SEED_DATA_DIR
except ModuleNotFoundError:
    from common import (
        check_database,
        convert_minor_units,
        format_minor,
        money_to_minor,
    )
    from paths import DATABASE_PATH, SEED_DATA_DIR

SEED_PATH = SEED_DATA_DIR / "fx_rates_seed.json"  #Keeps the starting rates easy to edit
SUPPORTED_CURRENCIES = {"NZD", "USD"}  #Currencies supported by the seed workflow


def read_seed_rates(seed_path=SEED_PATH):  #Reads and checks the saved exchange rates
    with seed_path.open(encoding="utf-8") as seed_file:
        rates = json.load(seed_file)  #Turns the JSON list into Python values

    if not isinstance(rates, list) or not rates:
        raise ValueError("The seed file must contain a non-empty list of rates.")

    required_fields = {
        "base_currency",
        "quote_currency",
        "rate",
        "rate_date",
        "source",
    }

    for rate in rates:
        missing_fields = required_fields - rate.keys()  #Catches incomplete rates before touching the database
        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(f"A seed rate is missing: {missing}")

        if (
            rate["base_currency"] not in SUPPORTED_CURRENCIES
            or rate["quote_currency"] not in SUPPORTED_CURRENCIES
        ):
            raise ValueError("Seed rates can only use NZD or USD.")

        try:
            decimal_rate = Decimal(rate["rate"])  #Decimal avoids float rounding problems
        except InvalidOperation as error:
            raise ValueError(f"Invalid exchange rate: {rate['rate']}") from error

        if decimal_rate <= 0:
            raise ValueError("Exchange rates must be greater than zero.")

    return rates


def seed_rates(database_path=DATABASE_PATH, seed_path=SEED_PATH):  #Saves the starting exchange rates
    rates = read_seed_rates(seed_path)

    with closing(sqlite3.connect(database_path)) as connection:  #Explicit closing avoids locked files on Windows
        connection.executemany(  #Rerunning seed updates the same rates instead of duplicating them
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
                (
                    rate["base_currency"],
                    rate["quote_currency"],
                    rate["rate"],
                    rate["rate_date"],
                    rate["source"],
                )
                for rate in rates
            ],
        )
        connection.commit()  #Saves every seed rate in one transaction

    return len(rates)


def get_latest_rate(base_currency, quote_currency, database_path=DATABASE_PATH):  #Finds the newest saved rate
    base_currency = base_currency.upper()  #Makes command input consistent
    quote_currency = quote_currency.upper()

    if base_currency == quote_currency:
        return Decimal("1"), None, "same_currency"  #No exchange rate is needed for the same currency

    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            """
            SELECT rate, rate_date, source
            FROM fx_rates
            WHERE base_currency = ?
              AND quote_currency = ?
            ORDER BY rate_date DESC, id DESC
            LIMIT 1
            """,
            (base_currency, quote_currency),
        ).fetchone()

    if row is None:
        raise ValueError(
            f"No exchange rate found for {base_currency} to {quote_currency}."
        )

    return Decimal(row[0]), row[1], row[2]  #Reads the stored text rate without losing precision


def list_rates(database_path=DATABASE_PATH):  #Gets all saved exchange rates for checking
    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(  #Lists rates in a predictable order for checking them
            """
            SELECT base_currency, quote_currency, rate, rate_date, source
            FROM fx_rates
            ORDER BY base_currency, quote_currency, rate_date DESC
            """
        ).fetchall()


def read_arguments():  #Reads the seed list and convert commands
    parser = argparse.ArgumentParser(  #Keeps this script runnable from PowerShell
        description="Seed and check the offline NZD/USD exchange rates."
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("seed", help="Add the seed rates to the database.")
    subparsers.add_parser("list", help="Show all saved exchange rates.")

    convert_parser = subparsers.add_parser(
        "convert", help="Convert an amount using the latest saved rate."
    )
    convert_parser.add_argument("amount", help="Major-unit amount, such as 100.00")
    convert_parser.add_argument("base_currency", choices=["NZD", "USD"])
    convert_parser.add_argument("quote_currency", choices=["NZD", "USD"])

    return parser.parse_args()


def main():  #Runs the right exchange rate command from the arguments
    arguments = read_arguments()
    command = arguments.command or "seed"  #Running without a subcommand seeds the rates

    try:
        check_database(DATABASE_PATH)
        if command == "seed":
            count = seed_rates()
            print(f"Saved {count} exchange rates.")
            print(f"Seed file: {SEED_PATH}")
        elif command == "list":
            rates = list_rates()
            if not rates:
                print("No exchange rates found. Run the seed command first.")
                return 0

            for base, quote, rate, rate_date, source in rates:
                print(f"{base} -> {quote}: {rate} ({rate_date}, {source})")
        elif command == "convert":
            amount_minor = money_to_minor(arguments.amount)
            rate, rate_date, source = get_latest_rate(
                arguments.base_currency, arguments.quote_currency
            )
            converted_minor = convert_minor_units(amount_minor, rate)
            print(
                f"{format_minor(amount_minor)} {arguments.base_currency} = "
                f"{format_minor(converted_minor)} {arguments.quote_currency}"
            )
            print(f"Rate: {rate} ({rate_date or 'not needed'}, {source})")
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        sqlite3.Error,
    ) as error:
        print(f"Could not process exchange rates: {error}", file=sys.stderr)  #Gives PowerShell a useful error
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
