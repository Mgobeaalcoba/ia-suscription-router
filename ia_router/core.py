"""Orchestration: build the prompt, route, run with fallback and log."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import adapters, attachments as att_mod, connectors as connectors_mod, router, scoring, state

MAX_CONTEXT_CHARS = 400_000


PACKAGED_MODELS = Path(__file__).resolve().parent / "data" / "models.json"


def models_path(path: Optional[str] = None) -> Path:
    """Which models.json applies: the requested one > ROUTER_MODELS > ~/.ia-router/models.json (your copy, survives updates) > the bundled one."""
    mine = state.home() / "models.json"
    return Path(path or os.environ.get("ROUTER_MODELS") or (mine if mine.exists() else PACKAGED_MODELS))


def load_config(path: Optional[str] = None, apply_scoring: bool = True) -> Dict:
    p = models_path(path)
    cfg = json.loads(p.read_text(encoding="utf-8"))
    return scoring.apply_to_config(cfg) if apply_scoring else cfg  # the score comes from the metrics and your priorities


def build_prompt(task: str, context_files: Optional[List[str]] = None) -> Tuple[str, int, List[str]]:
    """Adds the content of context files to the prompt. Returns (prompt, context_chars, warnings)."""
    warnings: List[str] = []
    if not context_files:
        return task, 0, warnings
    parts, used = [task, "\n\n--- CONTEXT ---"], 0
    for f in context_files:
        try:
            text = Path(f).expanduser().read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"could not read {f}: {exc.strerror}")
            continue
        if used + len(text) > MAX_CONTEXT_CHARS:
            text = text[: max(0, MAX_CONTEXT_CHARS - used)]
            warnings.append(f"{f} truncated to the limit of {MAX_CONTEXT_CHARS} characters")
        used += len(text)
        parts.append(f"\n### {f}\n{text}")
    return "".join(parts), used, warnings


def route(task: str, cfg: Dict, context_len: int = 0, prefer: Optional[str] = None,
          boost: Optional[Dict[str, float]] = None, needs_files: bool = False) -> Dict:
    """`boost` adds categories (e.g. multimodal because of an attached image); `needs_files` discards the CLIs that cannot open files."""
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
                r.update(usable=False, blocked=True, why=r["why"] + " (cannot open files)")
        ranking.sort(key=lambda r: (not r["usable"], -r["score"]))
    chosen = next((r["name"] for r in ranking if r["usable"]), None)
    return {"weights": weights, "classifier": "rules", "ranking": ranking, "chosen": chosen, "metrics": bool(cfg.get("_scored"))}


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
    connectors: Optional[bool] = None,
) -> Dict:
    """`preamble` (e.g. the chat history) is prepended to the prompt but does not influence routing.
    `route_text` lets you classify with a different text than the one sent (e.g. a short follow-up inherits the previous topic).
    `attachments`: text ones are appended as context; images/PDFs/binaries/folders are referenced by path and
    force the use of a model that can open files.
    `connectors`: give the model the registered MCP connectors (Gmail, Calendar…) through the router's proxy;
    None = automatic (on when at least one enabled connector is registered)."""
    inline, referenced = att_mod.split_for_prompt(attachments or [])
    prompt, ctx_len, warnings = build_prompt(task, list(context_files or []) + inline)
    prompt = preamble + prompt + att_mod.reference_block(referenced)
    boost = {"multimodal": 2.0} if any(a.kind in ("image", "pdf") for a in referenced) else None
    decision = route(route_text or task, cfg, ctx_len, boost=boost, needs_files=bool(referenced))
    models = cfg["models"]
    if model != "auto":
        if model not in models:
            raise ValueError(f"unknown model: {model} (options: auto, {', '.join(models)})")
        if referenced and not models[model].get("reads_files", True):
            raise ValueError(f"{model} cannot open attached files (images, PDFs, binaries) in non-interactive mode; use /model auto or another model")
        order = [model]
    else:
        order = [r["name"] for r in decision["ranking"] if r["usable"]][:max_attempts]

    att_dirs = sorted({str(a.path if a.kind == "dir" else a.path.parent) for a in referenced})
    use_mcp = connectors_mod.has_connectors() if connectors is None else bool(connectors)
    result: Dict = {"decision": decision, "order": order, "warnings": warnings, "attempts": [], "ok": False, "output": "", "connectors": use_mcp}
    if dry_run:
        result["dry_run"] = True
        return result
    if not order:
        result["error"] = "no model available (are the CLIs installed? in cooldown?). Run `python3 cli.py doctor`."
        return result

    for name in order:
        res = adapters.run_cli(name, models[name], prompt, timeout=timeout, cwd=cwd, usage=True, extra_dirs=att_dirs, mcp=use_mcp)
        attempt = {k: res[k] for k in ("seconds", "returncode", "rate_limited", "auth_required", "error", "model_id", "tokens")}
        attempt["model"] = name
        result["attempts"].append(attempt)
        state.remember_model_id(name, res["model_id"])  # this is how we learn which model each CLI uses without spending a separate query
        state.log_event({"model": name, "model_id": res["model_id"], "tokens": res["tokens"], "ok": res["ok"], "seconds": res["seconds"],
                         "error": res["error"], "task_chars": len(task), "weights": decision["weights"]})
        if res["ok"]:
            state.set_auth_missing(name, False)
            result.update(ok=True, output=res["output"], model_used=name, model_id=res["model_id"], tokens=res["tokens"])
            return result
        if res["rate_limited"]:
            state.set_cooldown(name, cfg.get("cooldown_minutes", 30))
        elif res["auth_required"]:  # no login/API key: do not retry until the user fixes it
            state.set_auth_missing(name, True)
            state.set_cooldown(name, cfg.get("auth_cooldown_minutes", 60))
    result["error"] = "all attempts failed"
    return result


def format_usage(res: Dict) -> str:
    """'claude (claude-sonnet-5-5) · in 15.9k · out 5' from the result of `ask`."""
    who = res["model_used"] + (f" ({res['model_id']})" if res.get("model_id") else "")
    return f"{who} · {adapters.fmt_tokens(res.get('tokens'))}"


def format_ranking(decision: Dict) -> str:
    lines = [f"Classification ({decision['classifier']}, metrics: {'yes' if decision.get('metrics') else 'no, models.json estimate'}): " + (", ".join(f"{c}×{w:g}" for c, w in decision["weights"].items()) or "general")]
    lines.append(f"{'model':<8} {'score':>5}  status")
    for r in decision["ranking"]:
        status = "OK" if r["usable"] else ("unavailable" if not r["available"] else "no files" if r.get("blocked") else f"cooldown {r['cooldown_s']}s")
        lines.append(f"{r['name']:<8} {r['score']:>5}  {status:<14} {r['why']}")
    lines.append(f"→ chosen: {decision['chosen'] or 'none'}")
    return "\n".join(lines)
