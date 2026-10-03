"""Adaptadores CLI-subprocess: lanzan los CLIs OFICIALES (claude, codex, agy).

Principio de diseño: este código NUNCA lee, copia ni envía tokens OAuth. Cada CLI usa su
propio login y su propia suscripción. Se ejecutan en modo no interactivo, con stdin cerrado
(salvo en modo stdin para prompts enormes) y sin shell (lista de argumentos).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Dict, List, Optional

RATE_LIMIT_RE = re.compile(
    r"rate.?limit|quota|\b429\b|too many requests|usage limit|limit reached|"
    r"resource.?exhausted|exceeded your|try again (later|in)",
    re.I,
)
AUTH_RE = re.compile(
    r"set an auth method|not logged in|please (log ?in|sign in)|/login|unauthori[sz]ed|\b401\b|"
    r"no longer supported|ineligible|invalid api key|api key (is )?(not set|missing|required)|authentication (required|failed)|GEMINI_API_KEY",
    re.I,
)
# Por encima de este tamaño el prompt va por stdin para no pasarse del límite de argv.
STDIN_THRESHOLD = 100_000


def _cmd_template(name: str, spec: dict, use_stdin: bool) -> List[str]:
    override = os.environ.get(f"ROUTER_CMD_{name.upper()}")
    if override:  # ej: ROUTER_CMD_CODEX='["codex","exec","{prompt}"]'
        return list(json.loads(override))
    return list(spec["cmd_stdin"] if use_stdin and spec.get("cmd_stdin") else spec["cmd"])


def is_available(name: str, spec: dict) -> bool:
    try:
        return shutil.which(_cmd_template(name, spec, False)[0]) is not None
    except (ValueError, KeyError, IndexError):
        return False


def run_cli(
    name: str,
    spec: dict,
    prompt: str,
    timeout: Optional[float] = None,
    cwd: Optional[str] = None,
) -> Dict:
    """Ejecuta el CLI del modelo y devuelve un dict normalizado."""
    use_stdin = len(prompt) > STDIN_THRESHOLD and bool(spec.get("cmd_stdin"))
    argv = [a.replace("{prompt}", prompt) for a in _cmd_template(name, spec, use_stdin)]
    t0 = time.time()
    try:
        proc = subprocess.run(
            argv,
            input=prompt if use_stdin else None,
            stdin=None if use_stdin else subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout or spec.get("timeout", 300),
            cwd=cwd,
        )
    except FileNotFoundError:
        return _result(False, "", f"CLI no encontrado: {argv[0]}", None, t0, False, "not_installed")
    except subprocess.TimeoutExpired:
        return _result(False, "", "timeout", None, t0, False, "timeout")
    out, err = proc.stdout.strip(), proc.stderr.strip()
    failed = proc.returncode != 0
    limited = failed and bool(RATE_LIMIT_RE.search(out + "\n" + err))
    auth = failed and not limited and bool(AUTH_RE.search(out + "\n" + err))
    ok = proc.returncode == 0 and bool(out)
    error = None if ok else ("rate_limited" if limited else "auth_required" if auth else (err[-400:] or "salida vacía"))
    return _result(ok, out, err[-400:], proc.returncode, t0, limited, error, auth)


def _result(ok, output, stderr, rc, t0, limited, error, auth=False) -> Dict:
    return {
        "ok": ok,
        "output": output,
        "stderr": stderr,
        "returncode": rc,
        "seconds": round(time.time() - t0, 2),
        "rate_limited": limited,
        "auth_required": auth,
        "error": error,
    }
