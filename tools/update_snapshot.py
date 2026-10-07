#!/usr/bin/env python3
"""Regenerates the Arena snapshot bundled with the software (ia_router/data/arena.json). It is for whoever MAINTAINS the repo:
run it before publishing a version. Users update their metrics with `python3 cli.py metrics refresh`.

It reads the public arena.ai pages (~11 requests, about a minute). Arena data is CC BY 4.0: attribution is kept.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ia_router import metrics  # noqa: E402


def main() -> int:
    pages = metrics.fetch_arena(say=print)
    if len(pages) < len(metrics.arena_pages()):
        print("Some pages are missing: the bundled snapshot is not updated.", file=sys.stderr)
        return 1
    data = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "https://arena.ai/leaderboard",
            "attribution": metrics.ATTRIBUTION["arena"], "pages": pages}
    metrics.SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    metrics.SNAPSHOT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {metrics.SNAPSHOT} ({metrics.SNAPSHOT.stat().st_size // 1024} KB, {sum(len(v) for v in pages.values())} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
