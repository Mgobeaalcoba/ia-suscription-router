"""Estado persistente mínimo: cooldowns por proveedor y log de ejecuciones.

Se guarda en ~/.ia-router (o en $ROUTER_HOME). Sin dependencias externas.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional


def home() -> Path:
    return Path(os.environ.get("ROUTER_HOME") or (Path.home() / ".ia-router"))


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


def flags() -> Dict:
    """Banderas de la sesión de inicio (cuándo se ofreció actualizar, si ya se ofrecieron las preguntas)."""
    try:
        d = json.loads((home() / "startup.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def set_flag(key: str, value) -> None:
    f = flags()
    f[key] = value
    try:
        home().mkdir(parents=True, exist_ok=True)
        (home() / "startup.json").write_text(json.dumps(f, indent=2), encoding="utf-8")
    except OSError:
        pass


def _seen_path() -> Path:
    return home() / "models_seen.json"


def seen_ids() -> Dict[str, str]:
    """Id real del modelo que respondió por cada CLI, la última vez que lo vimos."""
    try:
        d = json.loads(_seen_path().read_text(encoding="utf-8"))
        return {k: v for k, v in d.items() if isinstance(v, str)} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_model_id(model: str, model_id: Optional[str]) -> None:
    if not model_id:
        return
    seen = seen_ids()
    if seen.get(model) == model_id:
        return
    seen[model] = model_id
    try:
        home().mkdir(parents=True, exist_ok=True)
        _seen_path().write_text(json.dumps(seen, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def log_event(event: Dict) -> None:
    """Agrega una línea JSON a log.jsonl. Nunca guarda el prompt completo."""
    try:
        home().mkdir(parents=True, exist_ok=True)
        event = dict(event, ts=time.strftime("%Y-%m-%dT%H:%M:%S"))
        with open(home() / "log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # el log es best-effort


def stats() -> Dict[str, Dict]:
    """Resumen objetivo por modelo a partir de log.jsonl: corridas, tasa de éxito, latencia media, rate limits."""
    out: Dict[str, Dict] = {}
    try:
        lines = (home() / "log.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            ev = json.loads(line)
            m = out.setdefault(ev["model"], {"runs": 0, "ok": 0, "rate_limits": 0, "auth_errors": 0, "_secs": 0.0, "tokens_in": 0, "tokens_out": 0, "token_runs": 0})
        except (ValueError, KeyError, TypeError):
            continue
        m["runs"] += 1
        m["ok"] += 1 if ev.get("ok") else 0
        m["rate_limits"] += 1 if ev.get("error") == "rate_limited" else 0
        m["auth_errors"] += 1 if ev.get("error") == "auth_required" else 0
        m["_secs"] += float(ev.get("seconds") or 0)
        t = ev.get("tokens") or {}
        m["tokens_in"] += t.get("input", 0)
        m["tokens_out"] += t.get("output", 0)
        m["token_runs"] += 1 if t else 0  # corridas con tokens registrados (las viejas no los tienen)
    for m in out.values():
        m["ok_rate"] = round(m["ok"] / m["runs"], 2)
        m["avg_seconds"] = round(m.pop("_secs") / m["runs"], 1)
    return out
