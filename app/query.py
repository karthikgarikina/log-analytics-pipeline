from __future__ import annotations

import argparse
import json
from collections import Counter

from app.parsers import tokenize
from app.storage import iter_documents, load_index, matches_filters, parse_duration, search_ids
from app.timeutils import parse_iso_datetime, utc_now


def parse_field_filters(items: list[str]) -> dict[str, str]:
    filters: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"filter must be key=value, got {item!r}")
        key, value = item.split("=", 1)
        filters[key] = value
    return filters


def emit_documents(docs: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(docs, indent=2, sort_keys=True))
        return
    for doc in docs:
        print(json.dumps(doc, sort_keys=True))


def command_search(args: argparse.Namespace) -> None:
    index = load_index()
    candidate_ids = search_ids(index, args.keywords)
    filters = parse_field_filters(args.filters)
    docs = [
        doc
        for doc in iter_documents(candidate_ids)
        if matches_filters(doc, filters, args.from_ts, args.to_ts)
    ]
    docs.sort(key=lambda doc: doc.get("timestamp", ""))
    emit_documents(docs[: args.limit], args.json)


def command_filter(args: argparse.Namespace) -> None:
    filters = parse_field_filters(args.filters)
    docs = [doc for doc in iter_documents() if matches_filters(doc, filters, args.from_ts, args.to_ts)]
    docs.sort(key=lambda doc: doc.get("timestamp", ""))
    emit_documents(docs[: args.limit], args.json)


def command_aggregate(args: argparse.Namespace) -> None:
    if args.metric != "count":
        raise SystemExit("only aggregate count is supported")
    fields = [field.strip() for field in args.fields.split(",") if field.strip()]
    if not fields:
        raise SystemExit("at least one group-by field is required")
    since = utc_now() - parse_duration(args.last)
    counts: Counter[tuple[str, ...]] = Counter()
    for doc in iter_documents():
        try:
            timestamp = parse_iso_datetime(str(doc["timestamp"]))
        except (KeyError, ValueError):
            continue
        if timestamp >= since:
            counts[tuple(str(doc.get(field, "")) for field in fields)] += 1
    print_table(fields, counts)


def print_table(fields: list[str], counts: Counter[tuple[str, ...]]) -> None:
    rows = [(*key, str(count)) for key, count in sorted(counts.items())]
    headers = [*fields, "count"]
    widths = [len(header) for header in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))
    header_line = "| " + " | ".join(header.ljust(widths[i]) for i, header in enumerate(headers)) + " |"
    sep_line = "|-" + "-|-".join("-" * width for width in widths) + "-|"
    print(header_line)
    print(sep_line)
    for row in rows:
        print("| " + " | ".join(value.ljust(widths[i]) for i, value in enumerate(row)) + " |")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="query", description="Query the file-backed log analytics index.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser("search", help="Use the inverted index to search message tokens.")
    search.add_argument("keywords")
    search.add_argument("filters", nargs="*", help="Optional syntax: filter level=ERROR service=payment-service")
    search.add_argument("--from", dest="from_ts")
    search.add_argument("--to", dest="to_ts")
    search.add_argument("--limit", type=int, default=50)
    search.add_argument("--json", action="store_true")
    search.set_defaults(func=command_search)

    flt = subparsers.add_parser("filter", help="Scan structured documents by field and time.")
    flt.add_argument("filters", nargs="*", help="Field filters such as level=ERROR service=payment-service")
    flt.add_argument("--from", dest="from_ts")
    flt.add_argument("--to", dest="to_ts")
    flt.add_argument("--limit", type=int, default=50)
    flt.add_argument("--json", action="store_true")
    flt.set_defaults(func=command_filter)

    aggregate = subparsers.add_parser("aggregate", help="Aggregate logs over a recent time range.")
    aggregate.add_argument("metric", choices=["count"])
    aggregate.add_argument("by_keyword", choices=["by"])
    aggregate.add_argument("fields")
    aggregate.add_argument("--last", required=True)
    aggregate.set_defaults(func=command_aggregate)
    return parser


def normalize_combined_search_args(argv: list[str]) -> list[str]:
    if len(argv) > 3 and argv[0] == "search" and "filter" in argv[2:]:
        filter_index = argv.index("filter")
        return [*argv[:filter_index], *argv[filter_index + 1 :]]
    return argv


def main(argv: list[str] | None = None) -> None:
    import sys

    effective_argv = normalize_combined_search_args(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    args = parser.parse_args(effective_argv)
    args.func(args)


if __name__ == "__main__":
    main()
