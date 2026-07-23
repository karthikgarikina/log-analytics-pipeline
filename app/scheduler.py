from __future__ import annotations

import time
import urllib.error
import urllib.request
from datetime import timedelta

from app.config import env_bool, env_int, env_str
from app.timeutils import utc_now

INDEXER_URL = env_str("INDEXER_URL", "http://indexer:8001").rstrip("/")
REPORT_HOUR_UTC = env_int("REPORT_HOUR_UTC", 0)
REPORT_MINUTE_UTC = env_int("REPORT_MINUTE_UTC", 5)
RUN_REPORT_ON_START = env_bool("RUN_REPORT_ON_START", True)


def trigger_report() -> None:
    request = urllib.request.Request(f"{INDEXER_URL}/reports/daily", data=b"", method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        print(response.read().decode("utf-8"), flush=True)


def seconds_until_next_run() -> float:
    now = utc_now()
    target = now.replace(hour=REPORT_HOUR_UTC, minute=REPORT_MINUTE_UTC, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def main() -> None:
    if RUN_REPORT_ON_START:
        try:
            trigger_report()
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"scheduler initial report skipped: {exc}", flush=True)
    while True:
        sleep_for = seconds_until_next_run()
        print(f"scheduler sleeping {sleep_for:.0f}s before next daily report", flush=True)
        time.sleep(sleep_for)
        try:
            trigger_report()
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"scheduler report trigger failed: {exc}", flush=True)


if __name__ == "__main__":
    main()
