# Architectural Decisions & Design Considerations

This document outlines key engineering decisions, trade-offs, and design rationale across data ingestion, storage, operational classification, and edge-case handling.

---

## 1. Data Ingestion & Fault Tolerance

- **Non-blocking Quarantine Store**: 
  - Malformed records (empty IDs, unparseable dates, corrupted rows) and conflicting duplicates do not fail or abort batch ingestion.
  - Offending rows are isolated into a `quarantined_records` table preserving raw payload, feed type, timestamp, and specific rejection reason (`MISSING_ORDER_ID`, `INVALID_TIMESTAMP`, `CONFLICTING_DUPLICATE_ORDER`, etc.).
- **Idempotency & Deduplication**:
  - Exact duplicates (identical IDs and payloads) are safely ignored without error, allowing repeatable re-ingestion without count inflation.
  - Updates with differing payloads under the same key are quarantined as conflicting duplicates to prevent unverified overwrites.
- **Out-of-Order Shipment Ingestion**:
  - `shipment_events` relies on `orders(order_id)` via relational foreign keys.
  - If a shipment event arrives before its parent order exists, the event is routed to `quarantined_records` with `UNKNOWN_ORDER_ID` rather than dropped silently.
  - *Design consideration*: In distributed high-throughput event streams, this can be extended into a staging area / DLQ with auto-reprocessing triggered when the matching `order_id` is later ingested.

---

## 2. Timezone & Timestamp Handling

- **UTC Normalization at Ingestion**:
  - External timestamps arrive in diverse ISO 8601 formats (`Z`, `+05:30`, `-04:00`, microsecond precision).
  - All timestamps are converted at the boundary to ISO 8601 UTC strings and stored alongside epoch seconds (`REAL`) in SQLite.
- **Floating-point Epoch Seconds for Fast Comparison**:
  - Numeric epoch timestamps enable performant indexed range queries (`<`, `<=`, `BETWEEN`) and clean boundary comparisons independent of string lexicographical quirks.

---

## 3. Action Queue Classification & SLA Logic

- **Deterministic `as_of` Parameter**:
  - The action queue calculation accepts an explicit `as_of` reference timestamp instead of using system wall-clock time (`now()`).
  - Ensures fully deterministic, reproducible triage queries, historical back-testing, and consistent results across timezones.
- **Classification Buckets**:
  - **Exclusions**: Orders with status `CANCELLED` or orders with a recorded `DISPATCHED` shipment event (occurring on or before `as_of`) are excluded from the actionable queue.
  - **Overdue**: Open orders where `promised_dispatch_time < as_of`.
  - **At Risk**: Open orders where `as_of <= promised_dispatch_time <= as_of + 2 hours` (inclusive 2-hour window).
  - **On Track**: Open orders where `promised_dispatch_time > as_of + 2 hours`.
- **Precedence & Ordering**:
  - Priority sort: `overdue` (oldest promised time first) $\rightarrow$ `at_risk` $\rightarrow$ `on_track`.
  - Action items within each bucket are sorted chronologically by `promised_dispatch_time` ascending to guide immediate operational focus.

---

## 4. Storage & Relational Modeling

- **SQLite with Enforced Foreign Keys**:
  - Lightweight, embedded, zero-dependency relational database suitable for self-contained execution.
  - `PRAGMA foreign_keys = ON;` explicitly enforced on connections to safeguard referential integrity between orders and shipment events.
- **Targeted Indexing**:
  - Dedicated indexes on `orders(status)`, `orders(promised_dispatch_timestamp)`, `shipment_events(order_id)`, `shipment_events(event_type)`, and `quarantined_records(feed_type)` to support low-latency queue queries and audit queries.

---

## 5. API Design & Operational Usability

- **FastAPI + Pydantic**:
  - Strict input/output validation, descriptive error responses, and automatic interactive OpenAPI (`/docs`) and ReDoc documentation.
  - Endpoints provided for batch ingestion (`/api/v1/ingest/orders`, `/api/v1/ingest/shipments`), queue evaluation (`/api/v1/orders/action-queue`), and quarantine inspection (`/api/v1/quarantine`).
- **CLI & REST Parity**:
  - File ingestion (CSV and JSON) is supported both via REST endpoints and via standard CLI (`python -m app.cli ingest`), facilitating local automated pipelines or cron-driven workloads.

---

## 6. Containerization & Execution Model

- **Zero-Pollution Execution (`run.sh`)**:
  - All dependencies, test suites, database files, and servers run inside Docker to eliminate host environment drift.
  - Automated validation in `run.sh` builds the container, executes unit/integration tests with coverage, starts the service, runs idempotency checks against sample data, and verifies API health.
