import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import plaid_transactions
from scripts.accounts import add_account
from scripts.categories import seed_categories
from scripts.common import get_refresh_days
from scripts.db_init import create_database
from scripts.fx_seed import seed_rates
from scripts.plaid_accounts import get_production_verification, remove_sandbox_data
from scripts.plaid_api import PlaidRequestError
from scripts.recurring import get_recurring_forecast
from scripts.plaid_transactions import get_recent_transactions, save_fetched_transactions


class PlaidTransactionTests(unittest.TestCase):
    def test_failed_item_is_reported_while_later_items_still_fetch(self):
        items = [
            {
                "label": "First bank",
                "access_token": "first-private-token",
                "item_id": "first-private-item",
            },
            {
                "label": "Second bank",
                "access_token": "second-private-token",
                "item_id": "second-private-item",
            },
            {
                "label": "Third bank",
                "access_token": "third-private-token",
                "item_id": "third-private-item",
            },
        ]
        with (
            patch(
                "scripts.plaid_transactions.get_saved_accounts",
                return_value={
                    "first-account": {},
                    "second-account": {},
                    "third-account": {},
                },
            ),
            patch("scripts.plaid_transactions.get_plaid_refresh_days", return_value=30),
            patch("scripts.plaid_transactions.get_saved_items", return_value=items),
            patch(
                "scripts.plaid_transactions.fetch_accounts",
                side_effect=[
                    [{"account_id": "first-account"}],
                    [{"account_id": "second-account"}],
                    [{"account_id": "third-account"}],
                ],
            ) as fetch_accounts,
            patch(
                "scripts.plaid_transactions.fetch_transactions",
                side_effect=[
                    [{"transaction_id": "first-transaction"}],
                    PlaidRequestError("generic Plaid failure", "ITEM_LOGIN_REQUIRED"),
                    [{"transaction_id": "third-transaction"}],
                ],
            ) as fetch_transactions,
        ):
            result = get_recent_transactions(30, Path("unused.sqlite"))

        linked_accounts, transactions, detail_requests, refreshed_ids, failures = result
        message = failures[0]
        self.assertIn("Plaid connection 2 (Second bank) needs bank sign-in", message)
        self.assertIn(
            "python scripts\\plaid_link.py update --connection 2",
            message,
        )
        self.assertNotIn("first-private-token", message)
        self.assertNotIn("second-private-token", message)
        self.assertNotIn("second-private-item", message)
        self.assertEqual(fetch_accounts.call_count, 3)
        self.assertEqual(fetch_transactions.call_count, 3)
        self.assertEqual(
            [row["account_id"] for row in linked_accounts],
            ["first-account", "third-account"],
        )
        self.assertEqual(
            [row["transaction_id"] for row in transactions],
            ["first-transaction", "third-transaction"],
        )
        self.assertEqual(refreshed_ids, {"first-account", "third-account"})
        self.assertEqual([row["position"] for row in detail_requests], [1, 3])

    def test_partial_refresh_scopes_writes_and_records_attention_status(self):
        warning = "Plaid connection 2 (Second bank) needs bank sign-in"
        item_request = {
            "position": 1,
            "label": "First bank",
            "access_token": "private-token",
            "account_ids": ["healthy-account"],
        }
        with (
            patch("scripts.plaid_transactions.require_safe_review"),
            patch(
                "scripts.plaid_transactions.create_backup",
                return_value=Path("before-refresh.sqlite"),
            ),
            patch(
                "scripts.plaid_transactions.get_plaid_config",
                return_value={"name": "production"},
            ),
            patch("scripts.plaid_transactions.start_run", return_value=42),
            patch("scripts.plaid_transactions.finish_run") as finish_run,
            patch(
                "scripts.plaid_transactions.refresh_exchange_rates_or_saved",
                return_value=("2026-09-06", None, None, ""),
            ),
            patch(
                "scripts.plaid_transactions.get_recent_transactions",
                return_value=(
                    [{"account_id": "healthy-account"}],
                    [],
                    [item_request],
                    {"healthy-account"},
                    [warning],
                ),
            ),
            patch(
                "scripts.plaid_transactions.refresh_saved_balances",
                return_value=(1, None),
            ),
            patch(
                "scripts.plaid_transactions.save_fetched_transactions",
                return_value=(0, 0, 0, 0, 0),
            ) as save_transactions,
            patch(
                "scripts.plaid_transactions.refresh_investments",
                return_value=(0, []),
            ),
            patch("scripts.plaid_transactions.apply_rules", return_value=0),
        ):
            result = plaid_transactions.refresh_plaid(
                1,
                "REFRESH",
                Path("unused.sqlite"),
                Path("backups"),
            )

        self.assertEqual(result[7], (warning,))
        save_transactions.assert_called_once_with(
            [],
            "SAVE",
            Path("unused.sqlite"),
            refreshed_account_ids={"healthy-account"},
        )
        self.assertEqual(finish_run.call_args.args[1], "failed")
        self.assertIn("partial refresh", finish_run.call_args.args[2])

    def test_all_failed_refresh_does_not_call_finance_writers(self):
        with (
            patch("scripts.plaid_transactions.require_safe_review"),
            patch(
                "scripts.plaid_transactions.create_backup",
                return_value=Path("before-refresh.sqlite"),
            ),
            patch(
                "scripts.plaid_transactions.get_plaid_config",
                return_value={"name": "production"},
            ),
            patch("scripts.plaid_transactions.start_run", return_value=43),
            patch("scripts.plaid_transactions.finish_run") as finish_run,
            patch(
                "scripts.plaid_transactions.refresh_exchange_rates_or_saved",
                return_value=("2026-09-06", None, None, ""),
            ),
            patch(
                "scripts.plaid_transactions.get_recent_transactions",
                side_effect=PlaidRequestError(
                    "No US connections could be refreshed: First bank",
                    "ITEM_LOGIN_REQUIRED",
                ),
            ),
            patch("scripts.plaid_transactions.refresh_saved_balances") as refresh_balances,
            patch("scripts.plaid_transactions.save_fetched_transactions") as save_transactions,
            patch("scripts.plaid_transactions.refresh_investments") as refresh_investments,
            self.assertRaises(PlaidRequestError),
        ):
            plaid_transactions.refresh_plaid(
                1,
                "REFRESH",
                Path("unused.sqlite"),
                Path("backups"),
            )

        refresh_balances.assert_not_called()
        save_transactions.assert_not_called()
        refresh_investments.assert_not_called()
        finish_run.assert_called_once_with(
            43,
            "failed",
            "Plaid refresh failed",
            Path("unused.sqlite"),
        )

    def test_cli_returns_attention_exit_code_for_partial_refresh(self):
        result = (
            Path("backup.sqlite"),
            "2026-09-06",
            1,
            (0, 0, 0, 0, 0),
            0,
            0,
            "",
            ("Plaid connection 2 (Second bank) needs bank sign-in",),
        )
        with (
            patch(
                "scripts.plaid_transactions.read_arguments",
                return_value=SimpleNamespace(command="refresh", days=1, confirm="REFRESH"),
            ),
            patch("scripts.plaid_transactions.refresh_plaid", return_value=result),
            patch("sys.stdout", new_callable=io.StringIO),
            patch("sys.stderr", new_callable=io.StringIO) as error_output,
        ):
            exit_code = plaid_transactions.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("Second bank", error_output.getvalue())

    def test_all_failed_items_stop_without_a_partial_result(self):
        items = [
            {"label": "First bank", "access_token": "first-token"},
            {"label": "Second bank", "access_token": "second-token"},
        ]
        with (
            patch(
                "scripts.plaid_transactions.get_saved_accounts",
                return_value={"saved-account": {}},
            ),
            patch("scripts.plaid_transactions.get_plaid_refresh_days", return_value=30),
            patch("scripts.plaid_transactions.get_saved_items", return_value=items),
            patch(
                "scripts.plaid_transactions.fetch_accounts",
                side_effect=[
                    PlaidRequestError("first failed", "ITEM_LOGIN_REQUIRED"),
                    PlaidRequestError("second failed", "ITEM_LOGIN_REQUIRED"),
                ],
            ),
            patch("scripts.plaid_transactions.fetch_transactions") as fetch_transactions,
            self.assertRaises(PlaidRequestError) as raised,
        ):
            get_recent_transactions(30, Path("unused.sqlite"))

        message = str(raised.exception)
        self.assertEqual(raised.exception.error_code, "ITEM_LOGIN_REQUIRED")
        self.assertIn("No US connections could be refreshed", message)
        self.assertIn("First bank", message)
        self.assertIn("Second bank", message)
        self.assertNotIn("first-token", message)
        self.assertNotIn("second-token", message)
        fetch_transactions.assert_not_called()

    def test_partial_refresh_only_reconciles_pending_rows_for_healthy_accounts(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)
            seed_categories(database_path)
            add_account(
                "Healthy account",
                "USD",
                "100",
                provider="plaid-production-us",
                provider_account_id="healthy-account",
                database_path=database_path,
            )
            add_account(
                "Failed account",
                "USD",
                "200",
                provider="plaid-production-us",
                provider_account_id="failed-account",
                database_path=database_path,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                self.add_saved_transaction(
                    connection,
                    "plaid-production-us",
                    "healthy-account",
                    "2026-08-01",
                )
                self.add_saved_transaction(
                    connection,
                    "plaid-production-us",
                    "failed-account",
                    "2026-08-02",
                )
                connection.execute(
                    "UPDATE transactions SET transaction_status = 'pending'"
                )
                connection.commit()

            result = save_fetched_transactions(
                [],
                "SAVE",
                database_path,
                refreshed_account_ids={"healthy-account"},
            )

            with closing(sqlite3.connect(database_path)) as connection:
                remaining_accounts = connection.execute(
                    """
                    SELECT a.provider_account_id
                    FROM transactions t
                    JOIN accounts a ON a.id = t.account_id
                    ORDER BY a.provider_account_id
                    """
                ).fetchall()

        self.assertEqual(result[4], 1)
        self.assertEqual(remaining_accounts, [("failed-account",)])

    def test_refresh_window_uses_history_then_live_rows(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)
            add_account(
                "Plaid Checking",
                "USD",
                "110",
                provider="plaid-production-us",
                provider_account_id="account-id",
                institution="Production Bank",
                account_type="checking",
                database_path=database_path,
            )
            add_account(
                "Monarch Checking",
                "USD",
                "0",
                provider="monarch-history",
                provider_account_id="monarch-account",
                institution="Monarch",
                account_type="historical",
                database_path=database_path,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                self.add_saved_transaction(
                    connection,
                    "monarch-history",
                    "monarch-account",
                    "2026-06-25",
                )
                days_from_history = get_refresh_days(
                    connection,
                    "plaid-production-us",
                    "monarch-history",
                    default_days=30,
                    today=date(2026, 8, 10),
                )

                with self.assertRaisesRegex(ValueError, "Backfill transactions first"):
                    get_refresh_days(
                        connection,
                        "plaid-production-us",
                        "monarch-history",
                        default_days=30,
                        today=date(2026, 10, 1),
                    )

                self.add_saved_transaction(
                    connection,
                    "plaid-production-us",
                    "account-id",
                    "2026-08-08",
                )
                days_from_live = get_refresh_days(
                    connection,
                    "plaid-production-us",
                    "monarch-history",
                    default_days=30,
                    today=date(2026, 8, 10),
                )

        self.assertEqual(days_from_history, 50)
        self.assertEqual(days_from_live, 30)

    def test_plaid_skips_monarch_duplicates_but_allows_late_same_day_rows(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)
            add_account(
                "Plaid Checking",
                "USD",
                "110",
                provider="plaid-production-us",
                provider_account_id="account-id",
                institution="Production Bank",
                account_type="checking",
                database_path=database_path,
            )
            add_account(
                "Monarch Checking",
                "USD",
                "0",
                provider="monarch-history",
                provider_account_id="monarch-account",
                institution="Monarch",
                account_type="historical",
                database_path=database_path,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    UPDATE accounts
                    SET is_active = 0
                    WHERE provider = 'monarch-history'
                    """
                )
                category_id = connection.execute(
                    "SELECT id FROM categories WHERE name = 'Uncategorized'"
                ).fetchone()[0]
                monarch_account_id = connection.execute(
                    """
                    SELECT id
                    FROM accounts
                    WHERE provider = 'monarch-history'
                    """
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
                        "monarch-cutoff",
                        monarch_account_id,
                        "2026-06-20",
                        "Old Plaid duplicate",
                        category_id,
                        "USD",
                        -500,
                        "1.65",
                        "1",
                        "test",
                        "2026-06-20",
                        -825,
                        -500,
                    ),
                )
                connection.commit()

            old_plaid_transaction = {
                "transaction_id": "old-plaid",
                "account_id": "account-id",
                "pending": False,
                "date": "2026-06-20",
                "authorized_date": "2026-06-20",
                "name": "Old Plaid duplicate",
                "merchant_name": "Old Plaid duplicate",
                "amount": 5,
                "iso_currency_code": "USD",
            }
            new_plaid_transaction = old_plaid_transaction | {
                "transaction_id": "new-plaid",
                "date": "2026-06-21",
                "authorized_date": "2026-06-21",
                "name": "New Plaid charge",
            }
            same_day_missing_transaction = old_plaid_transaction | {
                "transaction_id": "same-day-missing",
                "name": "Same day missing charge",
                "merchant_name": "Same day missing charge",
            }

            result = save_fetched_transactions(
                [old_plaid_transaction, same_day_missing_transaction, new_plaid_transaction],
                "SAVE",
                database_path,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                saved_ids = [
                    row[0]
                    for row in connection.execute(
                        """
                        SELECT provider_transaction_id
                        FROM transactions
                        WHERE provider = 'plaid-production-us'
                        """
                    ).fetchall()
                ]

        self.assertEqual(result[0], 2)
        self.assertCountEqual(saved_ids, ["same-day-missing", "new-plaid"])

    def test_pending_charge_is_replaced_by_its_final_charge(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            create_database(database_path)
            seed_categories(database_path)
            seed_rates(database_path)
            add_account(
                "Historical Plaid account",
                "USD",
                "110",
                provider="plaid-us",
                provider_account_id="historical-account-id",
                institution="Plaid Sandbox",
                account_type="checking",
                database_path=database_path,
            )
            with closing(sqlite3.connect(database_path)) as connection:
                self.add_saved_transaction(
                    connection,
                    "plaid-us",
                    "historical-account-id",
                    "2026-05-01",
                )
            create_database(database_path)  #Moves historical Plaid rows into Sandbox
            add_account(
                "Plaid Checking",
                "USD",
                "110",
                provider="plaid-production-us",
                provider_account_id="account-id",
                institution="Production Bank",
                account_type="checking",
                database_path=database_path,
            )
            pending = {
                "transaction_id": "pending-id",
                "account_id": "account-id",
                "pending": True,
                "date": "2026-06-19",
                "authorized_date": "2026-06-19",
                "name": "Coffee shop",
                "merchant_name": "Coffee shop",
                "amount": 12.34,
                "iso_currency_code": "USD",
            }
            final = pending | {
                "transaction_id": "final-id",
                "pending_transaction_id": "pending-id",
                "pending": False,
                "date": "2026-06-20",
                "amount": 12.5,
            }
            same_pending = pending | {
                "transaction_id": "pending-same",
                "name": "Tea shop",
                "merchant_name": "Tea shop",
                "amount": 6.0,
            }
            same_final = same_pending | {
                "transaction_id": "final-same",
                "pending_transaction_id": "pending-same",
                "pending": False,
                "date": "2026-06-20",
            }

            first_result = save_fetched_transactions([pending, same_pending], "SAVE", database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    UPDATE transactions
                    SET review_status = 'reviewed',
                        reviewed_at = CURRENT_TIMESTAMP
                    WHERE provider_transaction_id IN ('pending-id', 'pending-same')
                    """
                )
                connection.commit()
            second_result = save_fetched_transactions([final, same_final], "SAVE", database_path)

            weekly_rows = []
            for transaction_id, posted_date in (
                ("weekly-1", "2026-06-06"),
                ("weekly-2", "2026-06-13"),
            ):
                weekly_rows.append(
                    final
                    | {
                        "transaction_id": transaction_id,
                        "pending_transaction_id": None,
                        "date": posted_date,
                    }
                )
            save_fetched_transactions(weekly_rows, "SAVE", database_path)
            forecast = get_recurring_forecast(
                "USD",
                30,
                date(2026, 6, 21),
                database_path,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                rows = connection.execute(
                    """
                    SELECT provider_transaction_id, transaction_status,
                           original_amount_minor, review_status
                    FROM transactions
                    WHERE provider_transaction_id IN ('final-id', 'final-same')
                    ORDER BY provider_transaction_id
                    """
                ).fetchall()

            linked_production_accounts = [
                {
                    "account_id": "account-id",
                    "name": "Plaid Checking",
                    "type": "depository",
                    "subtype": "checking",
                    "balances": {"current": 110, "iso_currency_code": "USD"},
                }
            ]

            summary = get_production_verification(
                linked_production_accounts,
                database_path,
            )
            removed = remove_sandbox_data(
                linked_production_accounts,
                "REMOVE_SANDBOX_DATA",
                database_path,
                temporary_path / "backups",
            )
            cleanup_backup_exists = removed[2].is_file()

            with closing(sqlite3.connect(database_path)) as connection:
                remaining_accounts = connection.execute(
                    "SELECT provider FROM accounts"
                ).fetchall()
                remaining_transactions = connection.execute(
                    "SELECT provider FROM transactions ORDER BY provider_transaction_id"
                ).fetchall()

        self.assertEqual(first_result[3], 2)
        self.assertEqual(second_result[1], 2)
        self.assertEqual(forecast["pattern_count"], 1)
        self.assertEqual(len(forecast["upcoming"]), 4)
        self.assertEqual(
            rows,
            [
                ("final-id", "posted", -1250, "not_reviewed"),
                ("final-same", "posted", -600, "reviewed"),
            ],
        )
        self.assertEqual(summary["matching_balances"], 1)
        self.assertEqual(summary["transactions"], 4)
        self.assertEqual(summary["sandbox_accounts"], 1)
        self.assertEqual(summary["sandbox_transactions"], 1)
        self.assertEqual(removed[:2], (1, 1))
        self.assertTrue(cleanup_backup_exists)
        self.assertEqual(remaining_accounts, [("plaid-production-us",)])
        self.assertEqual(remaining_transactions, [("plaid-production-us",)] * 4)

    @staticmethod
    def add_saved_transaction(connection, provider, provider_account_id, posted_date):  #Adds one simple posted row
        category_id = connection.execute(
            "SELECT id FROM categories WHERE name = 'Uncategorized'"
        ).fetchone()[0]
        account_id = connection.execute(
            """
            SELECT id
            FROM accounts
            WHERE provider = ?
              AND provider_account_id = ?
            """,
            (provider, provider_account_id),
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
                provider,
                f"{provider}-{posted_date}",
                account_id,
                posted_date,
                "Saved row",
                category_id,
                "USD",
                -100,
                "1.70",
                "1",
                "test",
                posted_date,
                -170,
                -100,
            ),
        )
        connection.commit()


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
