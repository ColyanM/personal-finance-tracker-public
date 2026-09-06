import argparse
import sqlite3
import sys
from contextlib import closing

try:
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from paths import DATABASE_PATH

CURRENT_SCHEMA_VERSION = 20  #Version 20 adds fast opening-balance history lookups


SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_account_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    institution TEXT,
    account_type TEXT,
    balance_type TEXT NOT NULL DEFAULT 'asset'
        CHECK (balance_type IN ('asset', 'liability')),
    native_currency TEXT NOT NULL CHECK (native_currency IN ('NZD', 'USD')),
    current_balance_minor INTEGER NOT NULL DEFAULT 0,
    balance_as_of TEXT,
    manual_interest_rate TEXT,
    manual_payment_minor INTEGER,
    manual_payment_day INTEGER CHECK (manual_payment_day BETWEEN 1 AND 31),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, provider_account_id)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    group_name TEXT,
    is_income INTEGER NOT NULL DEFAULT 0 CHECK (is_income IN (0, 1)),
    is_transfer INTEGER NOT NULL DEFAULT 0 CHECK (is_transfer IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_text TEXT NOT NULL COLLATE NOCASE UNIQUE,
    category_id INTEGER NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100 CHECK (priority >= 1),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories (id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_transaction_id TEXT NOT NULL,
    account_id INTEGER NOT NULL,
    posted_date TEXT NOT NULL,
    authorized_date TEXT,
    description_raw TEXT NOT NULL,
    merchant_clean TEXT,
    category_id INTEGER,
    original_currency TEXT NOT NULL CHECK (original_currency IN ('NZD', 'USD')),
    original_amount_minor INTEGER NOT NULL,
    fx_rate_to_nzd TEXT NOT NULL,
    fx_rate_to_usd TEXT NOT NULL,
    fx_rate_source TEXT NOT NULL,
    fx_rate_date TEXT NOT NULL,
    amount_nzd_minor_fixed INTEGER NOT NULL,
    amount_usd_minor_fixed INTEGER NOT NULL,
    transaction_status TEXT NOT NULL DEFAULT 'posted'
        CHECK (transaction_status IN ('pending', 'posted')),
    review_status TEXT NOT NULL DEFAULT 'not_reviewed'
        CHECK (review_status IN ('not_reviewed', 'reviewed')),
    reviewed_at TEXT,
    pending_provider_transaction_id TEXT,
    notes TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (provider, provider_transaction_id),
    FOREIGN KEY (account_id) REFERENCES accounts (id),
    FOREIGN KEY (category_id) REFERENCES categories (id)
);

CREATE TABLE IF NOT EXISTS transaction_splits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL,
    split_order INTEGER NOT NULL CHECK (split_order >= 1),
    category_id INTEGER NOT NULL,
    amount_nzd_minor_fixed INTEGER NOT NULL,
    amount_usd_minor_fixed INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (transaction_id, split_order),
    FOREIGN KEY (transaction_id) REFERENCES transactions (id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories (id)
);

CREATE TABLE IF NOT EXISTS fx_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_currency TEXT NOT NULL CHECK (base_currency IN ('NZD', 'USD')),
    quote_currency TEXT NOT NULL CHECK (quote_currency IN ('NZD', 'USD')),
    rate TEXT NOT NULL,
    rate_date TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (base_currency, quote_currency, rate_date, source)
);

CREATE TABLE IF NOT EXISTS weekly_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    week_start_date TEXT NOT NULL,
    budget_currency TEXT NOT NULL CHECK (budget_currency IN ('NZD', 'USD')),
    budget_amount_minor INTEGER NOT NULL,
    rollover_enabled INTEGER NOT NULL DEFAULT 0 CHECK (rollover_enabled IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (category_id, week_start_date, budget_currency),
    FOREIGN KEY (category_id) REFERENCES categories (id)
);

CREATE TABLE IF NOT EXISTS weekly_budget_rollovers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    from_week_start_date TEXT NOT NULL,
    to_week_start_date TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency IN ('NZD', 'USD')),
    rollover_amount_minor INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (category_id, from_week_start_date, to_week_start_date, currency),
    FOREIGN KEY (category_id) REFERENCES categories (id)
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Legacy schema compatibility only. Current code preserves existing rows but no
-- longer requests or stores Plaid liability details that the React UI cannot show.
CREATE TABLE IF NOT EXISTS liability_details (
    account_id INTEGER PRIMARY KEY,
    liability_type TEXT NOT NULL CHECK (liability_type IN ('credit_card', 'student_loan')),
    interest_rate TEXT,
    minimum_payment_minor INTEGER,
    next_payment_due_date TEXT,
    last_statement_balance_minor INTEGER,
    last_payment_minor INTEGER,
    last_payment_date TEXT,
    original_principal_minor INTEGER,
    outstanding_interest_minor INTEGER,
    loan_status TEXT,
    is_overdue INTEGER CHECK (is_overdue IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS investment_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    security_name TEXT,
    asset_class TEXT NOT NULL DEFAULT 'Equity',
    quantity TEXT NOT NULL DEFAULT '0',
    price_minor INTEGER,
    value_minor INTEGER NOT NULL,
    currency TEXT NOT NULL CHECK (currency IN ('NZD', 'USD')),
    as_of TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (account_id, ticker),
    FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS account_balance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    account_name TEXT NOT NULL,
    account_id INTEGER,
    balance_date TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency IN ('NZD', 'USD')),
    balance_minor INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, account_name, balance_date, currency),
    FOREIGN KEY (account_id) REFERENCES accounts (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    currency TEXT NOT NULL CHECK (currency IN ('NZD', 'USD')),
    target_amount_minor INTEGER NOT NULL CHECK (target_amount_minor > 0),
    saved_amount_minor INTEGER NOT NULL DEFAULT 0 CHECK (saved_amount_minor >= 0),
    target_date TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO app_settings (key, value)
VALUES
    ('display_currency', 'NZD'),
    ('daily_alert_enabled', 'false'),
    ('daily_alert_send_if_empty', 'false'),
    ('daily_alert_time', '07:00'),
    ('daily_alert_currency', 'NZD'),
    ('daily_alert_hours', '24'),
    ('daily_alert_max_rows', '20');

CREATE INDEX IF NOT EXISTS idx_transactions_account_id
    ON transactions (account_id);

CREATE INDEX IF NOT EXISTS idx_transactions_posted_date
    ON transactions (posted_date);

CREATE INDEX IF NOT EXISTS idx_transactions_category_id
    ON transactions (category_id);

CREATE INDEX IF NOT EXISTS idx_transaction_splits_transaction_id
    ON transaction_splits (transaction_id);

CREATE INDEX IF NOT EXISTS idx_transaction_splits_category_id
    ON transaction_splits (category_id);

CREATE INDEX IF NOT EXISTS idx_fx_rates_lookup
    ON fx_rates (base_currency, quote_currency, rate_date);

CREATE INDEX IF NOT EXISTS idx_weekly_budgets_week_start
    ON weekly_budgets (week_start_date);

CREATE INDEX IF NOT EXISTS idx_weekly_budgets_category_id
    ON weekly_budgets (category_id);

CREATE INDEX IF NOT EXISTS idx_weekly_budget_rollovers_to_week
    ON weekly_budget_rollovers (to_week_start_date);

CREATE INDEX IF NOT EXISTS idx_automation_runs_started_at
    ON automation_runs (started_at);

CREATE INDEX IF NOT EXISTS idx_goals_active
    ON goals (is_active);

CREATE INDEX IF NOT EXISTS idx_investment_positions_account
    ON investment_positions (account_id);

CREATE INDEX IF NOT EXISTS idx_balance_history_date
    ON account_balance_history (balance_date);

CREATE INDEX IF NOT EXISTS idx_balance_history_linked_opening
    ON account_balance_history (account_id, balance_date DESC, id DESC)
    WHERE account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_balance_history_legacy_opening
    ON account_balance_history (source, account_name, currency, balance_date DESC)
    WHERE account_id IS NULL;
"""  #Money uses integer cents and exchange rates use text for Decimal

TRANSACTION_COLUMN_MIGRATIONS = {  #Adds missing fields without deleting existing transactions
    "transaction_status": """
        ALTER TABLE transactions
        ADD COLUMN transaction_status TEXT NOT NULL DEFAULT 'posted'
        CHECK (transaction_status IN ('pending', 'posted'))
    """,
    "review_status": """
        ALTER TABLE transactions
        ADD COLUMN review_status TEXT NOT NULL DEFAULT 'not_reviewed'
        CHECK (review_status IN ('not_reviewed', 'reviewed'))
    """,
    "reviewed_at": """
        ALTER TABLE transactions
        ADD COLUMN reviewed_at TEXT
    """,
    "pending_provider_transaction_id": """
        ALTER TABLE transactions
        ADD COLUMN pending_provider_transaction_id TEXT
    """,
}

TRANSACTION_STATUS_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_transactions_status
    ON transactions (transaction_status);

CREATE INDEX IF NOT EXISTS idx_transactions_review_status
    ON transactions (review_status);

CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_pending_provider_id
    ON transactions (provider, pending_provider_transaction_id)
    WHERE pending_provider_transaction_id IS NOT NULL;
"""  #Makes status filters fast and blocks duplicate pending IDs

TRANSACTION_SPLIT_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_transaction_splits_transaction_id
    ON transaction_splits (transaction_id);

CREATE INDEX IF NOT EXISTS idx_transaction_splits_category_id
    ON transaction_splits (category_id);
"""

WEEKLY_BUDGET_COLUMN_MIGRATIONS = {  #Adds missing budget fields without deleting saved budgets
    "rollover_enabled": """
        ALTER TABLE weekly_budgets
        ADD COLUMN rollover_enabled INTEGER NOT NULL DEFAULT 0
        CHECK (rollover_enabled IN (0, 1))
    """,
}

ACCOUNT_COLUMN_MIGRATIONS = {  #Existing accounts start as assets until their provider refreshes them
    "balance_type": """
        ALTER TABLE accounts
        ADD COLUMN balance_type TEXT NOT NULL DEFAULT 'asset'
        CHECK (balance_type IN ('asset', 'liability'))
    """,
    "manual_interest_rate": """
        ALTER TABLE accounts
        ADD COLUMN manual_interest_rate TEXT
    """,
    "manual_payment_minor": """
        ALTER TABLE accounts
        ADD COLUMN manual_payment_minor INTEGER
    """,
    "manual_payment_day": """
        ALTER TABLE accounts
        ADD COLUMN manual_payment_day INTEGER
        CHECK (manual_payment_day BETWEEN 1 AND 31)
    """,
}


def read_arguments():  #Reads optional checks like showing tables or columns
    parser = argparse.ArgumentParser(  #Supports checks from PowerShell without opening SQLite
        description="Create the Finance Hub SQLite database."
    )
    parser.add_argument(
        "--show-tables",
        action="store_true",
        help="Print the database table names after creating the database.",
    )
    parser.add_argument(
        "--show-transaction-columns",
        action="store_true",
        help="Print the transactions table columns after updating the database.",
    )
    return parser.parse_args()


def get_column_names(connection, table_name):  #Gets the saved column names for one table
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()  #Reads SQLite's saved table details
    return [row[1] for row in rows]


def migrate_transaction_columns(connection):  #Adds newer transaction columns to older databases
    existing_columns = set(get_column_names(connection, "transactions"))  #Stops the same migration running twice

    for column_name, migration_sql in TRANSACTION_COLUMN_MIGRATIONS.items():
        if column_name not in existing_columns:
            connection.execute(migration_sql)  #Existing rows receive the safe defaults in the SQL

    connection.executescript(TRANSACTION_STATUS_INDEXES)


def migrate_weekly_budget_columns(connection):  #Adds newer budget columns to older databases
    existing_columns = set(get_column_names(connection, "weekly_budgets"))

    for column_name, migration_sql in WEEKLY_BUDGET_COLUMN_MIGRATIONS.items():
        if column_name not in existing_columns:
            connection.execute(migration_sql)


def migrate_account_columns(connection):  #Adds account fields without replacing saved balances
    existing_columns = set(get_column_names(connection, "accounts"))

    for column_name, migration_sql in ACCOUNT_COLUMN_MIGRATIONS.items():
        if column_name not in existing_columns:
            connection.execute(migration_sql)


def migrate_transaction_split_order(connection):  #Lets saved splits grow past two categories
    row = connection.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'transaction_splits'
        """
    ).fetchone()

    if not row or "split_order IN (1, 2)" not in (row[0] or ""):
        return

    connection.execute("ALTER TABLE transaction_splits RENAME TO transaction_splits_old")
    connection.execute(
        """
        CREATE TABLE transaction_splits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL,
            split_order INTEGER NOT NULL CHECK (split_order >= 1),
            category_id INTEGER NOT NULL,
            amount_nzd_minor_fixed INTEGER NOT NULL,
            amount_usd_minor_fixed INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (transaction_id, split_order),
            FOREIGN KEY (transaction_id) REFERENCES transactions (id) ON DELETE CASCADE,
            FOREIGN KEY (category_id) REFERENCES categories (id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO transaction_splits (
            id,
            transaction_id,
            split_order,
            category_id,
            amount_nzd_minor_fixed,
            amount_usd_minor_fixed,
            created_at,
            updated_at
        )
        SELECT
            id,
            transaction_id,
            split_order,
            category_id,
            amount_nzd_minor_fixed,
            amount_usd_minor_fixed,
            created_at,
            updated_at
        FROM transaction_splits_old
        """
    )
    connection.execute("DROP TABLE transaction_splits_old")
    connection.executescript(TRANSACTION_SPLIT_INDEXES)


def migrate_plaid_providers(connection):  #Marks every existing Plaid row as Sandbox data
    connection.execute(
        "UPDATE accounts SET provider = 'plaid-sandbox-us' WHERE provider = 'plaid-us'"
    )
    connection.execute(
        "UPDATE transactions SET provider = 'plaid-sandbox-us' WHERE provider = 'plaid-us'"
    )


def create_database(database_path=DATABASE_PATH):  #Creates or updates the local SQLite database
    database_path.parent.mkdir(parents=True, exist_ok=True)  #Creates the private folder on the first run

    with closing(sqlite3.connect(database_path)) as connection:  #Explicit closing avoids locked files on Windows
        connection.execute("PRAGMA foreign_keys = ON")  #Makes SQLite enforce table relationships
        connection.executescript(SCHEMA)  #IF NOT EXISTS makes this safe to rerun
        migrate_transaction_columns(connection)
        migrate_weekly_budget_columns(connection)
        migrate_account_columns(connection)
        migrate_transaction_split_order(connection)
        connection.executescript(TRANSACTION_SPLIT_INDEXES)
        migrate_plaid_providers(connection)
        connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")  #Records the completed schema version
        connection.commit()  #Saves the schema and migration together


def get_table_names(database_path=DATABASE_PATH):  #Gets the user-created table names
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(  #Hides internal SQLite tables from the display
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()

    return [row[0] for row in rows]


def get_transaction_columns(database_path=DATABASE_PATH):  #Gets transaction columns for a quick schema check
    with closing(sqlite3.connect(database_path)) as connection:
        return get_column_names(connection, "transactions")  #Useful when checking migrations worked


def main():  #Runs database setup and any optional display checks
    arguments = read_arguments()

    try:
        create_database()
    except (OSError, sqlite3.Error) as error:
        print(f"Could not create the database: {error}", file=sys.stderr)  #Keeps errors separate from normal output
        return 1

    print(f"Database ready: {DATABASE_PATH}")

    if arguments.show_tables:
        print("\nTables:")
        for table_name in get_table_names():
            print(f"- {table_name}")

    if arguments.show_transaction_columns:
        print("\nTransaction columns:")
        for column_name in get_transaction_columns():
            print(f"- {column_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
