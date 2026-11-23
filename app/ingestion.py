import csv
import io
import json
from pathlib import Path
from typing import Dict, Any, List, Union
import sqlite3

from app.database import get_db
from app.models import IngestionSummary
from app.utils import parse_and_normalize_timestamp, now_utc_iso


def _quarantine_record(conn: sqlite3.Connection, feed_type: str, raw_data: Any, reason: str) -> None:
    """Inserts an invalid or conflicting record into the quarantined_records table."""
    raw_str = json.dumps(raw_data) if not isinstance(raw_data, str) else raw_data
    conn.execute(
        """
        INSERT INTO quarantined_records (feed_type, raw_data, reason, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (feed_type, raw_str, reason, now_utc_iso())
    )


def ingest_orders_records(records: List[Dict[str, Any]], db_path: Union[Path, str] = None) -> IngestionSummary:
    """
    Ingests order records idempotently.
    Quarantines malformed rows and conflicting duplicates without stopping the batch.
    """
    from app import config
    target_db = db_path or config.DATABASE_PATH

    total = len(records)
    inserted = 0
    duplicates = 0
    quarantined = 0
    errors: List[str] = []

    with get_db(target_db) as conn:
        for idx, row in enumerate(records):
            if not isinstance(row, dict):
                _quarantine_record(conn, "order", row, "MALFORMED_ROW_NOT_AN_OBJECT")
                quarantined += 1
                continue

            order_id = str(row.get("order_id") or "").strip()
            status = str(row.get("status") or "").strip()
            promised_dispatch_time = row.get("promised_dispatch_time")

            # Validate mandatory fields
            if not order_id:
                _quarantine_record(conn, "order", row, "MISSING_OR_EMPTY_ORDER_ID")
                quarantined += 1
                continue

            if not status:
                _quarantine_record(conn, "order", row, "MISSING_OR_EMPTY_STATUS")
                quarantined += 1
                continue

            if not promised_dispatch_time:
                _quarantine_record(conn, "order", row, "MISSING_PROMISED_DISPATCH_TIME")
                quarantined += 1
                continue

            # Validate and normalize timestamp
            try:
                norm_time_iso, norm_timestamp = parse_and_normalize_timestamp(promised_dispatch_time)
            except Exception as e:
                _quarantine_record(conn, "order", row, f"INVALID_TIMESTAMP: {str(e)}")
                quarantined += 1
                continue

            # Check if order already exists in database
            cur = conn.execute(
                "SELECT status, promised_dispatch_timestamp FROM orders WHERE order_id = ?",
                (order_id,)
            )
            existing = cur.fetchone()

            if existing:
                existing_status, existing_ts = existing[0], existing[1]
                # Check for exact duplicate vs conflict
                if existing_status.upper() == status.upper() and abs(existing_ts - norm_timestamp) < 0.001:
                    duplicates += 1
                else:
                    _quarantine_record(conn, "order", row, "CONFLICTING_DUPLICATE_ORDER")
                    quarantined += 1
                continue

            # Insert new order
            now_str = now_utc_iso()
            conn.execute(
                """
                INSERT INTO orders (order_id, status, promised_dispatch_time, promised_dispatch_timestamp, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (order_id, status.upper(), norm_time_iso, norm_timestamp, now_str, now_str)
            )
            inserted += 1

    return IngestionSummary(
        feed_type="order",
        total_records=total,
        valid_inserted=inserted,
        duplicates_ignored=duplicates,
        quarantined=quarantined,
        errors=errors
    )


def ingest_shipments_records(records: List[Dict[str, Any]], db_path: Union[Path, str] = None) -> IngestionSummary:
    """
    Ingests shipment event records idempotently.
    Deduplicates events by event_id.
    Quarantines unknown order IDs, malformed rows, and conflicting duplicates.
    """
    from app import config
    target_db = db_path or config.DATABASE_PATH

    total = len(records)
    inserted = 0
    duplicates = 0
    quarantined = 0
    errors: List[str] = []

    with get_db(target_db) as conn:
        for idx, row in enumerate(records):
            if not isinstance(row, dict):
                _quarantine_record(conn, "shipment", row, "MALFORMED_ROW_NOT_AN_OBJECT")
                quarantined += 1
                continue

            event_id = str(row.get("event_id") or "").strip()
            order_id = str(row.get("order_id") or "").strip()
            event_type = str(row.get("event_type") or "").strip()
            event_time = row.get("event_time")

            # Validate mandatory fields
            if not event_id:
                _quarantine_record(conn, "shipment", row, "MISSING_OR_EMPTY_EVENT_ID")
                quarantined += 1
                continue

            if not order_id:
                _quarantine_record(conn, "shipment", row, "MISSING_OR_EMPTY_ORDER_ID")
                quarantined += 1
                continue

            if not event_type:
                _quarantine_record(conn, "shipment", row, "MISSING_OR_EMPTY_EVENT_TYPE")
                quarantined += 1
                continue

            if not event_time:
                _quarantine_record(conn, "shipment", row, "MISSING_EVENT_TIME")
                quarantined += 1
                continue

            # Validate and normalize timestamp
            try:
                norm_time_iso, norm_timestamp = parse_and_normalize_timestamp(event_time)
            except Exception as e:
                _quarantine_record(conn, "shipment", row, f"INVALID_TIMESTAMP: {str(e)}")
                quarantined += 1
                continue

            # Check if event already exists
            cur_event = conn.execute(
                "SELECT order_id, event_type, event_timestamp FROM shipment_events WHERE event_id = ?",
                (event_id,)
            )
            existing_event = cur_event.fetchone()

            if existing_event:
                ex_order, ex_type, ex_ts = existing_event[0], existing_event[1], existing_event[2]
                if ex_order == order_id and ex_type.upper() == event_type.upper() and abs(ex_ts - norm_timestamp) < 0.001:
                    duplicates += 1
                else:
                    _quarantine_record(conn, "shipment", row, "CONFLICTING_DUPLICATE_EVENT")
                    quarantined += 1
                continue

            # Check if order exists in orders table
            cur_order = conn.execute(
                "SELECT 1 FROM orders WHERE order_id = ?",
                (order_id,)
            )
            if not cur_order.fetchone():
                _quarantine_record(conn, "shipment", row, f"UNKNOWN_ORDER_ID: order '{order_id}' not found")
                quarantined += 1
                continue

            # Insert new shipment event
            now_str = now_utc_iso()
            conn.execute(
                """
                INSERT INTO shipment_events (event_id, order_id, event_type, event_time, event_timestamp, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (event_id, order_id, event_type.upper(), norm_time_iso, norm_timestamp, now_str)
            )
            inserted += 1

    return IngestionSummary(
        feed_type="shipment",
        total_records=total,
        valid_inserted=inserted,
        duplicates_ignored=duplicates,
        quarantined=quarantined,
        errors=errors
    )


def load_file_records(file_path: Union[Path, str]) -> List[Dict[str, Any]]:
    """Loads records from CSV or JSON file into a list of dicts."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    content = path.read_text(encoding="utf-8")

    if suffix == ".json":
        data = json.loads(content)
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            # If wrapped in an object like {"orders": [...]}
            for key in ["orders", "shipments", "records", "data"]:
                if key in data and isinstance(data[key], list):
                    return data[key]
            return [data]
        return []
    elif suffix == ".csv":
        reader = csv.DictReader(io.StringIO(content))
        # Strip whitespace from keys and values
        clean_rows = []
        for row in reader:
            clean_rows.append({
                k.strip(): (v.strip() if v is not None else None)
                for k, v in row.items() if k is not None
            })
        return clean_rows
    else:
        raise ValueError(f"Unsupported file format: {suffix}. Expected .csv or .json")
