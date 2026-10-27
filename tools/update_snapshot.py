#!/usr/bin/env python3
"""Regenerates the Arena snapshot bundled with the software (ia_router/data/arena.json). It is for whoever MAINTAINS the repo:
run it before publishing a version. Users update their metrics with `python3 cli.py metrics refresh`.

It reads the official Arena dataset (lmarena-ai/leaderboard-dataset, CC BY 4.0, through the Hugging Face datasets server): about
130 requests of 100 rows, one at a time, a few minutes. Attribution is kept.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ia_router import metrics  # noqa: E402


def main() -> int:
    pages = metrics.fetch_arena(prices=metrics.prices_from(metrics.bundled().get("pages") or {}), say=print)
    expected = {f"{s}/{c}" for pairs in metrics.ARENA_MAP.values() for s, c in pairs}
    missing = sorted(expected - set(pages))
    if missing:
        print(f"Missing categories: {', '.join(missing)}. The bundled snapshot is not updated.", file=sys.stderr)
        return 1
    data = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "https://huggingface.co/datasets/" + metrics.DATASET,
            "attribution": metrics.ATTRIBUTION["arena"], "pages": pages}
    metrics.SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    metrics.SNAPSHOT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {metrics.SNAPSHOT} ({metrics.SNAPSHOT.stat().st_size // 1024} KB, {sum(len(v) for v in pages.values())} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
