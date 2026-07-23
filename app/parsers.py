from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.timeutils import isoformat_z, parse_iso_datetime, utc_now

ParserPredicate = Callable[[str], bool]
ParserFunction = Callable[[str, str], dict]

NGINX_RE = re.compile(
    r'^(?P<remote>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] '
    r'"(?P<method>[A-Z]+) (?P<path>\S+) (?P<protocol>[^"]+)" '
    r'(?P<status>\d{3}) (?P<bytes>\d+) "[^"]*" "(?P<agent>[^"]*)"$'
)
SYSLOG_RE = re.compile(
    r"^<(?P<pri>\d+)>1 (?P<ts>\S+) (?P<hostname>\S+) (?P<app>\S+) "
    r"(?P<procid>\S+) (?P<msgid>\S+) (?P<structured>\S+) (?P<msg>.*)$"
)
TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Parser:
    log_type: str
    predicate: ParserPredicate
    parse: ParserFunction


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def _base_doc(doc_id: str, raw: str, timestamp: datetime, log_type: str, service: str, level: str, message: str) -> dict:
    return {
        "id": doc_id,
        "timestamp": isoformat_z(timestamp),
        "log_type": log_type,
        "service": service,
        "level": level.upper(),
        "message": message,
        "raw": raw,
    }


def is_json_log(raw: str) -> bool:
    return raw.lstrip().startswith("{")


def parse_json_log(raw: str, doc_id: str) -> dict:
    payload = json.loads(raw)
    timestamp = parse_iso_datetime(str(payload["timestamp"]))
    doc = _base_doc(
        doc_id=doc_id,
        raw=raw,
        timestamp=timestamp,
        log_type="json",
        service=str(payload.get("service", "application")),
        level=str(payload.get("level", "INFO")),
        message=str(payload.get("message", "")),
    )
    for key in ("trace_id",):
        if key in payload:
            doc[key] = payload[key]
    return doc


def is_nginx_log(raw: str) -> bool:
    return NGINX_RE.match(raw) is not None


def parse_nginx_log(raw: str, doc_id: str) -> dict:
    match = NGINX_RE.match(raw)
    if not match:
        raise ValueError("not an nginx log line")
    parts = match.groupdict()
    timestamp = datetime.strptime(parts["ts"], "%d/%b/%Y:%H:%M:%S %z").astimezone(timezone.utc)
    status = int(parts["status"])
    if status >= 500:
        level = "ERROR"
    elif status >= 400:
        level = "WARN"
    else:
        level = "INFO"
    message = f'{parts["method"]} {parts["path"]} {parts["protocol"]}'
    doc = _base_doc(doc_id, raw, timestamp, "nginx", "nginx-ingress", level, message)
    doc.update(
        {
            "remote_addr": parts["remote"],
            "http_status": status,
            "bytes_sent": int(parts["bytes"]),
            "user_agent": parts["agent"],
        }
    )
    return doc


def is_syslog(raw: str) -> bool:
    return SYSLOG_RE.match(raw) is not None


def parse_syslog(raw: str, doc_id: str) -> dict:
    match = SYSLOG_RE.match(raw)
    if not match:
        raise ValueError("not an RFC 5424 syslog line")
    parts = match.groupdict()
    priority = int(parts["pri"])
    severity_code = priority % 8
    if severity_code <= 3:
        level = "ERROR"
    elif severity_code == 4:
        level = "WARN"
    else:
        level = "INFO"
    doc = _base_doc(
        doc_id=doc_id,
        raw=raw,
        timestamp=parse_iso_datetime(parts["ts"]),
        log_type="syslog",
        service=parts["app"],
        level=level,
        message=parts["msg"],
    )
    doc.update({"hostname": parts["hostname"], "syslog_priority": priority, "syslog_severity": severity_code})
    return doc


def parse_unknown(raw: str, doc_id: str) -> dict:
    return _base_doc(doc_id, raw, utc_now(), "unknown", "unknown", "INFO", raw)


PARSER_REGISTRY: dict[str, Parser] = {
    "json": Parser("json", is_json_log, parse_json_log),
    "nginx": Parser("nginx", is_nginx_log, parse_nginx_log),
    "syslog": Parser("syslog", is_syslog, parse_syslog),
    "unknown": Parser("unknown", lambda raw: True, parse_unknown),
}


def parse_log(raw: str, doc_id: str) -> dict:
    for parser in PARSER_REGISTRY.values():
        if parser.predicate(raw):
            return parser.parse(raw, doc_id)
    raise ValueError("no parser registered for log line")


def extract_event_timestamp(raw: str) -> str:
    return parse_log(raw, "timestamp-probe")["timestamp"]
