import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path

from scripts.categories import seed_categories
from scripts.db_init import create_database
from scripts.fx_seed import seed_rates
from scripts.fx_refresh import parse_historical_usd_nzd_rates
from scripts.monarch_import import (
    clear_all_transactions,
    clear_monarch_history,
    clear_plaid_transactions,
    count_transactions,
    count_plaid_transactions,
    import_balances,
    import_transactions,
)


class MonarchImportTests(unittest.TestCase):
    def test_historical_fx_parser_accepts_public_api_rows(self):
        rows = [
            {"date": "2023-01-13", "base": "USD", "quote": "NZD", "rate": 1.5691},
            {"date": "2023-01-14", "base": "USD", "quote": "NZD", "rate": 1.5688},
        ]

        parsed = parse_historical_usd_nzd_rates(
            rows,
            date.fromisoformat("2023-01-13"),
            date.fromisoformat("2023-01-14"),
        )

        self.assertEqual(parsed[0][0], "2023-01-13")
        self.assertEqual(str(parsed[0][1]), "1.5691")

    def test_monarch_history_import_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            balance_path = temporary_path / "balances.csv"
            transaction_path = temporary_path / "transactions.csv"

            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)

            balance_path.write_text(
                "Date,Balance,Account\n"
                "2026-06-15,500.00,Example Savings\n",
                encoding="utf-8",
            )
            transaction_path.write_text(
                "Date,Merchant,Category,Account,Original Statement,Notes,Amount,Tags,Owner\n"
                "2026-06-15,Example Coffee Shop,Eating Out,Example Card,EXAMPLE COFFEE,,-20.00,,Shared\n",
                encoding="utf-8",
            )

            balance_summary = import_balances(balance_path, "USD", database_path)
            first_transaction_summary = import_transactions(transaction_path, "USD", database_path)
            second_transaction_summary = import_transactions(transaction_path, "USD", database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                history_rows = connection.execute(
                    "SELECT account_name, balance_minor, currency FROM account_balance_history"
                ).fetchall()
                transaction_rows = connection.execute(
                    """
                    SELECT t.provider, t.original_amount_minor, t.amount_nzd_minor_fixed,
                           t.fx_rate_date, t.fx_rate_source, t.review_status, a.is_active
                    FROM transactions t
                    JOIN accounts a ON a.id = t.account_id
                    """
                ).fetchall()

        self.assertEqual(balance_summary, {"balance_rows": 1, "balance_accounts": 1})
        self.assertEqual(first_transaction_summary["transactions_added"], 1)
        self.assertEqual(second_transaction_summary["transactions_skipped"], 1)
        self.assertEqual(history_rows, [("Example Savings", 50000, "USD")])
        self.assertEqual(
            transaction_rows,
            [
                (
                    "monarch-history",
                    -2000,
                    -3300,
                    "2026-06-07",
                    "monarch_export_using_manual_seed_closest_before",
                    "reviewed",
                    0,
                )
            ],
        )

    def test_monarch_merchant_rules_map_paychecks_and_transfers(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            transaction_path = temporary_path / "transactions.csv"

            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)
            transaction_path.write_text(
                "Date,Merchant,Category,Account,Original Statement,Notes,Amount,Tags,Owner\n"
                "2026-06-15,Example Employer One,Paychecks,Example Checking,EXAMPLE PAYROLL ONE,,2000,,\n"
                "2026-06-15,Example Retirement Contribution,Paychecks,Example Retirement Account,EXAMPLE RETIREMENT,,100,,\n"
                "2026-06-15,Example Employer Two,Paychecks,Example Savings,EXAMPLE PAYROLL TWO,,1800,,\n"
                "2026-06-15,Interest Income,Paychecks,Example Savings,EXAMPLE INTEREST,,5,,\n"
                "2026-06-15,Example Employer Reimbursement,General,Example Savings,EXAMPLE REIMBURSEMENT,,25,,\n"
                "2026-06-15,Example Savings Transfer,General,Example Savings,EXAMPLE TRANSFER,,-20,,\n",
                encoding="utf-8",
            )

            import_transactions(transaction_path, "USD", database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                rows = {
                    merchant: category
                    for merchant, category in connection.execute(
                        """
                        SELECT t.merchant_clean, c.name
                        FROM transactions t
                        JOIN categories c ON c.id = t.category_id
                        """
                    ).fetchall()
                }

        self.assertEqual(rows["Example Employer One"], "Primary paycheck")
        self.assertEqual(rows["Example Retirement Contribution"], "Primary paycheck")
        self.assertEqual(rows["Example Employer Two"], "Secondary paycheck")
        self.assertEqual(rows["Interest Income"], "Other income")
        self.assertEqual(rows["Example Employer Reimbursement"], "Primary paycheck")
        self.assertEqual(rows["Example Savings Transfer"], "Account transfer")

    def test_monarch_history_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            balance_path = temporary_path / "balances.csv"

            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)
            balance_path.write_text(
                "Date,Balance,Account\n"
                "2026-06-15,500.00,Example Savings\n",
                encoding="utf-8",
            )

            import_balances(balance_path, "USD", database_path)
            clear_result = clear_monarch_history(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                balance_count = connection.execute(
                    "SELECT COUNT(*) FROM account_balance_history"
                ).fetchone()[0]

        self.assertEqual(clear_result, (0, 1, 0))
        self.assertEqual(balance_count, 0)

    def test_only_plaid_transactions_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            backup_dir = temporary_path / "backups"

            create_database(database_path)
            seed_categories(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                category_id = connection.execute(
                    "SELECT id FROM categories WHERE name = 'Uncategorized'"
                ).fetchone()[0]
                plaid_account_id = self.save_test_account(
                    connection,
                    "plaid-production-us",
                    "example-plaid-account",
                )
                akahu_account_id = self.save_test_account(
                    connection,
                    "akahu-nz",
                    "example-akahu-account",
                )
                monarch_account_id = self.save_test_account(
                    connection,
                    "monarch-history",
                    "example-history-account",
                )
                self.save_test_transaction(
                    connection,
                    "plaid-production-us",
                    "example-plaid-transaction",
                    plaid_account_id,
                    category_id,
                )
                self.save_test_transaction(
                    connection,
                    "akahu-nz",
                    "example-akahu-transaction",
                    akahu_account_id,
                    category_id,
                )
                self.save_test_transaction(
                    connection,
                    "monarch-history",
                    "example-history-transaction",
                    monarch_account_id,
                    category_id,
                )
                connection.commit()

            found_before = count_plaid_transactions(database_path)
            deleted_count, backup_path = clear_plaid_transactions(
                "CLEAR_PLAID_TRANSACTIONS",
                database_path,
                backup_dir,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                remaining_providers = [
                    row[0]
                    for row in connection.execute(
                        "SELECT provider FROM transactions ORDER BY provider"
                    ).fetchall()
                ]
                plaid_account_count = connection.execute(
                    "SELECT COUNT(*) FROM accounts WHERE provider = 'plaid-production-us'"
                ).fetchone()[0]
                backup_exists = backup_path.exists()

        self.assertEqual(found_before, 1)
        self.assertEqual(deleted_count, 1)
        self.assertEqual(remaining_providers, ["akahu-nz", "monarch-history"])
        self.assertEqual(plaid_account_count, 1)
        self.assertIsNotNone(backup_path)
        self.assertTrue(backup_exists)

    def test_all_transactions_can_be_cleared_without_deleting_accounts_or_balances(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            backup_dir = temporary_path / "backups"

            create_database(database_path)
            seed_categories(database_path)

            with closing(sqlite3.connect(database_path)) as connection:
                category_id = connection.execute(
                    "SELECT id FROM categories WHERE name = 'Uncategorized'"
                ).fetchone()[0]
                account_id = self.save_test_account(
                    connection,
                    "akahu-nz",
                    "example-akahu-account",
                )
                self.save_test_transaction(
                    connection,
                    "akahu-nz",
                    "example-akahu-transaction",
                    account_id,
                    category_id,
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
                    (
                        "monarch-history",
                        "Example historical account",
                        "2026-06-15",
                        "USD",
                        10000,
                    ),
                )
                connection.commit()

            found_before = count_transactions(database_path)
            deleted_count, backup_path = clear_all_transactions(
                "CLEAR_TRANSACTIONS",
                database_path,
                backup_dir,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                transaction_count = connection.execute(
                    "SELECT COUNT(*) FROM transactions"
                ).fetchone()[0]
                account_count = connection.execute(
                    "SELECT COUNT(*) FROM accounts"
                ).fetchone()[0]
                balance_count = connection.execute(
                    "SELECT COUNT(*) FROM account_balance_history"
                ).fetchone()[0]
                backup_exists = backup_path.exists()

        self.assertEqual(found_before, 1)
        self.assertEqual(deleted_count, 1)
        self.assertEqual(transaction_count, 0)
        self.assertEqual(account_count, 1)
        self.assertEqual(balance_count, 1)
        self.assertTrue(backup_exists)

    @staticmethod
    def save_test_account(connection, provider, provider_account_id):
        connection.execute(
            """
            INSERT INTO accounts (
                provider,
                provider_account_id,
                display_name,
                balance_type,
                native_currency,
                current_balance_minor
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (provider, provider_account_id, provider_account_id, "asset", "USD", 0),
        )
        return connection.execute("SELECT last_insert_rowid()").fetchone()[0]

    @staticmethod
    def save_test_transaction(connection, provider, transaction_id, account_id, category_id):
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
                provider,
                transaction_id,
                account_id,
                "2026-06-15",
                transaction_id,
                category_id,
                "USD",
                -1000,
                "1.65",
                "1",
                "test",
                "2026-06-15",
                -1650,
                -1000,
            ),
        )


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
