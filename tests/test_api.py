import pytest


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_api_ingestion_and_action_queue_flow(client):
    # 1. Ingest orders via API
    orders_payload = [
        {"order_id": "ORD-API-1", "status": "PENDING", "promised_dispatch_time": "2026-11-23T11:00:00Z"},
        {"order_id": "ORD-API-2", "status": "PENDING", "promised_dispatch_time": "2026-11-23T13:00:00Z"},
        {"order_id": "ORD-API-3", "status": "CANCELLED", "promised_dispatch_time": "2026-11-23T10:00:00Z"},
        {"order_id": "", "status": "PENDING", "promised_dispatch_time": "2026-11-23T10:00:00Z"}, # malformed
    ]
    res_orders = client.post("/api/v1/ingest/orders", json=orders_payload)
    assert res_orders.status_code == 200
    data_orders = res_orders.json()
    assert data_orders["valid_inserted"] == 3
    assert data_orders["quarantined"] == 1

    # 2. Ingest shipments via API
    shipments_payload = [
        {"event_id": "EVT-API-1", "order_id": "UNKNOWN-ORD", "event_type": "DISPATCHED", "event_time": "2026-11-23T10:00:00Z"}
    ]
    res_shipments = client.post("/api/v1/ingest/shipments", json=shipments_payload)
    assert res_shipments.status_code == 200
    data_shipments = res_shipments.json()
    assert data_shipments["valid_inserted"] == 0
    assert data_shipments["quarantined"] == 1

    # 3. Query action queue
    res_queue = client.get("/api/v1/orders/action-queue?as_of=2026-11-23T12:00:00Z")
    assert res_queue.status_code == 200
    queue_data = res_queue.json()
    assert queue_data["counts"]["open"] == 2
    assert queue_data["counts"]["overdue"] == 1
    assert queue_data["counts"]["at_risk"] == 1
    assert queue_data["counts"]["on_track"] == 0
    assert len(queue_data["action_queue"]) == 2
    assert queue_data["action_queue"][0]["order_id"] == "ORD-API-1"
    assert queue_data["action_queue"][1]["order_id"] == "ORD-API-2"

    # 4. Check quarantine endpoint
    res_quarantine = client.get("/api/v1/quarantine")
    assert res_quarantine.status_code == 200
    quarantine_list = res_quarantine.json()
    assert len(quarantine_list) == 2


def test_action_queue_invalid_as_of(client):
    response = client.get("/api/v1/orders/action-queue?as_of=not-a-valid-date")
    assert response.status_code == 400
    assert "Invalid as_of timestamp format" in response.json()["detail"]


def test_action_queue_missing_as_of(client):
    response = client.get("/api/v1/orders/action-queue")
    assert response.status_code == 422
