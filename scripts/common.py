import argparse
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def check_database(database_path):  #Stops SQLite from creating an empty database by mistake
    if not database_path.exists():
        raise FileNotFoundError(
            "The database does not exist. Run python scripts/db_init.py first"
        )


def format_minor(amount_minor):  #Turns stored cents back into dollars
    return f"{Decimal(amount_minor) / Decimal('100'):.2f}"


def safe_text(value, fallback):  #Keeps external text safe for console output
    text = "".join(character for character in str(value or "") if character.isprintable())
    return text.strip() or fallback


def convert_minor_units(amount_minor, rate):  #Converts cents without float rounding
    converted = Decimal(amount_minor) * Decimal(rate)
    return int(converted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def money_to_minor(amount):  #Turns a typed money amount into cents
    try:
        decimal_amount = Decimal(amount)
    except InvalidOperation as error:
        raise ValueError(f"Invalid amount: {amount}") from error

    return int(
        (decimal_amount * Decimal("100")).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def normalized_match_text(*values):  #Makes bank text easier to compare across imports
    text = " ".join(safe_text(value, "") for value in values)
    return "".join(character for character in text.lower() if character.isalnum())


def text_is_close_enough(left, right):  #Lets late bank rows through unless the text really matches
    left = normalized_match_text(left)
    right = normalized_match_text(right)

    if not left or not right:
        return False

    if left == right:
        return True

    shorter, longer = sorted([left, right], key=len)
    return len(shorter) >= 4 and shorter in longer


def imported_transaction_exists(connection, provider, transaction):  #Checks if a CSV import already covered this bank row
    rows = connection.execute(
        """
        SELECT description_raw, merchant_clean
        FROM transactions
        WHERE provider = ?
          AND posted_date = ?
          AND original_currency = ?
          AND original_amount_minor = ?
        """,
        (
            provider,
            transaction["posted_date"],
            transaction["original_currency"],
            transaction["original_amount_minor"],
        ),
    ).fetchall()
    incoming_text = " ".join(
        part
        for part in [
            transaction["description_raw"],
            transaction.get("merchant_clean"),
        ]
        if part
    )

    for description, merchant in rows:
        existing_text = " ".join(part for part in [description, merchant] if part)
        if text_is_close_enough(existing_text, incoming_text):
            return True

    return False


def get_provider_cutoff_date(connection, provider):  #Finds the newest posted row for one data source
    row = connection.execute(
        """
        SELECT MAX(posted_date)
        FROM transactions
        WHERE provider = ?
          AND transaction_status = 'posted'
        """,
        (provider,),
    ).fetchone()

    return row[0] if row and row[0] else None


def get_refresh_days(
    connection,
    live_provider,
    history_provider,
    default_days,
    today=None,
    overlap_days=3,
    max_days=90,
):  #Checks enough days without going deep into old history every time
    if default_days < 1 or max_days < default_days:
        raise ValueError("Refresh day settings are invalid")

    today = today or date.today()
    latest_live = get_provider_cutoff_date(connection, live_provider)
    latest_history = get_provider_cutoff_date(connection, history_provider)
    saved_dates = [saved_date for saved_date in (latest_live, latest_history) if saved_date]

    if not saved_dates:
        return default_days

    try:
        latest_saved_date = max(
            date.fromisoformat(saved_date) for saved_date in saved_dates
        )
    except ValueError as error:
        raise ValueError("Saved transaction date is invalid") from error

    if latest_saved_date >= today:
        return default_days

    start_date = latest_saved_date - timedelta(days=overlap_days)
    needed_days = (today - start_date).days + 1

    if needed_days > max_days:
        raise ValueError(
            f"Refresh would need {needed_days} days. Backfill transactions first so no rows are missed"
        )

    return max(default_days, needed_days)


def get_latest_rate(connection, base_currency, quote_currency):  #Finds the newest saved exchange rate
    if base_currency == quote_currency:
        return Decimal("1"), None, "same_currency"

    rate = connection.execute(
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

    if rate is None:
        raise ValueError(
            f"No exchange rate found for {base_currency} to {quote_currency}"
        )

    return Decimal(rate[0]), rate[1], rate[2]


def get_rate_for_date(connection, base_currency, quote_currency, target_date):  #Finds the best saved exchange rate for a historic date
    if base_currency == quote_currency:
        return Decimal("1"), target_date, "same_currency"

    rate = connection.execute(
        """
        SELECT rate, rate_date, source
        FROM fx_rates
        WHERE base_currency = ?
          AND quote_currency = ?
          AND rate_date <= ?
        ORDER BY rate_date DESC, id DESC
        LIMIT 1
        """,
        (base_currency, quote_currency, target_date),
    ).fetchone()

    if rate is not None:
        source = rate[2] if rate[1] == target_date else f"{rate[2]}_closest_before"
        return Decimal(rate[0]), rate[1], source

    rate = connection.execute(
        """
        SELECT rate, rate_date, source
        FROM fx_rates
        WHERE base_currency = ?
          AND quote_currency = ?
          AND rate_date > ?
        ORDER BY rate_date ASC, id ASC
        LIMIT 1
        """,
        (base_currency, quote_currency, target_date),
    ).fetchone()

    if rate is None:
        raise ValueError(
            f"No exchange rate found for {base_currency} to {quote_currency}"
        )

    return Decimal(rate[0]), rate[1], f"{rate[2]}_closest_after"


def parse_active(value):  #Converts true or false text from PowerShell into a boolean
    value = value.lower()

    if value == "true":
        return True

    if value == "false":
        return False

    raise argparse.ArgumentTypeError("Active must be true or false")
