import pytest
from app.ingestion import ingest_orders_records, ingest_shipments_records
from app.classifier import get_action_queue
from app.models import QueueCategory


def test_time_boundaries_and_classification(temp_db):
    """
    Tests both time boundaries:
    - as_of exactly -> AT_RISK
    - as_of + 2 hours exactly -> AT_RISK
    - 1 second before as_of -> OVERDUE
    - 1 second after as_of + 2 hours -> ON_TRACK
    """
    as_of = "2026-11-23T12:00:00Z"
    orders = [
        {"order_id": "ORD-BEFORE", "status": "PENDING", "promised_dispatch_time": "2026-11-23T11:59:59Z"},
        {"order_id": "ORD-AT-BOUNDARY-START", "status": "PENDING", "promised_dispatch_time": "2026-11-23T12:00:00Z"},
        {"order_id": "ORD-BETWEEN", "status": "PENDING", "promised_dispatch_time": "2026-11-23T13:00:00Z"},
        {"order_id": "ORD-AT-BOUNDARY-END", "status": "PENDING", "promised_dispatch_time": "2026-11-23T14:00:00Z"},
        {"order_id": "ORD-AFTER", "status": "PENDING", "promised_dispatch_time": "2026-11-23T14:00:01Z"},
    ]
    ingest_orders_records(orders, db_path=temp_db)

    result = get_action_queue(as_of, db_path=temp_db)

    assert result.counts.overdue == 1
    assert result.counts.at_risk == 3
    assert result.counts.on_track == 1
    assert result.counts.open == 5

    # Check items in action queue
    items = {item.order_id: item.category for item in result.action_queue}
    assert items["ORD-BEFORE"] == QueueCategory.OVERDUE
    assert items["ORD-AT-BOUNDARY-START"] == QueueCategory.AT_RISK
    assert items["ORD-BETWEEN"] == QueueCategory.AT_RISK
    assert items["ORD-AT-BOUNDARY-END"] == QueueCategory.AT_RISK
    # On track orders are counted in counts but not in the action queue
    assert "ORD-AFTER" not in items


def test_future_dispatch_event_does_not_remove_from_queue(temp_db):
    """
    Future shipment events (> as_of) do not remove an order from the queue.
    Valid dispatch at or before as_of DOES remove it.
    """
    as_of = "2026-11-23T12:00:00Z"
    orders = [
        {"order_id": "ORD-PAST-DISPATCH", "status": "PROCESSING", "promised_dispatch_time": "2026-11-23T11:00:00Z"},
        {"order_id": "ORD-FUTURE-DISPATCH", "status": "PROCESSING", "promised_dispatch_time": "2026-11-23T11:00:00Z"},
    ]
    ingest_orders_records(orders, db_path=temp_db)

    shipments = [
        {"event_id": "EVT-1", "order_id": "ORD-PAST-DISPATCH", "event_type": "DISPATCHED", "event_time": "2026-11-23T11:30:00Z"},
        {"event_id": "EVT-2", "order_id": "ORD-FUTURE-DISPATCH", "event_type": "DISPATCHED", "event_time": "2026-11-23T13:00:00Z"},
    ]
    ingest_shipments_records(shipments, db_path=temp_db)

    result = get_action_queue(as_of, db_path=temp_db)

    order_ids = [item.order_id for item in result.action_queue]
    assert "ORD-PAST-DISPATCH" not in order_ids
    assert "ORD-FUTURE-DISPATCH" in order_ids
    assert result.counts.overdue == 1
    assert result.counts.open == 1


def test_cancelled_orders_excluded(temp_db):
    """Cancelled orders are excluded from action queue and counts."""
    as_of = "2026-11-23T12:00:00Z"
    orders = [
        {"order_id": "ORD-CANCELLED", "status": "CANCELLED", "promised_dispatch_time": "2026-11-23T10:00:00Z"},
        {"order_id": "ORD-ACTIVE", "status": "PENDING", "promised_dispatch_time": "2026-11-23T10:00:00Z"},
    ]
    ingest_orders_records(orders, db_path=temp_db)

    result = get_action_queue(as_of, db_path=temp_db)
    order_ids = [item.order_id for item in result.action_queue]
    assert "ORD-CANCELLED" not in order_ids
    assert "ORD-ACTIVE" in order_ids
    assert result.counts.open == 1


def test_action_queue_sorting(temp_db):
    """
    Action queue must return:
    - Overdue orders first, then at_risk orders.
    - Within each group: sorted by earliest promised dispatch time, then order ID.
    """
    as_of = "2026-11-23T12:00:00Z"
    orders = [
        {"order_id": "ORD-B-ATRISK", "status": "PENDING", "promised_dispatch_time": "2026-11-23T13:00:00Z"},
        {"order_id": "ORD-A-ATRISK", "status": "PENDING", "promised_dispatch_time": "2026-11-23T12:30:00Z"},
        {"order_id": "ORD-C-ATRISK-TIE-2", "status": "PENDING", "promised_dispatch_time": "2026-11-23T12:30:00Z"},
        {"order_id": "ORD-Y-OVERDUE", "status": "PENDING", "promised_dispatch_time": "2026-11-23T11:00:00Z"},
        {"order_id": "ORD-X-OVERDUE", "status": "PENDING", "promised_dispatch_time": "2026-11-23T10:00:00Z"},
        {"order_id": "ORD-Z-OVERDUE", "status": "PENDING", "promised_dispatch_time": "2026-11-23T11:00:00Z"},
    ]
    ingest_orders_records(orders, db_path=temp_db)

    result = get_action_queue(as_of, db_path=temp_db)
    ordered_ids = [item.order_id for item in result.action_queue]

    # Overdue group: ORD-X (10:00), then ORD-Y (11:00) vs ORD-Z (11:00 -> tiebreaker by order_id)
    # At-risk group: ORD-A (12:30) vs ORD-C (12:30 -> tiebreaker), then ORD-B (13:00)
    expected_order = [
        "ORD-X-OVERDUE",
        "ORD-Y-OVERDUE",
        "ORD-Z-OVERDUE",
        "ORD-A-ATRISK",
        "ORD-C-ATRISK-TIE-2",
        "ORD-B-ATRISK"
    ]
    assert ordered_ids == expected_order


def test_timezone_normalization_comparison(temp_db):
    """Orders with different timezone offsets must be normalized accurately."""
    as_of = "2026-11-23T12:00:00Z"
    orders = [
        # 17:00 in +05:30 is 11:30:00Z (< 12:00:00Z -> OVERDUE)
        {"order_id": "ORD-TZ-OVERDUE", "status": "PENDING", "promised_dispatch_time": "2026-11-23T17:00:00+05:30"},
        # 09:00 in -05:00 is 14:00:00Z (== 12:00 + 2h -> AT_RISK boundary)
        {"order_id": "ORD-TZ-ATRISK", "status": "PENDING", "promised_dispatch_time": "2026-11-23T09:00:00-05:00"},
        # 10:00 in -05:00 is 15:00:00Z (> 14:00:00Z -> ON_TRACK)
        {"order_id": "ORD-TZ-ONTRACK", "status": "PENDING", "promised_dispatch_time": "2026-11-23T10:00:00-05:00"},
    ]
    ingest_orders_records(orders, db_path=temp_db)

    result = get_action_queue(as_of, db_path=temp_db)
    assert result.counts.overdue == 1
    assert result.counts.at_risk == 1
    assert result.counts.on_track == 1
    queue_ids = [item.order_id for item in result.action_queue]
    assert queue_ids == ["ORD-TZ-OVERDUE", "ORD-TZ-ATRISK"]
