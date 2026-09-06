import json
import os
import unittest
from unittest.mock import Mock, patch

from scripts.plaid_api import (
    check_credentials,
    create_link_token,
    create_update_link_token,
    exchange_public_token,
    fetch_accounts,
    fetch_investment_holdings,
    fetch_transactions,
    get_error_details,
    get_credentials,
    get_client_user_id,
    get_item_products,
    get_payload_strings,
    hide_sensitive_values,
    PlaidRequestError,
    remove_item,
    request,
)
from scripts.plaid_environment import get_plaid_config


class PlaidApiTests(unittest.TestCase):
    def test_production_check_uses_one_fixed_request_and_hides_errors(self):
        response = Mock(status=200)
        response.read.return_value = json.dumps(
            {"institutions": [{"name": "Production bank"}]}
        ).encode()
        connection = Mock()
        connection.getresponse.return_value = response

        with (
            patch(
                "scripts.plaid_api.http.client.HTTPSConnection",
                return_value=connection,
            ) as https_connection,
            patch(
                "scripts.plaid_api.get_credentials",
                return_value=("client-id", "production-secret"),
            ),
        ):
            count = check_credentials()

        https_connection.assert_called_once_with("production.plaid.com", timeout=15)
        request_body = json.dumps(
            {
                "count": 1,
                "offset": 0,
                "country_codes": ["US"],
                "client_id": "client-id",
                "secret": "production-secret",
            }
        ).encode("utf-8")
        connection.request.assert_called_once_with(
            "POST",
            "/institutions/get",
            body=request_body,
            headers={
                "Content-Type": "application/json",
                "Plaid-Version": "2020-09-14",
            },
        )
        self.assertEqual(count, 1)

        with self.assertRaisesRegex(ValueError, "not approved"):
            request("/payments/get", {}, "client-id", "production-secret")

        response.status = 401
        response.read.return_value = json.dumps(
            {
                "error_type": "INVALID_INPUT",
                "error_code": "INVALID_FIELD",
                "error_message": "bad client-id production-secret public-token",
            }
        ).encode()
        with patch(
            "scripts.plaid_api.http.client.HTTPSConnection",
            return_value=connection,
        ):
            with self.assertRaisesRegex(ValueError, "INVALID_FIELD") as error:
                request("/institutions/get", {}, "client-id", "production-secret")

        self.assertNotIn("client-id", str(error.exception))
        self.assertNotIn("production-secret", str(error.exception))
        self.assertEqual(
            get_error_details(
                response.read.return_value,
                ("client-id", "production-secret", "public-token"),
            ),
            " (INVALID_INPUT | INVALID_FIELD | bad [hidden] [hidden] [hidden])",
        )
        self.assertEqual(
            hide_sensitive_values(
                "bad client-id and production-secret",
                ("client-id", "production-secret"),
            ),
            "bad [hidden] and [hidden]",
        )
        self.assertEqual(
            get_payload_strings({"outer": ["public-token", {"item": "item-id"}]}),
            ["public-token", "item-id"],
        )

    def test_credentials_are_trimmed_before_use(self):
        with patch(
            "scripts.plaid_api.get_secret",
            side_effect=[" client-id ", " production-secret "],
        ):
            self.assertEqual(get_credentials(), ("client-id", "production-secret"))

        with (
            patch(
                "scripts.plaid_api.get_secret",
                side_effect=["client-id", "production-secret"],
            ) as get_secret,
        ):
            config = get_plaid_config()
            self.assertEqual(config["host"], "production.plaid.com")
            self.assertEqual(config["item_secret"], "plaid-production-item")
            self.assertEqual(config["provider"], "plaid-production-us")
            self.assertEqual(get_credentials(), ("client-id", "production-secret"))

        self.assertEqual(
            [call.args[0] for call in get_secret.call_args_list],
            ["plaid-production-client-id", "plaid-production-secret"],
        )

        with (
            patch("scripts.plaid_api.get_secret", return_value=None),
            patch("scripts.plaid_api.set_secret_value") as save_user_id,
        ):
            user_id = get_client_user_id()

        self.assertGreater(len(user_id), 20)
        self.assertNotIn("@", user_id)
        save_user_id.assert_called_once_with("plaid-production-user-id", user_id)

    def test_stale_sandbox_environment_cannot_override_production(self):
        with patch.dict(
            os.environ,
            {"FINANCE_HUB_PLAID_ENVIRONMENT": "sandbox"},
            clear=False,
        ):
            config = get_plaid_config()

        self.assertEqual(config["name"], "production")
        self.assertEqual(config["host"], "production.plaid.com")
        self.assertEqual(config["provider"], "plaid-production-us")

    def test_link_flow_requests_only_reviewed_read_products(self):
        with (
            patch(
                "scripts.plaid_api.get_credentials",
                return_value=("client-id", "production-secret"),
            ),
            patch(
                "scripts.plaid_api.get_client_user_id",
                return_value="private-user-id",
            ),
            patch(
                "scripts.plaid_api.request",
                side_effect=[
                    {"link_token": "link-production-token"},
                    {"access_token": "access-production-token", "item_id": "item-id"},
                    {"accounts": [{"name": "Production checking"}]},
                    {"item": {"products": ["transactions", "investments"]}},
                    {"holdings": [], "securities": []},
                    {
                        "transactions": [{"transaction_id": "transaction-id"}],
                        "total_transactions": 1,
                    },
                    {"request_id": "request-id"},
                ],
            ) as plaid_request,
        ):
            link_token = create_link_token()
            access_token, item_id = exchange_public_token("public-production-token")
            accounts = fetch_accounts("access-production-token")
            products = get_item_products("access-production-token")
            holdings = fetch_investment_holdings("access-production-token", ["account-id"])
            transactions = fetch_transactions(
                "access-production-token",
                "2026-06-01",
                "2026-06-20",
                ["account-id"],
            )
            item_removed = remove_item("access-production-token")

        self.assertEqual(link_token, "link-production-token")
        self.assertEqual((access_token, item_id), ("access-production-token", "item-id"))
        self.assertEqual(accounts, [{"name": "Production checking"}])
        self.assertEqual(products, {"transactions", "investments"})
        self.assertEqual(holdings, ([], []))
        self.assertEqual(transactions, [{"transaction_id": "transaction-id"}])
        self.assertTrue(item_removed)
        link_payload = plaid_request.call_args_list[0].args[1]
        self.assertEqual(link_payload["products"], ["transactions"])
        self.assertEqual(
            link_payload["required_if_supported_products"],
            ["investments"],
        )
        self.assertNotIn("auth", link_payload["products"])
        self.assertEqual(link_payload["user"], {"client_user_id": "private-user-id"})
        self.assertEqual(
            plaid_request.call_args_list[1].args[1],
            {"public_token": "public-production-token"},
        )
        self.assertEqual(
            plaid_request.call_args_list[2].args[:2],
            ("/accounts/get", {"access_token": "access-production-token"}),
        )
        self.assertEqual(
            plaid_request.call_args_list[4].args[1]["options"]["account_ids"],
            ["account-id"],
        )
        self.assertEqual(
            plaid_request.call_args_list[5].args[:2],
            ("/transactions/get", {
                "access_token": "access-production-token",
                "start_date": "2026-06-01",
                "end_date": "2026-06-20",
                "options": {"account_ids": ["account-id"], "count": 100, "offset": 0},
            }),
        )
        self.assertEqual(
            plaid_request.call_args_list[6].args[:2],
            ("/item/remove", {"access_token": "access-production-token"}),
        )

        with (
            patch(
                "scripts.plaid_api.get_credentials",
                return_value=("client-id", "production-secret"),
            ),
            patch(
                "scripts.plaid_api.request",
                side_effect=PlaidRequestError("Item missing", "ITEM_NOT_FOUND"),
            ),
        ):
            self.assertFalse(remove_item("already-removed-token"))

    def test_update_link_token_repairs_one_existing_item_without_products(self):
        with (
            patch(
                "scripts.plaid_api.get_credentials",
                return_value=("client-id", "production-secret"),
            ),
            patch(
                "scripts.plaid_api.get_client_user_id",
                return_value="private-user-id",
            ),
            patch(
                "scripts.plaid_api.request",
                return_value={"link_token": "update-link-token"},
            ) as plaid_request,
        ):
            link_token = create_update_link_token("  existing-access-token  ")

        self.assertEqual(link_token, "update-link-token")
        plaid_request.assert_called_once_with(
            "/link/token/create",
            {
                "client_name": "Finance Hub",
                "country_codes": ["US"],
                "language": "en",
                "user": {"client_user_id": "private-user-id"},
                "access_token": "existing-access-token",
            },
            "client-id",
            "production-secret",
        )
        payload = plaid_request.call_args.args[1]
        self.assertNotIn("products", payload)
        self.assertNotIn("required_if_supported_products", payload)

        with (
            patch("scripts.plaid_api.get_credentials") as get_credentials,
            self.assertRaisesRegex(ValueError, "access token is missing"),
        ):
            create_update_link_token(" ")
        get_credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
