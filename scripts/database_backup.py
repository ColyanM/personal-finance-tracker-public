import argparse
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

try:
    from scripts.paths import BACKUP_DIR, DATABASE_PATH, PROJECT_ROOT, private_data_dir_is_safe
except ModuleNotFoundError:
    from paths import BACKUP_DIR, DATABASE_PATH, PROJECT_ROOT, private_data_dir_is_safe


def check_database(database_path):  #Stops before SQLite creates an empty database by mistake
    if not database_path.exists():
        raise FileNotFoundError(f"Database not found: {database_path}")


def check_backup_dir(backup_dir):  #Keeps database backups outside the project and OneDrive
    if not private_data_dir_is_safe(backup_dir, PROJECT_ROOT):
        raise ValueError("Backup folder must be outside the project and OneDrive")


def check_integrity(database_path):  #Checks a database can be safely opened by SQLite
    check_database(database_path)
    database_uri = f"{database_path.resolve().as_uri()}?mode=ro"

    with closing(sqlite3.connect(database_uri, uri=True)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]

    if result != "ok":
        raise ValueError(f"Database integrity check failed: {result}")


def create_backup(database_path=DATABASE_PATH, backup_dir=BACKUP_DIR, label="backup"):  #Creates one checked SQLite backup
    check_database(database_path)
    check_backup_dir(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"finance_hub-{label}-{timestamp}.sqlite"

    with (
        closing(sqlite3.connect(database_path)) as source,
        closing(sqlite3.connect(backup_path)) as target,
    ):
        source.backup(target)  #Uses SQLite's safe backup method

    check_integrity(backup_path)
    return backup_path


def list_backups(backup_dir=BACKUP_DIR):  #Lists newest backups first
    check_backup_dir(backup_dir)
    if not backup_dir.exists():
        return []

    return sorted(
        backup_dir.glob("finance_hub-*.sqlite"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )


def get_backup_path(filename, backup_dir=BACKUP_DIR):  #Only allows files from the private backup folder
    backup_dir = backup_dir.resolve()
    backup_path = (backup_dir / filename).resolve()

    try:
        backup_path.relative_to(backup_dir)
    except ValueError as error:
        raise ValueError("Backup file must be inside the Finance Hub backup folder") from error

    if backup_path not in list_backups(backup_dir):
        raise ValueError("Backup file must be shown by the list command")

    return backup_path


def restore_backup(filename, confirmation, database_path=DATABASE_PATH, backup_dir=BACKUP_DIR):  #Safely restores one listed backup
    if confirmation != "RESTORE":
        raise ValueError("Restore confirmation must be RESTORE")

    check_backup_dir(backup_dir)
    backup_path = get_backup_path(filename, backup_dir)
    check_integrity(backup_path)
    safety_backup = create_backup(database_path, backup_dir, label="before-restore")
    database_path.parent.mkdir(parents=True, exist_ok=True)

    temporary_file = tempfile.NamedTemporaryFile(
        prefix="finance_hub-restore-",
        suffix=".sqlite",
        dir=database_path.parent,
        delete=False,
    )
    temporary_path = Path(temporary_file.name)
    temporary_file.close()

    try:
        with (
            closing(sqlite3.connect(backup_path)) as source,
            closing(sqlite3.connect(temporary_path)) as target,
        ):
            source.backup(target)

        check_integrity(temporary_path)
        os.replace(temporary_path, database_path)  #Replaces the database only after every check passes
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

    return safety_backup


def cleanup_backups(keep, confirmation, backup_dir=BACKUP_DIR):  #Deletes older backups only after confirmation
    if confirmation != "DELETE_OLD_BACKUPS":
        raise ValueError("Backup cleanup confirmation must be DELETE_OLD_BACKUPS")

    if keep < 1:
        raise ValueError("Keep at least one backup")

    backups = list_backups(backup_dir)
    deleted = []
    for backup_path in backups[keep:]:
        backup_path.unlink()
        deleted.append(backup_path.name)

    return deleted


def print_backups(backups):  #Prints backup file names without exposing other private paths
    if not backups:
        print("No backups found")
        return

    for backup_path in backups:
        print(backup_path.name)


def read_arguments():  #Reads the backup or restore command
    parser = argparse.ArgumentParser(description="Back up and restore the Finance Hub database")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("backup", help="Create a database backup")
    subparsers.add_parser("list", help="List available backups")

    restore_parser = subparsers.add_parser("restore", help="Restore a saved backup")
    restore_parser.add_argument("filename", help="Backup file name shown by the list command")
    restore_parser.add_argument("--confirm", required=True, help="Type RESTORE to continue")

    cleanup_parser = subparsers.add_parser("cleanup", help="Delete old backups")
    cleanup_parser.add_argument("--keep", type=int, default=10)
    cleanup_parser.add_argument("--confirm", required=True, help="Type DELETE_OLD_BACKUPS to continue")
    return parser.parse_args()


def main():  #Runs the selected backup command
    arguments = read_arguments()

    try:
        if arguments.command == "backup":
            backup_path = create_backup()
            print(f"Backup created: {backup_path.name}")
        elif arguments.command == "list":
            print_backups(list_backups())
        elif arguments.command == "restore":
            safety_backup = restore_backup(arguments.filename, arguments.confirm)
            print(f"Database restored from: {arguments.filename}")
            print(f"Previous database backed up as: {safety_backup.name}")
        elif arguments.command == "cleanup":
            deleted = cleanup_backups(arguments.keep, arguments.confirm)
            print(f"Old backups deleted: {len(deleted)}")
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage database backup: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
