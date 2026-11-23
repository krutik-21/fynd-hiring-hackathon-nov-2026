# Operations Dispatch Queue Service

An operational order triage and action queue service for logistics operations leads. It ingests order and shipment feeds with duplicates, late arrivals, and corrupted rows, normalizes timezones to UTC, quarantines invalid records without stopping ingestion batches, and serves a prioritized action queue answering:
- **Which orders are already late (Overdue)?**
- **Which orders are about to miss dispatch (At Risk within 2 hours)?**
- **Which orders need no intervention (On Track)?**

---

## Tech Stack
- **Web Framework / REST**: FastAPI + Pydantic (Type validation, automatic OpenAPI /docs)
- **Database**: SQLite3 (ACID-compliant relational store with foreign key protection and indexes)
- **Ingestion & Classification**: Pure Python 3.12 (dateutil timezone normalization, idempotent pipeline)
- **Runtime & Environment**: Docker & Docker Compose (Zero local machine pollution)
- **Automation Runner**: Shell script (`./run.sh` complete single-run build, test, and execution)

---

## Quickstart (Single-Run Docker Execution)

Per the requirements, **nothing runs on the local machine; everything runs inside the Docker container**.

Ensure Docker is installed and running on your system, then execute:

```bash
./run.sh
```

### What `run.sh` does automatically:
1. Builds the Docker container image `dispatch-queue:latest`.
2. Executes the full automated test suite (`pytest -v --cov=app tests/`) **inside** the container.
3. Launches the API service in the background on port `8000`.
4. Ingests synthetic feeds (`data/orders.csv`, `data/shipments.csv`) inside the container.
5. Ingests the feeds a second time to prove **repeatability & idempotency** (zero count inflation).
6. Fires sample `curl` requests against the REST API and formats the output.

---

## Ingestion & API Commands

### 1. Ingest Feeds via CLI (Inside Container)

```bash
# Ingest CSV feeds
docker exec -it dispatch-queue-service python -m app.cli ingest \
  --orders data/orders.csv \
  --shipments data/shipments.csv

# Ingest JSON feeds
docker exec -it dispatch-queue-service python -m app.cli ingest \
  --orders data/orders.json \
  --shipments data/shipments.json
```

### 2. Ingest Feeds via REST API

#### Ingest Orders:
```bash
curl -X POST http://localhost:8000/api/v1/ingest/orders \
  -H "Content-Type: application/json" \
  -d '[
    {
      "order_id": "ORD-501",
      "status": "PENDING",
      "promised_dispatch_time": "2026-11-23T11:00:00Z"
    }
  ]'
```

#### Ingest Shipments:
```bash
curl -X POST http://localhost:8000/api/v1/ingest/shipments \
  -H "Content-Type: application/json" \
  -d '[
    {
      "event_id": "EVT-501",
      "order_id": "ORD-501",
      "event_type": "DISPATCHED",
      "event_time": "2026-11-23T10:30:00Z"
    }
  ]'
```

### 3. Query Action Queue

The action queue classifies orders relative to a caller-supplied `as_of` reference time (ISO 8601 with optional timezone offset):

```bash
curl -s "http://localhost:8000/api/v1/orders/action-queue?as_of=2026-11-23T12:00:00Z"
```

### 4. Query Quarantine Audit Log

Inspect all records isolated during ingestion along with the specific failure reason:

```bash
curl -s "http://localhost:8000/api/v1/quarantine?limit=20"
```

Interactive Swagger UI documentation is available at:
```
http://localhost:8000/docs
```

---

## Sample API Response

### Request
```http
GET /api/v1/orders/action-queue?as_of=2026-11-23T12:00:00Z HTTP/1.1
Host: localhost:8000
Accept: application/json
```

### Response (`200 OK`)
```json
{
  "as_of": "2026-11-23T12:00:00Z",
  "counts": {
    "open": 9,
    "overdue": 3,
    "at_risk": 4,
    "on_track": 2
  },
  "action_queue": [
    {
      "order_id": "ORD-102",
      "status": "PROCESSING",
      "promised_dispatch_time": "2026-11-23T09:30:00Z",
      "category": "overdue"
    },
    {
      "order_id": "ORD-101",
      "status": "PENDING",
      "promised_dispatch_time": "2026-11-23T10:00:00Z",
      "category": "overdue"
    },
    {
      "order_id": "ORD-110",
      "status": "PROCESSING",
      "promised_dispatch_time": "2026-11-23T11:00:00Z",
      "category": "overdue"
    },
    {
      "order_id": "ORD-103",
      "status": "PENDING",
      "promised_dispatch_time": "2026-11-23T12:00:00Z",
      "category": "at_risk"
    },
    {
      "order_id": "ORD-104",
      "status": "PENDING",
      "promised_dispatch_time": "2026-11-23T13:15:00Z",
      "category": "at_risk"
    },
    {
      "order_id": "ORD-111",
      "status": "PENDING",
      "promised_dispatch_time": "2026-11-23T13:30:00Z",
      "category": "at_risk"
    },
    {
      "order_id": "ORD-105",
      "status": "PENDING",
      "promised_dispatch_time": "2026-11-23T14:00:00Z",
      "category": "at_risk"
    }
  ]
}
```

### Response Structure Notes:
- **Prioritization**: `overdue` orders appear first, followed immediately by `at_risk` orders.
- **Sorting within groups**: Sorted chronologically by earliest `promised_dispatch_time` ASC, with `order_id` ASC as the deterministic tiebreaker.
- **Clean Action Queue**: Only actionable orders (`overdue` and `at_risk`) populate the queue list. `on_track` orders require no immediate intervention and are included in `counts.on_track` and `counts.open`.

---

## Data-Quality & Operational Decisions

### 1. Handling Unknown Order IDs
- **Problem**: Shipment events from couriers or logistics partners often reference order IDs that do not exist in the orders feed (e.g. out-of-order arrival or upstream typos).
- **Decision**: Ingesting orphan events compromises relational foreign key integrity. The system validates `order_id` against the `orders` table. Any shipment event referencing an unrecorded order is isolated into SQLite's `quarantined_records` table with reason `UNKNOWN_ORDER_ID: order '<id>' not found`. This maintains clean database referential integrity while providing an auditable trail for operations leads.

### 2. Conflicting Duplicate Events vs Exact Duplicates
- **Problem**: Feeds re-send duplicate records due to retry policies or network hiccups. Some duplicates are identical; others have differing timestamps or event types under the same `event_id`.
- **Decision**:
  - **Exact Duplicates**: If incoming `(event_id, order_id, event_type, event_time)` exactly matches the existing record, the system treats it as an **idempotent no-op**. The batch succeeds and duplicates are reported in `duplicates_ignored` without inflating database counts.
  - **Conflicting Duplicates**: If an incoming event has an existing `event_id` but conflicting attributes (e.g. mismatched `order_id`, `event_type`, or `event_time`), the system applies **First-Write-Wins** for stored state and quarantines the incoming conflicting event with reason `CONFLICTING_DUPLICATE_EVENT`. This prevents silent data corruption.

### 3. Timestamp Normalization & Timezone Offsets
- **Problem**: Operations feeds originate from warehouses across different geographic timezones (e.g., `+05:30` IST, `+02:00` CEST, `-05:00` EST, `Z` UTC).
- **Decision**: Every timestamp is parsed via ISO 8601 compliant parsers and normalized into two representations:
  1. Canonical UTC ISO 8601 string (`YYYY-MM-DDTHH:MM:SSZ`) for human-readable API responses.
  2. Unix Epoch timestamp float (`float`) for microsecond-precise, indexed relational SQL range queries.

### 4. Non-Blocking Quarantine Pattern
- **Problem**: Legacy pipelines fail an entire multi-thousand order batch when a single corrupted row (e.g., missing ID, malformed timestamp) is encountered.
- **Decision**: Ingestion uses row-level exception shielding. Malformed rows are captured, timestamped, and stored in `quarantined_records` with exact failure diagnostics (`MISSING_OR_EMPTY_ORDER_ID`, `INVALID_TIMESTAMP: ...`). The ingestion loop proceeds uninterrupted, ensuring valid orders are fulfilled without delay.

### 5. Time Boundary & Classification Semantics
Given reference timestamp $T_{\text{as\_of}}$:
- **Cancelled Orders**: Excluded from queue and open count.
- **Dispatched Orders**: Orders with a valid `DISPATCHED` event where $T_{\text{dispatch}} \le T_{\text{as\_of}}$ are considered completed and excluded.
- **Future Dispatch Events**: If $T_{\text{dispatch}} > T_{\text{as\_of}}$, the dispatch has not happened yet relative to the reference point. Therefore, **future shipment events do not remove the order from the queue**.
- **Classification Boundaries**:
  - **Overdue**: $T_{\text{promised}} < T_{\text{as\_of}}$
  - **At Risk**: $T_{\text{as\_of}} \le T_{\text{promised}} \le T_{\text{as\_of}} + 2\text{ hours}$ (inclusive boundaries)
  - **On Track**: $T_{\text{promised}} > T_{\text{as\_of}} + 2\text{ hours}$
- **Definition of Open**: $\text{Open} = \text{Overdue} + \text{At Risk} + \text{On Track}$.

---

## Automated Test Coverage

The automated test suite (`tests/`) covers:
1. **Feed Reprocessing Repeatability**: Re-ingesting feeds multiple times asserts 0 new insertions and 0 count inflation.
2. **Time Boundary Conditions**: Asserts boundary edge cases ($T_{\text{as\_of}} - 1\text{s}$, $T_{\text{as\_of}}$, $T_{\text{as\_of}} + 2\text{h}$, $T_{\text{as\_of}} + 2\text{h} + 1\text{s}$).
3. **Duplicate Dispatch Events**: Deduplicated cleanly by `event_id`.
4. **Future Dispatch Events**: Order remains in queue when dispatch event is after `as_of`.
5. **Non-blocking Malformed Rows**: Corrupted rows isolated to quarantine without blocking valid records.
6. **Unknown Order ID**: Quarantined with `UNKNOWN_ORDER_ID`.
7. **Conflicting Duplicates**: Quarantined with `CONFLICTING_DUPLICATE_EVENT`.
8. **Timezone Normalization**: Validates multi-timezone offset sorting and classification.
9. **REST API Endpoints**: Status codes, input validation (400 for bad dates, 422 for missing params), and sorting order.

To run tests independently inside the container:
```bash
docker run --rm -t dispatch-queue:latest pytest -v --cov=app tests/
```
