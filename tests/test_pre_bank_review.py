import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from scripts import bnz_refresh, plaid_transactions, pre_bank_review


class PreBankReviewTests(unittest.TestCase):
    def test_plaid_review_checks_only_production_credentials(self):
        config = {
            "label": "Plaid Production",
            "client_id_name": "plaid-production-client-id",
            "client_id_label": "Plaid Production Client ID",
            "secret_name": "plaid-production-secret",
            "secret_label": "Plaid Production Secret",
            "user_id_name": "plaid-production-user-id",
        }
        secret_dir = Path("private-secrets")

        with (
            patch("scripts.pre_bank_review.get_plaid_config", return_value=config),
            patch("scripts.pre_bank_review.has_secret", return_value=True) as has_secret,
        ):
            result = pre_bank_review.get_provider_credential_check("plaid", secret_dir)

        self.assertEqual(
            result,
            ("Plaid Production credentials", True, "Encrypted credentials are configured"),
        )
        self.assertEqual(
            has_secret.call_args_list,
            [
                call("plaid-production-client-id", secret_dir),
                call("plaid-production-secret", secret_dir),
                call("plaid-production-user-id", secret_dir),
            ],
        )

    def test_bnz_review_checks_only_akahu_tokens(self):
        secret_dir = Path("private-secrets")

        with patch("scripts.pre_bank_review.has_secret", return_value=True) as has_secret:
            result = pre_bank_review.get_provider_credential_check("bnz", secret_dir)

        self.assertEqual(
            result,
            ("Akahu tokens", True, "Both encrypted tokens are configured"),
        )
        self.assertEqual(
            has_secret.call_args_list,
            [call(secret_name, secret_dir) for secret_name, _ in pre_bank_review.AKAHU_SECRETS],
        )

    def test_review_includes_only_the_requested_provider_check(self):
        with (
            patch("scripts.pre_bank_review.run_checks", return_value=[]),
            patch(
                "scripts.pre_bank_review.get_database_check",
                return_value=("Database ready", True, "ready"),
            ),
            patch(
                "scripts.pre_bank_review.get_backup_check",
                return_value=("Verified backup", True, "ready"),
            ),
            patch(
                "scripts.pre_bank_review.get_provider_credential_check",
                return_value=("Plaid Production credentials", True, "ready"),
            ) as provider_check,
            patch("scripts.pre_bank_review.private_data_dir_is_safe", return_value=True),
        ):
            checks = pre_bank_review.run_review(
                "plaid",
                project_root=Path("project"),
                database_path=Path("database.sqlite"),
                backup_dir=Path("backups"),
                secret_dir=Path("secrets"),
                powershell_path=Path(__file__),
            )

        provider_check.assert_called_once_with("plaid", Path("secrets"))
        self.assertIn(("Plaid Production credentials", True, "ready"), checks)
        self.assertNotIn("Akahu tokens", [name for name, _, _ in checks])

    def test_missing_backup_points_to_explicit_bootstrap_command(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            backup_dir = Path(temporary_folder) / "backups"
            with patch("scripts.pre_bank_review.list_backups", return_value=[]):
                result = pre_bank_review.get_backup_check(backup_dir)

        self.assertEqual(
            result,
            (
                "Verified backup",
                False,
                "Run python scripts\\database_backup.py backup first",
            ),
        )

    def test_provider_must_be_explicit_and_supported(self):
        for provider in (None, "", "all", "akahu"):
            with self.subTest(provider=provider):
                with self.assertRaisesRegex(ValueError, "Bank provider must be one of"):
                    pre_bank_review.clean_provider(provider)

    def test_refresh_review_reports_the_backup_bootstrap_command(self):
        checks = [
            ("Database ready", True, "ready"),
            (
                "Verified backup",
                False,
                "Run python scripts\\database_backup.py backup first",
            ),
            ("Plaid Production credentials", True, "ready"),
        ]
        database_path = Path("finance_hub.sqlite")
        backup_dir = Path("backups")

        with patch("scripts.pre_bank_review.run_review", return_value=checks) as run_review:
            with self.assertRaisesRegex(
                ValueError,
                r"Verified backup\. Run python scripts\\database_backup\.py backup first",
            ):
                pre_bank_review.require_safe_review("plaid", database_path, backup_dir)

        run_review.assert_called_once_with(
            "plaid",
            database_path=database_path,
            backup_dir=backup_dir,
        )

    def test_bnz_refresh_requests_the_bnz_safety_review(self):
        database_path = Path("finance_hub.sqlite")
        backup_dir = Path("backups")

        with patch(
            "scripts.bnz_refresh.require_safe_review",
            side_effect=RuntimeError("stop after review"),
        ) as require_review:
            with self.assertRaisesRegex(RuntimeError, "stop after review"):
                bnz_refresh.refresh_bnz(1, "REFRESH", database_path, backup_dir)

        require_review.assert_called_once_with("bnz", database_path, backup_dir)

    def test_plaid_refresh_requests_the_plaid_safety_review(self):
        database_path = Path("finance_hub.sqlite")
        backup_dir = Path("backups")

        with patch(
            "scripts.plaid_transactions.require_safe_review",
            side_effect=RuntimeError("stop after review"),
        ) as require_review:
            with self.assertRaisesRegex(RuntimeError, "stop after review"):
                plaid_transactions.refresh_plaid(1, "REFRESH", database_path, backup_dir)

        require_review.assert_called_once_with("plaid", database_path, backup_dir)


if __name__ == "__main__":
    unittest.main()
