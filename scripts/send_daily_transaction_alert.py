import argparse
import smtplib
import sqlite3
import ssl
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.message import EmailMessage

try:
    from scripts.common import check_database, safe_text
    from scripts.paths import DATABASE_PATH
    from scripts.secret_store import get_secret, has_secret, set_secret, set_secret_value
except ModuleNotFoundError:
    from common import check_database, safe_text
    from paths import DATABASE_PATH
    from secret_store import get_secret, has_secret, set_secret, set_secret_value


SUPPORTED_CURRENCIES = {"NZD", "USD"}
EMAIL_CONFIRMATION = "SEND"
EMAIL_SECRET_SENDER = "daily-alert-email-sender"
EMAIL_SECRET_PASSWORD = "daily-alert-email-password"
EMAIL_SECRET_RECIPIENTS = "daily-alert-email-recipients"
YAHOO_SMTP_HOST = "smtp.mail.yahoo.com"
YAHOO_SMTP_PORT = 465


def clean_currency(currency):  #Keeps alert amounts to the two supported currencies
    currency = currency.upper().strip()
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Currency must be NZD or USD")

    return currency


def clean_email_address(email_address, label):  #Checks an email before it goes into the local secret store
    email_address = email_address.strip()
    has_bad_space = any(character.isspace() for character in email_address)
    has_basic_shape = "@" in email_address and "." in email_address.rsplit("@", 1)[-1]

    if not email_address or has_bad_space or not has_basic_shape:
        raise ValueError(f"{label} email address is not valid")

    return email_address


def split_recipients(recipients_text):  #Accepts multiple Yahoo recipients in one prompt
    recipients = [
        clean_email_address(recipient, "Recipient")
        for recipient in recipients_text.replace(";", ",").split(",")
        if recipient.strip()
    ]

    if not recipients:
        raise ValueError("Add at least one recipient email")

    return recipients


def get_since_time(hours):  #Finds the UTC import time used for the alert preview
    if hours < 1 or hours > 168:
        raise ValueError("Hours must be between 1 and 168")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    return since.strftime("%Y-%m-%d %H:%M:%S")


def get_recent_posted_transactions(since, database_path=DATABASE_PATH):  #Gets new posted transactions without exposing bank IDs
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT
                t.posted_date,
                a.display_name AS account,
                COALESCE(t.merchant_clean, t.description_raw) AS merchant,
                t.original_amount_minor,
                t.original_currency,
                t.amount_nzd_minor_fixed,
                t.amount_usd_minor_fixed,
                COALESCE(c.name, 'Uncategorized') AS category,
                COALESCE(c.is_transfer, 0) AS is_transfer,
                t.review_status,
                t.imported_at
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            WHERE t.imported_at >= ?
              AND t.transaction_status = 'posted'
            ORDER BY t.imported_at DESC, t.posted_date DESC, t.id DESC
            """,
            (since,),
        ).fetchall()


def format_money(amount_minor, currency):  #Formats cents for the alert text
    sign = "-" if amount_minor < 0 else ""
    amount = Decimal(abs(amount_minor)) / Decimal("100")
    return f"{sign}${amount:,.2f} {currency}"


def get_totals(transactions):  #Adds spending and income in both fixed currencies
    totals = {
        "spending_nzd": 0,
        "spending_usd": 0,
        "income_nzd": 0,
        "income_usd": 0,
    }

    for transaction in transactions:
        if transaction["is_transfer"]:
            continue

        nzd_amount = transaction["amount_nzd_minor_fixed"]
        usd_amount = transaction["amount_usd_minor_fixed"]

        if nzd_amount < 0:
            totals["spending_nzd"] += -nzd_amount
            totals["spending_usd"] += -usd_amount
        elif nzd_amount > 0:
            totals["income_nzd"] += nzd_amount
            totals["income_usd"] += usd_amount

    return totals


def get_display_amount(transaction, currency):  #Uses the frozen value for the selected display currency
    if currency == "NZD":
        return transaction["amount_nzd_minor_fixed"]

    return transaction["amount_usd_minor_fixed"]


def get_direction(amount_minor):  #Labels the row without needing private bank details
    if amount_minor > 0:
        return "income"
    if amount_minor < 0:
        return "spending"

    return "zero"


def build_alert_preview(
    currency="NZD",
    hours=24,
    max_rows=20,
    database_path=DATABASE_PATH,
):  #Builds the same alert data for the CLI, email, and local Settings page
    currency = clean_currency(currency)
    hours = int(hours)
    max_rows = int(max_rows)
    if max_rows < 1 or max_rows > 50:
        raise ValueError("Max rows must be between 1 and 50")

    since = get_since_time(hours)
    transactions = get_recent_posted_transactions(since, database_path)
    totals = get_totals(transactions)
    rows = []

    for transaction in transactions[:max_rows]:
        display_amount = get_display_amount(transaction, currency)
        rows.append(
            {
                "posted_date": transaction["posted_date"],
                "account": safe_text(transaction["account"], "Unnamed account"),
                "merchant": safe_text(transaction["merchant"], "Unknown transaction"),
                "category": safe_text(transaction["category"], "Uncategorized"),
                "review_status": transaction["review_status"],
                "original_amount": format_money(
                    transaction["original_amount_minor"],
                    transaction["original_currency"],
                ),
                "display_amount_minor": display_amount,
                "display_amount": format_money(display_amount, currency),
                "direction": get_direction(display_amount),
            }
        )

    return {
        "currency": currency,
        "hours": hours,
        "since": since,
        "transaction_count": len(transactions),
        "needs_review_count": sum(
            1
            for transaction in transactions
            if transaction["review_status"] == "not_reviewed"
        ),
        "shown_count": len(rows),
        "has_more": len(transactions) > len(rows),
        "spending_minor": totals[f"spending_{currency.lower()}"],
        "income_minor": totals[f"income_{currency.lower()}"],
        "rows": rows,
    }


def print_preview(preview):  #Prints the alert without sending email
    print("Daily transaction alert preview")
    print(f"Imported since: {preview['since']} UTC")
    print("Delivery: local preview only")
    print("Pending transactions: excluded until posted")
    print(f"New posted transactions: {preview['transaction_count']}")
    print(f"Needs review: {preview['needs_review_count']}")

    if not preview["transaction_count"]:
        print("Nothing would be sent")
        return

    print(f"Spending: {format_money(preview['spending_minor'], preview['currency'])}")
    print(f"Income: {format_money(preview['income_minor'], preview['currency'])}")

    current_account = ""
    for transaction in preview["rows"]:
        if transaction["account"] != current_account:
            current_account = transaction["account"]
            print(f"\n{safe_text(current_account, 'Unnamed account')}")

        print(
            f"- {transaction['posted_date']} | "
            f"{safe_text(transaction['merchant'], 'Unknown transaction')} | "
            f"{transaction['display_amount']} | "
            f"{safe_text(transaction['category'], 'Uncategorized')}"
        )

    if preview["has_more"]:
        hidden_count = preview["transaction_count"] - preview["shown_count"]
        print(f"\n{hidden_count} more not shown in this preview")


def build_email_subject(preview):  #Keeps the subject short for phone notifications
    count = preview["transaction_count"]
    transaction_word = "transaction" if count == 1 else "transactions"
    return f"Finance Hub daily alert: {count} new {transaction_word}"


def build_email_body(preview):  #Builds the email without bank IDs or account numbers
    lines = [
        "Finance Hub daily alert",
        "",
        f"Imported since: {preview['since']} UTC",
        "Pending transactions are excluded until posted",
        f"New posted transactions: {preview['transaction_count']}",
        f"Needs review: {preview['needs_review_count']}",
        f"Spending: {format_money(preview['spending_minor'], preview['currency'])}",
        f"Income: {format_money(preview['income_minor'], preview['currency'])}",
    ]

    if preview["rows"]:
        lines.append("")
        lines.append("Recent transactions:")

    for transaction in preview["rows"]:
        amount_text = transaction["display_amount"]
        if transaction["original_amount"] != amount_text:
            amount_text = f"{amount_text} (original {transaction['original_amount']})"

        lines.append(
            f"- {transaction['posted_date']} | "
            f"{transaction['merchant']} | "
            f"{amount_text} | "
            f"{transaction['category']}"
        )

    if preview["has_more"]:
        hidden_count = preview["transaction_count"] - preview["shown_count"]
        lines.append(f"- {hidden_count} more not shown")

    lines.append("")
    lines.append("Open Finance Hub locally to review anything that needs attention")
    return "\n".join(lines)


def setup_email():  #Saves Yahoo email settings in encrypted local files
    sender = clean_email_address(input("Yahoo sender email: "), "Sender")
    recipients = split_recipients(input("Recipient emails separated by commas: "))

    set_secret_value(EMAIL_SECRET_SENDER, sender)
    set_secret(EMAIL_SECRET_PASSWORD, "Yahoo app password")
    set_secret_value(EMAIL_SECRET_RECIPIENTS, ", ".join(recipients))

    print("Daily email alert configured")
    print(f"Sender: {sender}")
    print(f"Recipients: {len(recipients)} saved")


def email_is_configured():  #Checks setup without decrypting the saved values
    return (
        has_secret(EMAIL_SECRET_SENDER)
        and has_secret(EMAIL_SECRET_PASSWORD)
        and has_secret(EMAIL_SECRET_RECIPIENTS)
    )


def print_email_status():  #Shows email setup without printing private values
    status = "configured" if email_is_configured() else "not configured"
    print(f"Daily email alert: {status}")
    print(f"SMTP: {YAHOO_SMTP_HOST}:{YAHOO_SMTP_PORT}")


def get_email_settings():  #Reads encrypted settings only when sending
    sender = get_secret(EMAIL_SECRET_SENDER)
    password = get_secret(EMAIL_SECRET_PASSWORD)
    recipients_text = get_secret(EMAIL_SECRET_RECIPIENTS)
    missing = []

    if not sender:
        missing.append("sender")
    if not password:
        missing.append("Yahoo app password")
    if not recipients_text:
        missing.append("recipients")
    if missing:
        raise ValueError(
            "Daily email alert is not configured. Missing: "
            + ", ".join(missing)
        )

    return sender, password, split_recipients(recipients_text)


def send_text_email(subject, body):  #Sends one plain text email through Yahoo SMTP
    sender, password, recipients = get_email_settings()
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        YAHOO_SMTP_HOST,
        YAHOO_SMTP_PORT,
        context=context,
        timeout=30,
    ) as smtp:
        smtp.login(sender, password)
        smtp.send_message(message)

    print(f"Daily email sent to {len(recipients)} recipient(s)")


def send_email(preview):  #Sends the normal transaction alert email
    send_text_email(build_email_subject(preview), build_email_body(preview))


def send_daily_email(arguments):  #Sends only after the explicit confirmation word
    if arguments.confirm != EMAIL_CONFIRMATION:
        raise ValueError(f"Add --confirm {EMAIL_CONFIRMATION} to send the email")

    preview = build_alert_preview(
        arguments.currency,
        arguments.hours,
        arguments.max_rows,
    )
    if not preview["transaction_count"] and not arguments.send_if_empty:
        print("No new posted transactions. Email not sent")
        return

    send_email(preview)


def read_arguments():  #Reads the alert command without exposing email settings
    parser = argparse.ArgumentParser(description="Manage the daily transaction alert")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["preview", "setup-email", "email-status", "send-email"],
        default="preview",
    )
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--currency", default="NZD")
    parser.add_argument("--max-rows", type=int, default=20)
    parser.add_argument("--confirm")
    parser.add_argument(
        "--send-if-empty",
        action="store_true",
        help="Send the email even when no new posted transactions are found",
    )
    return parser.parse_args()


def main():  #Runs preview, setup, status, or confirmed email sending
    arguments = read_arguments()

    try:
        if arguments.command == "setup-email":
            setup_email()
        elif arguments.command == "email-status":
            print_email_status()
        elif arguments.command == "send-email":
            send_daily_email(arguments)
        else:
            preview = build_alert_preview(
                arguments.currency,
                arguments.hours,
                arguments.max_rows,
            )
            print_preview(preview)
    except (
        FileNotFoundError,
        OSError,
        ValueError,
        smtplib.SMTPException,
        sqlite3.Error,
    ) as error:
        print(f"Could not manage daily alert: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
