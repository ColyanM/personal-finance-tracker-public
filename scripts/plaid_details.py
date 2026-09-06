from datetime import date
from decimal import Decimal, InvalidOperation

try:
    from scripts.common import money_to_minor, safe_text
    from scripts.investments import replace_provider_positions
    from scripts.paths import DATABASE_PATH
    from scripts.plaid_api import (
        PlaidRequestError,
        fetch_investment_holdings,
        get_item_products,
    )
    from scripts.plaid_environment import get_plaid_config
    from scripts.plaid_items import describe_item_error
except ModuleNotFoundError:
    from common import money_to_minor, safe_text
    from investments import replace_provider_positions
    from paths import DATABASE_PATH
    from plaid_api import PlaidRequestError, fetch_investment_holdings, get_item_products
    from plaid_environment import get_plaid_config
    from plaid_items import describe_item_error


def decimal_text(value, label):  #Keeps rates exact in SQLite
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"Plaid returned an invalid {label}") from error

    if not number.is_finite():
        raise ValueError(f"Plaid returned an invalid {label}")

    return format(number, "f")


def optional_minor(value, label):  #Turns optional dollar values into cents
    if value is None:
        return None

    return money_to_minor(decimal_text(value, label))


def optional_date(value, label):  #Checks optional Plaid dates before saving them
    if value is None:
        return None

    cleaned = safe_text(value, "")[:10]
    try:
        date.fromisoformat(cleaned)
    except ValueError as error:
        raise ValueError(f"Plaid returned an invalid {label}") from error

    return cleaned


def security_label(security):  #Gets a useful ticker-like label from Plaid security data
    ticker = safe_text(security.get("ticker_symbol"), "").upper()
    if ticker:
        return ticker

    security_type = safe_text(security.get("type"), "").lower()
    if security_type == "cash":
        return "CASH"

    return safe_text(security.get("security_id"), "UNKNOWN").upper()


def security_asset_class(security):  #Keeps investment groups simple
    security_type = safe_text(security.get("type"), "").lower()
    if security_type in {"etf", "equity", "stock"}:
        return "Equity"
    if security_type in {"mutual fund", "money market fund"}:
        return "Fund"
    if security_type == "cash":
        return "Cash"
    if security_type == "bond":
        return "Bond"

    return safe_text(security.get("type"), "Other").title()


def get_security_map(securities):  #Maps Plaid security IDs to the displayed details
    return {
        security.get("security_id"): security
        for security in securities
        if isinstance(security, dict) and security.get("security_id")
    }


def prepare_investment_positions(holdings, securities):  #Turns Plaid holdings into local ticker rows
    security_map = get_security_map(securities)
    rows = []

    for holding in holdings:
        if not isinstance(holding, dict):
            raise ValueError("Plaid returned an invalid investment holding")

        security = security_map.get(holding.get("security_id"), {})
        currency = safe_text(
            holding.get("iso_currency_code") or security.get("iso_currency_code"),
            "",
        ).upper()
        if currency not in {"NZD", "USD"}:
            continue

        value = holding.get("institution_value")
        if value is None:
            continue

        price = holding.get("institution_price")
        if price is None:
            price = security.get("close_price")

        rows.append(
            {
                "provider_account_id": safe_text(holding.get("account_id"), ""),
                "ticker": security_label(security),
                "security_name": safe_text(security.get("name"), ""),
                "asset_class": security_asset_class(security),
                "quantity": decimal_text(holding.get("quantity"), "investment quantity"),
                "price_minor": optional_minor(price, "investment price"),
                "value_minor": money_to_minor(decimal_text(value, "investment value")),
                "currency": currency,
                "as_of": optional_date(
                    holding.get("institution_price_as_of") or security.get("close_price_as_of"),
                    "investment price date",
                ),
            }
        )

    return rows


def fetch_investment_positions(item_accounts):  #Fetches positions only for Items that have investments
    positions = []
    refreshed_account_ids = set()
    item_failures = []

    for item in item_accounts:
        access_token = item["access_token"]
        account_ids = item["account_ids"]
        try:
            products = get_item_products(access_token)

            if "investments" in products:
                holdings, securities = fetch_investment_holdings(
                    access_token,
                    account_ids,
                )
                positions.extend(prepare_investment_positions(holdings, securities))
        except PlaidRequestError as error:
            item_failures.append(
                describe_item_error(error, item, item["position"])
            )
            continue

        refreshed_account_ids.update(account_ids)

    return positions, refreshed_account_ids, item_failures


def refresh_investments(
    item_accounts,
    database_path=DATABASE_PATH,
):  #Fetches and stores investment positions without keeping raw responses
    positions, refreshed_account_ids, item_failures = fetch_investment_positions(
        item_accounts
    )
    saved_count = replace_provider_positions(
        positions,
        get_plaid_config()["provider"],
        database_path,
        provider_account_ids=refreshed_account_ids,
    )
    return saved_count, item_failures
