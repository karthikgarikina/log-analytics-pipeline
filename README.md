# Log Analytics Pipeline

A simplified, containerized log analytics engine with synthetic log generation, HTTP ingestion, out-of-order buffering, deduplication, parser registry, file-backed document storage, a custom inverted index, CLI queries, daily JSON reports, and a benchmark script.

## Start

```sh
docker compose up --build
```

The command starts `log-generator`, `ingestor`, `indexer`, `scheduler`, and a long-lived `querier` container.

## Query

```sh
docker compose run --rm querier query search "database connection"
docker compose run --rm querier query filter level=ERROR
docker compose run --rm querier query search "database" filter level=ERROR
docker compose run --rm querier query aggregate count by service,level --last 10m
```

## Reports

Reports are written to `reports/YYYY-MM-DD/<service_name>.json`. To trigger one manually:

```sh
curl -X POST http://localhost:8001/reports/daily
```

## Benchmark

```sh
bash benchmark.sh
```

The script defaults to 1,000,000 generated logs for quick local verification and appends results to `benchmark_results.txt`.
