#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Complete setup and execution runner for Operations Dispatch Queue Service
# Strictly runs inside Docker container - zero local machine dependencies
# ==============================================================================

PROJECT_NAME="dispatch-queue"
CONTAINER_NAME="dispatch-queue-service"
PORT=8000
AS_OF_TIME="2026-11-23T12:00:00Z"

echo "================================================================================"
echo " Starting Operations Dispatch Queue Setup & Run (Pure Docker Environment)"
echo "================================================================================"

# 1. Verify Docker availability
if ! command -v docker &> /dev/null; then
    echo "[-] Error: docker is not installed or not in PATH."
    exit 1
fi

echo "[*] 1/6 Building Docker container image (${PROJECT_NAME}:latest)..."
docker build -t ${PROJECT_NAME}:latest .

echo ""
echo "[*] 2/6 Running automated test suite inside Docker container..."
docker run --rm -t \
    -e PYTHONPATH=/app \
    ${PROJECT_NAME}:latest pytest -v --cov=app tests/

echo ""
echo "[*] 3/6 Starting dispatch queue service container..."
# Stop and remove existing container if running
docker rm -f ${CONTAINER_NAME} 2>/dev/null || true

docker run -d \
    --name ${CONTAINER_NAME} \
    -p ${PORT}:8000 \
    -e PYTHONUNBUFFERED=1 \
    -e PYTHONPATH=/app \
    -e DATABASE_PATH=/app/dispatch_queue.db \
    ${PROJECT_NAME}:latest

echo "[*] Waiting for API service to become ready..."
RETRY_COUNT=0
MAX_RETRIES=15
until curl -s "http://localhost:${PORT}/health" | grep -q "ok"; do
    sleep 1
    RETRY_COUNT=$((RETRY_COUNT + 1))
    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo "[-] Service failed to start within timeout. Container logs:"
        docker logs ${CONTAINER_NAME}
        exit 1
    fi
done
echo "[+] API is live at http://localhost:${PORT}"

echo ""
echo "[*] 4/6 Ingesting initial synthetic feeds inside container..."
docker exec -t ${CONTAINER_NAME} python -m app.cli ingest \
    --orders data/orders.csv \
    --shipments data/shipments.csv

echo ""
echo "[*] 5/6 Demonstrating Repeatability: Reprocessing the exact same feeds..."
docker exec -t ${CONTAINER_NAME} python -m app.cli ingest \
    --orders data/orders.csv \
    --shipments data/shipments.csv

echo ""
echo "[*] 6/6 Demonstrating REST API Response..."
echo "--------------------------------------------------------------------------------"
echo "GET http://localhost:${PORT}/api/v1/orders/action-queue?as_of=${AS_OF_TIME}"
echo "--------------------------------------------------------------------------------"
curl -s "http://localhost:${PORT}/api/v1/orders/action-queue?as_of=${AS_OF_TIME}" | python3 -m json.tool || \
docker exec -t ${CONTAINER_NAME} curl -s "http://localhost:8000/api/v1/orders/action-queue?as_of=${AS_OF_TIME}"

echo ""
echo "--------------------------------------------------------------------------------"
echo "GET http://localhost:${PORT}/api/v1/quarantine (First 5 Quarantined Records)"
echo "--------------------------------------------------------------------------------"
curl -s "http://localhost:${PORT}/api/v1/quarantine?limit=5" | python3 -m json.tool || \
docker exec -t ${CONTAINER_NAME} curl -s "http://localhost:8000/api/v1/quarantine?limit=5"

echo ""
echo "================================================================================"
echo " Setup and verification complete! Service is running."
echo " - OpenAPI Docs: http://localhost:${PORT}/docs"
echo " - Action Queue: http://localhost:${PORT}/api/v1/orders/action-queue?as_of=${AS_OF_TIME}"
echo " - Stop Service: docker rm -f ${CONTAINER_NAME}"
echo "================================================================================"
