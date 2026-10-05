"""Orquestación: construir prompt, rutear, ejecutar con fallback y registrar."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import adapters, attachments as att_mod, router, scoring, state

MAX_CONTEXT_CHARS = 400_000


def load_config(path: Optional[str] = None, apply_scoring: bool = True) -> Dict:
    p = Path(path or os.environ.get("ROUTER_MODELS") or Path(__file__).resolve().parent.parent / "models.json")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    return scoring.apply_to_config(cfg) if apply_scoring else cfg  # el puntaje sale de las métricas y de tus prioridades


def build_prompt(task: str, context_files: Optional[List[str]] = None) -> Tuple[str, int, List[str]]:
    """Agrega el contenido de archivos de contexto al prompt. Devuelve (prompt, chars_de_contexto, avisos)."""
    warnings: List[str] = []
    if not context_files:
        return task, 0, warnings
    parts, used = [task, "\n\n--- CONTEXTO ---"], 0
    for f in context_files:
        try:
            text = Path(f).expanduser().read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"no se pudo leer {f}: {exc.strerror}")
            continue
        if used + len(text) > MAX_CONTEXT_CHARS:
            text = text[: max(0, MAX_CONTEXT_CHARS - used)]
            warnings.append(f"{f} truncado al límite de {MAX_CONTEXT_CHARS} caracteres")
        used += len(text)
        parts.append(f"\n### {f}\n{text}")
    return "".join(parts), used, warnings


def route(task: str, cfg: Dict, context_len: int = 0, prefer: Optional[str] = None,
          boost: Optional[Dict[str, float]] = None, needs_files: bool = False) -> Dict:
    """`boost` suma categorías (p. ej. multimodal por una imagen adjunta); `needs_files` descarta los CLIs que no abren archivos."""
    weights = router.detect(task, context_len)
    for cat, w in (boost or {}).items():
        weights[cat] = max(weights.get(cat, 0), w)
    models = cfg["models"]
    ranking = router.rank(
        weights,
        models,
        is_available=lambda n: models[n].get("enabled", True) and adapters.is_available(n, models[n]),
        cooldown=state.cooldown_remaining,
        prefer=prefer,
    )
    if needs_files:
        for r in ranking:
            if not models[r["name"]].get("reads_files", True):
                r.update(usable=False, blocked=True, why=r["why"] + " (no abre archivos)")
        ranking.sort(key=lambda r: (not r["usable"], -r["score"]))
    chosen = next((r["name"] for r in ranking if r["usable"]), None)
    return {"weights": weights, "classifier": "reglas", "ranking": ranking, "chosen": chosen, "metrics": bool(cfg.get("_scored"))}


def ask(
    task: str,
    cfg: Dict,
    model: str = "auto",
    context_files: Optional[List[str]] = None,
    max_attempts: int = 3,
    dry_run: bool = False,
    timeout: Optional[float] = None,
    cwd: Optional[str] = None,
    preamble: str = "",
    route_text: Optional[str] = None,
    attachments: Optional[List[att_mod.Attachment]] = None,
) -> Dict:
    """`preamble` (p. ej. el historial del chat) se antepone al prompt pero no influye en el ruteo.
    `route_text` permite clasificar con otro texto que el enviado (p. ej. un seguimiento corto hereda el tema anterior).
    `attachments`: los de texto se anexan como contexto; imágenes/PDF/binarios/carpetas se referencian por ruta y
    obligan a usar un modelo que pueda abrir archivos."""
    inline, referenced = att_mod.split_for_prompt(attachments or [])
    prompt, ctx_len, warnings = build_prompt(task, list(context_files or []) + inline)
    prompt = preamble + prompt + att_mod.reference_block(referenced)
    boost = {"multimodal": 2.0} if any(a.kind in ("image", "pdf") for a in referenced) else None
    decision = route(route_text or task, cfg, ctx_len, boost=boost, needs_files=bool(referenced))
    models = cfg["models"]
    if model != "auto":
        if model not in models:
            raise ValueError(f"modelo desconocido: {model} (opciones: auto, {', '.join(models)})")
        if referenced and not models[model].get("reads_files", True):
            raise ValueError(f"{model} no puede abrir archivos adjuntos (imágenes, PDF, binarios) en modo no interactivo; usá /model auto u otro modelo")
        order = [model]
    else:
        order = [r["name"] for r in decision["ranking"] if r["usable"]][:max_attempts]

    att_dirs = sorted({str(a.path if a.kind == "dir" else a.path.parent) for a in referenced})
    result: Dict = {"decision": decision, "order": order, "warnings": warnings, "attempts": [], "ok": False, "output": ""}
    if dry_run:
        result["dry_run"] = True
        return result
    if not order:
        result["error"] = "ningún modelo disponible (¿CLIs instalados? ¿en cooldown?). Corré `python3 cli.py doctor`."
        return result

    for name in order:
        res = adapters.run_cli(name, models[name], prompt, timeout=timeout, cwd=cwd, usage=True, extra_dirs=att_dirs)
        attempt = {k: res[k] for k in ("seconds", "returncode", "rate_limited", "auth_required", "error", "model_id", "tokens")}
        attempt["model"] = name
        result["attempts"].append(attempt)
        state.remember_model_id(name, res["model_id"])  # así aprendemos qué modelo usa cada CLI sin gastar una consulta aparte
        state.log_event({"model": name, "model_id": res["model_id"], "tokens": res["tokens"], "ok": res["ok"], "seconds": res["seconds"],
                         "error": res["error"], "task_chars": len(task), "weights": decision["weights"]})
        if res["ok"]:
            result.update(ok=True, output=res["output"], model_used=name, model_id=res["model_id"], tokens=res["tokens"])
            return result
        if res["rate_limited"]:
            state.set_cooldown(name, cfg.get("cooldown_minutes", 30))
        elif res["auth_required"]:  # sin login/API key: no reintentar hasta que el usuario lo arregle
            state.set_cooldown(name, cfg.get("auth_cooldown_minutes", 60))
    result["error"] = "todos los intentos fallaron"
    return result


def format_usage(res: Dict) -> str:
    """'claude (claude-sonnet-5-5) · in 15.9k · out 5' del resultado de `ask`."""
    who = res["model_used"] + (f" ({res['model_id']})" if res.get("model_id") else "")
    return f"{who} · {adapters.fmt_tokens(res.get('tokens'))}"


def format_ranking(decision: Dict) -> str:
    lines = [f"Clasificación ({decision['classifier']}, métricas: {'sí' if decision.get('metrics') else 'no, estimación de models.json'}): " + (", ".join(f"{c}×{w:g}" for c, w in decision["weights"].items()) or "general")]
    lines.append(f"{'modelo':<8} {'score':>5}  estado")
    for r in decision["ranking"]:
        status = "OK" if r["usable"] else ("no disponible" if not r["available"] else "sin archivos" if r.get("blocked") else f"cooldown {r['cooldown_s']}s")
        lines.append(f"{r['name']:<8} {r['score']:>5}  {status:<14} {r['why']}")
    lines.append(f"→ elegido: {decision['chosen'] or 'ninguno'}")
    return "\n".join(lines)
