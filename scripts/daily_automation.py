import argparse
import smtplib
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

try:
    from scripts.automation import finish_run, start_run
    from scripts.accounts import get_net_worth
    from scripts.bnz_refresh import refresh_bnz
    from scripts.budgets import get_budget_progress
    from scripts.paths import DATABASE_PATH, PROJECT_ROOT
    from scripts.plaid_transactions import refresh_plaid
    from scripts.send_daily_transaction_alert import (
        build_alert_preview,
        format_money,
        send_text_email,
    )
    from scripts.settings import get_settings
    from scripts.summaries import get_spending_summary
except ModuleNotFoundError:
    from automation import finish_run, start_run
    from accounts import get_net_worth
    from bnz_refresh import refresh_bnz
    from budgets import get_budget_progress
    from paths import DATABASE_PATH, PROJECT_ROOT
    from plaid_transactions import refresh_plaid
    from send_daily_transaction_alert import build_alert_preview, format_money, send_text_email
    from settings import get_settings
    from summaries import get_spending_summary


JOB_NAME = "daily_refresh_and_email"
RUN_CONFIRMATION = "RUN_DAILY"
RUN_ERRORS = (FileNotFoundError, OSError, ValueError, smtplib.SMTPException, sqlite3.Error)
NZ_TIMEZONE = ZoneInfo("Pacific/Auckland")


def clean_error(error):  #Keeps scheduled task errors readable without dumping too much text
    return str(error).replace("\r", " ").replace("\n", " ")[:300]


def nz_time_text():  #Shows email run times in the timezone the scheduled task uses
    return datetime.now(NZ_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %Z")


def summarize_bnz_result(result):  #Turns the BNZ refresh result into one safe line
    fx_note = " using saved FX rate" if len(result) > 7 and result[7] else ""
    return (
        f"BNZ ok{fx_note}: {result[3]} posted added, "
        f"{result[4]} posted updated, {result[5]} pending saved"
    )


def summarize_plaid_result(result):  #Turns the Plaid refresh result into one safe line
    plaid_transactions = result[3]
    fx_note = " using saved FX rate" if len(result) > 6 and result[6] else ""
    item_failures = result[7] if len(result) > 7 else []
    status = "US partial" if item_failures else "US ok"
    summary = (
        f"{status}{fx_note}: {plaid_transactions[0]} posted added, "
        f"{plaid_transactions[1]} pending matched, "
        f"{plaid_transactions[3]} current pending"
    )
    if item_failures:
        summary += "; " + " | ".join(item_failures)
    return summary


def run_refresh_step(
    label,
    refresh_function,
    summary_function,
    success_check=None,
):  #Runs one bank refresh and lets the next one still try
    try:
        result = refresh_function()
        succeeded = success_check(result) if success_check else True
        return succeeded, summary_function(result)
    except RUN_ERRORS as error:
        return False, f"{label} failed: {clean_error(error)}"


def build_failure_email_body(step_lines):  #Sends enough detail to know the scheduled refresh needs attention
    lines = [
        "Finance Hub daily refresh had a problem",
        "",
        f"Time: {nz_time_text()}",
        "",
        "What happened:",
    ]
    lines.extend(f"- {line}" for line in step_lines)
    lines.append("")
    lines.append("Open Finance Hub locally and check automation history before rerunning it")
    return "\n".join(lines)


def get_monthly_budget_status(currency, database_path=DATABASE_PATH):  #Matches the dashboard monthly budget period
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    month_summary = get_spending_summary(
        "month",
        currency,
        today=today,
        database_path=database_path,
    )
    progress = get_budget_progress(
        week_start=month_start,
        database_path=database_path,
        display_currency=currency,
        budget_view="month",
    )
    expense_budget = sum(row[4] + row[7] for row in progress if not row[6])

    return {
        "currency": currency,
        "start_date": month_summary["start_date"],
        "end_date": month_summary["end_date"],
        "income_minor": month_summary["income_total_minor"],
        "spending_minor": month_summary["spending_total_minor"],
        "expense_budget_minor": expense_budget,
        "remaining_budget_minor": expense_budget - month_summary["spending_total_minor"],
    }


def get_dashboard_net_worth_series(currency, database_path=DATABASE_PATH):  #Reuses the app's hybrid daily net worth logic
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    import app as finance_app

    original_database_path = finance_app.DATABASE_PATH
    finance_app.DATABASE_PATH = database_path
    try:
        return finance_app.api_net_worth_series(currency, "1m")
    finally:
        finance_app.DATABASE_PATH = original_database_path


def get_net_worth_status(currency, database_path=DATABASE_PATH):  #Compares current net worth with the prior daily point
    today_text = date.today().isoformat()
    yesterday_text = (date.today() - timedelta(days=1)).isoformat()
    current = get_net_worth(currency, database_path)
    series = get_dashboard_net_worth_series(currency, database_path)
    previous = next(
        (row for row in reversed(series) if row["date"] == yesterday_text),
        None,
    )
    missing_history_count = get_missing_prior_balance_history_count(yesterday_text, database_path)

    return {
        "currency": currency,
        "date": today_text,
        "total_minor": current["total_minor"],
        "previous_date": previous["date"] if previous else None,
        "previous_total_minor": previous["value_minor"] if previous else None,
        "missing_history_count": missing_history_count,
        "change_minor": current["total_minor"] - previous["value_minor"] if previous and not missing_history_count else None,
    }


def get_missing_prior_balance_history_count(previous_date, database_path=DATABASE_PATH):  #Avoids false jumps when a current account has no prior snapshot
    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            """
            SELECT COUNT(*)
            FROM accounts a
            WHERE a.is_active = 1
              AND NOT EXISTS (
                  SELECT 1
                  FROM account_balance_history h
                  WHERE h.account_id = a.id
                    AND h.balance_date <= ?
              )
            """,
            (previous_date,),
        ).fetchone()[0]


def budget_remaining_text(status):  #Makes over-budget months read naturally
    remaining = status["remaining_budget_minor"]
    if remaining < 0:
        return f"Over budget: {format_money(-remaining, status['currency'])}"

    return f"Budget remaining: {format_money(remaining, status['currency'])}"


def net_worth_change_text(status):  #Formats the day-over-day net worth delta
    missing_history_count = status.get("missing_history_count", 0)
    if missing_history_count:
        account_word = "account" if missing_history_count == 1 else "accounts"
        return f"Change from day before: unavailable ({missing_history_count} {account_word} missing prior balance history)"

    if status["change_minor"] is None:
        return "Change from day before: unavailable"

    change = status["change_minor"]
    if change > 0:
        direction = "up"
    elif change < 0:
        direction = "down"
    else:
        direction = "no change"

    amount = format_money(abs(change), status["currency"])
    if direction == "no change":
        return f"Change from {status['previous_date']}: {amount}"

    return f"Change from {status['previous_date']}: {direction} {amount}"


def build_status_email_body(step_lines, preview, monthly_status, net_worth_status):  #Sends the daily account status email
    lines = [
        "Finance Hub daily status",
        "",
        f"Time: {nz_time_text()}",
        "",
        "Refresh results:",
    ]
    lines.extend(f"- {line}" for line in step_lines)
    lines.extend(
        [
            "",
            "Transaction summary:",
            f"- Imported since: {preview['since']} UTC",
            f"- New posted transactions: {preview['transaction_count']}",
            f"- Needs review: {preview['needs_review_count']}",
            f"- Spending: {format_money(preview['spending_minor'], preview['currency'])}",
            f"- Income: {format_money(preview['income_minor'], preview['currency'])}",
            "",
            "New transactions:",
        ]
    )

    if preview["rows"]:
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
    else:
        lines.append("- None")

    if preview["has_more"]:
        hidden_count = preview["transaction_count"] - preview["shown_count"]
        lines.append(f"- {hidden_count} more not shown")

    lines.extend(
        [
            "",
            f"Monthly budget ({monthly_status['start_date']} to {monthly_status['end_date']}):",
            f"- Income: {format_money(monthly_status['income_minor'], monthly_status['currency'])}",
            f"- Spend: {format_money(monthly_status['spending_minor'], monthly_status['currency'])}",
            f"- Planned expense budget: {format_money(monthly_status['expense_budget_minor'], monthly_status['currency'])}",
            f"- {budget_remaining_text(monthly_status)}",
            "",
            "Net worth:",
            f"- Current: {format_money(net_worth_status['total_minor'], net_worth_status['currency'])}",
            f"- {net_worth_change_text(net_worth_status)}",
            "",
            "Open Finance Hub locally to review anything that needs attention",
        ]
    )
    return "\n".join(lines)


def print_plan(arguments):  #Shows what the scheduled job would do without touching bank data
    print("Daily automation plan")
    print(f"- BNZ refresh: {'off' if arguments.skip_bnz else 'on'}")
    print(f"- US refresh: {'off' if arguments.skip_plaid else 'on'}")
    print(f"- Email alert: {'off' if arguments.skip_email else 'on'}")
    print(f"- Alert currency: {arguments.currency.upper()}")
    print(f"- Alert lookback hours: {arguments.hours}")


def apply_alert_settings(arguments, database_path=DATABASE_PATH):  #Uses Settings page values when the command does not override them
    settings = get_settings(database_path)

    if arguments.currency is None:
        arguments.currency = settings["daily_alert_currency"]
    if arguments.hours is None:
        arguments.hours = int(settings["daily_alert_hours"])
    if arguments.max_rows is None:
        arguments.max_rows = int(settings["daily_alert_max_rows"])
    if settings["daily_alert_enabled"] != "true":
        arguments.skip_email = True
    if not arguments.send_if_empty:
        arguments.send_if_empty = settings["daily_alert_send_if_empty"] == "true"

    return arguments


def run_daily(arguments, database_path=DATABASE_PATH):  #Refreshes accounts and sends the daily email
    arguments = apply_alert_settings(arguments, database_path)

    if arguments.confirm != RUN_CONFIRMATION:
        raise ValueError(f"Add --confirm {RUN_CONFIRMATION} to run the daily job")

    run_id = start_run(JOB_NAME, database_path)
    step_lines = []
    failed = False

    try:
        if not arguments.skip_bnz:
            ok, detail = run_refresh_step(
                "BNZ",
                lambda: refresh_bnz(arguments.bnz_days, "REFRESH", database_path),
                summarize_bnz_result,
            )
            failed = failed or not ok
            step_lines.append(detail)

        if not arguments.skip_plaid:
            ok, detail = run_refresh_step(
                "US",
                lambda: refresh_plaid(arguments.plaid_days, "REFRESH", database_path),
                summarize_plaid_result,
                lambda result: not (len(result) > 7 and result[7]),
            )
            failed = failed or not ok
            step_lines.append(detail)

        if failed:
            try:
                send_text_email(
                    "Finance Hub daily refresh failed",
                    build_failure_email_body(step_lines),
                )
                step_lines.append("Failure email sent")
            except RUN_ERRORS as error:
                step_lines.append(f"Failure email not sent: {clean_error(error)}")

            raise ValueError("Daily refresh failed")

        if not arguments.skip_email:
            preview = build_alert_preview(
                arguments.currency,
                arguments.hours,
                arguments.max_rows,
                database_path,
            )
            if preview["transaction_count"] or arguments.send_if_empty:
                monthly_status = get_monthly_budget_status(arguments.currency, database_path)
                net_worth_status = get_net_worth_status(arguments.currency, database_path)
                send_text_email(
                    "Finance Hub daily status",
                    build_status_email_body(
                        step_lines,
                        preview,
                        monthly_status,
                        net_worth_status,
                    ),
                )
                step_lines.append("Daily status email sent")
            else:
                step_lines.append("Daily email skipped: no new posted transactions")

        finish_run(run_id, "success", "; ".join(step_lines), database_path)
        return step_lines
    except RUN_ERRORS:
        finish_run(run_id, "failed", "; ".join(step_lines)[:500], database_path)
        raise


def read_arguments():  #Reads the daily automation command
    parser = argparse.ArgumentParser(description="Run the daily Finance Hub automation")
    parser.add_argument("command", choices=["plan", "run"])
    parser.add_argument("--confirm")
    parser.add_argument("--currency")
    parser.add_argument("--hours", type=int)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--bnz-days", type=int)
    parser.add_argument("--plaid-days", type=int)
    parser.add_argument("--skip-bnz", action="store_true")
    parser.add_argument("--skip-plaid", action="store_true")
    parser.add_argument("--skip-email", action="store_true")
    parser.add_argument("--send-if-empty", action="store_true")
    return parser.parse_args()


def main():  #Runs the daily plan or the real scheduled job
    arguments = read_arguments()

    try:
        if arguments.command == "plan":
            arguments = apply_alert_settings(arguments)
            print_plan(arguments)
        else:
            step_lines = run_daily(arguments)
            for line in step_lines:
                print(line)
    except RUN_ERRORS as error:
        print(f"Could not run daily automation: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
