import pytest
from app.ingestion import ingest_orders_records, ingest_shipments_records
from app.database import get_db


def test_malformed_order_does_not_block_valid_records(temp_db):
    """A malformed row must be quarantined without blocking valid records."""
    records = [
        {"order_id": "ORD-1", "status": "PENDING", "promised_dispatch_time": "2026-11-23T10:00:00Z"},
        {"order_id": "", "status": "PENDING", "promised_dispatch_time": "2026-11-23T10:00:00Z"},  # missing id
        {"order_id": "ORD-2", "status": "PENDING", "promised_dispatch_time": "invalid-datetime"},  # invalid time
        {"order_id": "ORD-3", "status": "PENDING", "promised_dispatch_time": "2026-11-23T11:00:00Z"},
    ]

    summary = ingest_orders_records(records, db_path=temp_db)
    assert summary.valid_inserted == 2
    assert summary.quarantined == 2
    assert summary.duplicates_ignored == 0

    with get_db(temp_db) as conn:
        orders = conn.execute("SELECT order_id FROM orders ORDER BY order_id").fetchall()
        assert [r["order_id"] for r in orders] == ["ORD-1", "ORD-3"]

        quarantined = conn.execute("SELECT feed_type, reason FROM quarantined_records").fetchall()
        assert len(quarantined) == 2
        assert quarantined[0]["reason"] == "MISSING_OR_EMPTY_ORDER_ID"
        assert "INVALID_TIMESTAMP" in quarantined[1]["reason"]


def test_shipment_unknown_order_id_is_quarantined(temp_db):
    """Shipment event referencing non-existent order ID must be quarantined."""
    shipments = [
        {"event_id": "EVT-1", "order_id": "NON-EXISTENT", "event_type": "DISPATCHED", "event_time": "2026-11-23T10:00:00Z"}
    ]
    summary = ingest_shipments_records(shipments, db_path=temp_db)
    assert summary.valid_inserted == 0
    assert summary.quarantined == 1

    with get_db(temp_db) as conn:
        q_row = conn.execute("SELECT feed_type, reason FROM quarantined_records").fetchone()
        assert q_row is not None
        assert "UNKNOWN_ORDER_ID" in q_row["reason"]


def test_duplicate_and_conflicting_events(temp_db):
    """Exact duplicate events are ignored idempotently; conflicting events are quarantined."""
    # First, insert valid order
    ingest_orders_records([
        {"order_id": "ORD-1", "status": "PENDING", "promised_dispatch_time": "2026-11-23T10:00:00Z"},
        {"order_id": "ORD-2", "status": "PENDING", "promised_dispatch_time": "2026-11-23T10:00:00Z"}
    ], db_path=temp_db)

    # Ingest event EVT-100
    res1 = ingest_shipments_records([
        {"event_id": "EVT-100", "order_id": "ORD-1", "event_type": "DISPATCHED", "event_time": "2026-11-23T09:00:00Z"}
    ], db_path=temp_db)
    assert res1.valid_inserted == 1

    # Ingest exact duplicate of EVT-100
    res2 = ingest_shipments_records([
        {"event_id": "EVT-100", "order_id": "ORD-1", "event_type": "DISPATCHED", "event_time": "2026-11-23T09:00:00Z"}
    ], db_path=temp_db)
    assert res2.valid_inserted == 0
    assert res2.duplicates_ignored == 1
    assert res2.quarantined == 0

    # Ingest conflicting duplicate of EVT-100 (different order_id)
    res3 = ingest_shipments_records([
        {"event_id": "EVT-100", "order_id": "ORD-2", "event_type": "DISPATCHED", "event_time": "2026-11-23T09:00:00Z"}
    ], db_path=temp_db)
    assert res3.valid_inserted == 0
    assert res3.duplicates_ignored == 0
    assert res3.quarantined == 1

    with get_db(temp_db) as conn:
        q = conn.execute("SELECT reason FROM quarantined_records WHERE reason = 'CONFLICTING_DUPLICATE_EVENT'").fetchone()
        assert q is not None


def test_feed_reprocessing_repeatability(temp_db):
    """Reprocessing the same feeds multiple times must not inflate counts."""
    orders = [
        {"order_id": "ORD-1", "status": "PENDING", "promised_dispatch_time": "2026-11-23T10:00:00Z"},
        {"order_id": "ORD-2", "status": "PROCESSING", "promised_dispatch_time": "2026-11-23T11:00:00Z"}
    ]
    shipments = [
        {"event_id": "EVT-1", "order_id": "ORD-1", "event_type": "DISPATCHED", "event_time": "2026-11-23T09:00:00Z"}
    ]

    # First pass
    s1_orders = ingest_orders_records(orders, db_path=temp_db)
    s1_shipments = ingest_shipments_records(shipments, db_path=temp_db)
    assert s1_orders.valid_inserted == 2
    assert s1_shipments.valid_inserted == 1

    # Second pass with same data
    s2_orders = ingest_orders_records(orders, db_path=temp_db)
    s2_shipments = ingest_shipments_records(shipments, db_path=temp_db)
    assert s2_orders.valid_inserted == 0
    assert s2_orders.duplicates_ignored == 2
    assert s2_shipments.valid_inserted == 0
    assert s2_shipments.duplicates_ignored == 1

    # Verify counts in DB
    with get_db(temp_db) as conn:
        order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        event_count = conn.execute("SELECT COUNT(*) FROM shipment_events").fetchone()[0]
        assert order_count == 2
        assert event_count == 1
