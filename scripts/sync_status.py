import argparse
import sqlite3
from contextlib import closing
from datetime import date, timedelta

try:
    from scripts.common import check_database, imported_transaction_exists
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import check_database, imported_transaction_exists
    from paths import DATABASE_PATH


PLAID_PROVIDERS = ("plaid-sandbox-us", "plaid-production-us")
BNZ_PROVIDERS = ("bnz-csv-history", "akahu-bnz")


def transaction_source_summary(connection, label, providers, note):  #Counts source data without showing private IDs
    placeholders = ",".join("?" for _ in providers)
    row = connection.execute(
        f"""
        SELECT
            COUNT(*),
            COUNT(DISTINCT account_id),
            MIN(posted_date),
            MAX(posted_date),
            MAX(imported_at),
            SUM(CASE WHEN transaction_status = 'pending' THEN 1 ELSE 0 END),
            SUM(CASE WHEN review_status = 'not_reviewed' THEN 1 ELSE 0 END)
        FROM transactions
        WHERE provider IN ({placeholders})
        """,
        providers,
    ).fetchone()
    total, accounts, first_date, last_date, last_saved, pending, needs_review = row

    return {
        "label": label,
        "type": "Transactions",
        "total": total or 0,
        "accounts": accounts or 0,
        "first_date": first_date or "",
        "last_date": last_date or "",
        "last_saved": last_saved or "",
        "pending": pending or 0,
        "needs_review": needs_review or 0,
        "note": note,
    }


def monarch_balance_summary(connection):  #Shows imported balance history without old account details
    row = connection.execute(
        """
        SELECT COUNT(*), COUNT(DISTINCT account_name), MIN(balance_date), MAX(balance_date), MAX(created_at)
        FROM account_balance_history
        WHERE source = 'monarch-history'
        """
    ).fetchone()
    total, accounts, first_date, last_date, last_saved = row

    return {
        "label": "Monarch balances",
        "type": "Balance history",
        "total": total or 0,
        "accounts": accounts or 0,
        "first_date": first_date or "",
        "last_date": last_date or "",
        "last_saved": last_saved or "",
        "pending": 0,
        "needs_review": 0,
        "note": "Old account names stay in the private database and are not shown here.",
    }


def fx_status_summary(connection):  #Shows exchange rate coverage by date only
    row = connection.execute(
        """
        SELECT COUNT(DISTINCT rate_date), MIN(rate_date), MAX(rate_date), MAX(fetched_at)
        FROM fx_rates
        """
    ).fetchone()
    total, first_date, last_date, last_saved = row

    return {
        "label": "Exchange rates",
        "type": "FX rates",
        "total": total or 0,
        "accounts": 0,
        "first_date": first_date or "",
        "last_date": last_date or "",
        "last_saved": last_saved or "",
        "pending": 0,
        "needs_review": 0,
        "note": "Transactions keep their original-day exchange rate once imported.",
    }


def provider_duplicate_check(connection):  #Double checks the database is not storing exact provider duplicates
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM (
            SELECT provider, provider_transaction_id
            FROM transactions
            WHERE provider_transaction_id IS NOT NULL
              AND provider_transaction_id != ''
            GROUP BY provider, provider_transaction_id
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()
    duplicate_count = row[0] or 0

    return {
        "label": "Provider ID duplicates",
        "status": "pass" if duplicate_count == 0 else "warn",
        "value": duplicate_count,
        "detail": "One provider transaction ID should only exist once per provider.",
    }


def history_cutoff(connection, provider):  #Finds where imported history ends for one provider
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


def live_rows_before_cutoff(connection, live_providers, cutoff_date):  #Gets live rows that could overlap imported history
    placeholders = ",".join("?" for _ in live_providers)
    return connection.execute(
        f"""
        SELECT posted_date, description_raw, merchant_clean, original_currency, original_amount_minor
        FROM transactions
        WHERE provider IN ({placeholders})
          AND transaction_status = 'posted'
          AND posted_date <= ?
        """,
        (*live_providers, cutoff_date),
    ).fetchall()


def history_overlap_check(connection, label, live_providers, history_provider):  #Checks if live refresh duplicated imported history
    cutoff_date = history_cutoff(connection, history_provider)
    if not cutoff_date:
        return {
            "label": label,
            "status": "pass",
            "value": 0,
            "detail": "No imported history baseline found yet.",
        }

    counts = overlap_counts(connection, live_providers, history_provider, cutoff_date)

    return {
        "label": label,
        "status": "pass" if counts["likely_duplicates"] == 0 and counts["unmatched_old_rows"] == 0 else "warn",
        "value": counts["likely_duplicates"],
        "detail": (
            f"Late same-day rows allowed: {counts['late_same_day_rows']}. "
            f"Unmatched older live rows: {counts['unmatched_old_rows']}."
        ),
    }


def old_pending_check(connection, today=None, days=14):  #Flags pending rows that probably need attention
    today = today or date.today()
    cutoff = (today - timedelta(days=days)).isoformat()
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM transactions
        WHERE transaction_status = 'pending'
          AND posted_date < ?
        """,
        (cutoff,),
    ).fetchone()
    old_pending = row[0] or 0

    return {
        "label": "Old pending rows",
        "status": "pass" if old_pending == 0 else "warn",
        "value": old_pending,
        "detail": f"Pending rows older than {days} days should clear on the next successful refresh.",
    }


def provider_date_summary(connection, providers):  #Gets only date/count totals for a source group
    placeholders = ",".join("?" for _ in providers)
    row = connection.execute(
        f"""
        SELECT COUNT(*), MIN(posted_date), MAX(posted_date)
        FROM transactions
        WHERE provider IN ({placeholders})
          AND transaction_status = 'posted'
        """,
        providers,
    ).fetchone()

    return {
        "count": row[0] or 0,
        "first_date": row[1] or "",
        "last_date": row[2] or "",
    }


def count_live_rows_after_cutoff(connection, live_providers, cutoff_date):  #Counts refresh rows saved after the imported baseline
    if not cutoff_date:
        return 0

    placeholders = ",".join("?" for _ in live_providers)
    row = connection.execute(
        f"""
        SELECT COUNT(*)
        FROM transactions
        WHERE provider IN ({placeholders})
          AND transaction_status = 'posted'
          AND posted_date > ?
        """,
        (*live_providers, cutoff_date),
    ).fetchone()

    return row[0] or 0


def overlap_counts(connection, live_providers, history_provider, cutoff_date):  #Separates duplicates from late same-day rows
    counts = {
        "overlap_rows": 0,
        "likely_duplicates": 0,
        "late_same_day_rows": 0,
        "unmatched_old_rows": 0,
    }
    if not cutoff_date:
        return counts

    for posted_date, description, merchant, currency, amount in live_rows_before_cutoff(connection, live_providers, cutoff_date):
        counts["overlap_rows"] += 1
        transaction = {
            "posted_date": posted_date,
            "description_raw": description,
            "merchant_clean": merchant,
            "original_currency": currency,
            "original_amount_minor": amount,
        }
        if imported_transaction_exists(connection, history_provider, transaction):
            counts["likely_duplicates"] += 1
        elif posted_date == cutoff_date:
            counts["late_same_day_rows"] += 1
        else:
            counts["unmatched_old_rows"] += 1

    return counts


def days_since_latest(latest_date, today):  #Keeps the wording simple on the Sync page
    if not latest_date:
        return None

    try:
        return (today - date.fromisoformat(latest_date)).days
    except ValueError:
        return None


def refresh_audit(connection, label, history_label, history_provider, live_label, live_providers, today=None):  #Checks the import-to-refresh handoff
    today = today or date.today()
    history = provider_date_summary(connection, (history_provider,))
    live = provider_date_summary(connection, live_providers)
    cutoff_date = history["last_date"]
    overlap = overlap_counts(connection, live_providers, history_provider, cutoff_date)
    live_rows_after_cutoff = count_live_rows_after_cutoff(connection, live_providers, cutoff_date)
    latest_saved = max(date_value for date_value in [history["last_date"], live["last_date"]] if date_value) if history["last_date"] or live["last_date"] else ""
    days_old = days_since_latest(latest_saved, today)

    status = "pass"
    if overlap["likely_duplicates"] or overlap["unmatched_old_rows"]:
        status = "warn"
    elif cutoff_date and not live_rows_after_cutoff and days_old is not None and days_old > 3:
        status = "warn"

    note = "Refresh handoff looks clean"
    if not cutoff_date:
        note = "No imported baseline found yet"
    elif overlap["likely_duplicates"]:
        note = "Live refresh has rows that look like imported history"
    elif overlap["unmatched_old_rows"]:
        note = "Live refresh has older rows that did not match the import"
    elif not live_rows_after_cutoff:
        note = "No live rows saved after the import cutoff yet"

    return {
        "label": label,
        "status": status,
        "history_label": history_label,
        "live_label": live_label,
        "history_rows": history["count"],
        "live_rows": live["count"],
        "history_cutoff": cutoff_date,
        "live_latest": live["last_date"],
        "latest_saved": latest_saved,
        "days_since_latest": days_old,
        "live_rows_after_cutoff": live_rows_after_cutoff,
        **overlap,
        "note": note,
    }


def refresh_audits(connection, today=None):  #Builds the two post-refresh audits
    return [
        refresh_audit(
            connection,
            "US history to Plaid",
            "Monarch transactions",
            "monarch-history",
            "Plaid refresh",
            PLAID_PROVIDERS,
            today=today,
        ),
        refresh_audit(
            connection,
            "BNZ CSV to Akahu",
            "BNZ CSV history",
            "bnz-csv-history",
            "Akahu refresh",
            ("akahu-bnz",),
            today=today,
        ),
    ]


def sync_reliability_checks(connection, today=None):  #Builds simple checks for the Sync page
    return [
        provider_duplicate_check(connection),
        history_overlap_check(connection, "Plaid vs Monarch history", PLAID_PROVIDERS, "monarch-history"),
        history_overlap_check(connection, "Akahu vs BNZ CSV history", ("akahu-bnz",), "bnz-csv-history"),
        old_pending_check(connection, today=today),
    ]


def get_sync_status(database_path=DATABASE_PATH, today=None):  #Builds sync status from local data only
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        sources = [
            transaction_source_summary(
                connection,
                "US Plaid",
                PLAID_PROVIDERS,
                "Live US refresh rows. Monarch history is listed separately.",
            ),
            transaction_source_summary(
                connection,
                "BNZ",
                BNZ_PROVIDERS,
                "BNZ CSV baseline plus Akahu refresh rows going forward.",
            ),
            transaction_source_summary(
                connection,
                "Monarch transactions",
                ("monarch-history",),
                "Imported US transaction baseline.",
            ),
            monarch_balance_summary(connection),
            fx_status_summary(connection),
        ]
        checks = sync_reliability_checks(connection, today=today)
        audits = refresh_audits(connection, today=today)
        runs = connection.execute(
            """
            SELECT job_name, status, started_at, COALESCE(finished_at, ''), COALESCE(details, '')
            FROM automation_runs
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()

    return {
        "sources": sources,
        "checks": checks,
        "audits": audits,
        "runs": [
            {
                "job_name": job_name,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "details": details,
            }
            for job_name, status, started_at, finished_at, details in runs
        ],
    }


def print_sync_status(status):  #Prints the same checks without private IDs
    print("Refresh audit")
    for audit in status["audits"]:
        print(f"- {audit['label']}: {audit['status']}")
        print(
            f"  cutoff {audit['history_cutoff'] or 'none'} | "
            f"latest {audit['latest_saved'] or 'none'} | "
            f"duplicates {audit['likely_duplicates']} | "
            f"unmatched old rows {audit['unmatched_old_rows']}"
        )

    print()
    print("Sync reliability checks")
    for check in status["checks"]:
        print(f"- {check['label']}: {check['status']} ({check['value']})")
        print(f"  {check['detail']}")

    print("\nData sources")
    for source in status["sources"]:
        first_date = source["first_date"] or "none"
        last_date = source["last_date"] or "none"
        print(f"- {source['label']}: {source['total']} rows | {first_date} to {last_date}")


def read_arguments():  #Keeps the command simple for PowerShell
    parser = argparse.ArgumentParser(description="Check local sync status without exposing bank IDs")
    return parser.parse_args()


def main():  #Runs the local sync status check
    read_arguments()

    try:
        print_sync_status(get_sync_status())
    except (FileNotFoundError, ValueError, sqlite3.Error) as error:
        print(f"Could not check sync status: {error}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
