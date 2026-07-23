from __future__ import annotations

import argparse
import json
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import timedelta

from app.config import env_float, env_int, env_str
from app.timeutils import isoformat_z, utc_now

INGESTOR_URL = env_str("INGESTOR_URL", "http://ingestor:8000/logs")
RATE_PER_SECOND = env_float("LOG_RATE_PER_SECOND", 5.0)
JITTER_SECONDS = env_int("LOG_JITTER_SECONDS", 30)
DUPLICATE_PROBABILITY = env_float("LOG_DUPLICATE_PROBABILITY", 0.02)
TOTAL = env_int("LOG_GENERATOR_TOTAL", 0)

LEVELS = ["INFO", "INFO", "INFO", "WARN", "ERROR"]
SERVICES = ["payment-service", "api-gateway", "inventory-service", "auth-service"]
MESSAGES = [
    "Database connection timed out",
    "User login succeeded",
    "Connection to DB failed",
    "Cache refresh completed",
    "Payment authorization declined",
    "Inventory count updated",
    "Request validation failed",
    "Background job completed",
]
PATHS = ["/", "/api/v1/orders", "/api/v2/users", "/checkout", "/health", "/inventory"]
AGENTS = ["Mozilla/5.0", "curl/8.0", "SyntheticClient/1.0"]


def event_time():
    return utc_now() + timedelta(seconds=random.randint(-JITTER_SECONDS, JITTER_SECONDS))


def nginx_log(ts) -> str:
    status = random.choice([200, 200, 201, 204, 400, 404, 500, 502])
    size = random.randint(128, 8192)
    method = random.choice(["GET", "POST", "PUT", "DELETE"])
    return (
        f'127.0.0.1 - - [{ts.strftime("%d/%b/%Y:%H:%M:%S +0000")}] '
        f'"{method} {random.choice(PATHS)} HTTP/1.1" {status} {size} "-" "{random.choice(AGENTS)}"'
    )


def app_json_log(ts) -> str:
    return json.dumps(
        {
            "timestamp": isoformat_z(ts),
            "level": random.choice(LEVELS),
            "service": random.choice(SERVICES),
            "trace_id": str(uuid.uuid4()),
            "message": random.choice(MESSAGES),
        },
        separators=(",", ":"),
    )


def syslog_log(ts) -> str:
    priority = random.choice([14, 11, 12, 6, 3])
    app_name = random.choice(["kernel", "sshd", "cron", "network-service"])
    message = random.choice(MESSAGES)
    return f"<{priority}>1 {isoformat_z(ts)} my-hostname {app_name} - - - {message}"


GENERATORS = {
    "nginx": nginx_log,
    "json": app_json_log,
    "syslog": syslog_log,
}


def send_log(raw: str, request_id: str) -> None:
    request = urllib.request.Request(
        INGESTOR_URL,
        data=raw.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "text/plain", "X-Request-ID": request_id},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        response.read()


def generate_one() -> tuple[str, str, str]:
    log_type, builder = random.choice(list(GENERATORS.items()))
    raw = builder(event_time())
    request_id = str(uuid.uuid4())
    send_log(raw, request_id)
    if random.random() < DUPLICATE_PROBABILITY:
        send_log(raw, request_id)
    return log_type, request_id, raw


def run(total: int, quiet: bool = False) -> None:
    count = 0
    while total <= 0 or count < total:
        try:
            log_type, request_id, _ = generate_one()
            count += 1
            if not quiet:
                print(f"generated type={log_type} request_id={request_id} count={count}", flush=True)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"log-generator waiting for ingestor: {exc}", flush=True)
            time.sleep(1)
            continue
        if RATE_PER_SECOND > 0:
            time.sleep(1 / RATE_PER_SECOND)


def run_parallel_burst(total: int, workers: int) -> None:
    lock = threading.Lock()
    next_number = 0
    sent = 0
    counts: Counter[str] = Counter()

    def reserve() -> int | None:
        nonlocal next_number
        with lock:
            if next_number >= total:
                return None
            next_number += 1
            return next_number

    def worker() -> None:
        nonlocal sent
        while True:
            number = reserve()
            if number is None:
                return
            while True:
                try:
                    log_type, _, _ = generate_one()
                    break
                except (urllib.error.URLError, TimeoutError) as exc:
                    print(f"log-generator burst retry: {exc}", flush=True)
                    time.sleep(0.25)
            with lock:
                sent += 1
                counts[log_type] += 1
                if sent % 10000 == 0 or sent == total:
                    print(f"burst generated count={sent}/{total}", flush=True)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(max(1, workers))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    print(f"burst complete total={sent} by_type={dict(counts)}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic nginx, JSON, and syslog logs.")
    parser.add_argument("--burst", type=int, default=None, help="Generate this many logs and exit.")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers for burst mode.")
    parser.add_argument("--quiet", action="store_true", help="Reduce per-log output.")
    args = parser.parse_args()
    total = args.burst if args.burst is not None else TOTAL
    if args.burst is not None and args.workers > 1:
        run_parallel_burst(args.burst, args.workers)
        return
    run(total, quiet=args.quiet)


if __name__ == "__main__":
    main()
