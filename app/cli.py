import argparse
import json
import sys
from pathlib import Path

from app.database import init_db
from app.ingestion import load_file_records, ingest_orders_records, ingest_shipments_records
from app.classifier import get_action_queue


def handle_ingest(args):
    init_db()
    if args.orders:
        orders_path = Path(args.orders)
        print(f"[*] Ingesting orders from {orders_path}...")
        records = load_file_records(orders_path)
        summary = ingest_orders_records(records)
        print(f"[+] Orders Ingested: {summary.valid_inserted} inserted, {summary.duplicates_ignored} duplicates ignored, {summary.quarantined} quarantined.")

    if args.shipments:
        shipments_path = Path(args.shipments)
        print(f"[*] Ingesting shipments from {shipments_path}...")
        records = load_file_records(shipments_path)
        summary = ingest_shipments_records(records)
        print(f"[+] Shipments Ingested: {summary.valid_inserted} inserted, {summary.duplicates_ignored} duplicates ignored, {summary.quarantined} quarantined.")


def handle_queue(args):
    init_db()
    result = get_action_queue(args.as_of)
    print(json.dumps(result.model_dump(), indent=2))


def main():
    parser = argparse.ArgumentParser(description="Operations Dispatch Queue CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest order and shipment feeds")
    ingest_parser.add_argument("--orders", type=str, help="Path to orders CSV or JSON file")
    ingest_parser.add_argument("--shipments", type=str, help="Path to shipments CSV or JSON file")

    # Queue command
    queue_parser = subparsers.add_parser("queue", help="Get action queue at reference time as_of")
    queue_parser.add_argument("--as-of", required=True, type=str, help="ISO 8601 reference timestamp")

    args = parser.parse_args()
    if args.command == "ingest":
        handle_ingest(args)
    elif args.command == "queue":
        handle_queue(args)


if __name__ == "__main__":
    main()
