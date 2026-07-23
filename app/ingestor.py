from __future__ import annotations

import json
import threading
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.config import env_int
from app.parsers import extract_event_timestamp
from app.timeutils import parse_iso_datetime, utc_now

HOST = "0.0.0.0"
PORT = env_int("INGESTOR_PORT", 8000)
BUFFER_WINDOW_SECONDS = env_int("BUFFER_WINDOW_SECONDS", 10)
BATCH_SIZE = env_int("BATCH_SIZE", 500)
SEEN_ID_CACHE_SIZE = env_int("SEEN_ID_CACHE_SIZE", 200000)


class BufferedLogStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buffer: list[dict] = []
        self._seen: set[str] = set()
        self._seen_order: deque[str] = deque()
        self._newest_event_ts = None
        self.received = 0
        self.duplicates = 0

    def add(self, request_id: str, raw: str) -> bool:
        with self._lock:
            if request_id in self._seen:
                self.duplicates += 1
                return False
            try:
                event_ts = parse_iso_datetime(extract_event_timestamp(raw))
            except Exception:
                event_ts = utc_now()
            self._seen.add(request_id)
            self._seen_order.append(request_id)
            while len(self._seen_order) > SEEN_ID_CACHE_SIZE:
                expired = self._seen_order.popleft()
                self._seen.discard(expired)
            if self._newest_event_ts is None or event_ts > self._newest_event_ts:
                self._newest_event_ts = event_ts
            self._buffer.append(
                {
                    "request_id": request_id,
                    "raw": raw,
                    "event_timestamp": event_ts.isoformat(),
                    "received_at": utc_now().isoformat(),
                }
            )
            self.received += 1
            return True

    def batch(self, limit: int, flush: bool = False) -> list[dict]:
        with self._lock:
            if flush or self._newest_event_ts is None:
                eligible = list(self._buffer)
            else:
                cutoff = self._newest_event_ts.timestamp() - BUFFER_WINDOW_SECONDS
                eligible = [
                    item for item in self._buffer if parse_iso_datetime(item["event_timestamp"]).timestamp() <= cutoff
                ]
            eligible.sort(key=lambda item: item["event_timestamp"])
            selected = eligible[:limit]
            selected_ids = {item["request_id"] for item in selected}
            self._buffer = [item for item in self._buffer if item["request_id"] not in selected_ids]
            return selected

    def stats(self) -> dict:
        with self._lock:
            return {
                "buffered": len(self._buffer),
                "received": self.received,
                "duplicates": self.duplicates,
                "buffer_window_seconds": BUFFER_WINDOW_SECONDS,
            }


STORE = BufferedLogStore()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send_json({"status": "ok", **STORE.stats()})
            return
        if parsed.path == "/logs/batch":
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", [BATCH_SIZE])[0])
            flush = query.get("flush", ["false"])[0].lower() in {"1", "true", "yes"}
            self._send_json({"logs": STORE.batch(limit=limit, flush=flush)})
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_POST(self) -> None:
        if self.path != "/logs":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        request_id = self.headers.get("X-Request-ID")
        if not request_id:
            self.send_error(HTTPStatus.BAD_REQUEST, "missing X-Request-ID header")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        accepted = STORE.add(request_id, raw)
        self._send_json({"accepted": accepted}, status=HTTPStatus.ACCEPTED)

    def log_message(self, fmt: str, *args) -> None:
        print(f"ingestor {self.address_string()} - {fmt % args}", flush=True)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"ingestor listening on {HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
