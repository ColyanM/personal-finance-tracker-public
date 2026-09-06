import argparse
import sqlite3
import sys
from calendar import monthrange
from collections import defaultdict
from contextlib import closing
from datetime import date, timedelta
from statistics import median

try:
    from scripts.common import (
        check_database,
        convert_minor_units,
        format_minor,
        get_latest_rate,
    )
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import check_database, convert_minor_units, format_minor, get_latest_rate
    from paths import DATABASE_PATH

FREQUENCIES = (
    ("Weekly", 7, 5, 9),
    ("Fortnightly", 14, 12, 16),
    ("Monthly", 30, 25, 35),
)
SUPPORTED_CURRENCIES = {"NZD", "USD"}


def classify_frequency(date_values):  #Finds only simple schedules with consistent gaps
    dates = sorted(set(date_values))[-6:]
    if len(dates) < 3:
        return None

    intervals = [
        (current - previous).days
        for previous, current in zip(dates, dates[1:])
    ]
    typical_gap = median(intervals)

    for label, days, minimum, maximum in FREQUENCIES:
        matching = sum(minimum <= gap <= maximum for gap in intervals)
        required_matches = max(2, len(intervals) - 1)
        if minimum <= typical_gap <= maximum and matching >= required_matches:
            return label, days

    return None


def next_monthly_date(today, payment_day):  #Uses the last day when a month is shorter
    day = min(payment_day, monthrange(today.year, today.month)[1])
    next_date = today.replace(day=day)
    if next_date >= today:
        return next_date

    if today.month == 12:
        year, month = today.year + 1, 1
    else:
        year, month = today.year, today.month + 1

    day = min(payment_day, monthrange(year, month)[1])
    return date(year, month, day)


def load_transaction_groups(connection, today):  #Loads enough history without keeping another copy
    start_date = (today - timedelta(days=400)).isoformat()
    rows = connection.execute(
        """
        SELECT t.account_id, a.display_name,
               COALESCE(t.merchant_clean, t.description_raw),
               COALESCE(c.name, 'Uncategorized'), t.posted_date,
               t.amount_nzd_minor_fixed, t.amount_usd_minor_fixed
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.transaction_status = 'posted'
          AND t.posted_date >= ?
          AND COALESCE(c.is_transfer, 0) = 0
        ORDER BY t.posted_date
        """,
        (start_date,),
    ).fetchall()

    groups = defaultdict(list)
    for account_id, account, description, category, posted, amount_nzd, amount_usd in rows:
        amount_sign = 1 if amount_usd >= 0 else -1
        key = (account_id, description.strip().casefold(), category, amount_sign)
        groups[key].append(
            {
                "account": account,
                "description": description.strip(),
                "category": category,
                "date": date.fromisoformat(posted),
                "amount_nzd_minor": amount_nzd,
                "amount_usd_minor": amount_usd,
            }
        )

    return groups


def amounts_are_consistent(rows):  #Rejects casual spending that only looks regular by date
    amounts = [abs(row["amount_usd_minor"]) for row in rows[-6:]]
    typical_amount = median(amounts)
    allowed_difference = max(200, typical_amount // 5)
    return all(
        abs(amount - typical_amount) <= allowed_difference
        for amount in amounts
    )


def detect_patterns(connection, today):  #Turns consistent history into estimated schedules
    patterns = []

    for rows in load_transaction_groups(connection, today).values():
        schedule = classify_frequency([row["date"] for row in rows])
        if schedule is None or not amounts_are_consistent(rows):
            continue

        frequency, interval_days = schedule
        last_date = max(row["date"] for row in rows)
        if (today - last_date).days > interval_days * 2:
            continue

        next_date = last_date + timedelta(days=interval_days)
        while next_date < today:
            next_date += timedelta(days=interval_days)

        patterns.append(
            {
                "account": rows[-1]["account"],
                "description": rows[-1]["description"],
                "category": rows[-1]["category"],
                "frequency": frequency,
                "interval_days": interval_days,
                "next_date": next_date,
                "amount_nzd_minor": int(median(row["amount_nzd_minor"] for row in rows)),
                "amount_usd_minor": int(median(row["amount_usd_minor"] for row in rows)),
                "source": "Detected",
            }
        )

    return patterns


def load_manual_loan_patterns(connection, today):  #Includes manually entered loan details
    rows = connection.execute(
        """
        SELECT display_name, native_currency, manual_payment_minor,
               manual_payment_day
        FROM accounts
        WHERE is_active = 1
          AND balance_type = 'liability'
          AND manual_payment_minor IS NOT NULL
          AND manual_payment_day IS NOT NULL
        ORDER BY display_name
        """
    ).fetchall()
    patterns = []

    for account, currency, payment_minor, payment_day in rows:
        if currency == "NZD":
            amount_nzd = -payment_minor
            rate, _, _ = get_latest_rate(connection, "NZD", "USD")
            amount_usd = -convert_minor_units(payment_minor, rate)
        else:
            amount_usd = -payment_minor
            rate, _, _ = get_latest_rate(connection, "USD", "NZD")
            amount_nzd = -convert_minor_units(payment_minor, rate)

        patterns.append(
            {
                "account": account,
                "description": f"{account} payment",
                "category": "Loan payment",
                "frequency": "Monthly",
                "interval_days": None,
                "payment_day": payment_day,
                "next_date": next_monthly_date(today, payment_day),
                "amount_nzd_minor": amount_nzd,
                "amount_usd_minor": amount_usd,
                "source": "Saved schedule",
            }
        )

    return patterns


def same_payment(detected, saved):  #Stops one monthly payment appearing twice
    if detected["frequency"] != "Monthly" or detected["amount_usd_minor"] >= 0:
        return False

    difference = abs(
        abs(detected["amount_usd_minor"]) - abs(saved["amount_usd_minor"])
    )
    allowed_difference = max(500, abs(saved["amount_usd_minor"]) // 20)
    return difference <= allowed_difference


def combine_patterns(connection, today):  #Prefers a saved schedule over an estimate
    detected = detect_patterns(connection, today)
    saved = load_manual_loan_patterns(connection, today)

    for saved_pattern in saved:
        detected = [
            pattern
            for pattern in detected
            if not same_payment(pattern, saved_pattern)
        ]

    return detected + saved


def add_month(date_value, payment_day):  #Moves a saved monthly schedule forward once
    next_day = date_value + timedelta(days=1)
    return next_monthly_date(next_day, payment_day)


def build_upcoming(patterns, currency, days, today):  #Creates each expected payment in the selected range
    end_date = today + timedelta(days=days)
    amount_key = f"amount_{currency.lower()}_minor"
    upcoming = []

    for pattern in patterns:
        due_date = pattern["next_date"]
        while due_date <= end_date:
            upcoming.append(
                {
                    "account": pattern["account"],
                    "description": pattern["description"],
                    "category": pattern["category"],
                    "frequency": pattern["frequency"],
                    "due_date": due_date.isoformat(),
                    "amount_minor": pattern[amount_key],
                    "source": pattern["source"],
                }
            )
            if pattern.get("payment_day"):
                due_date = add_month(due_date, pattern["payment_day"])
            else:
                due_date += timedelta(days=pattern["interval_days"])

    return sorted(upcoming, key=lambda row: (row["due_date"], row["description"]))


def get_recurring_forecast(
    currency="NZD",
    days=30,
    today=None,
    database_path=DATABASE_PATH,
):  #Builds the recurring page from history and saved schedules
    check_database(database_path)
    currency = currency.upper()
    today = today or date.today()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Currency must be NZD or USD")
    if days < 1 or days > 365:
        raise ValueError("Forecast days must be between 1 and 365")

    with closing(sqlite3.connect(database_path)) as connection:
        patterns = combine_patterns(connection, today)
        upcoming = build_upcoming(patterns, currency, days, today)

    income_minor = sum(row["amount_minor"] for row in upcoming if row["amount_minor"] > 0)
    expense_minor = sum(-row["amount_minor"] for row in upcoming if row["amount_minor"] < 0)
    return {
        "currency": currency,
        "days": days,
        "pattern_count": len(patterns),
        "income_minor": income_minor,
        "expense_minor": expense_minor,
        "net_minor": income_minor - expense_minor,
        "upcoming": upcoming,
    }


def read_arguments():  #Reads the small command line forecast options
    parser = argparse.ArgumentParser(description="Show estimated recurring transactions")
    parser.add_argument("--currency", default="NZD", choices=["NZD", "USD"])
    parser.add_argument("--days", default=30, type=int)
    return parser.parse_args()


def main():  #Prints a short forecast without changing anything
    arguments = read_arguments()
    try:
        forecast = get_recurring_forecast(arguments.currency, arguments.days)
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(f"Could not build recurring forecast: {error}", file=sys.stderr)
        return 1

    print(f"Recurring schedules: {forecast['pattern_count']}")
    print(f"Upcoming transactions: {len(forecast['upcoming'])}")
    print(f"Expected income: {format_minor(forecast['income_minor'])} {forecast['currency']}")
    print(f"Expected expenses: {format_minor(forecast['expense_minor'])} {forecast['currency']}")
    print(f"Expected net: {format_minor(forecast['net_minor'])} {forecast['currency']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code to Command Prompt
