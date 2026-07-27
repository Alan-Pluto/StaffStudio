from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.db.seed import PRICE_COMPARE_SKILL, PURCHASE_SKILL, REFUND_SKILL

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = REPO_ROOT / "contracts" / "agent" / "v1" / "corpus" / "production_seed"
SOURCES: dict[str, dict[str, Any]] = {
    "purchase": PURCHASE_SKILL,
    "price_compare": PRICE_COMPARE_SKILL,
    "refund": REFUND_SKILL,
}


def rendered_corpus() -> dict[Path, bytes]:
    return {
        OUTPUT_ROOT / f"{name}.json": (
            json.dumps(content, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        for name, content in SOURCES.items()
    }


def check() -> list[Path]:
    return [
        path
        for path, expected in rendered_corpus().items()
        if not path.is_file() or path.read_bytes() != expected
    ]


def write() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    for path, content in rendered_corpus().items():
        path.write_bytes(content)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        stale = check()
        if stale:
            for path in stale:
                print(path.relative_to(REPO_ROOT))
            return 1
        return 0
    write()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
