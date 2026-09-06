import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import database_backup, transactions


class BackupExportTests(unittest.TestCase):
    def test_transaction_exports_default_to_private_folder(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            private_folder = Path(temporary_folder) / "private"
            project_folder = Path(temporary_folder) / "project"
            export_folder = private_folder / "exports"

            with patch.object(transactions, "PROJECT_ROOT", project_folder):
                export_path = transactions.get_export_path(Path("transactions.csv"), export_folder)

            self.assertEqual(export_path, export_folder / "transactions.csv")

            with (
                patch.object(transactions, "PROJECT_ROOT", project_folder),
                self.assertRaises(ValueError),
            ):
                transactions.get_export_path(project_folder / "bad.csv", export_folder)

    def test_old_backups_are_deleted_only_after_confirmation(self):
        with tempfile.TemporaryDirectory() as temporary_folder:
            backup_folder = Path(temporary_folder) / "backups"
            backup_folder.mkdir()
            backup_files = []

            for index in range(3):
                backup_path = backup_folder / f"finance_hub-backup-{index}.sqlite"
                backup_path.write_text("backup", encoding="utf-8")
                os.utime(backup_path, (index + 1, index + 1))  #Makes the newest file easy to check
                backup_files.append(backup_path)

            with self.assertRaises(ValueError):
                database_backup.cleanup_backups(2, "WRONG", backup_folder)

            deleted = database_backup.cleanup_backups(2, "DELETE_OLD_BACKUPS", backup_folder)

            self.assertEqual(deleted, [backup_files[0].name])
            self.assertFalse(backup_files[0].exists())
            self.assertTrue(backup_files[1].exists())
            self.assertTrue(backup_files[2].exists())


if __name__ == "__main__":
    unittest.main()  #Allows this test file to run directly
