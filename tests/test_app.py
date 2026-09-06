import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import app
from scripts.budgets import add_budget, get_budget_progress
from scripts import send_daily_transaction_alert as daily_alert
from scripts.db_init import create_database
from scripts.recurring import classify_frequency, next_monthly_date
from scripts.remote_access import build_tailscale_settings
from scripts.summaries import get_spending_summary, get_spending_summary_between


CONFIGURED_BANK_ACCOUNTS = [
    (
        "Example Everyday",
        "akahu-bnz",
        "Example Bank",
        "checking",
        "NZD",
        0,
        "",
        1,
        "example-everyday-account",
        "asset",
        1,
    ),
    (
        "Example Checking",
        "plaid-production-us",
        "Example Credit Union",
        "checking",
        "USD",
        0,
        "",
        1,
        "example-checking-account",
        "asset",
        2,
    ),
]

TAILSCALE_ORIGIN = "https://finance-laptop.personal-tail.ts.net"
TAILSCALE_USER = "owner@example.com"
TAILSCALE_ACCESS = build_tailscale_settings(TAILSCALE_ORIGIN, TAILSCALE_USER)


class WebAppTests(unittest.TestCase):
    def test_core_helpers_still_match_the_app_workflow(self):
        self.assertEqual(app.get_account_group("checking", "asset"), "Cash")
        self.assertEqual(app.get_account_group("ira", "asset"), "Investments")
        self.assertEqual(app.get_account_group("credit card", "liability"), "Credit cards")
        self.assertEqual(app.get_account_group("loan", "liability"), "Loans")
        self.assertEqual(app.get_timeline({"timeline": ["5y"]}), "5y")
        self.assertEqual(app.get_timeline({"timeline": ["bad"]}), "1m")
        self.assertEqual(app.get_budget_view({}), "month")
        self.assertEqual(app.get_budget_sort({"sort": ["over_budget"]}), "over_budget")
        weekly_dates = [date(2026, 6, 1), date(2026, 6, 8), date(2026, 6, 15)]
        self.assertEqual(classify_frequency(weekly_dates), ("Weekly", 7))
        self.assertEqual(next_monthly_date(date(2026, 6, 22), 15), date(2026, 7, 15))

        self.assertEqual(app.percent_of_income_value(25000, 100000), "25.0")
        self.assertEqual(app.percent_of_income_text(25000, 100000), "25.0% of planned income")
        self.assertEqual(app.percent_of_income_text(25000, 0), "")
        with (
            patch("app.get_planned_income_total", return_value=0),
            patch(
                "app.get_spending_summary",
                return_value={"income_total_minor": 100000},
            ) as summary_mock,
        ):
            self.assertEqual(app.budget_amount_from_form("", "12.5", "NZD"), "125")
        summary_mock.assert_called_once_with(
            "week",
            "NZD",
            database_path=app.DATABASE_PATH,
        )
        with patch("app.get_planned_income_total", return_value=100000):
            self.assertEqual(
                app.budget_amount_from_form(
                    "200",
                    "12.5",
                    "NZD",
                    source="percent",
                ),
                "125",
            )
            self.assertEqual(
                app.budget_amount_from_form(
                    "200",
                    "12.5%",
                    "NZD",
                    source="percent",
                ),
                "125",
            )
        self.assertEqual(app.budget_amount_from_form("0", "12.5", "NZD"), "0")

    def test_write_requests_only_allow_the_local_app(self):
        self.assertTrue(app.read_request_is_allowed("127.0.0.1:8000"))
        self.assertTrue(
            app.write_request_is_allowed(
                "127.0.0.1:8000",
                "http://127.0.0.1:8000",
            )
        )
        self.assertTrue(
            app.write_request_is_allowed(
                "127.0.0.1:8000",
                None,
                "http://127.0.0.1:8000/",
            )
        )
        self.assertFalse(
            app.write_request_is_allowed(
                "127.0.0.1:8000",
                "https://example.com",
                "http://127.0.0.1:8000/",
            )
        )
        self.assertFalse(app.read_request_is_allowed("127.0.0.1:8000.evil.example"))
        handler = app.FinanceHubHandler.__new__(app.FinanceHubHandler)
        saved_headers = {}
        handler.send_header = saved_headers.__setitem__
        handler.send_security_headers()

        self.assertEqual(saved_headers["Cache-Control"], "no-store")
        self.assertEqual(saved_headers["Referrer-Policy"], "same-origin")
        self.assertIn("frame-ancestors 'none'", saved_headers["Content-Security-Policy"])
        self.assertEqual(saved_headers["X-Content-Type-Options"], "nosniff")

        form_values = {"confirmation": ["REFRESH"], "currency": ["NZD"]}
        with (
            patch("app.get_akahu_setup_status", return_value=[("App", True), ("User", True)]),
            patch("app.get_plaid_setup_status", return_value=[("Client", True), ("Item", True)]),
            patch("app.list_accounts", return_value=CONFIGURED_BANK_ACCOUNTS),
            patch("app.refresh_bnz") as refresh_bnz,
            patch("app.refresh_plaid") as refresh_plaid,
        ):
                self.assertEqual(
                    app.refresh_from_form(form_values),
                    "Refresh finished",
                )

        refresh_bnz.assert_called_once_with(None, "REFRESH", app.DATABASE_PATH)
        refresh_plaid.assert_called_once_with(None, "REFRESH", app.DATABASE_PATH)

        partial_plaid_result = (
            None,
            "2026-09-06",
            12,
            (3, 0, 4, 1, 0),
            2,
            5,
            "",
            [
                "Plaid connection 3 (Example Credit Union) needs bank "
                "sign-in. Run python scripts\\plaid_link.py update --connection 3 "
                "on this laptop"
            ],
        )
        with (
            patch("app.get_akahu_setup_status", return_value=[("App", True), ("User", True)]),
            patch("app.get_plaid_setup_status", return_value=[("Client", True), ("Item", True)]),
            patch("app.list_accounts", return_value=CONFIGURED_BANK_ACCOUNTS),
            patch("app.refresh_bnz", return_value=(None,) * 8),
            patch("app.refresh_plaid", return_value=partial_plaid_result),
        ):
            partial_message = app.refresh_from_form(form_values)

        self.assertIn("Refresh finished", partial_message)
        self.assertIn("US partially refreshed", partial_message)
        self.assertIn("Example Credit Union", partial_message)
        self.assertIn("update --connection 3", partial_message)

        with (
            patch("app.get_akahu_setup_status", return_value=[("App", True), ("User", True)]),
            patch("app.get_plaid_setup_status", return_value=[("Client", True), ("Item", True)]),
            patch("app.list_accounts", return_value=CONFIGURED_BANK_ACCOUNTS),
            patch("app.refresh_bnz", side_effect=ValueError("connection failed")),
            patch("app.refresh_plaid", return_value=partial_plaid_result),
            self.assertRaises(ValueError) as combined_error,
        ):
            app.refresh_from_form(form_values)

        self.assertIn("BNZ: connection failed", str(combined_error.exception))
        self.assertIn("Example Credit Union", str(combined_error.exception))

        with (
            patch("app.get_akahu_setup_status", return_value=[("App", True), ("User", True)]),
            patch("app.get_plaid_setup_status", return_value=[("Client", True), ("Item", True)]),
            patch("app.list_accounts", return_value=CONFIGURED_BANK_ACCOUNTS),
            patch("app.refresh_bnz", side_effect=ValueError("connection failed")),
            patch("app.refresh_plaid") as refresh_plaid,
            self.assertRaisesRegex(ValueError, "BNZ: connection failed"),
        ):
            app.refresh_from_form(form_values)

        refresh_plaid.assert_called_once()

    def test_tailscale_request_guards_pin_the_host_user_and_origin(self):
        self.assertTrue(
            app.read_request_is_allowed(
                TAILSCALE_ACCESS.host,
                TAILSCALE_USER,
                TAILSCALE_ACCESS,
            )
        )
        self.assertTrue(
            app.write_request_is_allowed(
                TAILSCALE_ACCESS.host,
                TAILSCALE_ORIGIN,
                tailscale_user_login=TAILSCALE_USER,
                remote_access=TAILSCALE_ACCESS,
            )
        )
        self.assertTrue(
            app.write_request_is_allowed(
                TAILSCALE_ACCESS.host,
                None,
                f"{TAILSCALE_ORIGIN}/transactions?review=not_reviewed",
                TAILSCALE_USER,
                TAILSCALE_ACCESS,
            )
        )

        for user_login in (None, "attacker@example.com"):
            with self.subTest(user_login=user_login):
                self.assertFalse(
                    app.read_request_is_allowed(
                        TAILSCALE_ACCESS.host,
                        user_login,
                        TAILSCALE_ACCESS,
                    )
                )
                self.assertFalse(
                    app.write_request_is_allowed(
                        TAILSCALE_ACCESS.host,
                        TAILSCALE_ORIGIN,
                        tailscale_user_login=user_login,
                        remote_access=TAILSCALE_ACCESS,
                    )
                )

        host_lookalikes = (
            f"{TAILSCALE_ACCESS.host}.evil.example",
            f"evil-{TAILSCALE_ACCESS.host}",
            "127.0.0.1:8000.evil.example",
        )
        for host in host_lookalikes:
            with self.subTest(host=host):
                self.assertFalse(
                    app.read_request_is_allowed(host, TAILSCALE_USER, TAILSCALE_ACCESS)
                )
                self.assertFalse(
                    app.write_request_is_allowed(
                        host,
                        TAILSCALE_ORIGIN,
                        tailscale_user_login=TAILSCALE_USER,
                        remote_access=TAILSCALE_ACCESS,
                    )
                )

        origin_lookalikes = (
            f"{TAILSCALE_ORIGIN}.evil.example",
            "https://evil-finance-laptop.personal-tail.ts.net",
            f"{TAILSCALE_ORIGIN}:443",
        )
        for origin in origin_lookalikes:
            with self.subTest(origin=origin):
                self.assertFalse(
                    app.write_request_is_allowed(
                        TAILSCALE_ACCESS.host,
                        origin,
                        tailscale_user_login=TAILSCALE_USER,
                        remote_access=TAILSCALE_ACCESS,
                    )
                )

        self.assertFalse(
            app.write_request_is_allowed(
                TAILSCALE_ACCESS.host,
                "https://attacker.example",
                f"{TAILSCALE_ORIGIN}/transactions",
                TAILSCALE_USER,
                TAILSCALE_ACCESS,
            )
        )

    def test_unauthorized_remote_get_does_not_dispatch(self):
        handler = app.FinanceHubHandler.__new__(app.FinanceHubHandler)
        handler.path = "/api/transactions?currency=USD"
        handler.headers = {"Host": TAILSCALE_ACCESS.host}
        handler.write_json = Mock()

        with (
            patch.object(app, "REMOTE_ACCESS_SETTINGS", TAILSCALE_ACCESS),
            patch.object(app, "get_api_data") as get_api_data,
        ):
            handler.do_GET()

        get_api_data.assert_not_called()
        handler.write_json.assert_called_once_with(
            403,
            {"ok": False, "error": "Request blocked"},
        )

    def test_refresh_all_skips_unconfigured_optional_providers(self):
        form_values = {"confirmation": ["REFRESH"], "currency": ["NZD"]}
        configured = [("Credential", True), ("Connection", True)]
        not_configured = [("Credential", False), ("Connection", False)]

        with self.assertRaisesRegex(ValueError, "Refresh confirmation must be REFRESH"):
            app.refresh_from_form({"confirmation": [""], "currency": ["NZD"]})

        with (
            patch("app.get_akahu_setup_status", return_value=configured),
            patch("app.get_plaid_setup_status", return_value=not_configured),
            patch("app.list_accounts", return_value=CONFIGURED_BANK_ACCOUNTS[:1]),
            patch("app.refresh_bnz", return_value=(None, None, 0, 0, 0, 0, 0, "")) as refresh_bnz,
            patch("app.refresh_plaid") as refresh_plaid,
        ):
            result = app.refresh_from_form(form_values)

        self.assertEqual(
            result,
            "Refresh finished: US skipped because it is not configured",
        )
        refresh_bnz.assert_called_once_with(None, "REFRESH", app.DATABASE_PATH)
        refresh_plaid.assert_not_called()

        with (
            patch("app.get_akahu_setup_status", return_value=not_configured),
            patch("app.get_plaid_setup_status", return_value=not_configured),
            patch("app.list_accounts", return_value=[]),
            patch("app.refresh_bnz") as refresh_bnz,
            patch("app.refresh_plaid") as refresh_plaid,
        ):
            result = app.refresh_from_form(form_values)

        self.assertEqual(
            result,
            "No bank connections are configured; nothing was refreshed",
        )
        refresh_bnz.assert_not_called()
        refresh_plaid.assert_not_called()

        with (
            patch("app.get_akahu_setup_status", return_value=configured),
            patch("app.get_plaid_setup_status", return_value=configured),
            patch("app.list_accounts", return_value=[]),
            patch("app.refresh_bnz") as refresh_bnz,
            patch("app.refresh_plaid") as refresh_plaid,
        ):
            result = app.refresh_from_form(form_values)

        self.assertEqual(
            result,
            "No bank connections are configured; nothing was refreshed",
        )
        refresh_bnz.assert_not_called()
        refresh_plaid.assert_not_called()

    def test_budget_amounts_inherit_to_future_periods_only(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO categories (name, group_name)
                    VALUES (?, ?)
                    """,
                    ("Groceries", "Variable Expenses"),
                )
                connection.commit()

            add_budget("Groceries", "2026-07-06", "100", "NZD", database_path=database_path)
            current = get_budget_progress("2026-07-06", database_path=database_path)
            future = get_budget_progress("2026-07-13", database_path=database_path)
            prior = get_budget_progress("2026-06-29", database_path=database_path)

            self.assertEqual(current[0][4], 10000)
            self.assertEqual(future[0][4], 10000)
            self.assertEqual(prior, [])

            add_budget("Groceries", "2026-07-20", "50", "NZD", database_path=database_path)
            unchanged_future = get_budget_progress("2026-07-13", database_path=database_path)
            edited_future = get_budget_progress("2026-07-20", database_path=database_path)

            self.assertEqual(unchanged_future[0][4], 10000)
            self.assertEqual(edited_future[0][4], 5000)

    def test_budget_rows_include_planned_income_percent_input(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO categories (name, group_name, is_income)
                    VALUES (?, ?, ?)
                    """,
                    ("Pay", "Income", 1),
                )
                connection.execute(
                    """
                    INSERT INTO categories (name, group_name)
                    VALUES (?, ?)
                    """,
                    ("Groceries", "Variable Expenses"),
                )
                connection.commit()

            add_budget("Pay", "2026-07-01", "5000", "NZD", database_path=database_path)
            add_budget("Groceries", "2026-07-01", "1250", "NZD", database_path=database_path)

            with patch.object(app, "DATABASE_PATH", database_path):
                groups, _, _ = app.api_budget_rows(
                    "NZD",
                    "2026-07-01",
                    "planned",
                    "month",
                    date(2026, 7, 18),
                )

            rows = {
                row["category"]: row
                for group in groups
                for row in group["rows"]
            }
            self.assertEqual(rows["Groceries"]["percent_of_income_input"], "25.0")
            self.assertEqual(rows["Groceries"]["percent_of_income"], "25.0% of planned income")
            groups_by_name = {group["name"]: group for group in groups}
            self.assertEqual(
                groups_by_name["Variable Expenses"]["planned_percent_of_income"],
                "25.0",
            )
            self.assertEqual(groups_by_name["Income"]["planned_percent_of_income"], "100.0")

    def test_account_rename_api_updates_account_name(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    ("manual", "checking-1", "Old Checking", "USD"),
                )
                account_id = cursor.lastrowid
                connection.commit()

            with patch.object(app, "DATABASE_PATH", database_path):
                result = app.post_api_data(
                    "/api/accounts/rename",
                    {"id": account_id, "name": "Main Checking"},
                )

            with closing(sqlite3.connect(database_path)) as connection:
                saved_name = connection.execute(
                    "SELECT display_name FROM accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()[0]

        self.assertEqual(result, {"ok": True})
        self.assertEqual(saved_name, "Main Checking")

    def test_transaction_split_feeds_category_summaries(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                account_cursor = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    ("manual", "checking-1", "Checking", "USD"),
                )
                account_id = account_cursor.lastrowid
                for category in ("Groceries", "Home", "Uncategorized"):
                    connection.execute(
                        """
                        INSERT INTO categories (name, group_name)
                        VALUES (?, ?)
                        """,
                        (category, "Variable Expenses"),
                    )
                category_id = connection.execute(
                    "SELECT id FROM categories WHERE name = ?",
                    ("Uncategorized",),
                ).fetchone()[0]
                transaction_cursor = connection.execute(
                    """
                    INSERT INTO transactions (
                        provider,
                        provider_transaction_id,
                        account_id,
                        posted_date,
                        description_raw,
                        category_id,
                        original_currency,
                        original_amount_minor,
                        fx_rate_to_nzd,
                        fx_rate_to_usd,
                        fx_rate_source,
                        fx_rate_date,
                        amount_nzd_minor_fixed,
                        amount_usd_minor_fixed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "manual",
                        "tx-1",
                        account_id,
                        "2026-06-20",
                        "Split shop",
                        category_id,
                        "USD",
                        -10000,
                        "1.70",
                        "1",
                        "test",
                        "2026-06-20",
                        -17000,
                        -10000,
                    ),
                )
                transaction_id = transaction_cursor.lastrowid
                connection.commit()

            with patch.object(app, "DATABASE_PATH", database_path):
                result = app.post_api_data(
                    "/api/transactions/split",
                    {
                        "id": transaction_id,
                        "first_category": "Groceries",
                        "first_amount": "60",
                        "second_category": "Home",
                        "second_amount": "40",
                        "currency": "USD",
                    },
                )
                rows, total_count = app.api_transaction_rows(
                    {"category": ["Groceries"]},
                    "USD",
                )

            with closing(sqlite3.connect(database_path)) as connection:
                grocery_id = connection.execute(
                    "SELECT id FROM categories WHERE name = ?",
                    ("Groceries",),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO transactions (
                        provider,
                        provider_transaction_id,
                        account_id,
                        posted_date,
                        description_raw,
                        category_id,
                        original_currency,
                        original_amount_minor,
                        fx_rate_to_nzd,
                        fx_rate_to_usd,
                        fx_rate_source,
                        fx_rate_date,
                        amount_nzd_minor_fixed,
                        amount_usd_minor_fixed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "manual",
                        "tx-2",
                        account_id,
                        "2026-06-21",
                        "Grocery payback",
                        grocery_id,
                        "USD",
                        2500,
                        "1.70",
                        "1",
                        "test",
                        "2026-06-21",
                        4250,
                        2500,
                    ),
                )
                connection.commit()

            add_budget("Groceries", "2026-06-15", "60", "USD", database_path=database_path)
            add_budget("Home", "2026-06-15", "40", "USD", database_path=database_path)
            progress = get_budget_progress("2026-06-15", database_path=database_path, display_currency="USD")
            summary = get_spending_summary("month", "USD", today=date(2026, 6, 20), database_path=database_path)
            range_summary = get_spending_summary_between("2026-06-01", "2026-06-30", "USD", database_path=database_path)
            spending = {row["category_name"]: row["amount_minor"] for row in summary["spending_by_category"]}
            range_spending = {row["category_name"]: row["amount_minor"] for row in range_summary["spending_by_category"]}
            budget_actuals = {row[2]: row[8] for row in progress}

        self.assertEqual(result, {"ok": True})
        self.assertEqual(total_count, 1)
        self.assertEqual(rows[0]["category"], "Groceries")
        self.assertTrue(rows[0]["is_split_line"])
        self.assertEqual(rows[0]["amount_minor"], -6000)
        self.assertEqual(rows[0]["splits"][0]["category"], "Groceries")
        self.assertEqual(spending["Groceries"], 3500)
        self.assertEqual(spending["Home"], 4000)
        self.assertEqual(range_spending["Groceries"], 3500)
        self.assertEqual(range_spending["Home"], 4000)
        self.assertEqual(budget_actuals["Groceries"], 3500)
        self.assertEqual(budget_actuals["Home"], 4000)

    def test_transaction_split_supports_more_than_two_lines(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                account_cursor = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    ("manual", "checking-1", "Checking", "USD"),
                )
                account_id = account_cursor.lastrowid
                for category in ("Groceries", "Home", "Hobbies"):
                    connection.execute(
                        """
                        INSERT INTO categories (name, group_name)
                        VALUES (?, ?)
                        """,
                        (category, "Variable Expenses"),
                    )
                transaction_cursor = connection.execute(
                    """
                    INSERT INTO transactions (
                        provider,
                        provider_transaction_id,
                        account_id,
                        posted_date,
                        description_raw,
                        original_currency,
                        original_amount_minor,
                        fx_rate_to_nzd,
                        fx_rate_to_usd,
                        fx_rate_source,
                        fx_rate_date,
                        amount_nzd_minor_fixed,
                        amount_usd_minor_fixed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "manual",
                        "tx-1",
                        account_id,
                        "2026-06-20",
                        "Three way split",
                        "USD",
                        -10000,
                        "1.70",
                        "1",
                        "test",
                        "2026-06-20",
                        -17000,
                        -10000,
                    ),
                )
                transaction_id = transaction_cursor.lastrowid
                connection.commit()

            with patch.object(app, "DATABASE_PATH", database_path):
                result = app.post_api_data(
                    "/api/transactions/split",
                    {
                        "id": transaction_id,
                        "currency": "USD",
                        "splits": [
                            {"category": "Groceries", "amount": "25"},
                            {"category": "Home", "amount": "35"},
                            {"category": "Hobbies", "amount": "40"},
                        ],
                    },
                )
                rows, total_count = app.api_transaction_rows({}, "USD")

            self.assertEqual(result, {"ok": True})
            self.assertEqual(total_count, 1)
            self.assertEqual([row["category"] for row in rows], ["Groceries", "Home", "Hobbies"])
            self.assertEqual([row["amount_minor"] for row in rows], [-2500, -3500, -4000])
            self.assertTrue(all(row["is_split_line"] for row in rows))

    def test_accounts_api_uses_monarch_balance_history(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO fx_rates (base_currency, quote_currency, rate, rate_date, source)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("USD", "NZD", "1.70", "2026-06-20", "test"),
                )
                connection.execute(
                    """
                    INSERT INTO account_balance_history (
                        source,
                        account_name,
                        balance_date,
                        currency,
                        balance_minor
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("monarch-history", "Old account", "2026-06-20", "USD", 10000),
                )
                connection.commit()

            with patch.object(app, "DATABASE_PATH", database_path):
                series = app.api_net_worth_series("NZD", "all")

        self.assertEqual(series, [{"label": "Jun 20", "date": "2026-06-20", "value_minor": 17000}])

    def test_net_worth_series_carries_account_balances_forward(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                checking_id = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    ("manual", "checking-1", "Checking", "USD"),
                ).lastrowid
                savings_id = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    ("manual", "savings-1", "Savings", "USD"),
                ).lastrowid
                connection.executemany(
                    """
                    INSERT INTO account_balance_history (
                        source,
                        account_name,
                        account_id,
                        balance_date,
                        currency,
                        balance_minor
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("manual", "Checking", checking_id, "2026-06-30", "USD", 10000),
                        ("manual", "Savings", savings_id, "2026-06-30", "USD", 90000),
                        ("manual", "Checking", checking_id, "2026-07-16", "USD", 12000),
                    ],
                )
                connection.commit()

            class FixedDate(date):
                @classmethod
                def today(cls):
                    return cls(2026, 7, 16)

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "date", FixedDate),
            ):
                series = app.api_net_worth_series("USD", "1m")

        self.assertEqual(len(series), 16)
        self.assertEqual(series[0], {"label": "Jul 1", "date": "2026-07-01", "value_minor": 100000})
        self.assertEqual(series[14], {"label": "Jul 15", "date": "2026-07-15", "value_minor": 100000})
        self.assertEqual(series[-1], {"label": "Jul 16", "date": "2026-07-16", "value_minor": 102000})

    def test_net_worth_series_fills_every_day_for_longer_timelines(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                account_id = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    ("manual", "checking-1", "Checking", "USD"),
                ).lastrowid
                connection.executemany(
                    """
                    INSERT INTO account_balance_history (
                        source,
                        account_name,
                        account_id,
                        balance_date,
                        currency,
                        balance_minor
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("manual", "Checking", account_id, "2026-04-30", "USD", 10000),
                        ("manual", "Checking", account_id, "2026-07-16", "USD", 12000),
                    ],
                )
                connection.commit()

            class FixedDate(date):
                @classmethod
                def today(cls):
                    return cls(2026, 7, 16)

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "date", FixedDate),
            ):
                series = app.api_net_worth_series("USD", "3m")

        self.assertEqual(len(series), 77)
        self.assertEqual(series[0], {"label": "May 1", "date": "2026-05-01", "value_minor": 10000})
        self.assertEqual(series[75], {"label": "Jul 15", "date": "2026-07-15", "value_minor": 10000})
        self.assertEqual(series[-1], {"label": "Jul 16", "date": "2026-07-16", "value_minor": 12000})

    def test_net_worth_series_reconstructs_missing_days_from_transactions(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                account_id = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    ("manual", "checking-1", "Checking", "USD"),
                ).lastrowid
                connection.executemany(
                    """
                    INSERT INTO account_balance_history (
                        source,
                        account_name,
                        account_id,
                        balance_date,
                        currency,
                        balance_minor
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("manual", "Checking", account_id, "2026-07-01", "USD", 10000),
                        ("manual", "Checking", account_id, "2026-07-04", "USD", 15000),
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO transactions (
                        provider,
                        provider_transaction_id,
                        account_id,
                        posted_date,
                        description_raw,
                        original_currency,
                        original_amount_minor,
                        fx_rate_to_nzd,
                        fx_rate_to_usd,
                        fx_rate_source,
                        fx_rate_date,
                        amount_nzd_minor_fixed,
                        amount_usd_minor_fixed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("manual", "tx-1", account_id, "2026-07-02", "Groceries", "USD", -100, "1.70", "1", "test", "2026-07-02", -170, -100),
                        ("manual", "tx-2", account_id, "2026-07-03", "Paycheck", "USD", 200, "1.70", "1", "test", "2026-07-03", 340, 200),
                    ],
                )
                connection.commit()

            class FixedDate(date):
                @classmethod
                def today(cls):
                    return cls(2026, 7, 16)

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "date", FixedDate),
            ):
                series = app.api_net_worth_series("USD", "1m")

        self.assertEqual(
            series,
            [
                {"label": "Jul 1", "date": "2026-07-01", "value_minor": 10000},
                {"label": "Jul 2", "date": "2026-07-02", "value_minor": 9900},
                {"label": "Jul 3", "date": "2026-07-03", "value_minor": 10100},
                {"label": "Jul 4", "date": "2026-07-04", "value_minor": 15000},
            ],
        )

    def test_net_worth_series_does_not_double_count_legacy_history_after_linked_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                account_id = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    ("manual", "checking-1", "Checking", "USD"),
                ).lastrowid
                connection.executemany(
                    """
                    INSERT INTO account_balance_history (
                        source,
                        account_name,
                        account_id,
                        balance_date,
                        currency,
                        balance_minor
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("monarch-history", "Checking", None, "2026-06-30", "USD", 10000),
                        ("manual", "Checking", account_id, "2026-07-16", "USD", 12000),
                    ],
                )
                connection.commit()

            class FixedDate(date):
                @classmethod
                def today(cls):
                    return cls(2026, 7, 16)

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "date", FixedDate),
            ):
                series = app.api_net_worth_series("USD", "1m")

        self.assertEqual(len(series), 16)
        self.assertEqual(series[0], {"label": "Jul 1", "date": "2026-07-01", "value_minor": 10000})
        self.assertEqual(series[14], {"label": "Jul 15", "date": "2026-07-15", "value_minor": 10000})
        self.assertEqual(series[-1], {"label": "Jul 16", "date": "2026-07-16", "value_minor": 12000})

    def test_net_worth_series_latest_point_uses_current_account_balances(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                checking_id = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency,
                        current_balance_minor,
                        balance_as_of
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("manual", "checking-1", "Checking", "USD", 12000, "2026-07-16"),
                ).lastrowid
                connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency,
                        current_balance_minor,
                        balance_as_of
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("manual", "savings-1", "Savings", "USD", 18000, "2026-07-16"),
                )
                connection.execute(
                    """
                    INSERT INTO account_balance_history (
                        source,
                        account_name,
                        account_id,
                        balance_date,
                        currency,
                        balance_minor
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    ("manual", "Checking", checking_id, "2026-07-16", "USD", 12000),
                )
                connection.commit()

            class FixedDate(date):
                @classmethod
                def today(cls):
                    return cls(2026, 7, 16)

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "date", FixedDate),
            ):
                series = app.api_net_worth_series("USD", "1m")

        self.assertEqual(series[-1], {"label": "Jul 16", "date": "2026-07-16", "value_minor": 30000})

    def test_net_worth_range_uses_latest_imported_balance_date(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                connection.executemany(
                    """
                    INSERT INTO account_balance_history (
                        source,
                        account_name,
                        balance_date,
                        currency,
                        balance_minor
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        ("monarch-history", "Old account", "2026-05-31", "USD", 10000),
                        ("monarch-history", "Old account", "2026-06-20", "USD", 20000),
                    ],
                )
                connection.commit()

            class FixedDate(date):
                @classmethod
                def today(cls):
                    return cls(2026, 6, 20)

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "date", FixedDate),
            ):
                series = app.api_net_worth_series("USD", "1m")

        self.assertEqual(len(series), 20)
        self.assertEqual(series[0], {"label": "Jun 1", "date": "2026-06-01", "value_minor": 10000})
        self.assertEqual(series[18], {"label": "Jun 19", "date": "2026-06-19", "value_minor": 10000})
        self.assertEqual(series[-1], {"label": "Jun 20", "date": "2026-06-20", "value_minor": 20000})

    def test_dashboard_budget_summary_uses_current_month(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch("app.get_rate_note", return_value="test rate"),
                patch("app.get_budget_progress", return_value=[]) as progress_mock,
                patch("app.api_budget_summary", return_value={"period": "month"}) as budget_summary_mock,
            ):
                result = app.get_api_data("/api/dashboard", {"currency": ["USD"]})

        current_month = date.today().replace(day=1).isoformat()
        progress_mock.assert_called_once_with(
            week_start=current_month,
            database_path=database_path,
            display_currency="USD",
            budget_view="month",
        )
        self.assertEqual(budget_summary_mock.call_args.args[1]["period"], "month")
        self.assertEqual(result["budget_summary"], {"period": "month"})

    def test_sync_status_uses_aggregate_data_only(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency,
                        current_balance_minor
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "plaid-production-us",
                        "example-account-id",
                        "Example Checking",
                        "USD",
                        10000,
                    ),
                )
                account_id = connection.execute("SELECT id FROM accounts").fetchone()[0]
                for provider, provider_id, description in (
                    ("monarch-history", "example-history-id", "Example Coffee Shop"),
                    ("plaid-production-us", "example-transaction-id", "Example Coffee Shop"),
                ):
                    connection.execute(
                        """
                        INSERT INTO transactions (
                            provider,
                            provider_transaction_id,
                            account_id,
                            posted_date,
                            description_raw,
                            original_currency,
                            original_amount_minor,
                            fx_rate_to_nzd,
                            fx_rate_to_usd,
                            fx_rate_source,
                            fx_rate_date,
                            amount_nzd_minor_fixed,
                            amount_usd_minor_fixed
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            provider,
                            provider_id,
                            account_id,
                            "2026-06-20",
                            description,
                            "USD",
                            -500,
                            "1.70",
                            "1",
                            "test",
                            "2026-06-20",
                            -850,
                            -500,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO account_balance_history (source, account_name, balance_date, currency, balance_minor)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "monarch-history",
                        "Example historical checking",
                        "2026-06-20",
                        "USD",
                        10000,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO fx_rates (base_currency, quote_currency, rate, rate_date, source)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    ("USD", "NZD", "1.70", "2026-06-20", "test"),
                )
                connection.execute(
                    """
                    INSERT INTO automation_runs (job_name, status, finished_at, details)
                    VALUES (?, ?, CURRENT_TIMESTAMP, ?)
                    """,
                    ("plaid_production_refresh", "success", "1 added"),
                )
                connection.commit()

            with patch.object(app, "DATABASE_PATH", database_path):
                status = app.get_api_data("/api/sync-status", {})

        status_text = str(status)
        self.assertIn("US Plaid", status_text)
        self.assertIn("Monarch balances", status_text)
        self.assertIn("Plaid vs Monarch history", status_text)
        self.assertEqual(status["checks"][1]["value"], 1)
        self.assertEqual(status["audits"][0]["label"], "US history to Plaid")
        self.assertEqual(status["audits"][0]["likely_duplicates"], 1)
        self.assertNotIn("example-account-id", status_text)
        self.assertNotIn("example-transaction-id", status_text)
        self.assertNotIn("example-history-id", status_text)

    def test_daily_alert_preview_uses_safe_display_data(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency,
                        current_balance_minor
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "plaid-production-us",
                        "example-account-id",
                        "Example Checking",
                        "USD",
                        10000,
                    ),
                )
                account_id = connection.execute("SELECT id FROM accounts").fetchone()[0]
                category_cursor = connection.execute(
                    """
                    INSERT INTO categories (name, group_name)
                    VALUES (?, ?)
                    """,
                    ("Uncategorized", "Holding"),
                )
                category_id = category_cursor.lastrowid
                connection.execute(
                    """
                    INSERT INTO transactions (
                        provider,
                        provider_transaction_id,
                        account_id,
                        posted_date,
                        description_raw,
                        merchant_clean,
                        category_id,
                        original_currency,
                        original_amount_minor,
                        fx_rate_to_nzd,
                        fx_rate_to_usd,
                        fx_rate_source,
                        fx_rate_date,
                        amount_nzd_minor_fixed,
                        amount_usd_minor_fixed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "plaid-production-us",
                        "example-transaction-id",
                        account_id,
                        "2026-06-20",
                        "Example coffee raw text",
                        "Example Coffee Shop",
                        category_id,
                        "USD",
                        -500,
                        "1.70",
                        "1",
                        "test",
                        "2026-06-20",
                        -850,
                        -500,
                    ),
                )
                connection.commit()

            with patch.object(app, "DATABASE_PATH", database_path):
                preview = app.get_api_data(
                    "/api/daily-alert-preview",
                    {"currency": ["NZD"], "hours": ["24"]},
                )

        preview_text = str(preview)
        self.assertEqual(preview["transaction_count"], 1)
        self.assertEqual(preview["needs_review_count"], 1)
        self.assertEqual(preview["rows"][0]["merchant"], "Example Coffee Shop")
        self.assertEqual(preview["rows"][0]["display_amount"], "-$8.50 NZD")

        email_body = daily_alert.build_email_body(preview)
        self.assertIn("Needs review: 1", email_body)
        self.assertIn("Example Coffee Shop", email_body)
        self.assertIn("-$8.50 NZD", email_body)
        self.assertNotIn("Checking", email_body)
        self.assertNotIn("example-account-id", preview_text)
        self.assertNotIn("example-transaction-id", preview_text)


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
