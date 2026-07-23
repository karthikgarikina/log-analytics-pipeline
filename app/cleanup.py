from __future__ import annotations

import shutil

from app.config import DOCS_DIR, INDEX_DIR, REPORTS_DIR


def main() -> None:
    targets = [DOCS_DIR, INDEX_DIR, REPORTS_DIR]
    for path in targets:
        shutil.rmtree(path, ignore_errors=True)
    for path in targets:
        path.mkdir(parents=True, exist_ok=True)
    print("benchmark cleanup complete")


if __name__ == "__main__":
    main()
