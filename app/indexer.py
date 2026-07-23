from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.config import env_float, env_int, env_str
from app.parsers import parse_log
from app.storage import add_to_index, generate_daily_reports, load_index, persist_index, write_document
from app.timeutils import parse_iso_datetime, utc_now

HOST = "0.0.0.0"
PORT = env_int("INDEXER_PORT", 8001)
BATCH_URL = env_str("INGESTOR_BATCH_URL", "http://ingestor:8000/logs/batch")
BATCH_SIZE = env_int("BATCH_SIZE", 500)
POLL_SECONDS = env_float("INDEXER_POLL_SECONDS", 1.0)


class IndexerState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.index = load_index()
        self.indexed = 0
        self.parse_errors = 0
        self.running = True

    def process_item(self, item: dict) -> bool:
        doc_id = str(item["request_id"])
        raw = str(item["raw"])
        try:
            doc = parse_log(raw, doc_id)
            indexed_at = utc_now()
            event_ts = parse_iso_datetime(str(doc["timestamp"]))
            doc["received_at"] = item.get("received_at")
            doc["indexed_at"] = indexed_at.isoformat()
            doc["ingestion_latency_ms"] = max(0.0, (indexed_at - event_ts).total_seconds() * 1000.0)
        except Exception as exc:
            self.parse_errors += 1
            print(f"indexer parse error for {doc_id}: {exc}", flush=True)
            return False

        with self.lock:
            created = write_document(doc)
            if created:
                add_to_index(self.index, doc)
                self.indexed += 1
            return created

    def flush_index(self) -> None:
        with self.lock:
            persist_index(self.index)

    def stats(self) -> dict:
        with self.lock:
            return {"indexed": self.indexed, "parse_errors": self.parse_errors, "tokens": len(self.index)}


STATE = IndexerState()


def fetch_batch(flush: bool = False) -> list[dict]:
    url = f"{BATCH_URL}?limit={BATCH_SIZE}&flush={'true' if flush else 'false'}"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return list(payload.get("logs", []))


def drain_ingestor() -> int:
    total = 0
    while True:
        batch = fetch_batch(flush=True)
        if not batch:
            break
        for item in batch:
            if STATE.process_item(item):
                total += 1
        STATE.flush_index()
    return total


def poll_loop() -> None:
    while STATE.running:
        try:
            batch = fetch_batch(flush=False)
            if batch:
                processed = 0
                for item in batch:
                    if STATE.process_item(item):
                        processed += 1
                STATE.flush_index()
                print(f"indexer processed batch size={processed}", flush=True)
            else:
                time.sleep(POLL_SECONDS)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"indexer waiting for ingestor: {exc}", flush=True)
            time.sleep(POLL_SECONDS)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json({"status": "ok", **STATE.stats()})
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/reports/daily":
            query = parse_qs(parsed.query)
            report_date = query.get("date", [None])[0]
            written = generate_daily_reports(report_date)
            self._send_json({"written": [str(path) for path in written]})
            return
        if parsed.path == "/drain":
            processed = drain_ingestor()
            self._send_json({"processed": processed, **STATE.stats()})
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def log_message(self, fmt: str, *args) -> None:
        print(f"indexer {self.address_string()} - {fmt % args}", flush=True)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    worker = threading.Thread(target=poll_loop, daemon=True)
    worker.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"indexer listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
