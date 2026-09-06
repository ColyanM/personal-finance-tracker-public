import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

import app
from scripts import summaries
from scripts.db_init import create_database
from scripts.summaries import get_daily_spending_totals_between


class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 15)


class LegacyDailyTotals:
    def __init__(self, currency):
        self.currency = currency

    def get(self, posted_date, default=0):
        selected_date = date.fromisoformat(posted_date)
        return app.spending_total_between(selected_date, selected_date, self.currency)


class SpendingComparisonTests(unittest.TestCase):
    def create_sample_database(self, database_path):
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
                VALUES ('manual', 'checking-1', 'Checking', 'NZD')
                """
            ).lastrowid
            categories = {}
            for name, group_name, is_income, is_transfer in (
                ("Groceries", "Variable Expenses", 0, 0),
                ("Home", "Variable Expenses", 0, 0),
                ("Investing", "Investments", 0, 0),
                ("Salary", "Income", 1, 0),
                ("Account transfer", "Transfers", 0, 1),
            ):
                categories[name] = connection.execute(
                    """
                    INSERT INTO categories (name, group_name, is_income, is_transfer)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, group_name, is_income, is_transfer),
                ).lastrowid

            transactions = (
                ("prior-week", "2026-07-06", "Groceries", -200),
                ("groceries", "2026-07-13", "Groceries", -1000),
                ("refund", "2026-07-13", "Groceries", 250),
                ("investment", "2026-07-13", "Investing", -4000),
                ("salary", "2026-07-13", "Salary", 10000),
                ("transfer", "2026-07-13", "Account transfer", -500),
                ("split-parent", "2026-07-14", "Groceries", -800),
            )
            transaction_ids = {}
            for provider_id, posted_date, category, amount_minor in transactions:
                transaction_ids[provider_id] = connection.execute(
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
                    VALUES ('manual', ?, ?, ?, ?, ?, 'NZD', ?, '1', '1', 'test', ?, ?, ?)
                    """,
                    (
                        provider_id,
                        account_id,
                        posted_date,
                        provider_id,
                        categories[category],
                        amount_minor,
                        posted_date,
                        amount_minor,
                        amount_minor,
                    ),
                ).lastrowid

            connection.executemany(
                """
                INSERT INTO transaction_splits (
                    transaction_id,
                    split_order,
                    category_id,
                    amount_nzd_minor_fixed,
                    amount_usd_minor_fixed
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    (transaction_ids["split-parent"], 1, categories["Groceries"], -300, -300),
                    (transaction_ids["split-parent"], 2, categories["Home"], -500, -500),
                ),
            )
            connection.commit()

    def test_daily_totals_match_summary_rules(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            self.create_sample_database(database_path)

            totals = get_daily_spending_totals_between(
                "2026-07-01",
                "2026-07-31",
                "NZD",
                database_path=database_path,
            )

        self.assertEqual(
            totals,
            {
                "2026-07-06": 200,
                "2026-07-13": 750,
                "2026-07-14": 800,
            },
        )

    def test_comparison_response_matches_legacy_queries_using_one_connection(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            self.create_sample_database(database_path)
            real_connect = sqlite3.connect

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "date", FixedDate),
                patch.object(summaries.sqlite3, "connect", wraps=real_connect) as connect_mock,
            ):
                optimized = app.api_spending_comparisons("NZD")

            self.assertEqual(connect_mock.call_count, 1)

            with (
                patch.object(app, "DATABASE_PATH", database_path),
                patch.object(app, "date", FixedDate),
                patch.object(
                    app,
                    "get_daily_spending_totals_between",
                    return_value=LegacyDailyTotals("NZD"),
                ),
            ):
                legacy = app.api_spending_comparisons("NZD")

        self.assertEqual(optimized, legacy)


if __name__ == "__main__":
    unittest.main()
