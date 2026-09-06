import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.accounts import add_account
from scripts.akahu_transactions import save_fetched_transactions
from scripts.bnz_csv_import import clear_bnz_transactions, import_bnz_csv
from scripts.categories import seed_categories
from scripts.db_init import create_database
from scripts.fx_seed import seed_rates


class BnzCsvImportTests(unittest.TestCase):
    def test_bnz_csv_import_marks_matching_transfers_reviewed(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            everyday_path = temporary_path / "Everyday-test.csv"
            savings_path = temporary_path / "Savings-test.csv"

            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)
            self.add_example_accounts(database_path)
            everyday_path.write_text(
                "Date,Amount,Payee,Particulars,Code,Reference,Tran Type\n"
                "15/06/26,-100.00,Savings,,,INTERNET XFR,FT\n"
                "15/06/26,-15.00,Example Cafe,,,,POS\n",
                encoding="utf-8",
            )
            savings_path.write_text(
                "Date,Amount,Payee,Particulars,Code,Reference,Tran Type\n"
                "15/06/26,100.00,Everyday,,,INTERNET XFR,FT\n",
                encoding="utf-8",
            )

            result = import_bnz_csv(
                everyday_path,
                savings_path,
                "IMPORT",
                database_path,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                rows = connection.execute(
                    """
                    SELECT a.display_name, t.merchant_clean, c.name, t.review_status
                    FROM transactions t
                    JOIN accounts a ON a.id = t.account_id
                    JOIN categories c ON c.id = t.category_id
                    ORDER BY t.original_amount_minor
                    """
                ).fetchall()

        self.assertEqual(result, {"added": 3, "skipped": 0, "transfers": 2})
        self.assertIn(("Everyday", "Savings", "Account transfer", "reviewed"), rows)
        self.assertIn(("Savings", "Everyday", "Account transfer", "reviewed"), rows)
        self.assertIn(("Everyday", "Example Cafe", "Uncategorized", "not_reviewed"), rows)

    def test_bnz_clear_keeps_monarch_and_akahu_allows_late_same_day_rows(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            backup_dir = temporary_path / "backups"
            everyday_path = temporary_path / "Everyday-test.csv"

            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)
            self.add_example_accounts(database_path)
            everyday_path.write_text(
                "Date,Amount,Payee,Particulars,Code,Reference,Tran Type\n"
                "15/06/26,-15.00,Example Cafe,,,,POS\n",
                encoding="utf-8",
            )
            import_bnz_csv(everyday_path, None, "IMPORT", database_path)

            old_akahu = {
                "_id": "example-existing-transaction",
                "_account": "example-everyday-account",
                "date": "2026-06-15",
                "description": "Example Cafe",
                "amount": -15,
            }
            same_day_missing = old_akahu | {
                "_id": "example-same-day-transaction",
                "description": "Example same-day purchase",
                "amount": -1,
            }
            new_akahu = old_akahu | {
                "_id": "example-next-day-transaction",
                "date": "2026-06-16",
                "description": "Example next-day purchase",
            }
            save_result = save_fetched_transactions(
                [old_akahu, same_day_missing, new_akahu],
                [],
                "SAVE",
                database_path,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                category_id = connection.execute(
                    "SELECT id FROM categories WHERE name = 'Uncategorized'"
                ).fetchone()[0]
                account_id = connection.execute(
                    "SELECT id FROM accounts WHERE display_name = 'Everyday'"
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
                        "monarch-history",
                        "example-history-transaction",
                        account_id,
                        "2026-06-15",
                        "Example history row",
                        category_id,
                        "USD",
                        -100,
                        "1.70",
                        "1",
                        "test",
                        "2026-06-15",
                        -170,
                        -100,
                    ),
                )
                connection.commit()

            deleted, backup_path = clear_bnz_transactions(
                "CLEAR_BNZ_TRANSACTIONS",
                database_path,
                backup_dir,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                remaining = connection.execute(
                    "SELECT provider, provider_transaction_id FROM transactions"
                ).fetchall()
                backup_exists = backup_path.exists()

        self.assertEqual(save_result[0], 2)
        self.assertEqual(deleted, 3)
        self.assertEqual(remaining, [("monarch-history", "example-history-transaction")])
        self.assertTrue(backup_exists)

    def test_bnz_csv_import_uses_confirmed_category_rules(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            everyday_path = temporary_path / "Everyday-test.csv"

            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)
            self.add_example_accounts(database_path)
            everyday_path.write_text(
                "Date,Amount,Payee,Particulars,Code,Reference,Tran Type\n"
                "15/06/26,-800.00,Example Property Manager,,,,AP\n"
                "15/06/26,-45.00,Example Recreation Provider,,,,POS\n"
                "15/06/26,2500.00,Example Employer One,Payroll,,,DC\n"
                "15/06/26,25.00,Example Payee,Meal Split,,,BP\n"
                "15/06/26,10.00,Example Payee,General Split,,,BP\n"
                "15/06/26,30.00,Example Payee,Equipment Rental,,,BP\n"
                "15/06/26,-15.00,Example Cafe,,,,POS\n",
                encoding="utf-8",
            )

            import_bnz_csv(everyday_path, None, "IMPORT", database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                rows = connection.execute(
                    """
                    SELECT t.merchant_clean, t.description_raw, c.name, t.review_status
                    FROM transactions t
                    JOIN categories c ON c.id = t.category_id
                    ORDER BY t.id
                    """
                ).fetchall()

        self.assertIn(
            ("Example Property Manager", "Example Property Manager | AP", "Rent", "reviewed"),
            rows,
        )
        self.assertIn(
            (
                "Example Recreation Provider",
                "Example Recreation Provider | POS",
                "Recreation",
                "reviewed",
            ),
            rows,
        )
        self.assertIn(
            (
                "Example Employer One",
                "Example Employer One | Payroll | DC",
                "Primary paycheck",
                "reviewed",
            ),
            rows,
        )
        self.assertIn(
            ("Example Payee", "Example Payee | Meal Split | BP", "Eating out", "reviewed"),
            rows,
        )
        self.assertIn(
            ("Example Payee", "Example Payee | General Split | BP", "General", "reviewed"),
            rows,
        )
        self.assertIn(
            ("Example Payee", "Example Payee | Equipment Rental | BP", "Hobbies", "reviewed"),
            rows,
        )
        self.assertIn(
            ("Example Cafe", "Example Cafe | POS", "Uncategorized", "not_reviewed"),
            rows,
        )

    @staticmethod
    def add_example_accounts(database_path):
        add_account(
            "Everyday",
            "NZD",
            "100",
            provider="akahu-bnz",
            provider_account_id="example-everyday-account",
            institution="Example Bank",
            account_type="checking",
            database_path=database_path,
        )
        add_account(
            "Savings",
            "NZD",
            "200",
            provider="akahu-bnz",
            provider_account_id="example-savings-account",
            institution="Example Bank",
            account_type="savings",
            database_path=database_path,
        )


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
