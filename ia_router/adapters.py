"""CLI-subprocess adapters: they launch the OFFICIAL CLIs (claude, codex, agy).

Design principle: this code NEVER reads, copies or sends OAuth tokens. Each CLI uses its
own login and its own subscription. They run in non-interactive mode, with stdin closed
(except in stdin mode for huge prompts) and without a shell (argument list).
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

from . import connectors as connectors_mod

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
# Above this size the prompt goes through stdin so it does not exceed the argv limit.
STDIN_THRESHOLD = 100_000


def _cmd_template(name: str, spec: dict, use_stdin: bool) -> List[str]:
    override = os.environ.get(f"ROUTER_CMD_{name.upper()}")
    if override:  # e.g. ROUTER_CMD_CODEX='["codex","exec","{prompt}"]'
        return list(json.loads(override))
    return list(spec["cmd_stdin"] if use_stdin and spec.get("cmd_stdin") else spec["cmd"])


def is_available(name: str, spec: dict) -> bool:
    try:
        return shutil.which(_cmd_template(name, spec, False)[0]) is not None
    except (ValueError, KeyError, IndexError):
        return False


def _with_usage_args(name: str, spec: dict, template: List[str], logfile: str) -> List[str]:
    """Inserts the flags that make the CLI return JSON with the model and tokens (spec["usage"]["args"])."""
    u = spec.get("usage") or {}
    if os.environ.get(f"ROUTER_CMD_{name.upper()}") or not u.get("args"):
        return template  # custom command: respected as is
    extra = [a.replace("{logfile}", logfile) for a in u["args"]]
    at = u.get("at", 1)
    return template[:at] + extra + template[at:]


def _with_dirs(name: str, spec: dict, template: List[str], dirs: List[str]) -> List[str]:
    """Gives read access to the attachments' folders (claude: --add-dir), which would otherwise be outside its working folder."""
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
    mcp: Optional[List[str]] = None,
    stream: Optional[Dict] = None,
) -> Dict:
    """Runs the model's CLI and returns a normalized dict.

    With `usage=True` it asks the CLI for JSON output and extracts the real model and the tokens from it
    (`res["model_id"]`, `res["tokens"]`). If the JSON cannot be interpreted, it returns the raw text."""
    use_stdin = len(prompt) > STDIN_THRESHOLD and bool(spec.get("cmd_stdin"))
    template = _cmd_template(name, spec, use_stdin)
    logfile = ""
    if usage and (spec.get("usage") or {}).get("parser") == "agy":
        fd, logfile = tempfile.mkstemp(prefix="ia-router-agy-", suffix=".log")
        os.close(fd)
    if usage:
        template = _with_usage_args(name, spec, template, logfile)
    template = _with_dirs(name, spec, template, list(extra_dirs or []))
    parser = (spec.get("usage") or {}).get("parser", "")
    use_mcp = bool(mcp) and not os.environ.get(f"ROUTER_CMD_{name.upper()}")  # a custom command is respected as is
    if use_mcp:
        template = connectors_mod.inject(parser, template, list(mcp or []))
        if parser == "agy":
            connectors_mod.write_selection(list(mcp or []))
    argv = [a.replace("{prompt}", prompt) for a in template]
    t0 = time.time()
    streaming = bool(stream) and usage and parser in STREAMERS
    if streaming:
        argv = stream_argv(parser, argv)
    try:
        if streaming:
            return _run_stream(name, spec, argv, prompt, use_stdin, timeout, cwd, t0, parser, logfile, stream or {})
        return _run(name, spec, argv, prompt, use_stdin, timeout, cwd, t0, usage, logfile)
    finally:
        if use_mcp and parser == "agy":
            connectors_mod.clear_selection()
        if logfile:
            try:
                os.unlink(logfile)
            except OSError:
                pass


# ---------- streaming ----------

STREAMERS = ("claude", "agy", "codex")


def stream_argv(parser: str, argv: List[str]) -> List[str]:
    """Switches a command that returns one JSON document to the CLI's streaming JSON (claude and agy emit text as it is written; codex already
    emits one JSON event per line, but only whole messages)."""
    argv = list(argv)
    if parser in ("claude", "agy") and "--output-format" in argv:
        i = argv.index("--output-format")
        if i + 1 < len(argv):
            argv[i + 1] = "stream-json"
        if parser == "claude":
            argv[i + 2:i + 2] = ["--verbose", "--include-partial-messages"]
    return argv


def parse_stream_line(parser: str, line: str):
    """One line of a CLI's streaming JSON -> ('text', delta) | ('status', what the model is doing) | ('final', object) | None."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    if parser == "claude":
        if d.get("type") == "result":
            return "final", d
        ev = d.get("event") if d.get("type") == "stream_event" else None
        if isinstance(ev, dict):
            if ev.get("type") == "content_block_delta" and (ev.get("delta") or {}).get("type") == "text_delta":
                return "text", ev["delta"].get("text", "")
            block = ev.get("content_block") or {}
            if ev.get("type") == "content_block_start" and block.get("type") == "tool_use":
                return "status", f"using {block.get('name', 'a tool')}"
    elif parser == "agy":
        if d.get("event") == "result":
            return "final", d.get("result") or {}
        step = d.get("step_update") if d.get("event") == "step_update" else None
        if isinstance(step, dict):
            if step.get("text_delta") and step.get("step_type") == "agent_response":
                return "text", step["text_delta"]
            if step.get("step_type") == "tool" and step.get("state") == "ACTIVE":
                return "status", f"using {step.get('tool_name', 'a tool')}"
    elif parser == "codex":
        item = d.get("item") if d.get("type") in ("item.started", "item.completed") else None
        if isinstance(item, dict):
            if d["type"] == "item.completed" and item.get("type") == "agent_message":
                return "text", item.get("text", "")
            if d["type"] == "item.started" and item.get("type") not in ("agent_message", "reasoning"):
                return "status", f"using {item.get('type', 'a tool')}"
    return None


def _run_stream(name, spec, argv, prompt, use_stdin, timeout, cwd, t0, parser, logfile, stream) -> Dict:
    """Like `_run`, but reads the CLI's output as it arrives and reports it: stream['on_text'](delta), stream['on_status'](text).
    The final answer, tokens and errors are interpreted exactly as in the non-streaming path."""
    import threading
    on_text, on_status = stream.get("on_text") or (lambda s: None), stream.get("on_status") or (lambda s: None)
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=cwd, bufsize=1)
    except FileNotFoundError:
        return _result(False, "", f"CLI not found: {argv[0]}", None, t0, False, "not_installed")
    timed_out = threading.Event()
    limit = timeout or spec.get("timeout", 300)
    watchdog = threading.Timer(limit, lambda: (timed_out.set(), proc.kill()))
    watchdog.daemon = True
    watchdog.start()
    err_parts: List[str] = []
    err_thread = threading.Thread(target=lambda: err_parts.append(proc.stderr.read()), daemon=True)
    err_thread.start()
    if use_stdin:
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass
    lines: List[str] = []
    final: Optional[Dict] = None
    for line in proc.stdout:
        lines.append(line)
        ev = parse_stream_line(parser, line.strip())
        if not ev:
            continue
        kind, value = ev
        if kind == "text" and value:
            on_text(value)
        elif kind == "status":
            on_status(value)
        elif kind == "final":
            final = value
    proc.wait()
    err_thread.join(timeout=5)
    watchdog.cancel()
    proc.stdout.close()
    proc.stderr.close()
    if timed_out.is_set():
        return _result(False, "", "timeout", None, t0, False, "timeout")
    err = "".join(err_parts).strip()
    if parser == "codex":
        out = "".join(lines).strip()
    elif final is not None:
        out = json.dumps(final)         # the same document the non-streaming mode would have printed
    else:
        out = "".join(lines).strip()
    return _finish(name, spec, argv, proc.returncode, out, err, t0, True, logfile)


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
        return _result(False, "", f"CLI not found: {argv[0]}", None, t0, False, "not_installed")
    except subprocess.TimeoutExpired:
        return _result(False, "", "timeout", None, t0, False, "timeout")
    return _finish(name, spec, argv, proc.returncode, proc.stdout.strip(), proc.stderr.strip(), t0, usage, logfile)


def _finish(name, spec, argv, returncode, out, err, t0, usage, logfile) -> Dict:
    """Interprets what a CLI printed (its answer, tokens, rate limit, missing login) the same way for every way of running it."""
    failed = returncode != 0
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
    error = None if ok else ("rate_limited" if limited else "auth_required" if auth else (err[-400:] or (out[-400:] if failed else "") or "empty output"))
    res = _result(ok, out, err[-400:], returncode, t0, limited, error, auth)
    res["model_id"] = info.get("model")
    res["tokens"] = info.get("tokens")
    return res


# ---------- usage (model and tokens) ----------

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
    """Codex does not report the model in its JSON output: it is read from the session it saved (rollout) or from its config."""
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
    """Antigravity does not either: the default model ends up in its log (`--log-file`)."""
    try:
        labels = re.findall(r'selected model override to backend: label="([^"]+)"', Path(logfile).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None
    return labels[-1] if labels else None


def parse_usage(parser: str, raw: str, argv: List[str], logfile: str = ""):
    """Returns (text, {"model", "tokens"}, is_error). text=None if the output was not the expected JSON."""
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
    """'in 15.9k (13.2k cached) · out 5' or 'tokens n/a'."""
    if not t:
        return "tokens n/a"
    def k(n: int) -> str:
        return f"{n / 1000:.1f}k" if n >= 1000 else str(n)
    cached = f" ({k(t['cached'])} cached)" if t.get("cached") else ""
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
