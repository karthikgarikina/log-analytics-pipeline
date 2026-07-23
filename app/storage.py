from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Iterable

from app.config import DOCS_DIR, INDEX_DIR, INDEX_FILE, REPORTS_DIR
from app.parsers import tokenize
from app.timeutils import parse_iso_datetime, utc_now


def ensure_storage() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def load_index() -> dict[str, set[str]]:
    ensure_storage()
    if not INDEX_FILE.exists():
        return {}
    with INDEX_FILE.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return {token: set(doc_ids) for token, doc_ids in payload.items()}


def persist_index(index: dict[str, set[str]]) -> None:
    ensure_storage()
    serializable = {token: sorted(doc_ids) for token, doc_ids in sorted(index.items())}
    tmp_path = INDEX_FILE.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(serializable, handle, separators=(",", ":"), sort_keys=True)
    os.replace(tmp_path, INDEX_FILE)


def doc_path(doc_id: str) -> Path:
    return DOCS_DIR / f"{doc_id}.json"


def write_document(doc: dict) -> bool:
    ensure_storage()
    path = doc_path(str(doc["id"]))
    if path.exists():
        return False
    tmp_path = path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2, sort_keys=True)
    os.replace(tmp_path, path)
    return True


def read_document(doc_id: str) -> dict | None:
    path = doc_path(doc_id)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_documents(candidate_ids: Iterable[str] | None = None) -> Iterable[dict]:
    ensure_storage()
    if candidate_ids is None:
        paths = sorted(DOCS_DIR.glob("*.json"))
    else:
        paths = [doc_path(doc_id) for doc_id in candidate_ids]
    for path in paths:
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                yield json.load(handle)
        except (json.JSONDecodeError, OSError):
            continue


def add_to_index(index: dict[str, set[str]], doc: dict) -> None:
    for token in tokenize(str(doc.get("message", ""))):
        index.setdefault(token, set()).add(str(doc["id"]))


def search_ids(index: dict[str, set[str]], query: str) -> set[str]:
    tokens = tokenize(query)
    if not tokens:
        return set()
    postings = [index.get(token, set()) for token in tokens]
    if not postings:
        return set()
    return set.intersection(*map(set, postings))


def matches_filters(doc: dict, field_filters: dict[str, str], from_ts: str | None, to_ts: str | None) -> bool:
    for field, expected in field_filters.items():
        if str(doc.get(field, "")).upper() != expected.upper():
            return False
    timestamp = parse_iso_datetime(str(doc["timestamp"]))
    if from_ts and timestamp < parse_iso_datetime(from_ts):
        return False
    if to_ts and timestamp > parse_iso_datetime(to_ts):
        return False
    return True


def parse_duration(value: str) -> timedelta:
    if len(value) < 2:
        raise ValueError("duration must look like 30m, 1h, or 7d")
    amount = int(value[:-1])
    unit = value[-1].lower()
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "d":
        return timedelta(days=amount)
    raise ValueError("duration unit must be one of m, h, d")


def generate_daily_reports(report_date: str | None = None) -> list[Path]:
    ensure_storage()
    end = utc_now()
    start = end - timedelta(hours=24)
    target_date = report_date or end.date().isoformat()
    by_service: dict[str, list[dict]] = defaultdict(list)
    for doc in iter_documents():
        try:
            timestamp = parse_iso_datetime(str(doc["timestamp"]))
        except (KeyError, ValueError):
            continue
        if start <= timestamp <= end:
            by_service[str(doc.get("service", "unknown"))].append(doc)

    output_dir = REPORTS_DIR / target_date
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for service, docs in sorted(by_service.items()):
        total = len(docs)
        errors = [doc for doc in docs if str(doc.get("level", "")).upper() == "ERROR"]
        error_counts = Counter(str(doc.get("message", "")) for doc in errors)
        latencies = sorted(float(doc.get("ingestion_latency_ms", 0.0)) for doc in docs)
        p95_index = min(len(latencies) - 1, int(len(latencies) * 0.95)) if latencies else 0
        report = {
            "service_name": service,
            "report_date": target_date,
            "total_events": total,
            "error_rate": (len(errors) / total) if total else 0.0,
            "top_10_error_messages": [
                {"message": message, "count": count} for message, count in error_counts.most_common(10)
            ],
            "p95_ingestion_latency_ms": latencies[p95_index] if latencies else 0.0,
        }
        safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in service)
        path = output_dir / f"{safe_name}.json"
        tmp_path = path.with_suffix(".json.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, path)
        written.append(path)
    return written
