"""Lector mínimo de archivos `.env` (sin dependencias): líneas CLAVE=valor, comentarios con #, comillas opcionales.

Nunca pisa una variable que ya esté definida en el entorno. Se busca `.env` en la carpeta del repo y en ~/.ia-router/.
El `.env` real está en .gitignore: nunca se versiona; lo que se versiona es `.env.example`.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, Optional

from . import state

REPO_ENV = Path(__file__).resolve().parent.parent / ".env"


def parse(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key.isidentifier() and value:
            out[key] = value
    return out


def load(paths: Optional[Iterable[Path]] = None) -> Dict[str, str]:
    """Carga los .env en os.environ (sin pisar lo ya definido). Devuelve lo que agregó."""
    added: Dict[str, str] = {}
    for p in paths if paths is not None else (REPO_ENV, state.home() / ".env"):
        try:
            values = parse(Path(p).read_text(encoding="utf-8"))
        except OSError:
            continue
        for k, v in values.items():
            if k not in os.environ:
                os.environ[k] = v
                added[k] = v
    return added
