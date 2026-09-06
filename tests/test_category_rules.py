import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.accounts import add_account
from scripts.categories import seed_categories
from scripts.category_rules import add_rule, cleanup_summary, list_rules, seed_rules
from scripts.db_init import create_database
from scripts.fx_seed import seed_rates


class CategoryRuleTests(unittest.TestCase):
    def test_cleanup_summary_and_seed_rules(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)
            add_account("Checking", "NZD", "100", database_path=database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                account_id = connection.execute("SELECT id FROM accounts").fetchone()[0]
                category_id = connection.execute(
                    "SELECT id FROM categories WHERE name = 'Uncategorized'"
                ).fetchone()[0]
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
                        amount_usd_minor_fixed,
                        review_status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "test",
                        "test-1",
                        account_id,
                        "2026-06-15",
                        "EXAMPLE  RECREATION PROVIDER | POS",
                        "EXAMPLE  RECREATION PROVIDER",
                        category_id,
                        "NZD",
                        -4000,
                        "1",
                        "0.60",
                        "test",
                        "2026-06-15",
                        -4000,
                        -2400,
                        "not_reviewed",
                    ),
                )
                connection.commit()

            add_rule("EXAMPLE RECREATION PROVIDER", "Hobbies", 50, database_path)
            seed_rules(database_path)

            summary = cleanup_summary(database_path)
            recreation_rule = [
                rule
                for rule in list_rules(database_path)
                if rule[1] == "EXAMPLE RECREATION PROVIDER"
            ][0]

        self.assertEqual(summary["needs_review"], 1)
        self.assertEqual(summary["uncategorized"], 1)
        self.assertEqual(summary["can_apply"], 1)
        self.assertEqual(
            summary["top_uncategorized"][0]["label"],
            "EXAMPLE  RECREATION PROVIDER",
        )
        self.assertEqual(recreation_rule[2], "Hobbies")
        self.assertEqual(recreation_rule[3], 50)

if __name__ == "__main__":
    unittest.main()  #Allows this test module to run directly
