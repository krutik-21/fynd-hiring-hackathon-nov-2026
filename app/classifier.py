from pathlib import Path
from typing import Union, List, Dict, Any
import sqlite3

from app.database import get_db
from app.models import ActionQueueResponse, ActionQueueItem, QueueCounts, QueueCategory
from app.utils import parse_and_normalize_timestamp


def get_action_queue(
    as_of: Union[str, float],
    db_path: Union[Path, str] = None
) -> ActionQueueResponse:
    """
    Classifies orders at a fixed reference time 'as_of'.
    - Excludes cancelled orders
    - Excludes orders with a valid dispatch event at or before as_of
    - Future dispatch events (> as_of) DO NOT remove an order from the queue
    - Remaining orders are classified into:
        - overdue: promised_dispatch_time < as_of
        - at_risk: as_of <= promised_dispatch_time <= as_of + 2 hours
        - on_track: promised_dispatch_time > as_of + 2 hours
    - Action queue contains overdue first, then at_risk.
      Within each group, sorted by earliest promised dispatch time, then order ID.
    - Counts returned for: open, overdue, at_risk, on_track.
    """
    from app import config
    target_db = db_path or config.DATABASE_PATH

    as_of_iso, as_of_ts = parse_and_normalize_timestamp(as_of)
    two_hours_ts = as_of_ts + (2 * 3600.0)

    overdue_items: List[ActionQueueItem] = []
    at_risk_items: List[ActionQueueItem] = []
    on_track_count = 0

    with get_db(target_db) as conn:
        # Exclude cancelled orders
        # Also check for valid dispatch event at or before as_of
        query = """
            SELECT 
                o.order_id,
                o.status,
                o.promised_dispatch_time,
                o.promised_dispatch_timestamp,
                EXISTS (
                    SELECT 1 
                    FROM shipment_events s 
                    WHERE s.order_id = o.order_id 
                      AND UPPER(s.event_type) = 'DISPATCHED' 
                      AND s.event_timestamp <= ?
                ) AS is_dispatched_before_as_of
            FROM orders o
            WHERE UPPER(o.status) != 'CANCELLED'
        """
        rows = conn.execute(query, (as_of_ts,)).fetchall()

        for row in rows:
            if row["is_dispatched_before_as_of"]:
                # Exclude fulfilled orders
                continue

            order_id = row["order_id"]
            status = row["status"]
            promised_time = row["promised_dispatch_time"]
            promised_ts = row["promised_dispatch_timestamp"]

            if promised_ts < as_of_ts:
                overdue_items.append(
                    ActionQueueItem(
                        order_id=order_id,
                        status=status,
                        promised_dispatch_time=promised_time,
                        category=QueueCategory.OVERDUE
                    )
                )
            elif as_of_ts <= promised_ts <= two_hours_ts:
                at_risk_items.append(
                    ActionQueueItem(
                        order_id=order_id,
                        status=status,
                        promised_dispatch_time=promised_time,
                        category=QueueCategory.AT_RISK
                    )
                )
            else:
                on_track_count += 1

    # Sorting rules:
    # Within each group, sort by earliest promised dispatch time, then by order ID.
    # Note: promised_dispatch_time in ISO UTC sorts chronologically identically to timestamp.
    overdue_items.sort(key=lambda item: (item.promised_dispatch_time, item.order_id))
    at_risk_items.sort(key=lambda item: (item.promised_dispatch_time, item.order_id))

    overdue_count = len(overdue_items)
    at_risk_count = len(at_risk_items)
    open_count = overdue_count + at_risk_count + on_track_count

    counts = QueueCounts(
        open=open_count,
        overdue=overdue_count,
        at_risk=at_risk_count,
        on_track=on_track_count
    )

    action_queue = overdue_items + at_risk_items

    return ActionQueueResponse(
        as_of=as_of_iso,
        counts=counts,
        action_queue=action_queue
    )
