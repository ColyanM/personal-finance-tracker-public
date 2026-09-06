import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.accounts import add_account, list_accounts
from scripts.db_init import create_database
from scripts.plaid_api import PlaidRequestError
from scripts.plaid_items import disconnect_item
from scripts.plaid_link import (
    build_link_page,
    get_result_path,
    request_is_allowed,
    save_connection,
    update_connection,
)


class PlaidLinkTests(unittest.TestCase):
    def test_local_handoff_encrypts_item_and_rejects_other_origins(self):
        self.assertTrue(
            request_is_allowed(
                "127.0.0.1:8765",
                "http://127.0.0.1:8765",
                "session-token",
                "session-token",
            )
        )
        self.assertFalse(
            request_is_allowed(
                "127.0.0.1:8765",
                "https://example.com",
                "session-token",
                "session-token",
            )
        )

        with (
            patch(
                "scripts.plaid_link.exchange_public_token",
                return_value=("example-access-token", "example-item-id"),
            ),
            patch("scripts.plaid_link.save_item") as save_item,
        ):
            save_connection("example-public-token", "Example Bank")

        save_item.assert_called_once_with(
            "example-access-token",
            "example-item-id",
            "Example Bank",
        )

        page = build_link_page("link-token", "session-token", "nonce").decode()
        self.assertIn("https://cdn.plaid.com/link/v2/stable/link-initialize.js", page)
        self.assertIn("institution_name", page)
        self.assertIn("This connects real account, transaction, and investment data.", page)
        self.assertIn("Continue through Plaid using your bank sign-in", page)
        self.assertNotIn("production-secret", page)

    def test_update_mode_repairs_selected_item_without_exchanging_tokens(self):
        page = build_link_page(
            "short-lived-update-token",
            "session-token",
            "nonce",
            update_mode=True,
            connection_label="Example Credit Union",
        ).decode()

        self.assertIn("Reconnect Example Credit Union", page)
        self.assertIn('fetch("/complete"', page)
        self.assertIn("without creating a new account link", page)
        self.assertNotIn('fetch("/exchange"', page)
        self.assertNotIn("institution_name", page)
        self.assertNotIn("public_token", page)
        self.assertNotIn("Saving encrypted Plaid connection", page)
        self.assertEqual(get_result_path(True), "/complete")
        self.assertEqual(get_result_path(False), "/exchange")

        with (
            patch(
                "scripts.plaid_link.get_saved_item",
                return_value={
                    "label": "Example Credit Union",
                    "access_token": "example-existing-access-token",
                    "item_id": "example-existing-item-id",
                    "secret_name": "example-encrypted-item",
                },
            ) as get_saved_item,
            patch(
                "scripts.plaid_link.create_update_link_token",
                return_value="short-lived-update-token",
            ) as create_update_link_token,
            patch("scripts.plaid_link.run_link") as run_link,
            patch("scripts.plaid_link.exchange_public_token") as exchange_public_token,
            patch("scripts.plaid_link.save_item") as save_item,
        ):
            update_connection(3)

        get_saved_item.assert_called_once_with(3)
        create_update_link_token.assert_called_once_with("example-existing-access-token")
        run_link.assert_called_once_with(
            "short-lived-update-token",
            "Example Credit Union",
            update_mode=True,
        )
        exchange_public_token.assert_not_called()
        save_item.assert_not_called()

    def test_disconnect_revokes_one_item_and_deactivates_only_its_accounts(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            temporary_path = Path(temporary_folder)
            database_path = temporary_path / "finance_hub.sqlite"
            backup_dir = temporary_path / "backups"
            create_database(database_path)
            add_account(
                "Example checking",
                "USD",
                "100",
                provider="plaid-production-us",
                provider_account_id="example-checking-id",
                database_path=database_path,
            )
            add_account(
                "Example savings",
                "USD",
                "200",
                provider="plaid-production-us",
                provider_account_id="example-savings-id",
                database_path=database_path,
            )

            with (
                patch(
                    "scripts.plaid_items.get_saved_items",
                    return_value=[
                        {
                            "secret_name": "example-plaid-item",
                            "label": "Example Bank",
                            "access_token": "example-access-token",
                            "item_id": "example-item-id",
                        }
                    ],
                ),
                patch(
                    "scripts.plaid_items.fetch_accounts",
                    return_value=[{"account_id": "example-checking-id"}],
                ),
                patch("scripts.plaid_items.remove_item") as remove_item,
                patch("scripts.plaid_items.delete_secret") as delete_secret,
            ):
                label, deactivated, backup_path = disconnect_item(
                    1,
                    "DISCONNECT",
                    database_path,
                    backup_dir,
                )

            accounts = {account[0]: account for account in list_accounts(database_path)}

            with (
                patch(
                    "scripts.plaid_items.get_saved_items",
                    return_value=[
                        {
                            "secret_name": "example-stale-item",
                            "label": "Removed example connection",
                            "access_token": "example-stale-token",
                            "item_id": "example-stale-id",
                        }
                    ],
                ),
                patch(
                    "scripts.plaid_items.fetch_accounts",
                    side_effect=PlaidRequestError("Item missing", "ITEM_NOT_FOUND"),
                ),
                patch("scripts.plaid_items.remove_item") as stale_remove_item,
                patch("scripts.plaid_items.delete_secret", return_value=True) as stale_delete,
            ):
                stale_label, stale_deactivated, _ = disconnect_item(
                    1,
                    "DISCONNECT",
                    database_path,
                    backup_dir,
                )

        self.assertEqual(label, "Example Bank")
        self.assertEqual(deactivated, 1)
        self.assertTrue(backup_path.name.startswith("finance_hub-before-plaid-disconnect"))
        self.assertEqual(accounts["Example checking"][7], 0)
        self.assertEqual(accounts["Example savings"][7], 1)
        remove_item.assert_called_once_with("example-access-token")
        delete_secret.assert_called_once_with("example-plaid-item")
        self.assertEqual(stale_label, "Removed example connection")
        self.assertEqual(stale_deactivated, 0)
        stale_remove_item.assert_not_called()
        stale_delete.assert_called_once_with("example-stale-item")


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
