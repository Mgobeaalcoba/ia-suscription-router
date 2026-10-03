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
import tempfile
import time
from pathlib import Path
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


def _with_usage_args(name: str, spec: dict, template: List[str], logfile: str) -> List[str]:
    """Inserta los flags que hacen al CLI devolver JSON con modelo y tokens (spec["usage"]["args"])."""
    u = spec.get("usage") or {}
    if os.environ.get(f"ROUTER_CMD_{name.upper()}") or not u.get("args"):
        return template  # comando personalizado: se respeta tal cual
    extra = [a.replace("{logfile}", logfile) for a in u["args"]]
    at = u.get("at", 1)
    return template[:at] + extra + template[at:]


def _with_dirs(name: str, spec: dict, template: List[str], dirs: List[str]) -> List[str]:
    """Da acceso de lectura a las carpetas de los adjuntos (claude: --add-dir), que si no quedan fuera de su carpeta de trabajo."""
    a = spec.get("add_dir") or {}
    if os.environ.get(f"ROUTER_CMD_{name.upper()}") or not a.get("args") or not dirs:
        return template
    extra = [x.replace("{dir}", d) for d in dirs for x in a["args"]]
    at = a.get("at", 1)
    return template[:at] + extra + template[at:]


def run_cli(
    name: str,
    spec: dict,
    prompt: str,
    timeout: Optional[float] = None,
    cwd: Optional[str] = None,
    usage: bool = False,
    extra_dirs: Optional[List[str]] = None,
) -> Dict:
    """Ejecuta el CLI del modelo y devuelve un dict normalizado.

    Con `usage=True` pide al CLI salida JSON y extrae de ahí el modelo real y los tokens
    (`res["model_id"]`, `res["tokens"]`). Si el JSON no se puede interpretar, devuelve el texto crudo."""
    use_stdin = len(prompt) > STDIN_THRESHOLD and bool(spec.get("cmd_stdin"))
    template = _cmd_template(name, spec, use_stdin)
    logfile = ""
    if usage and (spec.get("usage") or {}).get("parser") == "agy":
        fd, logfile = tempfile.mkstemp(prefix="ia-router-agy-", suffix=".log")
        os.close(fd)
    if usage:
        template = _with_usage_args(name, spec, template, logfile)
    template = _with_dirs(name, spec, template, list(extra_dirs or []))
    argv = [a.replace("{prompt}", prompt) for a in template]
    t0 = time.time()
    try:
        return _run(name, spec, argv, prompt, use_stdin, timeout, cwd, t0, usage, logfile)
    finally:
        if logfile:
            try:
                os.unlink(logfile)
            except OSError:
                pass


def _run(name, spec, argv, prompt, use_stdin, timeout, cwd, t0, usage, logfile) -> Dict:
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
    info: Dict = {}
    if usage and (spec.get("usage") or {}).get("parser"):
        text, info, is_err = parse_usage(spec["usage"]["parser"], out, argv, logfile)
        if text is not None:
            out = text
            failed = failed or is_err
            limited = failed and bool(RATE_LIMIT_RE.search(out + "\n" + err))
            auth = failed and not limited and bool(AUTH_RE.search(out + "\n" + err))
    ok = not failed and bool(out)
    error = None if ok else ("rate_limited" if limited else "auth_required" if auth else (err[-400:] or (out[-400:] if failed else "") or "salida vacía"))
    res = _result(ok, out, err[-400:], proc.returncode, t0, limited, error, auth)
    res["model_id"] = info.get("model")
    res["tokens"] = info.get("tokens")
    return res


# ---------- uso (modelo y tokens) ----------

def _tokens(inp, out, cached=0, reasoning=0) -> Dict:
    return {"input": int(inp or 0), "output": int(out or 0), "cached": int(cached or 0), "reasoning": int(reasoning or 0)}


def _json_lines(raw: str) -> List[dict]:
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def _argv_model(argv: List[str]) -> Optional[str]:
    for i, a in enumerate(argv[:-1]):
        if a in ("--model", "-m"):
            return argv[i + 1]
    return None


def codex_model(thread_id: str) -> Optional[str]:
    """Codex no informa el modelo en su salida JSON: se lee de la sesión que guardó (rollout) o de su config."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        for f in (home / "sessions").rglob(f"rollout-*{thread_id}.jsonl"):
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    m = re.search(r'"model":"([^"]+)"', line)
                    if m:
                        return m.group(1)
    except OSError:
        pass
    try:
        m = re.search(r'^model\s*=\s*"([^"]+)"', (home / "config.toml").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else None
    except OSError:
        return None


def agy_model(logfile: str) -> Optional[str]:
    """Antigravity tampoco: el modelo por defecto queda en su log (`--log-file`)."""
    try:
        labels = re.findall(r'selected model override to backend: label="([^"]+)"', Path(logfile).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return labels[-1] if labels else None


def parse_usage(parser: str, raw: str, argv: List[str], logfile: str = ""):
    """Devuelve (texto, {"model", "tokens"}, es_error). texto=None si la salida no era el JSON esperado."""
    try:
        if parser == "claude":
            d = json.loads(raw)
            u, mu = d.get("usage") or {}, d.get("modelUsage") or {}
            model = max(mu, key=lambda k: (mu[k].get("costUSD") or 0) + (mu[k].get("outputTokens") or 0), default=None) if mu else None
            cached = (u.get("cache_read_input_tokens") or 0)
            total_in = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + cached
            return d["result"], {"model": model, "tokens": _tokens(total_in, u.get("output_tokens"), cached)}, bool(d.get("is_error"))
        if parser == "codex":
            rows = _json_lines(raw)
            msgs = [r["item"]["text"] for r in rows if r.get("type") == "item.completed" and (r.get("item") or {}).get("type") == "agent_message"]
            done = [r for r in rows if r.get("type") == "turn.completed"]
            errors = [r for r in rows if r.get("type") in ("error", "turn.failed")]
            if not rows or not (msgs or done or errors):
                return None, {}, False
            u = done[-1].get("usage", {}) if done else {}
            thread = next((r.get("thread_id") for r in rows if r.get("type") == "thread.started"), "")
            model = _argv_model(argv) or (codex_model(thread) if thread else None)
            tokens = _tokens(u.get("input_tokens"), u.get("output_tokens"), u.get("cached_input_tokens"), u.get("reasoning_output_tokens")) if u else None
            return (msgs[-1] if msgs else ""), {"model": model, "tokens": tokens}, bool(errors) and not msgs
        if parser == "agy":
            d = json.loads(raw)
            u = d.get("usage") or {}
            model = _argv_model(argv) or (agy_model(logfile) if logfile else None)
            tokens = _tokens(u.get("input_tokens"), u.get("output_tokens"), u.get("cache_read_tokens"), u.get("thinking_tokens")) if u else None
            return d.get("response", ""), {"model": model, "tokens": tokens}, d.get("status") not in (None, "SUCCESS")
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    return None, {}, False


def fmt_tokens(t: Optional[Dict]) -> str:
    """'in 15.9k (13.2k caché) · out 5' o 'tokens n/d'."""
    if not t:
        return "tokens n/d"
    def k(n: int) -> str:
        return f"{n / 1000:.1f}k" if n >= 1000 else str(n)
    cached = f" ({k(t['cached'])} caché)" if t.get("cached") else ""
    return f"in {k(t['input'])}{cached} · out {k(t['output'])}"


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
        "model_id": None,
        "tokens": None,
    }
