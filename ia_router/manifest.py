"""Manifiesto de ruteo: qué modelo prefiere el usuario para cada tipo de tarea.

Lo arma un modelo "manager" barato (elegido por el usuario) a partir de información objetiva
(CLIs instalados, autenticación, latencia medida, historial de éxito/rate limits) y de las
preferencias que el usuario escribe en lenguaje natural. Es iterable: el usuario da feedback y
el manager actualiza el manifiesto. El router lo aplica sobre `strengths` de models.json.

Archivo: <home>/manifest.json (+ manifest.prev.json con la versión anterior).
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Callable, Dict, List, Optional

from . import adapters, router, state

CATEGORIES: List[str] = list(router.PATTERNS) + ["quick"]
PROBE_PROMPT = "Respondé solo: OK"
HISTORY_LIMIT = 20

Runner = Callable[[str], Optional[str]]


# ---------- persistencia ----------

def path():
    return state.home() / "manifest.json"


def load() -> Optional[Dict]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("tasks"), dict) else None


def save(manifest: Dict, reason: str) -> None:
    state.home().mkdir(parents=True, exist_ok=True)
    if path().exists():
        path().replace(path().with_name("manifest.prev.json"))
    manifest["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    hist = manifest.setdefault("history", [])
    hist.append({"ts": manifest["updated"], "reason": reason[:300]})
    del hist[:-HISTORY_LIMIT]
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path())


# ---------- información objetiva ----------

def probe(cfg: Dict) -> Dict[str, Dict]:
    """Prueba cada CLI con un prompt mínimo (gasta una llamada corta por modelo)."""
    info: Dict[str, Dict] = {}
    for name, spec in cfg["models"].items():
        row: Dict = {"installed": adapters.is_available(name, spec)}
        if row["installed"]:
            exe = adapters._cmd_template(name, spec, False)[0]
            try:
                v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
                row["version"] = (v.stdout or v.stderr).strip().splitlines()[0] if (v.stdout or v.stderr).strip() else "?"
            except (OSError, subprocess.TimeoutExpired):
                row["version"] = "?"
            res = adapters.run_cli(name, spec, PROBE_PROMPT, timeout=90)
            row["auth"] = "ok" if res["ok"] else "missing" if res["auth_required"] else "unknown"
            row["probe_seconds"] = res["seconds"]
            if not res["ok"]:
                row["probe_error"] = (res["error"] or "")[:200]
        else:
            row["auth"] = "n/a"
        info[name] = row
    return info


def objective_info(cfg: Dict, manifest: Optional[Dict] = None) -> Dict[str, Dict]:
    """Datos medidos por modelo: última sonda (guardada en el manifiesto) + estadísticas del log."""
    probed = (manifest or {}).get("models", {})
    stats = state.stats()
    out: Dict[str, Dict] = {}
    for name, spec in cfg["models"].items():
        row = dict(probed.get(name) or {"installed": adapters.is_available(name, spec)})
        row.update({k: v for k, v in stats.get(name, {}).items() if k != "ok"})
        row["cooldown_s"] = round(state.cooldown_remaining(name))
        out[name] = row
    return out


# ---------- generación con el manager LLM ----------

def _json_block(text: Optional[str]) -> Optional[Dict]:
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def parse(text: Optional[str], model_names: List[str]) -> Optional[Dict]:
    """Valida la respuesta del manager: solo categorías y modelos conocidos. None si es inutilizable."""
    data = _json_block(text)
    tasks_in = data.get("tasks") if data else None
    if not isinstance(tasks_in, dict):
        return None
    tasks: Dict[str, Dict] = {}
    for cat, val in tasks_in.items():
        if cat not in CATEGORIES or not isinstance(val, dict) or not isinstance(val.get("prefer"), list):
            continue
        prefer = [m for m in dict.fromkeys(val["prefer"]) if m in model_names]
        if prefer:
            tasks[cat] = {"prefer": prefer, "why": str(val.get("why", ""))[:200]}
    if not tasks:
        return None
    disabled = [m for m in (data.get("disabled") or []) if m in model_names] if isinstance(data.get("disabled"), list) else []
    return {"tasks": tasks, "disabled": disabled}


def draft_from_strengths(cfg: Dict, info: Dict[str, Dict]) -> Dict:
    """Manifiesto determinístico (sin LLM) a partir de las fortalezas iniciales. Sirve de respaldo."""
    models = cfg["models"]
    ok = [n for n in models if info.get(n, {}).get("installed") and info.get(n, {}).get("auth") != "missing"] or list(models)
    tasks = {}
    for cat in CATEGORIES:
        ordered = sorted(ok, key=lambda n: -models[n].get("strengths", {}).get(cat, models[n].get("strengths", {}).get("general", 5)))
        tasks[cat] = {"prefer": ordered, "why": "derivado de las fortalezas iniciales de models.json (sin LLM)"}
    return {"tasks": tasks, "disabled": []}


def _generate_prompt(cfg: Dict, info: Dict, notes: str) -> str:
    strengths = {n: s.get("strengths", {}) for n, s in cfg["models"].items()}
    return (
        'Sos el "manager" de un router que reparte tareas entre CLIs de IA con suscripción. '
        "Armá el manifiesto de preferencias: para cada categoría, qué modelos conviene usar y en qué orden.\n\n"
        f"MODELOS E INFO OBJETIVA (medida):\n{json.dumps(info, ensure_ascii=False)}\n\n"
        f"FORTALEZAS INICIALES, hipótesis 0-10 (no son verdades):\n{json.dumps(strengths)}\n\n"
        f"CATEGORÍAS: {', '.join(CATEGORIES)}\n\n"
        f"PREFERENCIAS DEL USUARIO (mandan sobre las hipótesis):\n{notes.strip() or 'ninguna todavía'}\n\n"
        'Respondé SOLO un JSON: {"tasks": {"<categoria>": {"prefer": ["modelo1", "modelo2"], "why": "motivo breve"}}, '
        '"disabled": ["modelo que no hay que usar"]}\n'
        "Reglas: usá solo los modelos y categorías listados; incluí todas las categorías; ordená de mejor a peor; "
        'no pongas en "prefer" modelos con auth distinto de "ok" o no instalados salvo que el usuario lo pida; '
        'poné en "disabled" solo lo que el usuario pida desactivar.'
    )


def _refine_prompt(manifest: Dict, info: Dict, feedback: str) -> str:
    current = {"tasks": manifest["tasks"], "disabled": manifest.get("disabled", [])}
    return (
        'Sos el "manager" de un router de CLIs de IA. Actualizá el manifiesto según el feedback del usuario. '
        "Cambiá solo lo que el feedback pide y devolvé el manifiesto COMPLETO.\n\n"
        f"MANIFIESTO ACTUAL:\n{json.dumps(current, ensure_ascii=False)}\n\n"
        f"INFO OBJETIVA:\n{json.dumps(info, ensure_ascii=False)}\n\n"
        f"CATEGORÍAS: {', '.join(CATEGORIES)}\n\n"
        f"FEEDBACK DEL USUARIO:\n{feedback.strip()}\n\n"
        'Respondé SOLO un JSON con el mismo formato: {"tasks": {"<categoria>": {"prefer": [...], "why": "..."}}, "disabled": [...]}'
    )


def generate(cfg: Dict, run: Optional[Runner], manager: str, notes: str = "", probed: Optional[Dict] = None) -> Dict:
    """Crea el manifiesto. Con `run` usa el manager LLM; si falla, cae al borrador determinístico."""
    base = {"models": probed} if probed else None
    info = objective_info(cfg, base)
    parsed = parse(run(_generate_prompt(cfg, info, notes)), list(cfg["models"])) if run else None
    source = "manager LLM"
    if parsed is None:
        parsed, source = draft_from_strengths(cfg, info), "reglas (el manager no respondió un JSON válido)"
    fill = draft_from_strengths(cfg, info)["tasks"]
    for cat in CATEGORIES:  # el manager puede omitir categorías
        parsed["tasks"].setdefault(cat, fill[cat])
    return {"version": 1, "manager": manager, "source": source, "user_notes": notes.strip(),
            "models": info, **parsed}


def refine(cfg: Dict, run: Runner, manifest: Dict, feedback: str) -> Optional[Dict]:
    """Aplica feedback del usuario con el manager. Devuelve el manifiesto nuevo o None si no se pudo."""
    parsed = parse(run(_refine_prompt(manifest, objective_info(cfg, manifest), feedback)), list(cfg["models"]))
    if parsed is None:
        return None
    new = {k: v for k, v in manifest.items() if k not in ("tasks", "disabled")}
    for cat in CATEGORIES:
        parsed["tasks"].setdefault(cat, manifest["tasks"].get(cat) or draft_from_strengths(cfg, {})["tasks"][cat])
    new.update(parsed, source="manager LLM", user_notes=(manifest.get("user_notes", "") + "\n" + feedback.strip()).strip())
    return new


# ---------- aplicación al router ----------

def apply_to_config(cfg: Dict, manifest: Optional[Dict]) -> Dict:
    """Sobrescribe `strengths` con el orden de preferencia del manifiesto y marca modelos desactivados."""
    if not manifest:
        return cfg
    for name, spec in cfg["models"].items():
        strengths = spec.setdefault("strengths", {})
        for cat, entry in manifest["tasks"].items():
            prefer = entry.get("prefer", [])
            strengths[cat] = max(10 - 2 * prefer.index(name), 3) if name in prefer else 1
        spec["enabled"] = name not in manifest.get("disabled", [])
    cfg["_manifest"] = True
    return cfg


# ---------- presentación ----------

def render(manifest: Dict) -> str:
    lines = [f"Manifiesto — manager: {manifest.get('manager')} · origen: {manifest.get('source')} · actualizado: {manifest.get('updated', '-')}"]
    if manifest.get("user_notes"):
        lines.append("Preferencias del usuario: " + manifest["user_notes"].replace("\n", " | "))
    if manifest.get("disabled"):
        lines.append("Desactivados: " + ", ".join(manifest["disabled"]))
    lines.append("")
    for cat in CATEGORIES:
        entry = manifest["tasks"].get(cat)
        if entry:
            lines.append(f"{cat:<13} {' > '.join(entry['prefer']):<28} {entry.get('why', '')}")
    return "\n".join(lines)


def diff(old: Dict, new: Dict) -> str:
    out = []
    for cat in CATEGORIES:
        a, b = old["tasks"].get(cat, {}).get("prefer"), new["tasks"].get(cat, {}).get("prefer")
        if a != b:
            out.append(f"  {cat}: {' > '.join(a or [])}  →  {' > '.join(b or [])}")
    if old.get("disabled", []) != new.get("disabled", []):
        out.append(f"  desactivados: {old.get('disabled', [])} → {new.get('disabled', [])}")
    return "\n".join(out) or "  (sin cambios en el orden de preferencias)"
