"""CLI probe: are they installed and logged in? Which version? WHICH MODEL do they really use?

The real model id (not the CLI name) is what gets matched against the metrics; it is learned from a minimal query and,
afterwards, from every normal response (core.ask records it). Each probe query spends a pinch of quota.
"""
from __future__ import annotations

import subprocess
from typing import Callable, Dict, List, Optional

from . import adapters, metrics, state

PROBE_PROMPT = "Reply only: OK"


def probe(cfg: Dict, only: Optional[List[str]] = None) -> Dict[str, Dict]:
    """Tries each CLI with a minimal prompt and records the model that answered. {model: {installed, version, auth, probe_seconds, model_id}}."""
    info: Dict[str, Dict] = {}
    for name, spec in cfg["models"].items():
        if only is not None and name not in only:
            continue
        row: Dict = {"installed": adapters.is_available(name, spec)}
        if row["installed"]:
            exe = adapters._cmd_template(name, spec, False)[0]
            try:
                v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL)
                row["version"] = (v.stdout or v.stderr).strip().splitlines()[0] if (v.stdout or v.stderr).strip() else "?"
            except (OSError, subprocess.TimeoutExpired):
                row["version"] = "?"
            res = adapters.run_cli(name, spec, PROBE_PROMPT, timeout=90, usage=True)
            row["auth"] = "ok" if res["ok"] else "missing" if res["auth_required"] else "unknown"
            row["probe_seconds"] = res["seconds"]
            row["model_id"] = res.get("model_id")
            state.remember_model_id(name, res.get("model_id"))
            if not res["ok"]:
                row["probe_error"] = (res["error"] or "")[:200]
        else:
            row["auth"] = "n/a"
        info[name] = row
    return info


def missing_ids(cfg: Dict) -> List[str]:
    """Installed models whose real id we do not know yet."""
    names = [n for n, s in cfg["models"].items() if s.get("enabled", True) and adapters.is_available(n, s)]
    ids = metrics.known_model_ids(names)
    return [n for n in names if not ids.get(n)]


def detect_ids(cfg: Dict, say: Callable[[str], None] = print) -> Dict[str, Optional[str]]:
    """Minimal query only to the CLIs whose model we do not know."""
    todo = missing_ids(cfg)
    found: Dict[str, Optional[str]] = {}
    for name, row in probe(cfg, only=todo).items():
        found[name] = row.get("model_id")
        say(f"  {name:<12} → {row.get('model_id') or 'could not determine the model'}" + (f"  ({row['probe_error'][:80]})" if row.get("probe_error") else ""))
    return found
