import http.client
import json
import secrets
import time

try:
    from scripts.plaid_environment import get_plaid_config
    from scripts.secret_store import get_secret, set_secret_value
except ModuleNotFoundError:
    from plaid_environment import get_plaid_config
    from secret_store import get_secret, set_secret_value


PLAID_VERSION = "2020-09-14"
MAX_RESPONSE_BYTES = 1_000_000
ALLOWED_PATHS = {
    "/accounts/get",
    "/institutions/get",
    "/investments/holdings/get",
    "/item/get",
    "/item/public_token/exchange",
    "/item/remove",
    "/link/token/create",
    "/transactions/get",
}  #Only reviewed Plaid paths are approved
MAX_TRANSACTION_ITEMS = 1_000
LINK_PRODUCTS = ["transactions"]
LINK_EXTRA_PRODUCTS = ["investments"]
RETRYABLE_PATHS = {
    "/accounts/get",
    "/investments/holdings/get",
    "/item/get",
    "/transactions/get",
}
RETRY_COUNT = 3


class PlaidRequestError(ValueError):  #Keeps the Plaid error code separate from its safe message
    def __init__(self, message, error_code=None):
        super().__init__(message)
        self.error_code = error_code


def get_credentials():  #Decrypts the saved Plaid Production credentials
    config = get_plaid_config()
    client_id = (get_secret(config["client_id_name"]) or "").strip()
    plaid_secret = (get_secret(config["secret_name"]) or "").strip()

    if not client_id or not plaid_secret:
        raise ValueError("Run python scripts\\plaid_setup.py setup first")

    return client_id, plaid_secret


def get_client_user_id():  #Creates one private ID without using personal details
    config = get_plaid_config()
    user_id = (get_secret(config["user_id_name"]) or "").strip()

    if not user_id:
        user_id = secrets.token_urlsafe(24)
        set_secret_value(config["user_id_name"], user_id)

    return user_id


def clean_access_token(access_token):  #Checks one decrypted token before it is sent
    if not isinstance(access_token, str) or not access_token.strip():
        raise ValueError("Plaid access token is missing")

    access_token = access_token.strip()
    if len(access_token) > 2_000:
        raise ValueError("Plaid access token is unexpectedly long")

    return access_token


def hide_sensitive_values(message, sensitive_values):  #Removes saved Plaid values from an error message
    safe_message = message

    for value in sensitive_values:
        if value:
            safe_message = safe_message.replace(value, "[hidden]")

    return safe_message


def get_payload_strings(value):  #Finds request strings that should not come back in errors
    if isinstance(value, str):
        return [value]

    if isinstance(value, dict):
        strings = []
        for item in value.values():
            strings.extend(get_payload_strings(item))
        return strings

    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(get_payload_strings(item))
        return strings

    return []


def get_error_details(response_body, sensitive_values=()):  #Gets safe Plaid error details without showing raw tokens
    try:
        error_details = json.loads(response_body)
    except json.JSONDecodeError:
        return ""

    if not isinstance(error_details, dict):
        return ""

    error_type = error_details.get("error_type")
    error_code = error_details.get("error_code")
    error_message = error_details.get("error_message")
    details = [
        value
        for value in (error_type, error_code)
        if isinstance(value, str) and value
    ]

    if isinstance(error_message, str) and error_message:
        details.append(hide_sensitive_values(error_message, sensitive_values))

    if not details:
        return ""

    return f" ({' | '.join(details)})"


def get_error_code(response_body):  #Gets only the code needed for safe error handling
    try:
        error_details = json.loads(response_body)
    except json.JSONDecodeError:
        return None

    if not isinstance(error_details, dict):
        return None

    error_code = error_details.get("error_code")
    return error_code if isinstance(error_code, str) else None


def request(path, payload, client_id, plaid_secret):  #Makes one approved Plaid Production request
    if path not in ALLOWED_PATHS:
        raise ValueError("Plaid path is not approved")

    config = get_plaid_config()
    label = config["label"]
    safe_payload = dict(payload)
    safe_payload["client_id"] = client_id
    safe_payload["secret"] = plaid_secret
    body = json.dumps(safe_payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Plaid-Version": PLAID_VERSION,
    }
    attempts = RETRY_COUNT if path in RETRYABLE_PATHS else 1
    for attempt in range(1, attempts + 1):
        connection = http.client.HTTPSConnection(config["host"], timeout=15)
        try:
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            break
        except (OSError, http.client.HTTPException) as error:
            if attempt == attempts:
                raise OSError(f"Could not securely reach {label}") from error
            time.sleep(2 * attempt)
        finally:
            connection.close()

    if len(response_body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"{label} response was unexpectedly large")

    if response.status != 200:
        sensitive_values = [client_id, plaid_secret, *get_payload_strings(payload)]
        details = get_error_details(response_body, sensitive_values)
        raise PlaidRequestError(
            f"{label} request failed with status {response.status}{details}",
            get_error_code(response_body),
        )

    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} returned an invalid response") from error

    if not isinstance(result, dict):
        raise ValueError(f"{label} returned an unexpected response")

    return result


def check_credentials():  #Checks setup using public institution data only
    client_id, plaid_secret = get_credentials()
    result = request(
        "/institutions/get",
        {"count": 1, "offset": 0, "country_codes": ["US"]},
        client_id,
        plaid_secret,
    )
    institutions = result.get("institutions")

    if not isinstance(institutions, list):
        raise ValueError("Plaid returned an unexpected response")

    return len(institutions)


def create_link_token():  #Creates one short-lived read-only Plaid Link session
    client_id, plaid_secret = get_credentials()
    user_id = get_client_user_id()
    result = request(
        "/link/token/create",
        {
            "client_name": "Finance Hub",
            "country_codes": ["US"],
            "language": "en",
            "products": LINK_PRODUCTS,
            "required_if_supported_products": LINK_EXTRA_PRODUCTS,
            "user": {"client_user_id": user_id},
        },
        client_id,
        plaid_secret,
    )
    link_token = result.get("link_token")

    if not isinstance(link_token, str) or not link_token:
        raise ValueError("Plaid did not return a Link token")

    return link_token


def create_update_link_token(access_token):  #Creates Link update mode for one saved item
    access_token = clean_access_token(access_token)
    client_id, plaid_secret = get_credentials()
    user_id = get_client_user_id()
    result = request(
        "/link/token/create",
        {
            "client_name": "Finance Hub",
            "country_codes": ["US"],
            "language": "en",
            "user": {"client_user_id": user_id},
            "access_token": access_token,
        },
        client_id,
        plaid_secret,
    )
    link_token = result.get("link_token")

    if not isinstance(link_token, str) or not link_token:
        raise ValueError("Plaid did not return an update Link token")

    return link_token


def exchange_public_token(public_token):  #Exchanges a short-lived public token without printing it
    if not isinstance(public_token, str) or not public_token.strip():
        raise ValueError("Plaid public token is missing")

    if len(public_token) > 2_000:
        raise ValueError("Plaid public token is unexpectedly long")

    client_id, plaid_secret = get_credentials()
    result = request(
        "/item/public_token/exchange",
        {"public_token": public_token.strip()},
        client_id,
        plaid_secret,
    )
    access_token = result.get("access_token")
    item_id = result.get("item_id")

    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Plaid did not return an access token")

    if not isinstance(item_id, str) or not item_id:
        raise ValueError("Plaid did not return an item ID")

    return access_token, item_id


def fetch_accounts(access_token):  #Gets accounts from one encrypted Plaid item
    access_token = clean_access_token(access_token)
    client_id, plaid_secret = get_credentials()
    result = request(
        "/accounts/get",
        {"access_token": access_token},
        client_id,
        plaid_secret,
    )
    accounts = result.get("accounts")

    if not isinstance(accounts, list):
        raise ValueError("Plaid returned an unexpected account response")

    return accounts


def get_item_products(access_token):  #Checks which approved details one Item supports
    access_token = clean_access_token(access_token)
    client_id, plaid_secret = get_credentials()
    result = request(
        "/item/get",
        {"access_token": access_token},
        client_id,
        plaid_secret,
    )
    item = result.get("item")
    if not isinstance(item, dict):
        raise ValueError("Plaid returned an unexpected Item response")

    products = item.get("products")
    billed_products = item.get("billed_products")
    supported = []
    for values in (products, billed_products):
        if isinstance(values, list):
            supported.extend(value for value in values if isinstance(value, str))

    return set(supported)


def clean_account_ids(account_ids):  #Limits detail requests to selected saved accounts
    if not account_ids or not all(isinstance(value, str) and value for value in account_ids):
        raise ValueError("Choose at least one valid Plaid account")

    return list(dict.fromkeys(account_ids))


def fetch_investment_holdings(access_token, account_ids):  #Gets ticker holdings for selected saved accounts
    access_token = clean_access_token(access_token)
    account_ids = clean_account_ids(account_ids)
    client_id, plaid_secret = get_credentials()
    result = request(
        "/investments/holdings/get",
        {"access_token": access_token, "options": {"account_ids": account_ids}},
        client_id,
        plaid_secret,
    )
    holdings = result.get("holdings")
    securities = result.get("securities")

    if not isinstance(holdings, list) or not isinstance(securities, list):
        raise ValueError("Plaid returned an unexpected investment response")

    return holdings, securities


def fetch_transactions(access_token, start_date, end_date, account_ids):  #Gets recent transactions for saved accounts only
    access_token = clean_access_token(access_token)
    account_ids = clean_account_ids(account_ids)

    client_id, plaid_secret = get_credentials()
    transactions = []
    offset = 0

    while True:
        result = request(
            "/transactions/get",
            {
                "access_token": access_token,
                "start_date": start_date,
                "end_date": end_date,
                "options": {
                    "account_ids": account_ids,
                    "count": 100,
                    "offset": offset,
                },
            },
            client_id,
            plaid_secret,
        )
        page = result.get("transactions")
        total = result.get("total_transactions")

        if not isinstance(page, list) or not isinstance(total, int) or total < 0:
            raise ValueError("Plaid returned an unexpected transaction response")

        transactions.extend(page)
        if len(transactions) > MAX_TRANSACTION_ITEMS:
            raise ValueError("Plaid transaction result is too large. Use fewer days")

        if len(transactions) >= total:
            return transactions

        if not page:
            raise ValueError("Plaid returned an incomplete transaction response")

        offset = len(transactions)


def remove_item(access_token):  #Revokes one Plaid item without printing its token
    access_token = clean_access_token(access_token)
    client_id, plaid_secret = get_credentials()
    try:
        request(
            "/item/remove",
            {"access_token": access_token},
            client_id,
            plaid_secret,
        )
    except PlaidRequestError as error:
        if error.error_code == "ITEM_NOT_FOUND":
            return False  #The Item was already revoked so local cleanup can finish
        raise

    return True
