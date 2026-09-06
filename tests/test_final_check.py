import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from scripts import final_check


class FinalCheckTests(unittest.TestCase):
    def test_private_phone_access_is_optional_but_malformed_settings_fail(self):
        with patch.object(final_check, "load_tailscale_settings", return_value=None):
            name, passed, details = final_check.get_private_access_check()

        self.assertEqual(name, "Private phone access")
        self.assertTrue(passed)
        self.assertIn("local-only", details)

        with patch.object(
            final_check,
            "load_tailscale_settings",
            side_effect=ValueError("invalid private origin"),
        ):
            name, passed, details = final_check.get_private_access_check()

        self.assertFalse(passed)
        self.assertEqual(details, "invalid private origin")

    def test_timezone_check_passes_when_auckland_is_available(self):
        with patch.object(final_check, "ZoneInfo", return_value=object()):
            name, passed, details = final_check.get_timezone_check()

        self.assertEqual(name, "Timezone data")
        self.assertTrue(passed)
        self.assertIn("Pacific/Auckland", details)

    def test_timezone_check_explains_how_to_install_missing_data(self):
        with patch.object(
            final_check,
            "ZoneInfo",
            side_effect=ZoneInfoNotFoundError("Pacific/Auckland"),
        ):
            name, passed, details = final_check.get_timezone_check()

        self.assertEqual(name, "Timezone data")
        self.assertFalse(passed)
        self.assertEqual(details, "Run python -m pip install -r requirements.txt")


if __name__ == "__main__":
    unittest.main()
