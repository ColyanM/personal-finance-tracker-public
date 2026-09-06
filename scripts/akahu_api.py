import http.client
import json
from urllib.parse import urlencode

try:
    from scripts.secret_store import get_secret
except ModuleNotFoundError:
    from secret_store import get_secret


AKAHU_HOST = "api.akahu.io"
MAX_RESPONSE_BYTES = 1_000_000
ALLOWED_PATHS = {
    "/v1/accounts",
    "/v1/transactions",
    "/v1/transactions/pending",
}  #Only the read-only Akahu requests used by this project


def get_tokens():  #Decrypts the tokens only while an Akahu command is running
    app_id_token = get_secret("akahu-app-id-token")
    user_access_token = get_secret("akahu-user-access-token")

    if not app_id_token or not user_access_token:
        raise ValueError("Run python scripts\\akahu_setup.py setup first")

    return app_id_token, user_access_token


def fetch_page(path, app_id_token, user_access_token, query=None):  #Makes one approved read-only Akahu request
    if path not in ALLOWED_PATHS:
        raise ValueError("Akahu path is not approved")

    request_path = path
    if query:
        request_path += "?" + urlencode(query)

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {user_access_token}",
        "X-Akahu-Id": app_id_token,
    }
    connection = http.client.HTTPSConnection(AKAHU_HOST, timeout=15)

    try:
        connection.request("GET", request_path, headers=headers)
        response = connection.getresponse()
        body = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, http.client.HTTPException) as error:
        raise OSError("Could not securely reach Akahu") from error
    finally:
        connection.close()

    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("Akahu response was unexpectedly large")

    if response.status == 401:
        raise ValueError("Akahu rejected the saved tokens")
    if response.status == 403:
        raise ValueError("Akahu denied access")
    if response.status != 200:
        raise ValueError(f"Akahu request failed with status {response.status}")

    try:
        result = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError("Akahu returned an invalid response") from error

    if (
        not isinstance(result, dict)
        or result.get("success") is not True
        or not isinstance(result.get("items"), list)
    ):
        raise ValueError("Akahu returned an unexpected response")

    return result
