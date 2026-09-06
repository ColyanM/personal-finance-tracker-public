import argparse
import json
import secrets
import sys
import threading
import webbrowser
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    from scripts.plaid_api import (
        create_link_token,
        create_update_link_token,
        exchange_public_token,
    )
    from scripts.plaid_environment import get_plaid_config
    from scripts.plaid_items import get_saved_item, save_item
except ModuleNotFoundError:
    from plaid_api import create_link_token, create_update_link_token, exchange_public_token
    from plaid_environment import get_plaid_config
    from plaid_items import get_saved_item, save_item


HOST = "127.0.0.1"
PORT = 8765
EXPECTED_HOST = f"{HOST}:{PORT}"
EXPECTED_ORIGIN = f"http://{EXPECTED_HOST}"
MAX_REQUEST_BYTES = 4_096


def save_connection(public_token, institution_name):  #Exchanges and encrypts one Plaid item
    access_token, item_id = exchange_public_token(public_token)
    save_item(access_token, item_id, institution_name)


def request_is_allowed(host, origin, session_token, expected_session_token):  #Checks the local Link handoff
    return (
        host == EXPECTED_HOST
        and origin == EXPECTED_ORIGIN
        and secrets.compare_digest(session_token or "", expected_session_token)
    )


def get_result_path(update_mode):  #Keeps new connections and repairs on separate callbacks
    return "/complete" if update_mode else "/exchange"


def build_link_page(
    link_token,
    session_token,
    nonce,
    update_mode=False,
    connection_label=None,
):  #Builds the temporary local Plaid Link page
    config = get_plaid_config()
    raw_label = connection_label or config["label"]
    label = escape(raw_label)
    if update_mode:
        page_title = f"Reconnect {label}"
        opening_text = (
            "This repairs the existing connection without creating a new account link."
        )
        help_text = "Continue through Plaid to renew your bank sign-in, MFA, or consent"
        button_text = f"Reconnect {label}"
        exit_text = "Bank sign-in was not repaired. You can close this page."
        success_action = """
                statusText.textContent = "Confirming the repaired connection";
                const response = await fetch("/complete", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Finance-Hub-Session": sessionToken
                    },
                    body: "{}"
                });
                statusText.textContent = response.ok
                    ? "Bank sign-in repaired. Close this page, then refresh Finance Hub."
                    : "Repair could not be confirmed. Check PowerShell.";
"""
        success_arguments = "_publicToken, _metadata"
    else:
        page_title = f"Connect {label}"
        opening_text = "This connects real account, transaction, and investment data."
        help_text = "Continue through Plaid using your bank sign-in"
        button_text = "Open Plaid Link"
        exit_text = "No connection saved. You can close this page."
        success_action = f"""
                statusText.textContent = "Saving encrypted Plaid connection";
                const response = await fetch("/exchange", {{
                    method: "POST",
                    headers: {{
                        "Content-Type": "application/json",
                        "X-Finance-Hub-Session": sessionToken
                    }},
                    body: JSON.stringify({{
                        public_token: publicToken,
                        institution_name: metadata.institution ? metadata.institution.name : {json.dumps(config["label"])}
                    }})
                }});
                statusText.textContent = response.ok
                    ? "Plaid connected. You can close this page."
                    : "Connection could not be saved. Check PowerShell.";
"""
        success_arguments = "publicToken, metadata"
    safe_link_token = json.dumps(link_token).replace("<", "\\u003c")
    safe_session_token = json.dumps(session_token).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{page_title}</title>
    <style nonce="{escape(nonce)}">
        body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 40px; max-width: 620px; }}
        button {{ background: #172f4a; border: 0; border-radius: 6px; color: white; padding: 10px 18px; }}
        .muted {{ color: #666; }}
    </style>
</head>
<body>
    <h1>{page_title}</h1>
    <p>{opening_text}</p>
    <p>Finance Hub does not request identity, bank account numbers, payments, or transfers.</p>
    <button id="connectButton" type="button">{button_text}</button>
    <p id="status" class="muted">{help_text}</p>
    <script nonce="{escape(nonce)}" src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
    <script nonce="{escape(nonce)}">
        const sessionToken = {safe_session_token};
        const statusText = document.getElementById("status");
        const handler = Plaid.create({{
            token: {safe_link_token},
            onSuccess: async function({success_arguments}) {{
{success_action}
            }},
            onExit: async function() {{
                await fetch("/cancel", {{
                    method: "POST",
                    headers: {{
                        "Content-Type": "application/json",
                        "X-Finance-Hub-Session": sessionToken
                    }},
                    body: "{{}}"
                }});
                statusText.textContent = {json.dumps(exit_text)};
            }}
        }});
        document.getElementById("connectButton").addEventListener("click", function() {{
            handler.open();
        }});
    </script>
</body>
</html>""".encode("utf-8")


class PlaidLinkHandler(BaseHTTPRequestHandler):
    link_token = ""
    session_token = ""
    nonce = ""
    update_mode = False
    connection_label = ""

    def log_message(self, format, *args):  #Keeps short-lived Plaid values out of request logs
        return

    def send_security_headers(self):  #Limits the temporary page to Plaid Link and this local server
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'self'; script-src 'nonce-{self.nonce}' "
            "https://cdn.plaid.com/link/v2/stable/link-initialize.js; "
            f"style-src 'nonce-{self.nonce}'; style-src-attr 'unsafe-inline'; "
            "frame-src https://cdn.plaid.com/; connect-src 'self' https://production.plaid.com/; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")

    def write_response(self, status, body, content_type="text/plain; charset=utf-8"):  #Sends one local Link response
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def stop_server(self):  #Stops the one-time local server after Link finishes
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    def do_GET(self):  #Serves only the temporary Plaid Link page
        self.close_connection = True

        if urlparse(self.path).path != "/" or self.headers.get("Host") != EXPECTED_HOST:
            self.write_response(404, b"Page not found")
            return

        page = build_link_page(
            self.link_token,
            self.session_token,
            self.nonce,
            self.update_mode,
            self.connection_label,
        )
        self.write_response(200, page, "text/html; charset=utf-8")

    def do_POST(self):  #Accepts only the local one-time Link result
        self.close_connection = True
        path = urlparse(self.path).path

        result_path = get_result_path(self.update_mode)
        if path not in {"/cancel", result_path}:
            self.write_response(404, b"Page not found")
            return

        if not request_is_allowed(
            self.headers.get("Host"),
            self.headers.get("Origin"),
            self.headers.get("X-Finance-Hub-Session"),
            self.session_token,
        ):
            self.write_response(403, b"Request blocked")
            return

        if self.headers.get_content_type() != "application/json":
            self.write_response(400, b"JSON request required")
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.write_response(400, b"Invalid request length")
            return

        if length < 0 or length > MAX_REQUEST_BYTES:
            self.write_response(400, b"Request too large")
            return

        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Request body must be an object")
            if path == "/exchange":
                save_connection(body.get("public_token"), body.get("institution_name"))
        except (json.JSONDecodeError, OSError, ValueError):
            self.write_response(400, b"Connection could not be saved")
            self.stop_server()
            return

        response_body = (
            b"Plaid connection repaired"
            if path == "/complete"
            else b"Plaid connection finished"
        )
        self.write_response(200, response_body)
        self.stop_server()


def run_link(link_token, label, update_mode=False):  #Runs one isolated local Link session
    PlaidLinkHandler.link_token = link_token
    PlaidLinkHandler.session_token = secrets.token_urlsafe(32)
    PlaidLinkHandler.nonce = secrets.token_urlsafe(24)
    PlaidLinkHandler.update_mode = update_mode
    PlaidLinkHandler.connection_label = label
    server = ThreadingHTTPServer((HOST, PORT), PlaidLinkHandler)
    url = f"http://{EXPECTED_HOST}"
    action = "reconnection" if update_mode else "connection"
    print(f"{label} {action} Link is ready at {url}")
    print("The temporary local server stops when Link finishes")
    webbrowser.open(url)

    try:
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        print(f"\nStopping {label} Link")
    finally:
        server.server_close()


def connect():  #Runs one temporary new-connection Plaid Link session
    label = get_plaid_config()["label"]
    run_link(create_link_token(), label)


def update_connection(position):  #Repairs one saved item without replacing its token
    item = get_saved_item(position)
    link_token = create_update_link_token(item["access_token"])
    run_link(link_token, item["label"], update_mode=True)


def read_arguments():  #Reads the Plaid Link command
    parser = argparse.ArgumentParser(description="Connect or repair a Plaid item")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("connect", help="Connect a new Plaid item")
    update_parser = subparsers.add_parser("update", help="Repair an existing Plaid item")
    update_parser.add_argument("--connection", required=True, type=int)
    return parser.parse_args()


def main():  #Runs the selected Plaid Link command
    arguments = read_arguments()

    try:
        if arguments.command == "connect":
            connect()
        else:
            update_connection(arguments.connection)
    except (OSError, ValueError) as error:
        print(f"Could not open Plaid Link: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
