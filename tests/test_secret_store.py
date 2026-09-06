import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.secret_store import (
    delete_secret,
    get_secret,
    get_secret_path,
    set_secret,
    set_secret_value,
)


class SecretStoreTests(unittest.TestCase):
    def test_secret_can_be_saved_read_and_deleted_without_print_command(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            private_data_dir = Path(temporary_folder)
            secret_dir = private_data_dir / "secrets"
            secret_path = secret_dir / "test-bank-token.txt"
            completed_process = Mock(stdout="decrypted-token\r\n")

            with (
                patch("scripts.secret_store.PRIVATE_DATA_DIR", private_data_dir),
                patch("scripts.secret_store.SECRET_DIR", secret_dir),
                patch("scripts.secret_store.PROJECT_ROOT", private_data_dir / "project"),
                patch("scripts.secret_store.run_powershell") as run_powershell,
            ):
                set_secret("test-bank-token")
                set_secret_value("test-api-token", "returned-api-token")
                secret_path.write_text("encrypted-token", encoding="utf-8")
                run_powershell.return_value = completed_process
                secret_value = get_secret("test-bank-token")
                deleted = delete_secret("test-bank-token")

            self.assertEqual(secret_value, "decrypted-token")
            self.assertTrue(deleted)
            self.assertFalse(secret_path.exists())
            self.assertEqual(run_powershell.call_count, 3)
            value_call = run_powershell.call_args_list[1]
            self.assertNotIn("returned-api-token", value_call.args[0])
            self.assertEqual(value_call.kwargs["input_text"], "returned-api-token")

    def test_secret_name_cannot_leave_the_secret_folder(self):
        with self.assertRaises(ValueError):
            get_secret_path("../unsafe-token")

if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
