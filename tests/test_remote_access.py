import json
import tempfile
import unittest
from pathlib import Path

from scripts.remote_access import (
    CONFIG_VERSION,
    ORIGIN_ENVIRONMENT_VARIABLE,
    USER_ENVIRONMENT_VARIABLE,
    TailscaleAccessSettings,
    build_tailscale_settings,
    clean_tailscale_origin,
    clean_tailscale_user,
    load_tailscale_settings,
    remove_tailscale_settings,
    save_tailscale_settings,
    settings_from_data,
)


TAILSCALE_ORIGIN = "https://finance-laptop.personal-tail.ts.net"
TAILSCALE_USER = "owner@example.com"


class RemoteAccessTests(unittest.TestCase):
    def test_origin_and_user_are_cleaned_into_exact_settings(self):
        settings = build_tailscale_settings(
            "  HTTPS://Finance-Laptop.Personal-Tail.TS.NET/  ",
            "  Owner@Example.com  ",
        )

        self.assertEqual(
            settings,
            TailscaleAccessSettings(
                origin=TAILSCALE_ORIGIN,
                host="finance-laptop.personal-tail.ts.net",
                user_login="Owner@Example.com",
            ),
        )

    def test_origin_validation_rejects_non_tailscale_or_non_origin_values(self):
        invalid_origins = (
            "http://finance-laptop.personal-tail.ts.net",
            "https://finance-laptop.personal-tail.ts.net.evil.example",
            "https://finance-laptop.personal-tail.example",
            "https://device.ts.net",
            "https://*.personal-tail.ts.net",
            "https://owner@finance-laptop.personal-tail.ts.net",
            "https://finance-laptop.personal-tail.ts.net:443",
            "https://finance-laptop.personal-tail.ts.net/app",
            "https://finance-laptop.personal-tail.ts.net?mode=remote",
            "https://finance-laptop.personal-tail.ts.net#remote",
        )

        for origin in invalid_origins:
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                clean_tailscale_origin(origin)

    def test_user_validation_rejects_missing_control_or_oversized_values(self):
        invalid_users = (None, "", "   ", "owner\n@example.com", "x" * 321)

        for user_login in invalid_users:
            with self.subTest(user_login=user_login), self.assertRaises(ValueError):
                clean_tailscale_user(user_login)

    def test_json_configuration_requires_the_supported_version_and_mode(self):
        valid_data = {
            "version": CONFIG_VERSION,
            "mode": "tailscale-serve",
            "origin": TAILSCALE_ORIGIN,
            "user_login": TAILSCALE_USER,
        }
        self.assertEqual(
            settings_from_data(valid_data),
            build_tailscale_settings(TAILSCALE_ORIGIN, TAILSCALE_USER),
        )

        invalid_data = (
            [],
            {**valid_data, "version": CONFIG_VERSION + 1},
            {**valid_data, "mode": "funnel"},
            {**valid_data, "origin": "https://example.com"},
            {**valid_data, "user_login": ""},
        )
        for data in invalid_data:
            with self.subTest(data=data), self.assertRaises(ValueError):
                settings_from_data(data)

    def test_environment_configuration_requires_an_origin_and_user_pair(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            config_path = Path(temporary_folder) / "missing.json"

            self.assertIsNone(load_tailscale_settings(config_path, environment={}))
            for environment in (
                {ORIGIN_ENVIRONMENT_VARIABLE: TAILSCALE_ORIGIN},
                {USER_ENVIRONMENT_VARIABLE: TAILSCALE_USER},
            ):
                with self.subTest(environment=environment), self.assertRaisesRegex(
                    ValueError,
                    "Set both",
                ):
                    load_tailscale_settings(config_path, environment=environment)

            loaded = load_tailscale_settings(
                config_path,
                environment={
                    ORIGIN_ENVIRONMENT_VARIABLE: TAILSCALE_ORIGIN,
                    USER_ENVIRONMENT_VARIABLE: TAILSCALE_USER,
                },
            )

        self.assertEqual(
            loaded,
            build_tailscale_settings(TAILSCALE_ORIGIN, TAILSCALE_USER),
        )

    def test_environment_pair_takes_precedence_over_the_private_file(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            config_path = Path(temporary_folder) / "tailscale_access.json"
            save_tailscale_settings(
                "https://old-laptop.personal-tail.ts.net",
                "old-owner@example.com",
                config_path,
            )

            loaded = load_tailscale_settings(
                config_path,
                environment={
                    ORIGIN_ENVIRONMENT_VARIABLE: TAILSCALE_ORIGIN,
                    USER_ENVIRONMENT_VARIABLE: TAILSCALE_USER,
                },
            )

        self.assertEqual(
            loaded,
            build_tailscale_settings(TAILSCALE_ORIGIN, TAILSCALE_USER),
        )

    def test_private_file_round_trip_and_remove(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            config_path = Path(temporary_folder) / "private" / "tailscale_access.json"
            saved = save_tailscale_settings(
                "HTTPS://Finance-Laptop.Personal-Tail.TS.NET/",
                TAILSCALE_USER,
                config_path,
            )
            loaded = load_tailscale_settings(config_path, environment={})
            data = json.loads(config_path.read_text(encoding="utf-8"))

            self.assertEqual(loaded, saved)
            self.assertEqual(
                data,
                {
                    "version": CONFIG_VERSION,
                    "mode": "tailscale-serve",
                    "origin": TAILSCALE_ORIGIN,
                    "user_login": TAILSCALE_USER,
                },
            )
            self.assertFalse(config_path.with_suffix(".json.tmp").exists())
            self.assertTrue(remove_tailscale_settings(config_path))
            self.assertFalse(config_path.exists())
            self.assertFalse(remove_tailscale_settings(config_path))

    def test_invalid_private_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            config_path = Path(temporary_folder) / "tailscale_access.json"
            config_path.write_text("not-json", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "not valid JSON"):
                load_tailscale_settings(config_path, environment={})


if __name__ == "__main__":
    unittest.main()
