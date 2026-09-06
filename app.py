import json
import mimetypes
import smtplib
import sqlite3
from bisect import bisect_right
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlparse

from scripts.accounts import get_net_worth, list_accounts, rename_account
from scripts.akahu_setup import get_setup_status as get_akahu_setup_status
from scripts.bnz_refresh import refresh_bnz
from scripts.budgets import (
    add_budget,
    delete_budget,
    get_budget_progress,
    get_planned_income_total,
    has_prior_budget,
    list_budgets,
    update_budget,
)
from scripts.category_rules import add_rule, apply_rules, cleanup_summary, deactivate_rule, list_rules
from scripts.categories import CATEGORY_GROUP_ORDER, add_category, category_group_rank, list_categories, update_category
from scripts.common import convert_minor_units
from scripts.fx_refresh import refresh_exchange_rates
from scripts.fx_seed import get_latest_rate as get_saved_rate
from scripts.goals import list_goals, save_goal
from scripts.investments import list_positions, save_position
from scripts.paths import DATABASE_PATH
from scripts.plaid_environment import get_plaid_config
from scripts.plaid_setup import get_setup_status as get_plaid_setup_status
from scripts.plaid_transactions import refresh_plaid
from scripts.recurring import get_recurring_forecast
from scripts.remote_access import load_tailscale_settings
from scripts.send_daily_transaction_alert import build_alert_preview, send_text_email
from scripts.settings import DEFAULT_SETTINGS, get_settings, update_settings
from scripts.summaries import (
    get_daily_spending_totals_between,
    get_spending_summary,
    get_spending_summary_between,
)
from scripts.sync_status import get_sync_status
from scripts.transactions import (
    clear_transaction_split,
    count_transactions,
    edit_transaction,
    list_transaction_splits,
    list_transactions,
    save_transaction_split,
    set_review_status,
)


HOST = "127.0.0.1"
PORT = 8000
EXPECTED_HOST = f"{HOST}:{PORT}"
EXPECTED_ORIGIN = f"http://{EXPECTED_HOST}"
TAILSCALE_USER_HEADER = "Tailscale-User-Login"
PROJECT_ROOT = Path(__file__).resolve().parent
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
FRONTEND_INDEX = FRONTEND_DIST / "index.html"
DISPLAY_CURRENCIES = {"NZD", "USD"}
TIMELINE_OPTIONS = ("1m", "3m", "6m", "1y", "3y", "5y", "all")
DEFAULT_TRANSACTION_LIMIT = 150
MAX_TRANSACTION_LIMIT = 500
REMOTE_ACCESS_SETTINGS = None


def get_query_value(query_values, name):  #Gets one value from query strings or React JSON
    value = query_values.get(name, [""])[0]
    return value.strip()


def get_current_week_start(today=None):  #Finds the Monday for the current budget week
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


def get_timeline(query_values):  #Reads the graph timeline switch
    timeline = get_query_value(query_values, "timeline")
    return timeline if timeline in TIMELINE_OPTIONS else "1m"


def get_transaction_limit(query_values):  #Keeps transaction pages from loading too many rows at once
    raw_limit = get_query_value(query_values, "limit")
    if not raw_limit:
        return DEFAULT_TRANSACTION_LIMIT

    try:
        limit = int(raw_limit)
    except ValueError:
        return DEFAULT_TRANSACTION_LIMIT

    return max(1, min(limit, MAX_TRANSACTION_LIMIT))


def get_budget_view(query_values):  #Reads whether the requested budget view is weekly or monthly
    view = get_query_value(query_values, "view")
    return "week" if view == "week" else "month"


def get_budget_sort(query_values):  #Reads the budget row sort choice
    sort_by = get_query_value(query_values, "sort") or get_query_value(query_values, "budget_sort")
    if sort_by in {"actual", "left_over", "over_budget"}:
        return sort_by

    return "planned"


def get_budget_week_start(query_values):  #Gets the budget week and keeps it on Monday
    week_start = get_query_value(query_values, "week_start")
    if not week_start:
        return get_current_week_start()

    try:
        selected_date = date.fromisoformat(week_start)
    except ValueError as error:
        raise ValueError("Week start must use YYYY-MM-DD") from error

    monday = selected_date - timedelta(days=selected_date.weekday())
    return monday.isoformat()


def get_budget_start(query_values):  #Gets the right budget date for week or month views
    if get_budget_view(query_values) == "month":
        month_start = get_query_value(query_values, "week_start")
        try:
            selected_date = date.fromisoformat(month_start) if month_start else date.today()
        except ValueError as error:
            raise ValueError("Month start must use YYYY-MM-DD") from error

        return selected_date.replace(day=1).isoformat()

    return get_budget_week_start(query_values)


def add_months(selected_date, months):  #Moves month buttons by calendar month
    month_index = selected_date.year * 12 + selected_date.month - 1 + months
    year = month_index // 12
    month = (month_index % 12) + 1
    day = min(selected_date.day, 28)
    return date(year, month, day)


def read_request_is_allowed(host, tailscale_user_login=None, remote_access=None):  #Allows local reads or one pinned Tailscale user
    if host == EXPECTED_HOST:
        return True

    if remote_access is None or not isinstance(host, str):
        return False

    return (
        host.casefold() == remote_access.host.casefold()
        and isinstance(tailscale_user_login, str)
        and tailscale_user_login.casefold() == remote_access.user_login.casefold()
    )


def request_origin_is_allowed(origin, referer, expected_origin):  #Checks browser writes came from the page being served
    if origin:
        return origin == expected_origin

    if not referer:
        return False

    parsed_referer = urlparse(referer)
    referer_origin = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
    return referer_origin == expected_origin


def write_request_is_allowed(
    host,
    origin,
    referer=None,
    tailscale_user_login=None,
    remote_access=None,
):  #Adds an exact same-origin check to authorized writes
    if not read_request_is_allowed(host, tailscale_user_login, remote_access):
        return False

    expected_origin = (
        EXPECTED_ORIGIN
        if host == EXPECTED_HOST
        else remote_access.origin
    )
    return request_origin_is_allowed(origin, referer, expected_origin)


def money_input_value(amount_minor):  #Turns stored cents into a clean input value
    amount = Decimal(abs(amount_minor)) / Decimal("100")
    return f"{amount:.2f}"


def get_display_rates(currency):  #Gets one current rate for each account currency
    return {
        native_currency: get_saved_rate(
            native_currency,
            currency,
            DATABASE_PATH,
        )[0]
        for native_currency in DISPLAY_CURRENCIES
    }


def get_display_currency(query_values):  #Reads the NZD or USD display choice
    currency = get_query_value(query_values, "currency").upper()
    if currency in DISPLAY_CURRENCIES:
        return currency

    try:
        return get_settings(DATABASE_PATH)["display_currency"]
    except (FileNotFoundError, sqlite3.Error):
        return DEFAULT_SETTINGS["display_currency"]


def get_rate_note(currency):  #Shows which latest saved rate is being used
    base_currency = "USD" if currency == "NZD" else "NZD"
    rate, rate_date, source = get_saved_rate(base_currency, currency, DATABASE_PATH)
    return f"1 {base_currency} = {rate:.5f} {currency} | {rate_date} | {source}"


def build_budget_title(view, week_start, selected_date=None):  #Shows the selected budget period
    week_start_date = date.fromisoformat(week_start)
    if view == "month":
        month_date = selected_date or week_start_date
        return month_date.strftime("%B %Y")

    week_end = week_start_date + timedelta(days=6)
    return f"Week of {week_start_date.isoformat()} to {week_end.isoformat()}"


def percent_of_income_value(amount_minor, income_minor):  #Shows a planned budget as a planned-income %
    if income_minor <= 0 or amount_minor <= 0:
        return ""

    percent = Decimal(amount_minor) * Decimal("100") / Decimal(income_minor)
    return str(percent.quantize(Decimal("0.1")))


def percent_of_income_text(amount_minor, income_minor):  #Shows a planned budget as income %
    percent = percent_of_income_value(amount_minor, income_minor)
    if not percent:
        return ""

    return f"{percent}% of planned income"


def budget_amount_from_form(amount, percent, budget_currency, week_start=None, source="", budget_view="week"):  #Gets budget dollars or income %
    amount = amount.strip()
    percent = percent.strip()
    if percent.endswith("%"):
        percent = percent[:-1].strip()
    source = source.strip()

    if source == "amount":
        return amount or "0"

    if source == "percent":
        if not percent:
            return "0"
        amount = ""

    if amount:
        return amount

    if not percent:
        return ""

    try:
        percent_value = Decimal(percent)
    except InvalidOperation as error:
        raise ValueError("Budget percent must be a number") from error

    if percent_value < 0 or percent_value > 100:
        raise ValueError("Budget percent must be between 0 and 100")

    if percent_value == 0:
        return "0"

    week_start = week_start or get_current_week_start()
    week_income = get_planned_income_total(
        week_start,
        budget_currency,
        database_path=DATABASE_PATH,
        budget_view=budget_view,
    )

    if week_income <= 0:
        week_income = get_spending_summary(
            "week",
            budget_currency,
            database_path=DATABASE_PATH,
        )["income_total_minor"]

    if week_income <= 0:
        raise ValueError("Plan income or import income before saving a percent budget")

    amount_minor = int((Decimal(week_income) * percent_value / Decimal("100")).quantize(Decimal("1")))
    return str(Decimal(amount_minor) / Decimal("100"))


def budget_amount_is_zero(amount):  #Checks if a saved budget should be cleared
    try:
        return Decimal(amount.strip()) == 0
    except InvalidOperation:
        return False


def get_account_group(account_type, balance_type):  #Places each account in one clear section
    account_type = (account_type or "").lower()
    balance_type = (balance_type or "").lower()

    if balance_type == "liability":
        if "credit" in account_type:
            return "Credit cards"
        return "Loans"

    if any(word in account_type for word in ("ira", "brokerage", "401", "investment", "roth")):
        return "Investments"

    return "Cash"


def add_quantity(left, right):  #Adds quantity text when it is a normal number
    try:
        total = Decimal(left or "0") + Decimal(right or "0")
        return f"{total.normalize():f}"
    except InvalidOperation:
        return left or right or ""


def save_position_from_form(form_values):  #Saves one manual investment position from React
    account_id = int(get_query_value(form_values, "account_id"))
    save_position(
        account_id=account_id,
        ticker=get_query_value(form_values, "ticker"),
        security_name=get_query_value(form_values, "security_name"),
        asset_class=get_query_value(form_values, "asset_class"),
        quantity=get_query_value(form_values, "quantity"),
        price=get_query_value(form_values, "price"),
        value=get_query_value(form_values, "value"),
        currency=get_query_value(form_values, "position_currency"),
        as_of=get_query_value(form_values, "as_of"),
        database_path=DATABASE_PATH,
    )


def get_review_filter(query_values):  #Reads the transaction review filter
    review = get_query_value(query_values, "review")
    return review if review in {"not_reviewed", "reviewed"} else ""


def get_transaction_date(query_values, name):  #Only keeps real date filters
    value = get_query_value(query_values, name)
    if not value:
        return ""

    try:
        date.fromisoformat(value)
    except ValueError:
        return ""

    return value


def get_transaction_filters(query_values):  #Keeps all transaction filters in one dict
    status = get_query_value(query_values, "status")
    if status not in {"pending", "posted"}:
        status = ""

    return {
        "account": get_query_value(query_values, "account"),
        "category": get_query_value(query_values, "category"),
        "status": status,
        "review": get_review_filter(query_values),
        "start": get_transaction_date(query_values, "start"),
        "end": get_transaction_date(query_values, "end"),
        "search": get_query_value(query_values, "search"),
    }


def provider_setup_is_complete(status_rows, account_rows, provider):  #Requires credentials and one selected account
    setup_is_ready = bool(status_rows) and all(
        is_configured for _, is_configured in status_rows
    )
    has_active_account = any(
        account[1] == provider and bool(account[7])
        for account in account_rows
    )
    return setup_is_ready and has_active_account


def refresh_from_form(form_values):  #Refreshes configured providers and safely skips optional connections
    confirmation = get_query_value(form_values, "confirmation")
    if confirmation != "REFRESH":
        raise ValueError("Refresh confirmation must be REFRESH")

    failures = []
    warnings = []
    skipped = []
    account_rows = list_accounts(DATABASE_PATH)

    if provider_setup_is_complete(
        get_akahu_setup_status(),
        account_rows,
        "akahu-bnz",
    ):
        try:
            bnz_result = refresh_bnz(None, confirmation, DATABASE_PATH)
            if len(bnz_result) > 7 and bnz_result[7]:
                warnings.append("BNZ used the last saved exchange rate")
        except (OSError, ValueError, sqlite3.Error) as error:
            failures.append(f"BNZ: {error}")
    else:
        skipped.append("BNZ")

    if provider_setup_is_complete(
        get_plaid_setup_status(),
        account_rows,
        get_plaid_config()["provider"],
    ):
        try:
            plaid_result = refresh_plaid(None, confirmation, DATABASE_PATH)
            if len(plaid_result) > 6 and plaid_result[6]:
                warnings.append("US used the last saved exchange rate")
            if len(plaid_result) > 7 and plaid_result[7]:
                warnings.append(
                    "US partially refreshed; " + " | ".join(plaid_result[7])
                )
        except (OSError, ValueError, sqlite3.Error) as error:
            failures.append(f"US: {error}")
    else:
        skipped.append("US")

    if failures:
        raise ValueError(" | ".join([*failures, *warnings]))

    if len(skipped) == 2:
        message = "No bank connections are configured; nothing was refreshed"
    else:
        details = list(warnings)
        if skipped:
            details.append(f"{skipped[0]} skipped because it is not configured")
        message = "Refresh finished"
        if details:
            message += ": " + "; ".join(details)

    return message


def rename_account_from_form(form_values):  #Renames one account without touching provider IDs
    account_id = int(get_query_value(form_values, "id"))
    rename_account(account_id, get_query_value(form_values, "name"), DATABASE_PATH)


def split_transaction_from_data(data):  #Saves a multi-line split for one transaction
    form_values = form_from_json(data)
    split_lines = data.get("splits")
    if not isinstance(split_lines, list):
        split_lines = [
            {
                "category": get_query_value(form_values, "first_category"),
                "amount": get_query_value(form_values, "first_amount"),
            },
            {
                "category": get_query_value(form_values, "second_category"),
                "amount": get_query_value(form_values, "second_amount"),
            },
        ]

    save_transaction_split(
        transaction_id=int(get_query_value(form_values, "id")),
        currency=get_display_currency(form_values),
        split_lines=split_lines,
        database_path=DATABASE_PATH,
    )


def save_rule_from_form(form_values):  #Saves one category rule
    match_text = get_query_value(form_values, "match_text")
    category = get_query_value(form_values, "category")
    priority_text = get_query_value(form_values, "priority")
    priority = int(priority_text) if priority_text else 100
    add_rule(match_text, category, priority, DATABASE_PATH)


def deactivate_rule_from_form(form_values):  #Turns off one rule
    rule_id = int(get_query_value(form_values, "rule_id"))
    deactivate_rule(rule_id, DATABASE_PATH)


def apply_rules_from_form(form_values):  #Runs saved rules only when confirmed
    if get_query_value(form_values, "confirmation") != "APPLY":
        raise ValueError("Rules were not applied")

    apply_rules(DATABASE_PATH)


def save_goal_from_form(form_values):  #Saves one goal from React
    save_goal(
        name=get_query_value(form_values, "name"),
        target_amount=get_query_value(form_values, "target_amount"),
        saved_amount=get_query_value(form_values, "saved_amount"),
        currency=get_query_value(form_values, "goal_currency"),
        target_date=get_query_value(form_values, "target_date"),
        database_path=DATABASE_PATH,
    )


def month_start_months_back(months_back):  #Gets the first day of a prior month
    today = date.today()
    month_index = today.year * 12 + today.month - 1 - months_back
    year = month_index // 12
    month = (month_index % 12) + 1
    return date(year, month, 1)


def first_transaction_date():  #Finds how far back all time charts should go
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            value = connection.execute("SELECT MIN(posted_date) FROM transactions").fetchone()[0]
    except sqlite3.Error:
        return None

    return date.fromisoformat(value) if value else None


def month_count_for_timeline(timeline):  #Turns graph buttons into monthly chart counts
    if timeline == "all":
        first_date = first_transaction_date()
        if not first_date:
            return 1
        today = date.today()
        return max(1, (today.year - first_date.year) * 12 + today.month - first_date.month + 1)

    counts = {
        "1m": 1,
        "3m": 3,
        "6m": 6,
        "1y": 12,
        "3y": 36,
        "5y": 60,
    }
    return counts.get(timeline, 1)


def get_monthly_summary_series(currency, month_count=12):  #Gets data for the combo chart
    series = []

    for months_back in range(month_count - 1, -1, -1):
        month_start = month_start_months_back(months_back)
        month_end = add_months(month_start, 1).replace(day=1) - timedelta(days=1)
        summary = get_spending_summary(
            "month",
            currency,
            today=month_start,
            database_path=DATABASE_PATH,
        )
        series.append(
            {
                "label": month_start.strftime("%b %Y"),
                "date": month_start.isoformat(),
                "end_date": month_end.isoformat(),
                "income": summary["income_total_minor"],
                "spending": summary["spending_total_minor"],
                "net": summary["net_minor"],
            }
        )

    return series


def get_daily_summary_series(currency, start_date, end_date):  #Uses daily rows when the graph is only showing this month
    series = []
    current_date = date.fromisoformat(start_date)
    final_date = date.fromisoformat(end_date)

    while current_date <= final_date:
        day_text = current_date.isoformat()
        summary = get_spending_summary_between(
            day_text,
            day_text,
            currency,
            database_path=DATABASE_PATH,
        )
        series.append(
            {
                "label": f"{current_date.strftime('%b')} {current_date.day}",
                "date": day_text,
                "end_date": day_text,
                "income": summary["income_total_minor"],
                "spending": summary["spending_total_minor"],
                "net": summary["net_minor"],
            }
        )
        current_date += timedelta(days=1)

    return series


def get_weekly_summary_series(currency, start_date, end_date):  #Uses weekly rows for medium length charts
    series = []
    current_date = date.fromisoformat(start_date)
    final_date = date.fromisoformat(end_date)

    while current_date <= final_date:
        week_end = min(current_date + timedelta(days=6), final_date)
        summary = get_spending_summary_between(
            current_date.isoformat(),
            week_end.isoformat(),
            currency,
            database_path=DATABASE_PATH,
        )
        series.append(
            {
                "label": f"{current_date.strftime('%b')} {current_date.day}",
                "date": current_date.isoformat(),
                "end_date": week_end.isoformat(),
                "income": summary["income_total_minor"],
                "spending": summary["spending_total_minor"],
                "net": summary["net_minor"],
            }
        )
        current_date = week_end + timedelta(days=1)

    return series


def save_budget_from_form(form_values):  #Adds, updates, or clears one budget row
    category = get_query_value(form_values, "category")
    amount = get_query_value(form_values, "amount")
    income_percent = get_query_value(form_values, "income_percent")
    budget_currency = get_query_value(form_values, "budget_currency").upper()
    rollover_value = get_query_value(form_values, "rollover_enabled").lower()
    view = get_budget_view(form_values)
    week_start = get_budget_start(form_values)
    source = get_query_value(form_values, "budget_source")

    if rollover_value not in {"true", "false"}:
        raise ValueError("Rollover must be true or false")

    rollover_enabled = rollover_value == "true"
    category_rows = list_categories(DATABASE_PATH)
    is_income_budget = any(
        name == category and is_income and is_active
        for name, _, is_income, is_transfer, is_active in category_rows
        if not is_transfer
    )
    if is_income_budget:
        rollover_enabled = False

    amount = budget_amount_from_form(amount, income_percent, budget_currency, week_start, source, view)

    if amount == "":
        return

    existing = list_budgets(week_start=week_start, database_path=DATABASE_PATH)
    category_budgets = [row for row in existing if row[2] == category]
    matching_budget = any(row[3] == budget_currency for row in category_budgets)

    if budget_amount_is_zero(amount):
        if has_prior_budget(category, week_start, view, DATABASE_PATH):
            if matching_budget and len(category_budgets) == 1:
                update_budget(
                    category=category,
                    week_start=week_start,
                    amount=amount,
                    currency=budget_currency,
                    rollover_enabled=rollover_enabled,
                    database_path=DATABASE_PATH,
                )
            else:
                if category_budgets:
                    delete_budget(category, week_start, database_path=DATABASE_PATH)
                add_budget(
                    category=category,
                    week_start=week_start,
                    amount=amount,
                    currency=budget_currency,
                    rollover_enabled=rollover_enabled,
                    database_path=DATABASE_PATH,
                )
        else:
            delete_budget(category, week_start, database_path=DATABASE_PATH)
        return

    if matching_budget and len(category_budgets) == 1:
        update_budget(
            category=category,
            week_start=week_start,
            amount=amount,
            currency=budget_currency,
            rollover_enabled=rollover_enabled,
            database_path=DATABASE_PATH,
        )
    else:
        if category_budgets:
            delete_budget(category, week_start, database_path=DATABASE_PATH)
        add_budget(
            category=category,
            week_start=week_start,
            amount=amount,
            currency=budget_currency,
            rollover_enabled=rollover_enabled,
            database_path=DATABASE_PATH,
        )


def add_budget_category_from_form(form_values):  #Adds one category from the budget page
    category_type = get_query_value(form_values, "category_type").lower()

    if category_type not in {"expense", "income"}:
        raise ValueError("Budget categories must be income or expense")

    name = get_query_value(form_values, "name")
    group_name = get_query_value(form_values, "group_name")

    try:
        add_category(
            name=name,
            group_name=group_name,
            category_type=category_type,
            database_path=DATABASE_PATH,
        )
    except sqlite3.IntegrityError as error:
        if "categories.name" not in str(error):
            raise
        update_category(
            name=name,
            group_name=group_name,
            category_type=category_type,
            is_active=True,
            database_path=DATABASE_PATH,
        )


def update_settings_from_form(form_values):  #Saves settings without accepting unknown fields
    changes = {
        "display_currency": get_query_value(form_values, "display_currency"),
        "daily_alert_enabled": get_query_value(form_values, "daily_alert_enabled"),
        "daily_alert_send_if_empty": get_query_value(
            form_values,
            "daily_alert_send_if_empty",
        ),
        "daily_alert_time": get_query_value(form_values, "daily_alert_time"),
        "daily_alert_currency": get_query_value(form_values, "daily_alert_currency"),
        "daily_alert_hours": get_query_value(form_values, "daily_alert_hours"),
        "daily_alert_max_rows": get_query_value(form_values, "daily_alert_max_rows"),
    }
    update_settings(changes, DATABASE_PATH)


def build_test_email_body(settings):  #Keeps the test email free of account or transaction data
    return "\n".join(
        [
            "Finance Hub test email",
            "",
            "Yahoo email alerts are configured correctly.",
            f"Alert time: {settings['daily_alert_time']}",
            f"Alert currency: {settings['daily_alert_currency']}",
            f"Lookback hours: {settings['daily_alert_hours']}",
            "",
            "No bank details were included in this test.",
        ]
    )


def send_settings_test_email():  #Sends a safe test email from the Settings page
    settings = get_settings(DATABASE_PATH)
    try:
        send_text_email("Finance Hub test email", build_test_email_body(settings))
    except (OSError, smtplib.SMTPException) as error:
        raise ValueError(f"Test email could not be sent: {error}") from error


def form_from_json(data):  #Lets React reuse the small form helpers
    return {key: ["" if value is None else str(value)] for key, value in data.items()}


def json_default(value):  #Turns database helper values into plain JSON
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, Path):
        return str(value)

    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def json_bytes(data):  #Keeps API responses encoded the same way
    return json.dumps(data, default=json_default, separators=(",", ":")).encode("utf-8")


def api_category_rows():  #Returns active categories in display order
    rows = [
        {
            "name": name,
            "group_name": group_name,
            "is_income": bool(is_income),
            "is_transfer": bool(is_transfer),
            "is_active": bool(is_active),
            "rank": category_group_rank(group_name),
        }
        for name, group_name, is_income, is_transfer, is_active in list_categories(DATABASE_PATH)
        if is_active
    ]
    return sorted(rows, key=lambda row: (row["rank"], row["group_name"], row["name"]))


def api_account_rows(currency):  #Returns accounts without private provider IDs
    rates = get_display_rates(currency)
    rows = []

    for account in list_accounts(DATABASE_PATH):
        name, provider, institution, account_type, native_currency, balance, as_of, is_active, _, balance_type, account_id = account
        if provider == "monarch-history":
            continue

        display_balance = convert_minor_units(abs(balance), rates[native_currency])
        rows.append(
            {
                "id": account_id,
                "name": name,
                "provider": provider,
                "institution": institution or "",
                "account_type": account_type or "",
                "native_currency": native_currency,
                "native_balance_minor": balance,
                "display_balance_minor": display_balance,
                "balance_as_of": as_of or "",
                "is_active": bool(is_active),
                "balance_type": balance_type,
                "group": get_account_group(account_type, balance_type),
            }
        )

    return sorted(rows, key=lambda row: (row["group"], -abs(row["display_balance_minor"]), row["name"]))


def api_transaction_rows(query_values, currency, limit=None, count_total=True):  #Returns filtered transactions for React
    filters = get_transaction_filters(query_values)
    transactions = list_transactions(
        account=filters["account"] or None,
        category=filters["category"] or None,
        transaction_status=filters["status"] or None,
        review_status=filters["review"] or None,
        start=filters["start"] or None,
        end=filters["end"] or None,
        search=filters["search"] or None,
        limit=limit,
        database_path=DATABASE_PATH,
    )
    total_count = len(transactions)
    if count_total:
        total_count = count_transactions(
            account=filters["account"] or None,
            category=filters["category"] or None,
            transaction_status=filters["status"] or None,
            review_status=filters["review"] or None,
            start=filters["start"] or None,
            end=filters["end"] or None,
            search=filters["search"] or None,
            database_path=DATABASE_PATH,
        )
    rows = []
    split_map = list_transaction_splits([transaction[0] for transaction in transactions], DATABASE_PATH)

    for transaction in transactions:
        row_id, posted_date, account, description, category, original_amount, original_currency, amount_nzd, amount_usd, status, review_status, notes = transaction
        amount_minor = amount_nzd if currency == "NZD" else amount_usd
        split_rows = split_map.get(row_id, [])
        split_details = [
            {
                "category": split["category"],
                "amount_minor": split["amount_nzd_minor"] if currency == "NZD" else split["amount_usd_minor"],
                "split_order": split["split_order"],
            }
            for split in split_rows
        ]
        if split_details:
            visible_splits = split_details
            if filters["category"]:
                visible_splits = [
                    split
                    for split in split_details
                    if split["category"] == filters["category"]
                ]

            for split in visible_splits:
                rows.append(
                    {
                        "id": f"{row_id}-split-{split['split_order']}",
                        "source_id": row_id,
                        "posted_date": posted_date,
                        "account": account,
                        "description": description,
                        "category": split["category"],
                        "original_amount_minor": original_amount,
                        "original_currency": original_currency,
                        "amount_minor": split["amount_minor"],
                        "parent_amount_minor": amount_minor,
                        "transaction_status": status,
                        "review_status": review_status,
                        "notes": notes,
                        "is_split_line": True,
                        "split_order": split["split_order"],
                        "splits": split_details,
                    }
                )
            continue

        rows.append(
            {
                "id": row_id,
                "source_id": row_id,
                "posted_date": posted_date,
                "account": account,
                "description": description,
                "category": category or "Uncategorized",
                "original_amount_minor": original_amount,
                "original_currency": original_currency,
                "amount_minor": amount_minor,
                "transaction_status": status,
                "review_status": review_status,
                "notes": notes,
                "is_split_line": False,
                "splits": [],
            }
        )

    return rows, total_count


def api_summary(period, currency):  #Wraps spending summary with savings rate
    summary = get_spending_summary(period, currency, database_path=DATABASE_PATH)
    income = summary["income_total_minor"]
    savings_rate = 0
    if income:
        savings_rate = round((summary["net_minor"] / income) * 100, 1)
    summary["savings_rate"] = savings_rate
    return summary


def api_timeline_summary(timeline, currency):  #Builds totals for graph timeline buttons
    start_date = get_timeline_start_date(timeline)
    end_date = get_timeline_end_date()
    summary = get_spending_summary_between(
        start_date or "0001-01-01",
        end_date,
        currency,
        database_path=DATABASE_PATH,
    )
    income = summary["income_total_minor"]
    savings_rate = 0
    if income:
        savings_rate = round((summary["net_minor"] / income) * 100, 1)
    summary["savings_rate"] = savings_rate
    summary["timeline"] = timeline
    return summary


def month_end_for(month_start):  #Gets the last day in the month for period comparisons
    return add_months(month_start, 1).replace(day=1) - timedelta(days=1)


def spending_total_between(start_date, end_date, currency):  #Gets one period-to-date spending total
    return get_spending_summary_between(
        start_date.isoformat(),
        end_date.isoformat(),
        currency,
        database_path=DATABASE_PATH,
    )["spending_total_minor"]


def cumulative_spending_series(
    start_date,
    day_count,
    currency,
    end_date=None,
    stop_after_end=False,
    daily_totals=None,
):  #Builds daily running spend for comparisons
    running_total = 0
    series = []

    for day_offset in range(day_count):
        selected_date = start_date + timedelta(days=day_offset)
        if end_date is not None and selected_date > end_date:
            series.append(None if stop_after_end else running_total)
            continue

        if daily_totals is None:
            daily_total = spending_total_between(selected_date, selected_date, currency)
        else:
            daily_total = daily_totals.get(selected_date.isoformat(), 0)
        running_total += daily_total
        series.append(running_total)

    return series


def average_cumulative_series(
    start_dates,
    day_count,
    currency,
    end_dates=None,
    daily_totals=None,
):  #Averages running spend by day position
    if not start_dates:
        return [0 for _ in range(day_count)]

    comparison_series = []
    end_dates = end_dates or [None for _ in start_dates]
    for start_date, end_date in zip(start_dates, end_dates):
        comparison_series.append(
            cumulative_spending_series(
                start_date,
                day_count,
                currency,
                end_date,
                daily_totals=daily_totals,
            )
        )

    return [
        int(round(sum(series[day_offset] for series in comparison_series) / len(comparison_series)))
        for day_offset in range(day_count)
    ]


def latest_spending_value(series):  #Gets the latest real point from a period-to-date series
    for amount in reversed(series):
        if amount is not None:
            return amount
    return 0


def spending_comparison_option(
    key,
    label,
    period_label,
    current_label,
    reference_label,
    start_date,
    end_date,
    current_series,
    reference_series,
    date_labels,
):  #Packages one dashboard comparison chart option
    points = []
    for index, date_label in enumerate(date_labels):
        points.append(
            {
                "label": date_label,
                "current_minor": current_series[index] if index < len(current_series) else None,
                "reference_minor": reference_series[index] if index < len(reference_series) else None,
            }
        )

    return {
        "key": key,
        "label": label,
        "period_label": period_label,
        "current_label": current_label,
        "reference_label": reference_label,
        "amount_minor": latest_spending_value(current_series),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "points": points,
    }


def api_spending_comparisons(currency):  #Compares current spending against equivalent prior periods
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_day_count = 7
    week_end = week_start + timedelta(days=6)
    week_labels = [
        (week_start + timedelta(days=day_offset)).strftime("%a")
        for day_offset in range(week_day_count)
    ]

    month_start = today.replace(day=1)
    month_end = month_end_for(month_start)
    month_day_count = month_end.day
    month_labels = [
        f"Day {(month_start + timedelta(days=day_offset)).day}"
        for day_offset in range(month_day_count)
    ]

    last_week_start = week_start - timedelta(days=7)
    last_month_start = add_months(month_start, -1).replace(day=1)
    average_week_starts = [
        week_start - timedelta(days=7 * weeks_back)
        for weeks_back in range(1, 53)
    ]
    average_month_starts = [
        add_months(month_start, -months_back).replace(day=1)
        for months_back in range(1, 13)
    ]
    daily_totals = get_daily_spending_totals_between(
        min(average_week_starts + average_month_starts).isoformat(),
        today.isoformat(),
        currency,
        database_path=DATABASE_PATH,
    )

    week_series = cumulative_spending_series(
        week_start,
        week_day_count,
        currency,
        today,
        stop_after_end=True,
        daily_totals=daily_totals,
    )
    month_series = cumulative_spending_series(
        month_start,
        month_day_count,
        currency,
        today,
        stop_after_end=True,
        daily_totals=daily_totals,
    )
    last_week_series = cumulative_spending_series(
        last_week_start,
        week_day_count,
        currency,
        daily_totals=daily_totals,
    )
    last_month_series = cumulative_spending_series(
        last_month_start,
        month_day_count,
        currency,
        month_end_for(last_month_start),
        daily_totals=daily_totals,
    )

    average_week_series = average_cumulative_series(
        average_week_starts,
        week_day_count,
        currency,
        daily_totals=daily_totals,
    )
    average_month_series = average_cumulative_series(
        average_month_starts,
        month_day_count,
        currency,
        [month_end_for(month_start) for month_start in average_month_starts],
        daily_totals=daily_totals,
    )

    return [
        spending_comparison_option(
            "week_last",
            "This week vs. last week",
            "this week",
            "This week",
            "Last week",
            week_start,
            week_end,
            week_series,
            last_week_series,
            week_labels,
        ),
        spending_comparison_option(
            "week_average",
            "This week vs. avg week",
            "this week",
            "This week",
            "Average week",
            week_start,
            week_end,
            week_series,
            average_week_series,
            week_labels,
        ),
        spending_comparison_option(
            "month_last",
            "This month vs. last month",
            "this month",
            "This month",
            "Last month",
            month_start,
            month_end,
            month_series,
            last_month_series,
            month_labels,
        ),
        spending_comparison_option(
            "month_average",
            "This month vs. avg month",
            "this month",
            "This month",
            "Average month",
            month_start,
            month_end,
            month_series,
            average_month_series,
            month_labels,
        ),
    ]


def api_budget_summary(progress_rows, week_summary, currency):  #Builds the right side budget numbers
    income_rows = [row for row in progress_rows if row[6]]
    expense_rows = [row for row in progress_rows if not row[6]]
    income_planned = sum(row[4] + row[7] for row in income_rows)
    income_actual = week_summary["income_total_minor"]
    expense_planned = sum(row[4] + row[7] for row in expense_rows)
    expense_actual = week_summary["spending_total_minor"]

    return {
        "left_to_budget_minor": income_planned - expense_planned,
        "income": {
            "planned_minor": income_planned,
            "actual_minor": income_actual,
            "remaining_minor": income_planned - income_actual,
        },
        "expenses": {
            "planned_minor": expense_planned,
            "actual_minor": expense_actual,
            "remaining_minor": expense_planned - expense_actual,
        },
        "currency": currency,
    }


def api_budget_rows(currency, week_start, sort_by, view="week", selected_date=None):  #Builds editable budget rows for every category
    categories = api_category_rows()
    progress = get_budget_progress(week_start=week_start, database_path=DATABASE_PATH, display_currency=currency, budget_view=view)
    summary_period = "month" if view == "month" else "week"
    summary_date = selected_date or date.fromisoformat(week_start)
    week_summary = get_spending_summary(summary_period, currency, today=summary_date, database_path=DATABASE_PATH)
    planned_income = get_planned_income_total(week_start, currency, DATABASE_PATH, budget_view=view)
    income_base = planned_income

    progress_by_category = {row[2]: row for row in progress}
    actuals = {}
    for row in week_summary["income_by_category"]:
        actuals[row["category_name"]] = row["amount_minor"]
    for row in week_summary["spending_by_category"]:
        actuals[row["category_name"]] = row["amount_minor"]

    groups = {}
    for category in categories:
        if category["is_transfer"]:
            continue

        name = category["name"]
        progress_row = progress_by_category.get(name)
        if progress_row:
            _, group_name, _, row_currency, budget, rollover_enabled, is_income, saved_rollover, actual, remaining = progress_row
            planned = budget + saved_rollover
            if view == "month":
                actual = actuals.get(name, 0)
                remaining = planned - actual
            is_budgeted = planned > 0
        else:
            group_name = category["group_name"]
            row_currency = currency
            budget = 0
            rollover_enabled = False
            is_income = category["is_income"]
            actual = actuals.get(name, 0)
            planned = 0
            remaining = -actual
            is_budgeted = False

        row = {
            "category": name,
            "group_name": group_name,
            "currency": row_currency,
            "amount_minor": budget,
            "amount_input": money_input_value(budget) if budget else "",
            "rollover_enabled": bool(rollover_enabled),
            "is_income": bool(is_income),
            "actual_minor": actual,
            "remaining_minor": remaining,
            "planned_minor": planned,
            "is_budgeted": is_budgeted,
            "income_base_minor": income_base,
            "percent_of_income_input": percent_of_income_value(planned, income_base),
            "percent_of_income": percent_of_income_text(planned, income_base),
        }
        group = groups.setdefault(
            group_name,
            {
                "name": group_name,
                "rank": category_group_rank(group_name),
                "planned_minor": 0,
                "actual_minor": 0,
                "remaining_minor": 0,
                "rows": [],
            },
        )
        group["planned_minor"] += planned
        group["actual_minor"] += actual
        group["remaining_minor"] += remaining
        group["rows"].append(row)

    for group in groups.values():
        group["planned_percent_of_income"] = percent_of_income_value(
            group["planned_minor"], income_base
        )
        if sort_by == "actual":
            group["rows"].sort(key=lambda row: (-abs(row["actual_minor"]), row["category"]))
        elif sort_by == "left_over":
            group["rows"].sort(key=lambda row: (-row["remaining_minor"], row["category"]))
        elif sort_by == "over_budget":
            group["rows"].sort(key=lambda row: (row["remaining_minor"], row["category"]))
        else:
            group["rows"].sort(key=lambda row: (-abs(row["planned_minor"]), row["category"]))

    return sorted(groups.values(), key=lambda group: (group["rank"], group["name"])), progress, week_summary


def api_monthly_series(currency, timeline="1y"):  #Returns chart data with short labels
    if timeline == "1m":
        return get_daily_summary_series(currency, get_timeline_start_date(timeline), get_timeline_end_date())

    if timeline in {"3m", "6m"}:
        return get_weekly_summary_series(currency, get_timeline_start_date(timeline), get_timeline_end_date())

    return get_monthly_summary_series(currency, month_count_for_timeline(timeline))


def get_timeline_start_date(timeline):  #Turns graph buttons into a starting date
    if timeline == "all":
        return None

    current_month = date.today().replace(day=1)
    months_by_timeline = {
        "1m": 1,
        "3m": 3,
        "6m": 6,
        "1y": 12,
        "3y": 36,
        "5y": 60,
    }

    return add_months(current_month, -(months_by_timeline.get(timeline, 1) - 1)).isoformat()


def get_timeline_end_date():  #Keeps timeline summaries ending today
    return date.today().isoformat()


def api_net_worth_series(currency, timeline="1m"):  #Uses saved balance history for the net worth graph
    rate_cache = {}
    try:
        with closing(sqlite3.connect(DATABASE_PATH)) as connection:
            start_date = get_net_worth_start_date(connection, timeline)
            end_date = get_timeline_end_date()
            start = date.fromisoformat(start_date) if start_date else None
            latest_balances = {}
            series = []

            rows = load_net_worth_history_rows(connection, start_date, end_date)

            current_date = None
            current_rows = []
            anchor_added = start is None

            def flush_balance_date(balance_date, balance_rows):
                nonlocal anchor_added, latest_balances
                parsed_date = date.fromisoformat(balance_date)
                if start and not anchor_added:
                    if parsed_date > start and latest_balances:
                        total_minor = net_worth_total_for_date(connection, rate_cache, latest_balances, currency, start_date)
                        series.append(net_worth_series_row(start_date, total_minor))
                    if parsed_date >= start:
                        anchor_added = True

                if any(row[2] is not None for row in balance_rows):
                    latest_balances = {
                        key: value
                        for key, value in latest_balances.items()
                        if key[0] != "legacy"
                    }

                for source, account_name, account_id, _, native_currency, balance_minor, balance_type in balance_rows:
                    latest_balances[net_worth_balance_key(source, account_name, account_id, native_currency)] = (
                        native_currency,
                        balance_minor,
                        balance_type,
                    )

                if not start or parsed_date >= start:
                    total_minor = net_worth_total_for_date(connection, rate_cache, latest_balances, currency, balance_date)
                    series.append(net_worth_series_row(balance_date, total_minor))

            for row in rows:
                balance_date = row[3]
                if current_date is not None and balance_date != current_date:
                    flush_balance_date(current_date, current_rows)
                    current_rows = []
                current_date = balance_date
                current_rows.append(row)

            if current_date is not None:
                flush_balance_date(current_date, current_rows)

            if start and not anchor_added and latest_balances:
                total_minor = net_worth_total_for_date(connection, rate_cache, latest_balances, currency, start_date)
                series.append(net_worth_series_row(start_date, total_minor))

            if current_account_balances_exist(connection) and (not start or date.fromisoformat(end_date) >= start):
                current_total = get_net_worth(currency, DATABASE_PATH)["total_minor"]
                current_row = net_worth_series_row(end_date, current_total)
                if series and series[-1]["date"] == end_date:
                    series[-1] = current_row
                else:
                    series.append(current_row)

            transaction_deltas = net_worth_transaction_deltas(connection, currency, start_date, series[-1]["date"] if series else end_date)

    except (sqlite3.Error, ValueError):
        return []

    return daily_net_worth_series(series, start_date, transaction_deltas)


def load_net_worth_history_rows(connection, start_date, end_date):
    """Loads all-time history or compact opening balances plus the requested range."""
    if not start_date:
        return connection.execute(
            """
            SELECT
                h.source,
                h.account_name,
                h.account_id,
                h.balance_date,
                h.currency,
                h.balance_minor,
                COALESCE(a.balance_type, 'asset')
            FROM account_balance_history h
            LEFT JOIN accounts a ON a.id = h.account_id
            WHERE h.balance_date <= ?
              AND (h.account_id IS NULL OR a.is_active = 1)
            ORDER BY h.balance_date, h.id
            """,
            (end_date,),
        ).fetchall()

    return connection.execute(
        """
        WITH linked_dates AS (
            SELECT h.account_id, MAX(h.balance_date) AS balance_date
            FROM account_balance_history h
            JOIN accounts a ON a.id = h.account_id
            WHERE h.account_id IS NOT NULL
              AND a.is_active = 1
              AND h.balance_date < :start_date
              AND h.balance_date <= :end_date
            GROUP BY h.account_id
        ),
        last_linked AS (
            SELECT MAX(balance_date) AS balance_date
            FROM linked_dates
        ),
        legacy_dates AS (
            SELECT
                h.source,
                h.account_name,
                h.currency,
                MAX(h.balance_date) AS balance_date
            FROM account_balance_history h
            CROSS JOIN last_linked l
            WHERE h.account_id IS NULL
              AND h.balance_date < :start_date
              AND h.balance_date <= :end_date
              AND (l.balance_date IS NULL OR h.balance_date >= l.balance_date)
            GROUP BY h.source, h.account_name, h.currency
        ),
        linked_ids AS (
            SELECT h.account_id, MAX(h.id) AS id
            FROM account_balance_history h
            JOIN linked_dates d
              ON d.account_id = h.account_id
             AND d.balance_date = h.balance_date
            WHERE h.account_id IS NOT NULL
            GROUP BY h.account_id
        ),
        selected AS (
            SELECT
                h.id,
                h.source,
                h.account_name,
                h.account_id,
                h.balance_date,
                h.currency,
                h.balance_minor,
                'asset' AS balance_type
            FROM account_balance_history h
            JOIN legacy_dates d
              ON d.source = h.source
             AND d.account_name = h.account_name
             AND d.currency = h.currency
             AND d.balance_date = h.balance_date
            WHERE h.account_id IS NULL

            UNION ALL

            SELECT
                h.id,
                h.source,
                h.account_name,
                h.account_id,
                h.balance_date,
                h.currency,
                h.balance_minor,
                a.balance_type
            FROM linked_ids d
            JOIN account_balance_history h ON h.id = d.id
            JOIN accounts a ON a.id = h.account_id
            WHERE a.is_active = 1

            UNION ALL

            SELECT
                h.id,
                h.source,
                h.account_name,
                h.account_id,
                h.balance_date,
                h.currency,
                h.balance_minor,
                COALESCE(a.balance_type, 'asset') AS balance_type
            FROM account_balance_history h
            LEFT JOIN accounts a ON a.id = h.account_id
            WHERE h.balance_date >= :start_date
              AND h.balance_date <= :end_date
              AND (h.account_id IS NULL OR a.is_active = 1)
        )
        SELECT
            source,
            account_name,
            account_id,
            balance_date,
            currency,
            balance_minor,
            balance_type
        FROM selected
        ORDER BY balance_date, id
        """,
        {"start_date": start_date, "end_date": end_date},
    ).fetchall()


def current_account_balances_exist(connection):  #Checks whether the accounts table can supply a current chart point
    return connection.execute(
        """
        SELECT 1
        FROM accounts
        WHERE is_active = 1
          AND balance_as_of IS NOT NULL
        LIMIT 1
        """
    ).fetchone() is not None


def net_worth_transaction_deltas(connection, currency, start_date, end_date):  #Uses posted transactions to reconstruct days between snapshots
    if not start_date or not end_date:
        return {}

    amount_column = "amount_nzd_minor_fixed" if currency == "NZD" else "amount_usd_minor_fixed"
    rows = connection.execute(
        f"""
        SELECT t.posted_date, SUM(t.{amount_column})
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE t.transaction_status = 'posted'
          AND a.is_active = 1
          AND t.posted_date >= ?
          AND t.posted_date <= ?
        GROUP BY t.posted_date
        """,
        (start_date, end_date),
    ).fetchall()
    return {posted_date: amount_minor or 0 for posted_date, amount_minor in rows}


def daily_net_worth_series(series, start_date, transaction_deltas=None):  #Shows net worth as one point per day
    if not series:
        return []

    transaction_deltas = transaction_deltas or {}
    start = date.fromisoformat(start_date) if start_date else date.fromisoformat(series[0]["date"])
    end = date.fromisoformat(series[-1]["date"])
    rows_by_date = {row["date"]: row for row in series}
    filled = []
    current_date = start
    last_value = None

    while current_date <= end:
        date_text = current_date.isoformat()
        if date_text in rows_by_date:
            last_value = rows_by_date[date_text]["value_minor"]
        elif last_value is not None:
            last_value += transaction_deltas.get(date_text, 0)
        if last_value is not None:
            filled.append(net_worth_series_row(date_text, last_value))
        current_date += timedelta(days=1)

    return filled


def net_worth_balance_key(source, account_name, account_id, currency):  #Groups history by account when possible, with legacy imports kept separate
    if account_id is not None:
        return ("account", account_id)
    return ("legacy", source, account_name, currency)


def net_worth_total_for_date(connection, rate_cache, latest_balances, currency, balance_date):  #Carries account balances forward between refreshes
    total_minor = 0
    for native_currency, balance_minor, balance_type in latest_balances.values():
        rate = cached_rate_for_date(connection, rate_cache, native_currency, currency, balance_date)
        converted = convert_minor_units(balance_minor, rate)
        if balance_type == "liability":
            total_minor -= converted
        else:
            total_minor += converted
    return total_minor


def net_worth_series_row(balance_date, total_minor):  #Keeps chart row formatting in one place
    parsed_date = date.fromisoformat(balance_date)
    return {
        "label": f"{parsed_date.strftime('%b')} {parsed_date.day}",
        "date": balance_date,
        "value_minor": total_minor,
    }


def get_net_worth_start_date(connection, timeline):  #Uses the newest saved balance as the graph anchor
    if timeline == "all":
        return None

    latest_balance_date = connection.execute(
        """
        SELECT MAX(balance_date)
        FROM account_balance_history
        """
    ).fetchone()[0]

    if not latest_balance_date:
        return get_timeline_start_date(timeline)

    latest_month = date.fromisoformat(latest_balance_date).replace(day=1)
    months_by_timeline = {
        "1m": 1,
        "3m": 3,
        "6m": 6,
        "1y": 12,
        "3y": 36,
        "5y": 60,
    }

    return add_months(latest_month, -(months_by_timeline.get(timeline, 1) - 1)).isoformat()


def cached_rate_for_date(connection, rate_cache, base_currency, quote_currency, target_date):  #Avoids hundreds of tiny FX lookups for charts
    if base_currency == quote_currency:
        return Decimal("1")

    cache_key = (base_currency, quote_currency)
    if cache_key not in rate_cache:
        rows = connection.execute(
            """
            SELECT rate_date, rate
            FROM fx_rates
            WHERE base_currency = ?
              AND quote_currency = ?
            ORDER BY rate_date
            """,
            cache_key,
        ).fetchall()
        if not rows:
            raise ValueError(f"No exchange rate found for {base_currency} to {quote_currency}")
        rate_cache[cache_key] = ([row[0] for row in rows], [Decimal(row[1]) for row in rows])

    rate_dates, rates = rate_cache[cache_key]
    index = bisect_right(rate_dates, target_date) - 1
    if index < 0:
        index = 0
    return rates[index]


def api_holding_rows(currency):  #Groups investment positions by ticker only
    rates = get_display_rates(currency)
    grouped = {}
    provider_count = 0
    manual_count = 0

    for position in list_positions(DATABASE_PATH):
        _, _, _, ticker, security_name, asset_class, quantity, _, value, native_currency, _, source = position
        converted_value = convert_minor_units(value, rates[native_currency])
        if converted_value <= 0:
            continue

        if source == "manual":
            manual_count += 1
        else:
            provider_count += 1

        row = grouped.setdefault(
            ticker,
            {
                "ticker": ticker,
                "security_name": security_name,
                "asset_class": asset_class,
                "quantity": "0",
                "amount_minor": 0,
                "category_name": ticker,
            },
        )
        row["quantity"] = add_quantity(row["quantity"], quantity)
        row["amount_minor"] += converted_value

    holdings = sorted(grouped.values(), key=lambda row: (-row["amount_minor"], row["ticker"]))
    return holdings, provider_count, manual_count


def get_api_data(path, query_values):  #Routes read-only API calls
    currency = get_display_currency(query_values)

    if path == "/api/status":
        return {"ok": True, "database_found": DATABASE_PATH.exists()}

    if not DATABASE_PATH.exists():
        return {"ok": False, "error": f"Database not found at {DATABASE_PATH}"}

    if path == "/api/dashboard":
        recent, _ = api_transaction_rows({"review": [""]}, currency, limit=6, count_total=False)
        needs_review_count = count_transactions(review_status="not_reviewed", database_path=DATABASE_PATH)
        month_summary = api_summary("month", currency)
        month_start = date.today().replace(day=1).isoformat()
        progress = get_budget_progress(
            week_start=month_start,
            database_path=DATABASE_PATH,
            display_currency=currency,
            budget_view="month",
        )
        return {
            "ok": True,
            "currency": currency,
            "rate_note": get_rate_note(currency),
            "month": month_summary,
            "net_worth": get_net_worth(currency, DATABASE_PATH),
            "recent_transactions": recent,
            "needs_review_count": needs_review_count,
            "budget_summary": api_budget_summary(progress, month_summary, currency),
            "spending_comparisons": api_spending_comparisons(currency),
            "recurring": get_recurring_forecast(currency=currency, days=30, database_path=DATABASE_PATH),
        }

    if path == "/api/net-worth-series":
        timeline = get_timeline(query_values)
        return {
            "ok": True,
            "currency": currency,
            "timeline": timeline,
            "net_worth_series": api_net_worth_series(currency, timeline),
        }

    if path == "/api/accounts":
        accounts = api_account_rows(currency)
        groups = {group: [] for group in ["Cash", "Investments", "Credit cards", "Loans"]}
        for account in accounts:
            groups.setdefault(account["group"], []).append(account)
        return {
            "ok": True,
            "currency": currency,
            "rate_note": get_rate_note(currency),
            "net_worth": get_net_worth(currency, DATABASE_PATH),
            "groups": groups,
        }

    if path == "/api/transactions":
        limit = get_transaction_limit(query_values)
        transactions, total_count = api_transaction_rows(query_values, currency, limit=limit)
        return {
            "ok": True,
            "currency": currency,
            "limit": limit,
            "transactions": transactions,
            "total_count": total_count,
            "accounts": api_account_rows(currency),
            "categories": api_category_rows(),
        }

    if path == "/api/sync-status":
        return {"ok": True, **get_sync_status(DATABASE_PATH)}

    if path == "/api/daily-alert-preview":
        settings = get_settings(DATABASE_PATH)
        preview_currency = get_query_value(query_values, "currency").upper()
        if not preview_currency:
            preview_currency = settings["daily_alert_currency"]
        hours = get_query_value(query_values, "hours") or settings["daily_alert_hours"]
        max_rows = get_query_value(query_values, "max_rows") or settings["daily_alert_max_rows"]
        preview = build_alert_preview(
            preview_currency,
            hours,
            int(max_rows),
            database_path=DATABASE_PATH,
        )
        preview["alert_enabled"] = settings["daily_alert_enabled"] == "true"
        preview["send_if_empty"] = settings["daily_alert_send_if_empty"] == "true"
        preview["scheduled_time"] = settings["daily_alert_time"]
        preview["delivery"] = "email ready from PowerShell"
        return {"ok": True, **preview}

    if path == "/api/budgets":
        view = get_budget_view(query_values)
        sort_by = get_budget_sort(query_values)

        if view == "month":
            raw_start = get_query_value(query_values, "week_start")
            try:
                selected = date.fromisoformat(raw_start) if raw_start else date.today()
            except ValueError as error:
                raise ValueError("Month start must use YYYY-MM-DD") from error
            selected = selected.replace(day=1)
            week_start = selected.isoformat()
            previous_start = add_months(selected, -1).replace(day=1).isoformat()
            next_start = add_months(selected, 1).replace(day=1).isoformat()
            current_start = date.today().replace(day=1).isoformat()
            title_date = selected
        else:
            week_start = get_budget_week_start(query_values)
            title_date = date.fromisoformat(week_start)
            current_start = get_current_week_start()
            previous_start = (date.fromisoformat(week_start) - timedelta(days=7)).isoformat()
            next_start = (date.fromisoformat(week_start) + timedelta(days=7)).isoformat()

        groups, progress, week_summary = api_budget_rows(currency, week_start, sort_by, view, title_date)

        return {
            "ok": True,
            "currency": currency,
            "view": view,
            "title": build_budget_title(view, week_start, title_date),
            "week_start": week_start,
            "current_start": current_start,
            "previous_start": previous_start,
            "next_start": next_start,
            "sort": sort_by,
            "category_groups": CATEGORY_GROUP_ORDER,
            "groups": groups,
            "summary": api_budget_summary(progress, week_summary, currency),
        }

    if path in {"/api/cash-flow", "/api/reports"}:
        timeline = get_timeline(query_values)
        summary = api_timeline_summary(timeline, currency)
        return {
            "ok": True,
            "currency": currency,
            "timeline": timeline,
            "summary": summary,
            "savings_rate": summary["savings_rate"],
            "monthly_series": api_monthly_series(currency, timeline),
        }

    if path == "/api/recurring":
        return {"ok": True, **get_recurring_forecast(currency=currency, days=30, database_path=DATABASE_PATH)}

    if path == "/api/goals":
        rates = get_display_rates(currency)
        goals = []
        for goal_id, name, native_currency, target, saved, target_date, is_active in list_goals(DATABASE_PATH):
            target_display = convert_minor_units(target, rates[native_currency])
            saved_display = convert_minor_units(saved, rates[native_currency])
            percent_complete = round(saved / target * 100, 1) if target else 0
            goals.append(
                {
                    "id": goal_id,
                    "name": name,
                    "native_currency": native_currency,
                    "target_minor": target,
                    "saved_minor": saved,
                    "target_display_minor": target_display,
                    "saved_display_minor": saved_display,
                    "target_date": target_date or "",
                    "is_active": bool(is_active),
                    "percent_complete": percent_complete,
                }
            )
        return {"ok": True, "currency": currency, "goals": goals}

    if path == "/api/investments":
        holdings, provider_count, manual_count = api_holding_rows(currency)
        total_minor = sum(row["amount_minor"] for row in holdings)
        investment_accounts = [
            {"id": account["id"], "name": account["name"], "institution": account["institution"]}
            for account in api_account_rows(currency)
            if account["group"] == "Investments" and account["is_active"]
        ]
        return {
            "ok": True,
            "currency": currency,
            "holdings": holdings,
            "investment_accounts": investment_accounts,
            "total_minor": total_minor,
            "provider_count": provider_count,
            "manual_count": manual_count,
            "largest": holdings[0]["ticker"] if holdings else "-",
        }

    if path == "/api/rules":
        rules = [
            {
                "id": rule_id,
                "match_text": match_text,
                "category": category,
                "priority": priority,
                "is_active": bool(is_active),
            }
            for rule_id, match_text, category, priority, is_active in list_rules(DATABASE_PATH)
            if is_active
        ]
        return {
            "ok": True,
            "rules": rules,
            "categories": api_category_rows(),
            "summary": cleanup_summary(DATABASE_PATH),
        }

    if path == "/api/settings":
        return {"ok": True, "settings": get_settings(DATABASE_PATH)}

    return {"ok": False, "error": "API route not found"}


def post_api_data(path, data):  #Routes React write requests through existing helpers
    form_values = form_from_json(data)

    if path == "/api/refresh-all":
        return {"ok": True, "message": refresh_from_form(form_values)}

    if path == "/api/accounts/rename":
        rename_account_from_form(form_values)
        return {"ok": True}

    if path == "/api/transactions/review":
        set_review_status(int(data["id"]), data["review_status"], DATABASE_PATH)
        return {"ok": True}

    if path == "/api/transactions/category":
        edit_transaction(int(data["id"]), category=data["category"], database_path=DATABASE_PATH)
        return {"ok": True}

    if path == "/api/transactions/split":
        split_transaction_from_data(data)
        return {"ok": True}

    if path == "/api/transactions/split-clear":
        clear_transaction_split(int(data["id"]), DATABASE_PATH)
        return {"ok": True}

    if path == "/api/budgets/save":
        save_budget_from_form(form_values)
        return {"ok": True}

    if path == "/api/budgets/category":
        add_budget_category_from_form(form_values)
        return {"ok": True}

    if path == "/api/goals/save":
        if "currency" in data and "goal_currency" not in data:
            form_values["goal_currency"] = [data["currency"]]
        save_goal_from_form(form_values)
        return {"ok": True}

    if path == "/api/investments/position":
        save_position_from_form(form_values)
        return {"ok": True}

    if path == "/api/rules/add":
        save_rule_from_form(form_values)
        return {"ok": True}

    if path == "/api/rules/apply":
        form_values["confirmation"] = ["APPLY"]
        apply_rules_from_form(form_values)
        return {"ok": True}

    if path == "/api/rules/deactivate":
        deactivate_rule_from_form(form_values)
        return {"ok": True}

    if path == "/api/settings":
        update_settings_from_form(form_values)
        return {"ok": True}

    if path == "/api/settings/test-email":
        send_settings_test_email()
        return {"ok": True, "message": "Test email sent"}

    return {"ok": False, "error": "API route not found"}


def get_static_file(path):  #Finds a built React file without allowing path tricks
    frontend_root = FRONTEND_DIST.resolve()
    if path == "/":
        return FRONTEND_INDEX

    relative_path = path.lstrip("/")
    file_path = (FRONTEND_DIST / relative_path).resolve()
    try:
        file_path.relative_to(frontend_root)
    except ValueError:
        return None

    if file_path.is_file():
        return file_path

    return FRONTEND_INDEX if FRONTEND_INDEX.exists() else None


class FinanceHubHandler(BaseHTTPRequestHandler):
    def send_security_headers(self):  #Adds simple browser protections to every response
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'; "
            "form-action 'self'",
        )
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")

    def write_response(self, status, body, content_type="text/plain; charset=utf-8"):  #Sends one complete browser response
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_security_headers()
        self.end_headers()

        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def write_json(self, status, data):  #Sends JSON back to the React app
        self.write_response(status, json_bytes(data), "application/json; charset=utf-8")

    def write_file(self, file_path):  #Serves the built React files
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.write_response(200, file_path.read_bytes(), content_type)

    def do_GET(self):  #Handles browser requests for the local React app
        self.close_connection = True
        request_url = urlparse(self.path)
        path = request_url.path
        query_values = parse_qs(request_url.query)

        if not read_request_is_allowed(
            self.headers.get("Host"),
            self.headers.get(TAILSCALE_USER_HEADER),
            REMOTE_ACCESS_SETTINGS,
        ):
            if path.startswith("/api/"):
                self.write_json(403, {"ok": False, "error": "Request blocked"})
            else:
                self.write_response(403, b"Request blocked")
            return

        if path.startswith("/api/"):
            try:
                self.write_json(200, get_api_data(path, query_values))
            except (FileNotFoundError, OSError, ValueError, sqlite3.Error) as error:
                self.write_json(400, {"ok": False, "error": str(error)})
            return

        if not FRONTEND_INDEX.exists():
            message = b"React front end has not been built. Run: cd frontend && npm run build"
            self.write_response(503, message)
            return

        file_path = get_static_file(path)
        if file_path is not None and file_path.exists():
            self.write_file(file_path)
            return

        self.write_response(404, b"Page not found")

    def do_POST(self):  #Handles local React API writes
        self.close_connection = True
        request_url = urlparse(self.path)

        if not write_request_is_allowed(
            self.headers.get("Host"),
            self.headers.get("Origin"),
            self.headers.get("Referer"),
            self.headers.get(TAILSCALE_USER_HEADER),
            REMOTE_ACCESS_SETTINGS,
        ):
            if request_url.path.startswith("/api/"):
                self.write_json(403, {"ok": False, "error": "Write request blocked"})
            else:
                self.write_response(403, b"Write request blocked")
            return

        if not request_url.path.startswith("/api/"):
            self.write_response(404, b"Page not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(raw_body or "{}")
            self.write_json(200, post_api_data(request_url.path, data))
        except (json.JSONDecodeError, KeyError, OSError, ValueError, sqlite3.Error) as error:
            self.write_json(400, {"ok": False, "error": str(error)})


def refresh_rate_on_start():  #Updates the public rate but still lets the app work offline
    try:
        rate_date, _, _ = refresh_exchange_rates(DATABASE_PATH)
        print(f"Exchange rate refreshed: {rate_date}")
    except (FileNotFoundError, OSError, ValueError, sqlite3.Error):
        print("Using the last saved exchange rate")


def main():  #Starts the local web app
    global REMOTE_ACCESS_SETTINGS

    try:
        REMOTE_ACCESS_SETTINGS = load_tailscale_settings()
    except (OSError, ValueError) as error:
        REMOTE_ACCESS_SETTINGS = None
        print(f"Tailscale access is disabled: {error}")

    server = ThreadingHTTPServer((HOST, PORT), FinanceHubHandler)
    print(f"Finance Hub is running at http://{HOST}:{PORT}")
    if REMOTE_ACCESS_SETTINGS is not None:
        print(f"Private Tailscale access: {REMOTE_ACCESS_SETTINGS.origin}")
    print("Press Ctrl+C to stop it")
    Thread(
        target=refresh_rate_on_start,
        name="finance-hub-fx-refresh",
        daemon=True,
    ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Finance Hub")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
