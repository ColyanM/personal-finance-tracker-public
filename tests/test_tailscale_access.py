import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts import tailscale_access


TAILSCALE_STATUS = {
    "BackendState": "Running",
    "Self": {
        "DNSName": "Finance-Laptop.TailABC.ts.net.",
        "UserID": 42,
    },
    "User": {
        "42": {
            "LoginName": "owner@example.com",
        }
    },
}


def serve_status(target=tailscale_access.LOCAL_TARGET, allow_funnel=False):
    return {
        "Web": {
            "finance-laptop.tailabc.ts.net:443": {
                "Handlers": {
                    "/": {
                        "Proxy": target,
                    }
                }
            }
        },
        "AllowFunnel": {"finance-laptop.tailabc.ts.net:443": allow_funnel},
    }


class TailscaleAccessTests(unittest.TestCase):
    def test_node_access_derives_exact_https_origin_and_owner_login(self):
        origin, user_login = tailscale_access.get_node_access(TAILSCALE_STATUS)

        self.assertEqual(origin, "https://finance-laptop.tailabc.ts.net")
        self.assertEqual(user_login, "owner@example.com")

        origin, user_login = tailscale_access.get_node_access(
            TAILSCALE_STATUS,
            user_login="explicit@example.com",
        )
        self.assertEqual(origin, "https://finance-laptop.tailabc.ts.net")
        self.assertEqual(user_login, "explicit@example.com")

    def test_node_access_fails_closed_when_identity_is_unavailable(self):
        status = {
            "BackendState": "Running",
            "Self": {
                "DNSName": "finance-laptop.tailabc.ts.net.",
                "UserID": 42,
            },
            "User": {},
        }

        with self.assertRaisesRegex(ValueError, "user login is unavailable"):
            tailscale_access.get_node_access(status)

    def test_funnel_detection_only_accepts_private_serve_state(self):
        self.assertFalse(tailscale_access.funnel_is_enabled({}))
        self.assertFalse(tailscale_access.funnel_is_enabled(serve_status()))
        self.assertFalse(
            tailscale_access.funnel_is_enabled(
                {"Web": {"Enabled": True}, "AllowFunnel": False}
            )
        )
        self.assertTrue(tailscale_access.funnel_is_enabled(serve_status(allow_funnel=True)))
        self.assertTrue(
            tailscale_access.funnel_is_enabled(
                {"nested": [{"ALLOWFUNNEL": {"443": True}}]}
            )
        )

    def test_target_verification_requires_the_exact_loopback_url(self):
        self.assertTrue(tailscale_access.configuration_contains_target(serve_status()))
        self.assertTrue(
            tailscale_access.configuration_contains_target(
                serve_status(target=f"{tailscale_access.LOCAL_TARGET}/")
            )
        )
        self.assertFalse(
            tailscale_access.configuration_contains_target(
                serve_status(target="http://127.0.0.1:8001")
            )
        )
        self.assertFalse(
            tailscale_access.configuration_contains_target(
                serve_status(target="http://127.0.0.1:8000.example.com")
            )
        )

    def test_enable_uses_only_the_exact_private_serve_command(self):
        arguments = SimpleNamespace(
            confirm=tailscale_access.ENABLE_CONFIRMATION,
            user_login=None,
        )
        saved_settings = SimpleNamespace(
            origin="https://finance-laptop.tailabc.ts.net",
            user_login="owner@example.com",
        )

        with (
            patch(
                "scripts.tailscale_access.get_tailscale_status",
                return_value=TAILSCALE_STATUS,
            ),
            patch(
                "scripts.tailscale_access.get_serve_status",
                side_effect=[serve_status(target=""), serve_status()],
            ),
            patch("scripts.tailscale_access.run_tailscale") as run_tailscale,
            patch(
                "scripts.tailscale_access.save_tailscale_settings",
                return_value=saved_settings,
            ) as save_settings,
        ):
            tailscale_access.enable_access(arguments)

        run_tailscale.assert_called_once_with(
            [
                "serve",
                "--bg",
                "--https=443",
                "http://127.0.0.1:8000",
            ],
            capture_output=False,
        )
        flattened_command = " ".join(run_tailscale.call_args.args[0]).casefold()
        self.assertNotIn("funnel", flattened_command)
        save_settings.assert_called_once_with(
            "https://finance-laptop.tailabc.ts.net",
            "owner@example.com",
        )

    def test_enable_does_not_save_configuration_until_target_is_verified(self):
        arguments = SimpleNamespace(
            confirm=tailscale_access.ENABLE_CONFIRMATION,
            user_login=None,
        )

        with (
            patch(
                "scripts.tailscale_access.get_tailscale_status",
                return_value=TAILSCALE_STATUS,
            ),
            patch(
                "scripts.tailscale_access.get_serve_status",
                side_effect=[serve_status(target=""), serve_status(target="http://127.0.0.1:9000")],
            ),
            patch("scripts.tailscale_access.run_tailscale"),
            patch("scripts.tailscale_access.save_tailscale_settings") as save_settings,
        ):
            with self.assertRaisesRegex(ValueError, "localhost target"):
                tailscale_access.enable_access(arguments)

        save_settings.assert_not_called()

    def test_disable_uses_serve_off_and_never_funnel(self):
        arguments = SimpleNamespace(confirm=tailscale_access.DISABLE_CONFIRMATION)

        with (
            patch("scripts.tailscale_access.run_tailscale") as run_tailscale,
            patch("scripts.tailscale_access.remove_tailscale_settings", return_value=True),
        ):
            tailscale_access.disable_access(arguments)

        expected_command = [*tailscale_access.SERVE_ARGUMENTS, "off"]
        run_tailscale.assert_called_once_with(expected_command, capture_output=False)
        self.assertEqual(expected_command[0], "serve")
        self.assertNotIn("funnel", " ".join(expected_command).casefold())

    def test_enable_and_disable_require_exact_confirmation_phrases(self):
        with patch("scripts.tailscale_access.run_tailscale") as run_tailscale:
            with self.assertRaisesRegex(ValueError, tailscale_access.ENABLE_CONFIRMATION):
                tailscale_access.enable_access(
                    SimpleNamespace(confirm="ENABLE", user_login=None)
                )
            with self.assertRaisesRegex(ValueError, tailscale_access.DISABLE_CONFIRMATION):
                tailscale_access.disable_access(SimpleNamespace(confirm="DISABLE"))

        run_tailscale.assert_not_called()


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
