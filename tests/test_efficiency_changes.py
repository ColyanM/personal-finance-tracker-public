import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import app
from scripts.db_init import create_database


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 16)


class EfficiencyChangeTests(unittest.TestCase):
    def test_finite_net_worth_range_loads_compact_per_account_opening_state(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                account_a = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES ('manual', 'account-a', 'Account A', 'USD')
                    """
                ).lastrowid
                account_b = connection.execute(
                    """
                    INSERT INTO accounts (
                        provider,
                        provider_account_id,
                        display_name,
                        native_currency
                    )
                    VALUES ('manual', 'account-b', 'Account B', 'USD')
                    """
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
                    VALUES ('manual', ?, ?, ?, 'USD', ?)
                    """,
                    [
                        ("Account A", account_a, "2026-06-15", 10000),
                        ("Account A", account_a, "2026-06-30", 12000),
                        ("Account B", account_b, "2026-06-20", 30000),
                        ("Account A", account_a, "2026-07-10", 13000),
                    ],
                )
                connection.commit()

                compact_rows = app.load_net_worth_history_rows(
                    connection,
                    "2026-07-01",
                    "2026-07-16",
                )
                index_names = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA index_list(account_balance_history)"
                    ).fetchall()
                }

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "date", FixedDate),
            ):
                series = app.api_net_worth_series("USD", "1m")

        self.assertEqual(len(compact_rows), 3)
        self.assertEqual(
            {row[3] for row in compact_rows},
            {"2026-06-20", "2026-06-30", "2026-07-10"},
        )
        self.assertIn("idx_balance_history_linked_opening", index_names)
        self.assertIn("idx_balance_history_legacy_opening", index_names)
        self.assertEqual(
            series[0],
            {"label": "Jul 1", "date": "2026-07-01", "value_minor": 42000},
        )
        self.assertEqual(
            series[9],
            {"label": "Jul 10", "date": "2026-07-10", "value_minor": 43000},
        )

    def test_main_routes_leave_net_worth_series_to_dedicated_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)
            expected_series = [
                {"label": "Jul 1", "date": "2026-07-01", "value_minor": 10000}
            ]

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(
                    app,
                    "get_saved_rate",
                    return_value=(Decimal("1"), "2026-07-16", "test"),
                ),
                patch.object(
                    app,
                    "api_net_worth_series",
                    return_value=expected_series,
                ) as series_mock,
            ):
                dashboard = app.get_api_data("/api/dashboard", {"currency": ["NZD"]})
                accounts = app.get_api_data("/api/accounts", {"currency": ["NZD"]})
                series_mock.assert_not_called()
                dedicated = app.get_api_data(
                    "/api/net-worth-series",
                    {"currency": ["NZD"], "timeline": ["3m"]},
                )

        self.assertNotIn("net_worth_series", dashboard)
        self.assertNotIn("net_worth_series", accounts)
        self.assertEqual(dedicated["net_worth_series"], expected_series)
        series_mock.assert_called_once_with("NZD", "3m")

    def test_dashboard_skips_unused_summary_and_monthly_series_work(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "get_rate_note", return_value="test rate"),
                patch.object(app, "api_summary", return_value={"period": "month"}) as summary_mock,
                patch.object(app, "api_budget_summary", return_value={}),
                patch.object(app, "api_monthly_series") as monthly_series_mock,
            ):
                dashboard = app.get_api_data("/api/dashboard", {"currency": ["NZD"]})

        summary_mock.assert_called_once_with("month", "NZD")
        monthly_series_mock.assert_not_called()
        self.assertNotIn("week", dashboard)
        self.assertNotIn("year", dashboard)
        self.assertNotIn("monthly_series", dashboard)

    def test_main_starts_server_before_background_fx_refresh(self):
        events = []
        thread_arguments = {}

        class FakeServer:
            def serve_forever(self):
                events.append("serve")

            def server_close(self):
                events.append("close")

        class FakeThread:
            def start(self):
                events.append("thread_start")

        def make_server(*_args, **_kwargs):
            events.append("server_create")
            return FakeServer()

        def make_thread(**kwargs):
            events.append("thread_create")
            thread_arguments.update(kwargs)
            return FakeThread()

        with (
            patch.object(app, "ThreadingHTTPServer", side_effect=make_server),
            patch.object(app, "Thread", side_effect=make_thread),
            patch.object(app, "refresh_rate_on_start") as refresh_mock,
            patch("builtins.print"),
        ):
            app.main()

        self.assertEqual(
            events,
            ["server_create", "thread_create", "thread_start", "serve", "close"],
        )
        self.assertIs(thread_arguments["target"], refresh_mock)
        self.assertEqual(thread_arguments["name"], "finance-hub-fx-refresh")
        self.assertTrue(thread_arguments["daemon"])
        refresh_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
