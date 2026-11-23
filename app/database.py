import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from app import config


def get_db_connection(db_path: Path | str = None) -> sqlite3.Connection:
    target_path = db_path if db_path is not None else config.DATABASE_PATH
    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def get_db(db_path: Path | str = None) -> Generator[sqlite3.Connection, None, None]:
    conn = get_db_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str = None) -> None:
    """Initializes tables and indexes if they do not exist."""
    with get_db(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                promised_dispatch_time TEXT NOT NULL,
                promised_dispatch_timestamp REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
            CREATE INDEX IF NOT EXISTS idx_orders_promised_timestamp ON orders(promised_dispatch_timestamp);

            CREATE TABLE IF NOT EXISTS shipment_events (
                event_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_time TEXT NOT NULL,
                event_timestamp REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_shipments_order_id ON shipment_events(order_id);
            CREATE INDEX IF NOT EXISTS idx_shipments_event_type ON shipment_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_shipments_event_timestamp ON shipment_events(event_timestamp);

            CREATE TABLE IF NOT EXISTS quarantined_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feed_type TEXT NOT NULL,
                raw_data TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_quarantine_feed_type ON quarantined_records(feed_type);
            CREATE INDEX IF NOT EXISTS idx_quarantine_reason ON quarantined_records(reason);
            """
        )
