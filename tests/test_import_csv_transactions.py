import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from scripts.accounts import add_account
from scripts.categories import seed_categories
from scripts.db_init import create_database
from scripts.fx_seed import seed_rates
from scripts.import_csv_transactions import import_transactions


class ManualCsvImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.folder = Path(self.temporary_directory.name)
        self.database_path = self.folder / "finance_hub.sqlite"
        self.csv_path = self.folder / "transactions.csv"
        create_database(self.database_path)
        seed_rates(self.database_path)
        seed_categories(self.database_path)
        add_account("Everyday", "NZD", "1000.00", database_path=self.database_path)

    def write_rows(self, headers, rows):
        with self.csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(headers)
            writer.writerows(rows)

    def transaction_count(self):
        with closing(sqlite3.connect(self.database_path)) as connection:
            return connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    def test_readme_examples_import_once_and_keep_account_balance(self):
        self.write_rows(
            ["account_name", "posted_date", "description", "amount", "currency", "category", "transaction_id"],
            [
                ["Everyday", "2026-09-01", "Example income", "1000.00", "NZD", "Other income", "example-income-001"],
                ["Everyday", "2026-09-02", "Example grocery shop", "-50.00", "NZD", "Groceries", "example-groceries-001"],
            ],
        )

        self.assertEqual(import_transactions(self.csv_path, self.database_path), (2, 0, 0))
        self.assertEqual(import_transactions(self.csv_path, self.database_path), (0, 0, 2))
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                """
                SELECT t.posted_date, t.original_amount_minor, c.name
                FROM transactions t JOIN categories c ON c.id = t.category_id
                ORDER BY t.posted_date
                """
            ).fetchall()
            balance = connection.execute("SELECT current_balance_minor FROM accounts").fetchone()[0]
        self.assertEqual(rows, [("2026-09-01", 100000, "Other income"), ("2026-09-02", -5000, "Groceries")])
        self.assertEqual(balance, 100000)

    def test_optional_columns_can_be_omitted_and_generated_ids_are_repeatable(self):
        self.write_rows(
            ["account_name", "posted_date", "description", "amount", "currency"],
            [["Everyday", "2026-09-02", "Example grocery shop", "-50.00", "NZD"]],
        )

        self.assertEqual(import_transactions(self.csv_path, self.database_path), (1, 0, 0))
        self.assertEqual(import_transactions(self.csv_path, self.database_path), (0, 0, 1))
        with closing(sqlite3.connect(self.database_path)) as connection:
            category = connection.execute(
                "SELECT c.name FROM transactions t JOIN categories c ON c.id = t.category_id"
            ).fetchone()[0]
        self.assertEqual(category, "Uncategorized")

    def test_missing_or_blank_required_cells_report_the_row_and_roll_back(self):
        headers = ["account_name", "posted_date", "description", "amount", "currency"]
        valid_row = ["Everyday", "2026-09-02", "Example grocery shop", "-50.00", "NZD"]
        malformed_rows = [("currency", valid_row[:-1]), ("amount", valid_row[:-2])]
        for index, column in enumerate(headers):
            blank_row = valid_row.copy()
            blank_row[index] = "   "
            malformed_rows.append((column, blank_row))

        for column, row in malformed_rows:
            with self.subTest(column=column, row=row):
                self.write_rows(headers, [valid_row, row])
                with self.assertRaisesRegex(ValueError, f"CSV row 3: {column} cannot be empty"):
                    import_transactions(self.csv_path, self.database_path)
                self.assertEqual(self.transaction_count(), 0)

    def test_noncanonical_or_invalid_dates_report_the_row_and_roll_back(self):
        headers = ["account_name", "posted_date", "description", "amount", "currency", "authorized_date"]
        valid_row = ["Everyday", "2026-09-02", "Example grocery shop", "-50.00", "NZD", "2026-09-01"]

        for column in ["posted_date", "authorized_date"]:
            for invalid_date in ["20260901", "2026-W36-2", "2026-02-30"]:
                with self.subTest(column=column, invalid_date=invalid_date):
                    row = valid_row.copy()
                    row[headers.index(column)] = invalid_date
                    self.write_rows(headers, [valid_row, row])
                    with self.assertRaisesRegex(ValueError, f"CSV row 3: {column} must use YYYY-MM-DD"):
                        import_transactions(self.csv_path, self.database_path)
                    self.assertEqual(self.transaction_count(), 0)


if __name__ == "__main__":
    unittest.main()
