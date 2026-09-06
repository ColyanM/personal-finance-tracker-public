import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

from scripts.accounts import add_account, get_net_worth, list_accounts, save_balance_snapshot, set_loan_details
from scripts.db_init import create_database
from scripts.fx_seed import seed_rates
from scripts.investments import replace_provider_positions
from scripts.plaid_accounts import (
    get_safe_accounts,
    get_linked_accounts,
    parse_selection,
    print_accounts,
    refresh_saved_balances,
    save_selected_accounts,
)
from scripts.plaid_api import PlaidRequestError
from scripts.plaid_details import prepare_investment_positions, refresh_investments
from scripts.recurring import get_recurring_forecast


class PlaidAccountTests(unittest.TestCase):
    def test_preview_hides_private_fields_and_checks_selection(self):
        raw_accounts = [
            {
                "account_id": "private-account-id",
                "mask": "1111",
                "name": "Checking\nAccount",
                "official_name": "Private official name",
                "type": "depository",
                "subtype": "checking",
                "balances": {
                    "current": 110.25,
                    "available": 100.25,
                    "iso_currency_code": "USD",
                },
            },
            {
                "account_id": "second-private-id",
                "name": "Credit card",
                "type": "credit",
                "subtype": "credit card",
                "balances": {"current": 200, "iso_currency_code": "USD"},
            },
        ]

        safe_accounts = get_safe_accounts(raw_accounts)
        selections = parse_selection("2, 1, 2", len(safe_accounts))

        with patch("builtins.print") as print_line:
            print_accounts(safe_accounts)

        printed = " ".join(str(call) for call in print_line.call_args_list)
        self.assertEqual(selections, [1, 0])
        self.assertEqual(safe_accounts[0]["name"], "Checking Account")
        self.assertEqual(safe_accounts[1]["balance_type"], "liability")
        self.assertIn("110.25 USD", printed)
        self.assertNotIn("private-account-id", printed)
        self.assertNotIn("1111", printed)
        self.assertNotIn("Private official name", printed)

        with (
            patch(
                "scripts.plaid_accounts.get_saved_items",
                return_value=[
                    {"access_token": "first", "label": "First bank"},
                    {"access_token": "second", "label": "Second bank"},
                ],
            ),
            patch(
                "scripts.plaid_accounts.fetch_accounts",
                side_effect=[[{"name": "First"}], [{"name": "Second"}]],
            ),
        ):
            linked_accounts = get_linked_accounts()

        self.assertEqual(linked_accounts[0]["_connection_label"], "First bank")
        self.assertEqual(linked_accounts[1]["_connection_label"], "Second bank")

    def test_balance_snapshots_keep_duplicate_display_names_separate(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)

            add_account(
                "Shared IRA",
                "USD",
                "100",
                provider="plaid-production-us",
                provider_account_id="account-one",
                database_path=database_path,
            )
            add_account(
                "Shared IRA",
                "USD",
                "200",
                provider="plaid-production-us",
                provider_account_id="account-two",
                database_path=database_path,
            )

            with closing(sqlite3.connect(database_path)) as connection:
                accounts = connection.execute(
                    """
                    SELECT id, display_name
                    FROM accounts
                    ORDER BY provider_account_id
                    """
                ).fetchall()
                for account_id, display_name in accounts:
                    save_balance_snapshot(
                        connection,
                        "plaid-production-us",
                        account_id,
                        display_name,
                        "2026-07-18",
                        "USD",
                        account_id * 1000,
                    )
                connection.commit()
                snapshots = connection.execute(
                    """
                    SELECT account_name, account_id
                    FROM account_balance_history
                    ORDER BY account_id
                    """
                ).fetchall()

        self.assertEqual(len(snapshots), 2)
        self.assertEqual([row[1] for row in snapshots], [accounts[0][0], accounts[1][0]])
        self.assertEqual(
            [row[0] for row in snapshots],
            [f"Shared IRA #{accounts[0][0]}", f"Shared IRA #{accounts[1][0]}"],
        )

    def test_confirmed_save_and_refresh_only_change_selected_accounts(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            backup_dir = temporary_path / "backups"
            create_database(database_path)
            accounts = [
                {
                    "account_id": "checking-id",
                    "name": "Plaid Checking",
                    "type": "depository",
                    "subtype": "checking",
                    "balances": {
                        "current": 110,
                        "available": 100,
                        "iso_currency_code": "USD",
                    },
                },
                {
                    "account_id": "saving-id",
                    "name": "Plaid Saving",
                    "type": "depository",
                    "subtype": "savings",
                    "balances": {
                        "current": 210,
                        "available": 200,
                        "iso_currency_code": "USD",
                    },
                },
                {
                    "account_id": "credit-id",
                    "name": "Plaid Credit",
                    "type": "credit",
                    "subtype": "credit card",
                    "balances": {
                        "current": 40,
                        "iso_currency_code": "USD",
                    },
                },
            ]

            with self.assertRaisesRegex(ValueError, "confirmation was not SAVE"):
                save_selected_accounts(
                    accounts,
                    [0],
                    "save",
                    database_path,
                    backup_dir,
                )

            saved, skipped, save_backup = save_selected_accounts(
                accounts,
                [0, 2],
                "SAVE",
                database_path,
                backup_dir,
            )
            accounts[0]["balances"]["current"] = 125.5
            accounts[1]["balances"]["current"] = 999
            accounts[2]["balances"]["current"] = 50
            updated, refresh_backup = refresh_saved_balances(
                accounts,
                "REFRESH",
                database_path,
                backup_dir,
            )
            saved_accounts = list_accounts(database_path)
            net_worth = get_net_worth("USD", database_path)
            with closing(sqlite3.connect(database_path)) as connection:
                balance_history_count = connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM account_balance_history
                    WHERE source = 'plaid-production-us'
                    """
                ).fetchone()[0]
            save_backup_exists = save_backup.is_file()
            refresh_backup_exists = refresh_backup.is_file()

        self.assertEqual(saved, ["Plaid Checking", "Plaid Credit"])
        self.assertEqual(skipped, [])
        self.assertTrue(save_backup_exists)
        self.assertTrue(refresh_backup_exists)
        self.assertEqual(updated, 2)
        self.assertEqual(len(saved_accounts), 2)
        saved_by_name = {account[0]: account for account in saved_accounts}
        self.assertEqual(saved_by_name["Plaid Checking"][1], "plaid-production-us")
        self.assertEqual(saved_by_name["Plaid Checking"][5], 12550)
        self.assertEqual(saved_by_name["Plaid Checking"][9], "asset")
        self.assertEqual(saved_by_name["Plaid Credit"][5], 5000)
        self.assertEqual(saved_by_name["Plaid Credit"][9], "liability")
        self.assertEqual(balance_history_count, 2)
        self.assertEqual(net_worth["total_minor"], 7550)

    def test_investment_positions_keep_only_displayed_normalized_fields(self):
        holdings = [
            {
                "account_id": "investment-id",
                "security_id": "security-id",
                "quantity": 2,
                "institution_price": 10.5,
                "institution_value": 21,
                "institution_price_as_of": "2026-07-01",
                "iso_currency_code": "USD",
            }
        ]
        securities = [
            {
                "security_id": "security-id",
                "ticker_symbol": "fund",
                "name": "Example Fund",
                "type": "mutual fund",
            }
        ]

        self.assertEqual(
            prepare_investment_positions(holdings, securities),
            [
                {
                    "provider_account_id": "investment-id",
                    "ticker": "FUND",
                    "security_name": "Example Fund",
                    "asset_class": "Fund",
                    "quantity": "2",
                    "price_minor": 1050,
                    "value_minor": 2100,
                    "currency": "USD",
                    "as_of": "2026-07-01",
                }
            ],
        )

    def test_partial_investment_refresh_preserves_failed_item_accounts(self):
        item_accounts = [
            {
                "position": 1,
                "label": "Healthy bank",
                "access_token": "healthy-private-token",
                "account_ids": ["healthy-account"],
            },
            {
                "position": 2,
                "label": "Failed bank",
                "access_token": "failed-private-token",
                "account_ids": ["failed-account"],
            },
        ]
        prepared_position = {
            "provider_account_id": "healthy-account",
            "ticker": "FUND",
            "security_name": "Example Fund",
            "asset_class": "Fund",
            "quantity": "1",
            "price_minor": 1000,
            "value_minor": 1000,
            "currency": "USD",
            "as_of": "2026-09-06",
        }

        with (
            patch(
                "scripts.plaid_details.get_item_products",
                side_effect=[
                    {"investments"},
                    PlaidRequestError("item unavailable", "ITEM_LOGIN_REQUIRED"),
                ],
            ),
            patch(
                "scripts.plaid_details.fetch_investment_holdings",
                return_value=([{"holding": "safe"}], [{"security": "safe"}]),
            ),
            patch(
                "scripts.plaid_details.prepare_investment_positions",
                return_value=[prepared_position],
            ),
            patch(
                "scripts.plaid_details.replace_provider_positions",
                return_value=1,
            ) as replace_positions,
        ):
            saved_count, failures = refresh_investments(
                item_accounts,
                Path("unused.sqlite"),
            )

        self.assertEqual(saved_count, 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("Plaid connection 2 (Failed bank)", failures[0])
        self.assertNotIn("failed-private-token", failures[0])
        replace_positions.assert_called_once_with(
            [prepared_position],
            "plaid-production-us",
            Path("unused.sqlite"),
            provider_account_ids={"healthy-account"},
        )

    def test_scoped_position_replacement_keeps_failed_account_holdings(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)
            add_account(
                "Healthy investment",
                "USD",
                "100",
                provider="plaid-production-us",
                provider_account_id="healthy-investment",
                account_type="investment",
                database_path=database_path,
            )
            add_account(
                "Failed investment",
                "USD",
                "200",
                provider="plaid-production-us",
                provider_account_id="failed-investment",
                account_type="investment",
                database_path=database_path,
            )
            base_row = {
                "ticker": "FUND",
                "security_name": "Example Fund",
                "asset_class": "Fund",
                "quantity": "1",
                "price_minor": 1000,
                "value_minor": 1000,
                "currency": "USD",
                "as_of": "2026-09-05",
            }
            replace_provider_positions(
                [
                    base_row | {"provider_account_id": "healthy-investment"},
                    base_row | {
                        "provider_account_id": "failed-investment",
                        "value_minor": 2000,
                    },
                ],
                "plaid-production-us",
                database_path,
            )
            replace_provider_positions(
                [
                    base_row
                    | {
                        "provider_account_id": "healthy-investment",
                        "value_minor": 1500,
                        "as_of": "2026-09-06",
                    }
                ],
                "plaid-production-us",
                database_path,
                provider_account_ids={"healthy-investment"},
            )

            with closing(sqlite3.connect(database_path)) as connection:
                values = dict(
                    connection.execute(
                        """
                        SELECT a.provider_account_id, p.value_minor
                        FROM investment_positions p
                        JOIN accounts a ON a.id = p.account_id
                        ORDER BY a.provider_account_id
                        """
                    ).fetchall()
                )

        self.assertEqual(values["healthy-investment"], 1500)
        self.assertEqual(values["failed-investment"], 2000)

    def test_manual_loan_schedule_does_not_depend_on_plaid_liability_details(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            database_path = Path(temporary_folder) / "finance_hub.sqlite"
            create_database(database_path)
            seed_rates(database_path)
            add_account(
                "Student Loan",
                "USD",
                "1000",
                account_type="loan",
                balance_type="liability",
                database_path=database_path,
            )
            set_loan_details("Student Loan", "4.25", "150", 10, database_path)

            forecast = get_recurring_forecast(
                "USD",
                days=31,
                today=date(2026, 7, 1),
                database_path=database_path,
            )

        self.assertEqual(forecast["pattern_count"], 1)
        self.assertEqual(
            forecast["upcoming"][0],
            {
                "account": "Student Loan",
                "description": "Student Loan payment",
                "category": "Loan payment",
                "frequency": "Monthly",
                "due_date": "2026-07-10",
                "amount_minor": -15000,
                "source": "Saved schedule",
            },
        )


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
