import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.bank_permissions import validate_read_only_permissions
from scripts.security_check import (
    find_hardcoded_credentials,
    find_token_leaks,
    get_app_host,
    get_git_tracked_paths,
    get_plaid_connection_mode_check,
    get_request_guard_check,
    get_tailscale_identity_check,
    get_tracked_private_file_check,
    private_path_is_unsafe,
    run_checks,
)


class SecurityCheckTests(unittest.TestCase):
    def setUp(self):  #Builds a small project folder for each security test
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temporary_directory.name)

    def tearDown(self):  #Removes the temporary project folder
        self.temporary_directory.cleanup()

    def test_permission_allowlist_matches_data_the_current_app_reads(self):
        self.assertEqual(
            validate_read_only_permissions(
                ["accounts.read", "transactions.read", "investments.read"]
            ),
            ["accounts.read", "investments.read", "transactions.read"],
        )
        with self.assertRaisesRegex(ValueError, "liabilities.read"):
            validate_read_only_permissions(["accounts.read", "liabilities.read"])

    def test_plaid_connection_mode_reports_the_production_default(self):
        self.assertEqual(
            get_plaid_connection_mode_check(),
            (True, "Plaid Production is the default connection mode"),
        )

    def test_secure_project_passes_all_checks(self):
        (self.project_root / "app.py").write_text(
            (
                'HOST = "127.0.0.1"\n'
                'TAILSCALE_USER_HEADER = "Tailscale-User-Login"\n'
                "def read_request_is_allowed():\n"
                "    return True\n"
                "def write_request_is_allowed():\n"
                "    return read_request_is_allowed()\n"
                "class Handler:\n"
                "    def send_security_headers(self):\n"
                "        self.send_header('Cache-Control', 'no-store')\n"
                "        self.send_header('Content-Security-Policy', \"frame-ancestors 'none'; form-action 'self'\")\n"
                "        self.send_header('Referrer-Policy', 'same-origin')\n"
                "        self.send_header('X-Content-Type-Options', 'nosniff')\n"
                "    def do_GET(self):\n"
                "        if not read_request_is_allowed(\n"
                "            self.headers.get(TAILSCALE_USER_HEADER)\n"
                "        ):\n"
                "            return 'Request blocked'\n"
                "    def do_POST(self):\n"
                "        if not write_request_is_allowed(\n"
                "            self.headers.get(TAILSCALE_USER_HEADER)\n"
                "        ):\n"
                "            return 'Write request blocked'\n"
            ),
            encoding="utf-8",
        )
        (self.project_root / ".gitignore").write_text(
            ".env\n.env.*\n*.key\n*.pem\n*.p12\ndata/*\n",
            encoding="utf-8",
        )
        private_data_dir = self.project_root.parent / f"{self.project_root.name}-private"

        with patch.dict(
            os.environ,
            {
                "FINANCE_HUB_BANK_PERMISSIONS": "accounts.read,transactions.read",
                "FINANCE_HUB_DATA_DIR": str(private_data_dir),
            },
            clear=True,
        ):
            checks = run_checks(self.project_root)

        self.assertTrue(all(passed for _, passed, _ in checks))

    def test_unsafe_project_fails_the_expected_checks(self):
        (self.project_root / "app.py").write_text(
            'HOST = "0.0.0.0"\nTOKEN = "configured-app-token"\n',
            encoding="utf-8",
        )
        (self.project_root / ".gitignore").write_text(".env\n", encoding="utf-8")
        project_data_dir = self.project_root / "data"
        project_data_dir.mkdir()
        (project_data_dir / "finance_hub.sqlite").write_text("", encoding="utf-8")

        with patch.dict(
            os.environ,
            {
                "FINANCE_HUB_BANK_ACCESS_TOKEN": "configured-app-token",
                "FINANCE_HUB_BANK_PERMISSIONS": "accounts.read,payments.create",
                "FINANCE_HUB_DATA_DIR": str(project_data_dir),
            },
            clear=True,
        ):
            checks = run_checks(self.project_root)

        results = {name: passed for name, passed, _ in checks}
        self.assertFalse(results["Loopback-only web server"])
        self.assertFalse(results["Browser security headers"])
        self.assertFalse(results["Read and write request guards"])
        self.assertFalse(results["Tailscale identity guard"])
        self.assertFalse(results["Secret files ignored"])
        self.assertFalse(results["Tokens kept out of project files"])
        self.assertFalse(results["Bank tokens outside environment"])
        self.assertFalse(results["Read-only bank permissions"])
        self.assertFalse(results["Private data location"])
        self.assertFalse(results["Private files outside project"])

    def test_request_guard_check_requires_reads_writes_and_tailscale_identity(self):
        secure_app = (
            'HOST = "127.0.0.1"\n'
            'TAILSCALE_USER_HEADER = "Tailscale-User-Login"\n'
            "def read_request_is_allowed():\n"
            "    return True\n"
            "def write_request_is_allowed():\n"
            "    return read_request_is_allowed()\n"
            "class Handler:\n"
            "    def do_GET(self):\n"
            "        return read_request_is_allowed(self.headers.get(TAILSCALE_USER_HEADER))\n"
            "    def do_POST(self):\n"
            "        return write_request_is_allowed(self.headers.get(TAILSCALE_USER_HEADER))\n"
        )

        cases = (
            (
                "missing GET guard",
                secure_app.replace(
                    "return read_request_is_allowed(self.headers.get(TAILSCALE_USER_HEADER))",
                    "return True",
                ),
                get_request_guard_check,
            ),
            (
                "missing POST guard",
                secure_app.replace(
                    "return write_request_is_allowed(self.headers.get(TAILSCALE_USER_HEADER))",
                    "return True",
                ),
                get_request_guard_check,
            ),
            (
                "missing GET identity",
                secure_app.replace(
                    "return read_request_is_allowed(self.headers.get(TAILSCALE_USER_HEADER))",
                    "return read_request_is_allowed(None)",
                ),
                get_tailscale_identity_check,
            ),
            (
                "missing POST identity",
                secure_app.replace(
                    "return write_request_is_allowed(self.headers.get(TAILSCALE_USER_HEADER))",
                    "return write_request_is_allowed(None)",
                ),
                get_tailscale_identity_check,
            ),
            (
                "wrong identity header",
                secure_app.replace(
                    'TAILSCALE_USER_HEADER = "Tailscale-User-Login"',
                    'TAILSCALE_USER_HEADER = "X-Untrusted-User"',
                ),
                get_tailscale_identity_check,
            ),
        )

        for case_name, source, check_function in cases:
            with self.subTest(case=case_name):
                (self.project_root / "app.py").write_text(source, encoding="utf-8")
                passed, _ = check_function(self.project_root / "app.py")
                self.assertFalse(passed)

    def test_web_server_bind_must_be_a_literal_loopback_address(self):
        app_path = self.project_root / "app.py"
        app_path.write_text('HOST = "127.0.0.1"\n', encoding="utf-8")
        self.assertEqual(get_app_host(app_path), "127.0.0.1")

        app_path.write_text('HOST = "0.0.0.0"\n', encoding="utf-8")
        self.assertNotEqual(get_app_host(app_path), "127.0.0.1")

        app_path.write_text('HOST = get_configured_host()\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            get_app_host(app_path)

    def test_recognizable_credentials_are_found_without_environment_secrets(self):
        token = "access-production-" + "12345678-1234-1234-1234-123456789abc"
        private_key_header = "-----BEGIN " + "PRIVATE KEY-----"
        files = {
            "frontend/src/connection.jsx": f'const token = "{token}";',
            "frontend/vite.config.js": f'export default {{ token: "{token}" }};',
            ".env.production": f"BANK_ACCESS_TOKEN={token}",
            "local-settings.txt": private_key_header,
        }
        for filename, contents in files.items():
            path = self.project_root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents, encoding="utf-8")

        with patch.dict(os.environ, {}, clear=True):
            findings = find_hardcoded_credentials(self.project_root)

        self.assertEqual(
            {Path(filename).as_posix() for _, filename in findings}, set(files),
        )
        self.assertNotIn(token, repr(findings))
        self.assertNotIn(private_key_header, repr(findings))

    def test_environment_token_scan_includes_frontend_and_dotenv(self):
        token = "configured-value-from-environment"
        for filename in ("connection.jsx", "config.js", ".env", ".env.production"):
            (self.project_root / filename).write_text(token, encoding="utf-8")
        with patch.dict(os.environ, {"FINANCE_HUB_BANK_TOKEN": token}, clear=True):
            leaks = find_token_leaks(self.project_root)
        self.assertEqual(len(leaks), 4)

    def test_security_failure_details_identify_files_without_exposing_credentials(self):
        token = "access-production-" + "12345678-1234-1234-1234-123456789abc"
        (self.project_root / "app.py").write_text('HOST = "127.0.0.1"\n', encoding="utf-8")
        (self.project_root / ".gitignore").write_text(".env\n", encoding="utf-8")
        (self.project_root / "connection.jsx").write_text(token, encoding="utf-8")
        with (
            patch.dict(os.environ, {
                "FINANCE_HUB_BANK_TOKEN": token,
                "FINANCE_HUB_DATA_DIR": str(self.project_root.parent / "private-data"),
            }, clear=True),
            patch("scripts.security_check.get_git_tracked_paths", return_value=["exports/accounts.json"]),
        ):
            checks = {name: (passed, details) for name, passed, details in run_checks(self.project_root)}

        for name, expected_label in (
            ("Recognizable credentials kept out of project files", "Plaid access token"),
            ("Tokens kept out of project files", "FINANCE_HUB_BANK_TOKEN"),
        ):
            passed, details = checks[name]
            self.assertFalse(passed)
            self.assertIn(expected_label, details)
            self.assertIn("connection.jsx", details)
        self.assertIn("exports/accounts.json", checks["Private files kept out of Git"][1])
        self.assertNotIn(token, repr(checks))

    def test_credential_scan_skips_installed_dependencies_and_build_outputs(self):
        token = "ghp_" + "A" * 36
        for folder in ("frontend/node_modules/vendor", "frontend/dist", ".venv/Lib"):
            path = self.project_root / folder / "sample.js"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(token, encoding="utf-8")
        (self.project_root / "example.py").write_text(
            'token = "example-access-token"\nowner = "owner@example.com"\n',
            encoding="utf-8",
        )
        self.assertEqual(find_hardcoded_credentials(self.project_root), [])

    def test_private_path_detection_keeps_seeds_and_rejects_sensitive_files(self):
        for filename in (
            "data/categories_seed.json", "data/fx_rates_seed.json", "scripts/secret_store.py",
            "tests/test_secret_store.py", "frontend/package-lock.json",
        ):
            with self.subTest(filename=filename):
                self.assertFalse(private_path_is_unsafe(filename))
        for filename in (
            "secrets/akahu-user-access-token.txt", "data/accounts.json",
            "exports/statement.json", "bank-statement.PDF", "bank.OFX",
            "client.pfx", ".env.production", "tailscale_access.json",
            "finance_hub_daily_task.cmd", "frontend/node_modules/pkg/index.js",
            "frontend/dist/assets/index.js", "finance_hub.sqlite-wal",
        ):
            with self.subTest(filename=filename):
                self.assertTrue(private_path_is_unsafe(filename))

    @unittest.skipUnless(shutil.which("git"), "Git is optional for ZIP installs")
    def test_git_index_check_catches_force_added_private_files(self):
        command = ["git", "-c", f"safe.directory={self.project_root.as_posix()}"]
        subprocess.run(
            [*command, "init", "--quiet"], cwd=self.project_root,
            capture_output=True, check=True,
        )
        (self.project_root / ".gitignore").write_text("*.sqlite\n", encoding="utf-8")
        database_path = self.project_root / "finance_hub.sqlite"
        database_path.write_bytes(b"synthetic private file")
        subprocess.run(
            [*command, "add", "--force", "finance_hub.sqlite"], cwd=self.project_root,
            capture_output=True, check=True,
        )
        database_path.unlink()  #The staged copy remains dangerous after a local deletion.

        self.assertEqual(get_git_tracked_paths(self.project_root), ["finance_hub.sqlite"])
        passed, details = get_tracked_private_file_check(self.project_root)
        self.assertFalse(passed)
        self.assertIn("1 private or generated file", details)
        self.assertIn("finance_hub.sqlite", details)

    def test_zip_install_without_git_can_run_the_security_check(self):
        with patch("scripts.security_check.subprocess.run", side_effect=FileNotFoundError):
            passed, details = get_tracked_private_file_check(self.project_root)
        self.assertTrue(passed)
        self.assertIn("Skipped", details)


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
