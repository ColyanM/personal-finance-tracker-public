import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent  #Gets back to the main project folder
SEED_DATA_DIR = PROJECT_ROOT / "data"  #Keeps the non-private starting data with the project
SEED_FILES = {"categories_seed.json", "fx_rates_seed.json"}


def get_private_data_dir():  #Keeps private finance files outside the project and OneDrive
    custom_path = os.getenv("FINANCE_HUB_DATA_DIR")
    if custom_path:
        return Path(custom_path).expanduser()

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "FinanceHub"

    return Path.home() / ".finance_hub"


def private_data_dir_is_safe(private_data_dir, project_root=PROJECT_ROOT):  #Checks private files stay outside synced project folders
    private_data_dir = private_data_dir.resolve()
    project_root = project_root.resolve()

    try:
        private_data_dir.relative_to(project_root)
        return False
    except ValueError:
        pass

    return not any("onedrive" in part.lower() for part in private_data_dir.parts)


PRIVATE_DATA_DIR = get_private_data_dir()
DATABASE_PATH = PRIVATE_DATA_DIR / "finance_hub.sqlite"
BACKUP_DIR = PRIVATE_DATA_DIR / "backups"
EXPORT_DIR = PRIVATE_DATA_DIR / "exports"
