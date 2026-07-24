#!/usr/bin/env bash
set -euo pipefail

KEYWORD="${BENCHMARK_KEYWORD:-database}"
COUNT=10000
WORKERS="${BENCHMARK_WORKERS:-16}"
RESULTS_FILE="benchmark_results.txt"

export BUFFER_WINDOW_SECONDS=1
export LOG_GENERATOR_TOTAL=0
export LOG_RATE_PER_SECOND=0

docker compose down
mkdir -p data/docs data/index reports
docker compose build indexer
docker compose run --rm --no-deps indexer python -m app.cleanup

docker compose up --build -d ingestor indexer
docker compose run --rm -e LOG_GENERATOR_TOTAL="$COUNT" -e LOG_RATE_PER_SECOND=0 log-generator python -m app.generator --burst "$COUNT" --workers "$WORKERS" --quiet
docker compose exec -T indexer python -c "import urllib.request; req=urllib.request.Request('http://127.0.0.1:8001/drain', data=b'', method='POST'); print(urllib.request.urlopen(req, timeout=3600).read().decode())"

INDEX_TIME="$({ /usr/bin/time -p docker compose run --rm querier query search "$KEYWORD" --limit 20 >/tmp/log-analytics-index.out; } 2>&1 | awk '/real/ {print $2}')"
GREP_TIME="$({ /usr/bin/time -p grep -Ril "$KEYWORD" data/docs >/tmp/log-analytics-grep.out; } 2>&1 | awk '/real/ {print $2}')"

{
  echo "Benchmark Results (on ${COUNT} logs for keyword '${KEYWORD}'):"
  echo "Inverted Index Search Time: ${INDEX_TIME}s"
  echo "Linear Scan (grep) Time: ${GREP_TIME}s"
  echo
} >> "$RESULTS_FILE"

cat "$RESULTS_FILE"
