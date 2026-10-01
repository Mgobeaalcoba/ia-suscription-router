"""Estado persistente mínimo: cooldowns por proveedor y log de ejecuciones.

Se guarda en ~/.llm-router-poc (o en $ROUTER_HOME). Sin dependencias externas.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict


def home() -> Path:
    return Path(os.environ.get("ROUTER_HOME") or (Path.home() / ".llm-router-poc"))


def _state_path() -> Path:
    return home() / "state.json"


def load_state() -> Dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("cooldowns", {})
    return data


def save_state(state: Dict) -> None:
    home().mkdir(parents=True, exist_ok=True)
    tmp = _state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    os.replace(tmp, _state_path())


def cooldown_remaining(model: str, now: float | None = None) -> float:
    """Segundos que faltan para que `model` vuelva a estar disponible (0 si ya lo está)."""
    now = time.time() if now is None else now
    until = load_state()["cooldowns"].get(model, 0)
    return max(0.0, float(until) - now)


def set_cooldown(model: str, minutes: float) -> None:
    state = load_state()
    state["cooldowns"][model] = time.time() + minutes * 60
    save_state(state)


def reset_cooldowns() -> None:
    state = load_state()
    state["cooldowns"] = {}
    save_state(state)


def log_event(event: Dict) -> None:
    """Agrega una línea JSON a log.jsonl. Nunca guarda el prompt completo."""
    try:
        home().mkdir(parents=True, exist_ok=True)
        event = dict(event, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        with open(home() / "log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # el log es best-effort
