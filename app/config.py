import os
from pathlib import Path


def env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw in (None, "") else int(raw)


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return default if raw in (None, "") else float(raw)


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


DATA_DIR = Path(env_str("DATA_DIR", "/app/data"))
DOCS_DIR = DATA_DIR / "docs"
INDEX_DIR = DATA_DIR / "index"
INDEX_FILE = INDEX_DIR / "inverted_index.json"
REPORTS_DIR = Path(env_str("REPORTS_DIR", "/app/reports"))

INGESTOR_PORT = env_int("INGESTOR_PORT", 8000)
INDEXER_PORT = env_int("INDEXER_PORT", 8001)
