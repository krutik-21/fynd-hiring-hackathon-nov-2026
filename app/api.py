from typing import List, Optional, Union, Dict, Any
from fastapi import FastAPI, Query, HTTPException, status
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from app.classifier import get_action_queue
from app.database import init_db, get_db
from app.ingestion import ingest_orders_records, ingest_shipments_records
from app.models import (
    ActionQueueResponse,
    IngestionSummary,
    QuarantineRecordResponse,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database is initialized on startup
    init_db()
    yield


app = FastAPI(
    title="Operations Dispatch Queue Service",
    description="Operational order triage service classifying orders by SLA risk at a fixed reference time.",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok"}


@app.get(
    "/api/v1/orders/action-queue",
    response_model=ActionQueueResponse,
    tags=["Queue"],
    summary="Get operational action queue at fixed as_of reference time"
)
async def get_queue(
    as_of: str = Query(
        ...,
        description="Reference timestamp (ISO 8601 with optional timezone offset, e.g., '2026-11-23T12:00:00Z')",
        examples=["2026-11-23T12:00:00Z"]
    )
):
    try:
        return get_action_queue(as_of)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid as_of timestamp format: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while computing the action queue: {str(e)}"
        )


@app.post(
    "/api/v1/ingest/orders",
    response_model=IngestionSummary,
    tags=["Ingestion"],
    summary="Ingest orders batch via JSON"
)
async def ingest_orders_api(payload: Union[List[Dict[str, Any]], Dict[str, Any]]):
    records = payload if isinstance(payload, list) else [payload]
    return ingest_orders_records(records)


@app.post(
    "/api/v1/ingest/shipments",
    response_model=IngestionSummary,
    tags=["Ingestion"],
    summary="Ingest shipment events batch via JSON"
)
async def ingest_shipments_api(payload: Union[List[Dict[str, Any]], Dict[str, Any]]):
    records = payload if isinstance(payload, list) else [payload]
    return ingest_shipments_records(records)


@app.get(
    "/api/v1/quarantine",
    response_model=List[QuarantineRecordResponse],
    tags=["Audit"],
    summary="View quarantined records and rejection reasons"
)
async def list_quarantine(
    feed_type: Optional[str] = Query(None, description="Filter by feed type: 'order' or 'shipment'"),
    limit: int = Query(100, ge=1, le=1000)
):
    with get_db() as conn:
        if feed_type:
            cur = conn.execute(
                """
                SELECT id, feed_type, raw_data, reason, created_at 
                FROM quarantined_records 
                WHERE feed_type = ? 
                ORDER BY id DESC LIMIT ?
                """,
                (feed_type.lower(), limit)
            )
        else:
            cur = conn.execute(
                """
                SELECT id, feed_type, raw_data, reason, created_at 
                FROM quarantined_records 
                ORDER BY id DESC LIMIT ?
                """,
                (limit,)
            )
        rows = cur.fetchall()
        return [
            QuarantineRecordResponse(
                id=r["id"],
                feed_type=r["feed_type"],
                raw_data=r["raw_data"],
                reason=r["reason"],
                created_at=r["created_at"]
            )
            for r in rows
        ]
