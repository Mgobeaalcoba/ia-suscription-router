"""Orquestación: construir prompt, rutear, ejecutar con fallback y registrar."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import adapters, router, state

MAX_CONTEXT_CHARS = 400_000


def load_config(path: Optional[str] = None) -> Dict:
    p = Path(path or os.environ.get("ROUTER_MODELS") or Path(__file__).resolve().parent.parent / "models.json")
    return json.loads(p.read_text(encoding="utf-8"))


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


def _classifier_runner(cfg: Dict):
    cmd = cfg.get("classifier_cmd")
    if not cmd:
        return None
    spec = {"cmd": cmd, "timeout": 60}

    def run(prompt: str) -> Optional[str]:
        res = adapters.run_cli("classifier", spec, prompt)
        return res["output"] if res["ok"] else None

    return run


def route(task: str, cfg: Dict, context_len: int = 0, prefer: Optional[str] = None, use_llm: bool = False) -> Dict:
    weights = router.detect(task, context_len)
    source = "reglas"
    if use_llm:
        runner = _classifier_runner(cfg)
        llm_weights = router.classify_with_llm(task, runner) if runner else None
        if llm_weights:
            weights, source = llm_weights, "clasificador LLM"
    models = cfg["models"]
    ranking = router.rank(
        weights,
        models,
        is_available=lambda n: adapters.is_available(n, models[n]),
        cooldown=state.cooldown_remaining,
        prefer=prefer,
    )
    chosen = next((r["name"] for r in ranking if r["usable"]), None)
    return {"weights": weights, "classifier": source, "ranking": ranking, "chosen": chosen}


def ask(
    task: str,
    cfg: Dict,
    model: str = "auto",
    context_files: Optional[List[str]] = None,
    max_attempts: int = 3,
    dry_run: bool = False,
    use_llm: bool = False,
    timeout: Optional[float] = None,
    cwd: Optional[str] = None,
) -> Dict:
    prompt, ctx_len, warnings = build_prompt(task, context_files)
    decision = route(task, cfg, ctx_len, use_llm=use_llm)
    models = cfg["models"]
    if model != "auto":
        if model not in models:
            raise ValueError(f"modelo desconocido: {model} (opciones: auto, {', '.join(models)})")
        order = [model]
    else:
        order = [r["name"] for r in decision["ranking"] if r["usable"]][:max_attempts]

    result: Dict = {"decision": decision, "order": order, "warnings": warnings, "attempts": [], "ok": False, "output": ""}
    if dry_run:
        result["dry_run"] = True
        return result
    if not order:
        result["error"] = "ningún modelo disponible (¿CLIs instalados? ¿en cooldown?). Corré `python3 cli.py doctor`."
        return result

    for name in order:
        res = adapters.run_cli(name, models[name], prompt, timeout=timeout, cwd=cwd)
        attempt = {k: res[k] for k in ("seconds", "returncode", "rate_limited", "error")}
        attempt["model"] = name
        result["attempts"].append(attempt)
        state.log_event({"model": name, "ok": res["ok"], "seconds": res["seconds"], "error": res["error"],
                         "task_chars": len(task), "weights": decision["weights"]})
        if res["ok"]:
            result.update(ok=True, output=res["output"], model_used=name)
            return result
        if res["rate_limited"]:
            state.set_cooldown(name, cfg.get("cooldown_minutes", 30))
    result["error"] = "todos los intentos fallaron"
    return result


def format_ranking(decision: Dict) -> str:
    lines = [f"Clasificación ({decision['classifier']}): " + (", ".join(f"{c}×{w:g}" for c, w in decision["weights"].items()) or "general")]
    lines.append(f"{'modelo':<8} {'score':>5}  estado")
    for r in decision["ranking"]:
        status = "OK" if r["usable"] else ("no instalado" if not r["available"] else f"cooldown {r['cooldown_s']}s")
        lines.append(f"{r['name']:<8} {r['score']:>5}  {status:<14} {r['why']}")
    lines.append(f"→ elegido: {decision['chosen'] or 'ninguno'}")
    return "\n".join(lines)
