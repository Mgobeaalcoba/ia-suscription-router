#!/usr/bin/env python3
"""Regenera la foto de Arena que viene incluida en el software (ia_router/data/arena.json). Es para quien MANTIENE el repo:
correlo antes de publicar una versión. Los usuarios actualizan sus métricas con `python3 cli.py metrics refresh`.

Lee las páginas públicas de arena.ai (~11 pedidos, alrededor de un minuto). Los datos de Arena son CC BY 4.0: se conserva la atribución.
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
        print("Faltaron páginas: no se actualiza la foto incluida.", file=sys.stderr)
        return 1
    data = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "source": "https://arena.ai/leaderboard",
            "attribution": metrics.ATTRIBUTION["arena"], "pages": pages}
    metrics.SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    metrics.SNAPSHOT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Escribí {metrics.SNAPSHOT} ({metrics.SNAPSHOT.stat().st_size // 1024} KB, {sum(len(v) for v in pages.values())} filas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
