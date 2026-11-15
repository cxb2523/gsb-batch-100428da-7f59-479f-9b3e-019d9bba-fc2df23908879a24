"""SQLite storage, compatible with existing TEXT-based databases."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().with_name("currencies.db")
FIELDS = ("dollar", "pound", "euro")


def get_db_connection():
    return sqlite3.connect(str(DB_PATH), timeout=5)


def create_table():
    with closing(get_db_connection()) as con, con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS tgju(
                dollar TEXT,
                pound TEXT,
                euro TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)


def add_row(usd, gbp, eur):
    """Store a snapshot; any individual currency may be missing (None)."""
    values = tuple(
        None if value is None else int(value) for value in (usd, gbp, eur)
    )
    if all(value is None for value in values):
        raise ValueError("A snapshot must contain at least one valid price")
    if any(value is not None and value <= 0 for value in values):
        raise ValueError("Prices must be positive tomans")
    with closing(get_db_connection()) as con, con:
        con.execute(
            "INSERT INTO tgju (dollar, pound, euro, created_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)", values
        )


def _stored_price(value):
    """Return a positive whole-toman price, or None for missing/invalid data."""
    if value is None:
        return None
    try:
        price = int(value)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def retrieve_latest():
    """Return the latest snapshot with at least one valid price, or None.

    Missing currencies are represented as None. Database errors propagate for
    callers to handle. Skip legacy rows whose values and timestamp are all
    unusable.
    """
    create_table()
    with closing(get_db_connection()) as con:
        rows = con.execute(
            "SELECT dollar, pound, euro, created_at FROM tgju ORDER BY rowid DESC"
        )
        for row in rows:
            try:
                updated_at = datetime.fromisoformat(row[3]).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue
            prices = tuple(_stored_price(value) for value in row[:3])
            if any(price is not None for price in prices):
                return dict(zip(FIELDS, prices), updated_at=updated_at)
    return None


def retrieve_dollar():
    latest = retrieve_latest()
    return latest["dollar"] if latest else None


def retrieve_pound():
    latest = retrieve_latest()
    return latest["pound"] if latest else None


def retrieve_euro():
    latest = retrieve_latest()
    return latest["euro"] if latest else None
