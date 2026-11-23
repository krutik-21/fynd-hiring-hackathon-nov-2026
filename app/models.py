from enum import Enum
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    CREATED = "CREATED"
    CANCELLED = "CANCELLED"


class ShipmentEventType(str, Enum):
    DISPATCHED = "DISPATCHED"
    PICKED = "PICKED"
    PACKED = "PACKED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"


class QueueCategory(str, Enum):
    OVERDUE = "overdue"
    AT_RISK = "at_risk"
    ON_TRACK = "on_track"


class OrderInput(BaseModel):
    order_id: str = Field(..., min_length=1, description="Unique Order Identifier")
    status: str = Field(..., description="Order status, e.g. PENDING, CANCELLED")
    promised_dispatch_time: str = Field(..., description="ISO 8601 promised dispatch timestamp")


class ShipmentEventInput(BaseModel):
    event_id: str = Field(..., min_length=1, description="Unique Event Identifier")
    order_id: str = Field(..., min_length=1, description="Associated Order Identifier")
    event_type: str = Field(..., description="Event type, e.g. DISPATCHED, PICKED")
    event_time: str = Field(..., description="ISO 8601 event timestamp")


class ActionQueueItem(BaseModel):
    order_id: str
    status: str
    promised_dispatch_time: str
    category: QueueCategory


class QueueCounts(BaseModel):
    open: int = Field(..., description="Total non-cancelled, non-dispatched orders (overdue + at_risk + on_track)")
    overdue: int = Field(..., description="Orders with promised dispatch time earlier than as_of")
    at_risk: int = Field(..., description="Orders with promised dispatch time between as_of and as_of + 2 hours (inclusive)")
    on_track: int = Field(..., description="Orders with promised dispatch time after as_of + 2 hours")


class ActionQueueResponse(BaseModel):
    as_of: str
    counts: QueueCounts
    action_queue: List[ActionQueueItem]


class QuarantineRecordResponse(BaseModel):
    id: int
    feed_type: str
    raw_data: str
    reason: str
    created_at: str


class IngestionSummary(BaseModel):
    feed_type: str
    total_records: int
    valid_inserted: int
    duplicates_ignored: int
    quarantined: int
    errors: List[str] = Field(default_factory=list)
