import argparse
import sqlite3
import sys
from contextlib import closing
from decimal import Decimal, InvalidOperation

try:
    from scripts.common import check_database, format_minor, money_to_minor
    from scripts.paths import DATABASE_PATH
except ModuleNotFoundError:
    from common import check_database, format_minor, money_to_minor
    from paths import DATABASE_PATH


def clean_ticker(ticker):  #Keeps ticker symbols consistent on the investments page
    ticker = ticker.strip().upper()

    if not ticker:
        raise ValueError("Ticker cannot be empty")

    return ticker


def list_positions(database_path=DATABASE_PATH):  #Gets manual investment positions for the web page
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        return connection.execute(
            """
            SELECT
                p.id,
                p.account_id,
                a.display_name,
                p.ticker,
                COALESCE(p.security_name, ''),
                p.asset_class,
                p.quantity,
                p.price_minor,
                p.value_minor,
                p.currency,
                COALESCE(p.as_of, ''),
                p.source
            FROM investment_positions p
            JOIN accounts a ON a.id = p.account_id
            ORDER BY p.asset_class, p.ticker, a.display_name
            """
        ).fetchall()


def save_position(
    account_id,
    ticker,
    security_name,
    asset_class,
    quantity,
    price,
    value,
    currency,
    as_of="",
    database_path=DATABASE_PATH,
):  #Adds or updates one local ticker position
    check_database(database_path)
    ticker = clean_ticker(ticker)
    security_name = security_name.strip()
    asset_class = asset_class.strip() or "Equity"
    quantity = quantity.strip() or "0"
    currency = currency.strip().upper()

    if currency not in {"NZD", "USD"}:
        raise ValueError("Currency must be NZD or USD")

    price_minor = money_to_minor(price) if price.strip() else None
    value_minor = money_to_minor(value)

    with closing(sqlite3.connect(database_path)) as connection:
        account = connection.execute(
            """
            SELECT id
            FROM accounts
            WHERE id = ?
            """,
            (account_id,),
        ).fetchone()

        if account is None:
            raise ValueError("Investment account not found")

        connection.execute(
            """
            INSERT INTO investment_positions (
                account_id,
                ticker,
                security_name,
                asset_class,
                quantity,
                price_minor,
                value_minor,
                currency,
                as_of
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (account_id, ticker) DO UPDATE SET
                security_name = excluded.security_name,
                asset_class = excluded.asset_class,
                quantity = excluded.quantity,
                price_minor = excluded.price_minor,
                value_minor = excluded.value_minor,
                currency = excluded.currency,
                as_of = excluded.as_of,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                account_id,
                ticker,
                security_name,
                asset_class,
                quantity,
                price_minor,
                value_minor,
                currency,
                as_of.strip(),
            ),
        )
        connection.commit()


def add_decimal_text(left, right):  #Adds Plaid quantities without turning them into floats
    try:
        total = Decimal(left or "0") + Decimal(right or "0")
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("Investment quantity is invalid") from error

    return format(total.normalize(), "f")


def replace_provider_positions(
    position_rows,
    provider,
    database_path=DATABASE_PATH,
    provider_account_ids=None,
):  #Replaces provider-synced rows only for accounts that refreshed successfully
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        account_map = {
            provider_account_id: account_id
            for account_id, provider_account_id in connection.execute(
                """
                SELECT id, provider_account_id
                FROM accounts
                WHERE provider = ?
                  AND is_active = 1
                """,
                (provider,),
            )
        }
        if provider_account_ids is not None:
            allowed_provider_ids = set(provider_account_ids)
            account_map = {
                provider_account_id: account_id
                for provider_account_id, account_id in account_map.items()
                if provider_account_id in allowed_provider_ids
            }

        grouped = {}
        for row in position_rows:
            account_id = account_map.get(row["provider_account_id"])
            if account_id is None:
                continue

            ticker = clean_ticker(row["ticker"])
            key = (account_id, ticker)
            saved = grouped.setdefault(
                key,
                {
                    "account_id": account_id,
                    "ticker": ticker,
                    "security_name": row["security_name"],
                    "asset_class": row["asset_class"],
                    "quantity": "0",
                    "price_minor": row["price_minor"],
                    "value_minor": 0,
                    "currency": row["currency"],
                    "as_of": row["as_of"],
                },
            )
            saved["quantity"] = add_decimal_text(saved["quantity"], row["quantity"])
            saved["value_minor"] += row["value_minor"]
            saved["price_minor"] = row["price_minor"] or saved["price_minor"]
            saved["as_of"] = row["as_of"] or saved["as_of"]

        account_ids = tuple(account_map.values())
        if account_ids:
            placeholders = ",".join("?" for _ in account_ids)
            connection.execute(
                f"DELETE FROM investment_positions WHERE source = ? AND account_id IN ({placeholders})",
                (provider, *account_ids),
            )

        connection.executemany(
            """
            INSERT INTO investment_positions (
                account_id, ticker, security_name, asset_class, quantity,
                price_minor, value_minor, currency, as_of, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    row["account_id"],
                    row["ticker"],
                    row["security_name"],
                    row["asset_class"],
                    row["quantity"],
                    row["price_minor"],
                    row["value_minor"],
                    row["currency"],
                    row["as_of"],
                    provider,
                )
                for row in grouped.values()
            ],
        )
        connection.commit()

    return len(grouped)


def investment_account_filter():  #Keeps this check matched to supported investment account types
    return """
        a.is_active = 1
        AND a.balance_type = 'asset'
        AND (
            LOWER(COALESCE(a.account_type, '')) LIKE '%invest%'
            OR LOWER(COALESCE(a.account_type, '')) LIKE '%ira%'
            OR LOWER(COALESCE(a.account_type, '')) LIKE '%401%'
            OR LOWER(COALESCE(a.account_type, '')) LIKE '%broker%'
            OR LOWER(COALESCE(a.account_type, '')) LIKE '%roth%'
            OR LOWER(COALESCE(a.account_type, '')) LIKE '%retirement%'
        )
    """


def get_holding_check(database_path=DATABASE_PATH):  #Compares ticker holdings to saved investment balances
    check_database(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        account_rows = connection.execute(
            f"""
            SELECT
                a.id,
                a.display_name,
                a.native_currency,
                ABS(a.current_balance_minor),
                COALESCE(SUM(p.value_minor), 0),
                COUNT(p.id)
            FROM accounts a
            LEFT JOIN investment_positions p
                ON p.account_id = a.id
               AND p.currency = a.native_currency
            WHERE {investment_account_filter()}
            GROUP BY a.id, a.display_name, a.native_currency, a.current_balance_minor
            ORDER BY ABS(a.current_balance_minor) DESC, a.display_name
            """
        ).fetchall()
        source_rows = connection.execute(
            """
            SELECT source, COUNT(*), COALESCE(SUM(value_minor), 0)
            FROM investment_positions
            GROUP BY source
            ORDER BY source
            """
        ).fetchall()

    provider_count = sum(count for source, count, _ in source_rows if source != "manual")
    manual_count = sum(count for source, count, _ in source_rows if source == "manual")
    account_total = sum(row[3] for row in account_rows)
    holding_total = sum(row[4] for row in account_rows)

    return {
        "accounts": account_rows,
        "provider_count": provider_count,
        "manual_count": manual_count,
        "account_total": account_total,
        "holding_total": holding_total,
        "difference": account_total - holding_total,
    }


def print_holding_check(check):  #Prints a safe holdings check for PowerShell
    print("Investment holding check")
    print(f"Provider positions: {check['provider_count']}")
    print(f"Manual positions: {check['manual_count']}")
    print(f"Account total: {format_minor(check['account_total'])}")
    print(f"Holding total: {format_minor(check['holding_total'])}")
    print(f"Difference: {format_minor(check['difference'])}")

    if not check["accounts"]:
        print("No active investment accounts found")
        return

    if check["provider_count"] == 0:
        print("No provider holdings saved yet. Reconnect the investment institution if refresh still shows 0")

    print("")
    for _, name, currency, balance, holding_total, position_count in check["accounts"]:
        difference = balance - holding_total
        print(
            f"{name} | {currency} | account {format_minor(balance)} | "
            f"holdings {format_minor(holding_total)} | "
            f"difference {format_minor(difference)} | positions {position_count}"
        )


def print_positions(positions):  #Prints saved ticker rows without provider account IDs
    if not positions:
        print("No investment positions found")
        return

    for position in positions:
        _, _, account, ticker, security_name, asset_class, quantity, price, value, currency, as_of, source = position
        price_text = format_minor(price) if price is not None else "-"
        print(
            f"{ticker} | {security_name or '-'} | {asset_class} | {account} | "
            f"qty {quantity} | price {price_text} {currency} | "
            f"value {format_minor(value)} {currency} | {as_of or '-'} | {source}"
        )


def read_arguments():  #Keeps this helper usable from PowerShell
    parser = argparse.ArgumentParser(description="Manage local investment positions")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="List saved positions")
    subparsers.add_parser("check", help="Compare holdings to investment balances")

    save_parser = subparsers.add_parser("save", help="Add or update one position")
    save_parser.add_argument("--account-id", required=True, type=int)
    save_parser.add_argument("--ticker", required=True)
    save_parser.add_argument("--name", default="")
    save_parser.add_argument("--asset-class", default="Equity")
    save_parser.add_argument("--quantity", default="0")
    save_parser.add_argument("--price", default="")
    save_parser.add_argument("--value", required=True)
    save_parser.add_argument("--currency", required=True, choices=["NZD", "USD"])
    save_parser.add_argument("--as-of", default="")

    return parser.parse_args()


def main():  #Runs the selected investment command
    arguments = read_arguments()

    try:
        if arguments.command == "list":
            print_positions(list_positions())
        elif arguments.command == "check":
            print_holding_check(get_holding_check())
        elif arguments.command == "save":
            save_position(
                arguments.account_id,
                arguments.ticker,
                arguments.name,
                arguments.asset_class,
                arguments.quantity,
                arguments.price,
                arguments.value,
                arguments.currency,
                arguments.as_of,
            )
            print(f"Saved position: {arguments.ticker.upper()}")
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"Could not manage investment positions: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())  #Returns the result code back to PowerShell
