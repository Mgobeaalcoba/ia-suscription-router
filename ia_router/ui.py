"""`ia-router ui`: the same router in a browser tab, for people who would rather not live in a terminal.

A small local HTTP server (standard library only) that serves one page and a JSON API on top of the functions the CLI already uses
(core.ask / core.compare, usage, setup, sessions). It listens on 127.0.0.1 only. Because any web page can send requests to localhost, every
request must carry a random per-run token (the page gets it in the URL that `ui` opens), the Host header must be the local one, and a
POST must be JSON (a cross-origin page cannot send that without a preflight, which this server never answers).

The page is static (ui_page.py); this module is the API. Answers stream as NDJSON lines: {"type": "text"|"status"|"reset"|"done"|"error", ...}.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
import shlex
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__, attachments as att_mod, chat, connectors as connectors_mod, core, metrics, priorities as priorities_mod, probe as probe_mod, router, scoring, sessions, setup as setup_mod, state, usage as usage_mod
from .ui_page import render as render_page

MAX_BODY = 1_000_000
MAX_UPLOAD = 25_000_000          # bytes of a file attached from the browser (it travels as base64, so the request is a third bigger)
UPLOAD_KEEP_DAYS = 7
CONNECTOR_MODES = {"auto": None, "on": True, "off": False}


# ---------- pure-ish helpers (tested without a socket) ----------

def snapshot(cfg: Dict) -> Dict:
    """Everything the page shows on load: which CLIs are ready, usage, connectors and whether sessions are saved."""
    rows = setup_mod.statuses(cfg)
    seen = state.seen_ids()
    summary = usage_mod.summarize(usage_mod.read_log(), cfg=cfg)
    return {
        "version": __version__,
        "models": [dict(r, model_id=seen.get(r["name"])) for r in rows],
        "advice": setup_mod.advice(rows),
        "ready": setup_mod.usable(rows),
        "usage": summary,
        "usage_warnings": usage_mod.warnings(summary),
        "connectors": [{"name": n, "enabled": bool(s.get("enabled", True)), "remote": bool(s.get("url"))} for n, s in connectors_mod.load().items()],
        "sessions_enabled": sessions.enabled(),
        "cooldowns": {r["name"]: round(state.cooldown_remaining(r["name"])) for r in rows},
        "stats": state.stats(),
        "metrics_line": metrics.status_line(),
    }


def run_ask(body: Dict, cfg: Dict, emit) -> Dict:
    """Runs one task (or a comparison) and returns the payload of the final 'done' event. `emit(event_dict)` receives the streaming events."""
    task = str(body.get("task") or "").strip()
    if not task:
        raise ValueError("write a task first")
    mode = body.get("connectors", "auto")
    if mode not in CONNECTOR_MODES:
        raise ValueError("connectors must be auto, on or off")
    conn = CONNECTOR_MODES[mode]
    if body.get("compare"):
        models = [m for m in (body.get("models") or []) if isinstance(m, str)] or None
        results = core.compare(task, cfg, models=models, connectors=conn, attachments=attachments_for(task, body.get("files")))
        return {"compare": [core.result_json(r, cfg) for r in results]}

    atts = attachments_for(task, body.get("files"))
    session = sessions.load(body["session"]) if body.get("session") else None
    session = session or sessions.new_session()
    history = sessions.to_history(session)
    follow_up = history and set(router.detect(task)) <= {"quick"}   # same rule as the chat: a short follow-up inherits the previous topic
    res = core.ask(task, cfg, model=str(body.get("model") or "auto"), attachments=atts, preamble=chat.build_preamble(history), connectors=conn,
                   route_text=(history[-1][0] + "\n" + task) if follow_up else None,
                   stream={"on_text": lambda d: emit({"type": "text", "text": d}), "on_status": lambda s: emit({"type": "status", "text": s}),
                           "on_reset": lambda: emit({"type": "reset"})})
    out = core.result_json(res, cfg)
    if res["ok"]:
        sessions.add_turn(session, task, res["model_used"], res["output"])
        sessions.save(session)
        out["session"] = session["id"] if sessions.enabled() else None
        out["usage_warnings"] = usage_mod.warnings(usage_mod.summarize(usage_mod.read_log()), [res["model_used"]])
    return out


# ---------- files, routing preview, scores, metrics, priorities, logins (the rest of what the CLI can do) ----------

def uploads_dir():
    return state.home() / "uploads"


def save_upload(name: str, data_b64: str) -> Dict:
    """A file chosen in the browser, stored (0600) in ~/.ia-router/uploads so the models can open it by path, like a file dragged into the terminal."""
    try:
        raw = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("the file could not be read")
    if len(raw) > MAX_UPLOAD:
        raise ValueError(f"the file is too large (limit {att_mod.human_size(MAX_UPLOAD)})")
    clean = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(str(name or "file")))[:80].lstrip(".") or "file"
    uploads_dir().mkdir(parents=True, exist_ok=True)
    path = uploads_dir() / f"{uuid.uuid4().hex[:8]}-{clean}"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(raw)
    return {"path": str(path), "label": att_mod.Attachment(att_mod.Path(clean), att_mod.classify(path), len(raw)).label()}   # shown by its own name, not the stored one


def prune_uploads(now=None) -> int:
    """Uploads are scratch copies: the ones older than a week go when the server starts."""
    n, cutoff = 0, (now or time.time()) - UPLOAD_KEEP_DAYS * 86400
    try:
        for p in uploads_dir().iterdir():
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
    except OSError:
        pass
    return n


def attachments_for(task: str, files) -> list:
    """Paths written in the task (like the chat) plus files uploaded from the page. Only files inside the uploads folder are accepted from the page."""
    found = att_mod.find(task)
    root = uploads_dir().resolve()
    for f in files or []:
        p = os.path.realpath(str(f))
        if not (p.startswith(str(root) + os.sep) and os.path.isfile(p)):
            raise ValueError("an attached file is not one the page uploaded")
        found.append(att_mod.Attachment(att_mod.Path(p), att_mod.classify(att_mod.Path(p)), os.path.getsize(p)))
    return found


def route_preview(body: Dict, cfg: Dict) -> Dict:
    """`ia-router route`: which model would take this task and why. Spends nothing."""
    task = str(body.get("task") or "").strip()
    if not task:
        raise ValueError("write a task first")
    atts = attachments_for(task, body.get("files"))
    inline, referenced = att_mod.split_for_prompt(atts)
    boost = {"multimodal": 2.0} if any(a.kind in ("image", "pdf") for a in referenced) else None
    dec = core.route(task, cfg, 0, boost=boost, needs_files=bool(referenced))
    return {"text": core.format_ranking(dec), "weights": dec["weights"], "chosen": dec["chosen"],
            "ranking": [{"model": r["name"], "score": r["score"], "usable": r["usable"]} for r in dec["ranking"]],
            "connectors": connectors_mod.select(task, connectors_mod.enabled_servers())}


def scores_view(category: str, cfg: Dict) -> Dict:
    if category and category not in scoring.CATEGORIES:
        raise ValueError(f"unknown category '{category}' (options: {', '.join(scoring.CATEGORIES)})")
    return {"categories": scoring.CATEGORIES, "table": scoring.render_table(cfg, [category] if category else None),
            "explain": scoring.explain(cfg, category) if category else ""}


def probe_logins(cfg: Dict) -> Dict:
    """`doctor --probe` / the login check of setup: one minimal query per installed CLI (spends a pinch of quota; the page asks first)."""
    installed = [r["name"] for r in setup_mod.statuses(cfg) if r["installed"]]
    checked = probe_mod.probe(cfg, only=installed)
    for name, row in checked.items():
        state.set_auth_missing(name, row.get("auth") == "missing")
    return {"checked": {n: {k: r.get(k) for k in ("auth", "version", "model_id", "probe_seconds", "probe_error")} for n, r in checked.items()}}


def refresh_metrics(body: Dict, emit) -> Dict:
    """`metrics refresh`: downloads Arena (and Artificial Analysis with a key). Uses the network, never model quota. Progress lines go to `emit`."""
    ok = scoring.refresh_and_report(core.load_config(apply_scoring=False), say=lambda t: emit({"type": "line", "text": str(t)}), force=body.get("force") is True)
    return {"ok": bool(ok), "sources": scoring.describe_sources(core.load_config())}


def change_sessions(action: str, body: Dict) -> Dict:
    if action == "clear":
        return {"deleted": sessions.clear()}
    if action == "saving":
        state.set_flag("sessions_off", body.get("on") is not True)
        return {"enabled": sessions.enabled()}
    raise ValueError("unknown action")


# ---------- connectors (the page's manager; the same registry as `ia-router connectors`) ----------

def connector_view(name: str, spec: Dict) -> Dict:
    """One registered connector for the page. Secret values (env, headers) are never sent: only the names of the variables."""
    return {"name": name, "enabled": bool(spec.get("enabled", True)), "kind": "remote" if spec.get("url") else "local",
            "target": spec.get("url") or " ".join(shlex.quote(a) for a in spec.get("command") or []),
            "env": sorted((spec.get("env") or {}).keys()), "headers": sorted((spec.get("headers") or {}).keys()),
            "allow": spec.get("allow") or [], "deny": spec.get("deny") or [], "literal_secrets": connectors_mod.secret_literals(spec)}


def connectors_state() -> Dict:
    templates = []
    for key, t in sorted(connectors_mod.all_templates().items()):
        templates.append({"key": key, "description": t.get("description", ""), "kind": "remote" if t.get("url") else "local", "needs_args": t.get("needs_args"),
                          "needs_env": t.get("needs_env") or [], "missing_env": connectors_mod.missing_env(key), "requires": t.get("requires"),
                          "verified": t.get("verified", True), "source": t.get("source")})
    return {"connectors": [connector_view(n, s) for n, s in connectors_mod.load().items()], "templates": templates,
            "registry": str(connectors_mod.registry_path())}


def _pairs(text, sep: str) -> Dict[str, str]:
    """'A=1' lines (or a list of them) from the form -> dict; blank lines are ignored."""
    items = text if isinstance(text, list) else str(text or "").splitlines()
    return connectors_mod.parse_pairs([i.strip() for i in items if str(i).strip()], sep)


def add_connector(body: Dict) -> Dict:
    """Registers a connector from the form: from a template, a free local command (needs `confirm: true`: the page shows the exact command first), or a URL.
    The command is split like a shell would but never run through one, so `;`, `|` or `$(...)` are plain characters, not operators."""
    name = str(body.get("name") or "").strip()
    template = str(body.get("template") or "").strip()
    env, headers = _pairs(body.get("env"), "="), _pairs(body.get("headers"), ":")
    command = url = None
    if template:
        extra = shlex.split(str(body.get("args") or ""))
        built = connectors_mod.from_template(template, extra or None)
        command, url = built.get("command"), built.get("url")
        env, headers = {**(built.get("env") or {}), **env}, {**(built.get("headers") or {}), **headers}
    elif str(body.get("url") or "").strip():
        url = str(body["url"]).strip()
    else:
        try:
            command = shlex.split(str(body.get("command") or ""))
        except ValueError as exc:
            raise ValueError(f"cannot read the command: {exc}")
        if not command:
            raise ValueError("write the command that starts the MCP server (for example: npx -y @modelcontextprotocol/server-memory) or a URL")
        if body.get("confirm") is not True:
            raise ValueError("a free command runs on this machine: confirm it first")
    spec = connectors_mod.add(name, command=command or None, url=url, env=env, headers=headers)
    warnings = [f"{', '.join(connectors_mod.secret_literals(spec))} is stored in {connectors_mod.registry_path()} (readable only by you). To keep secrets out of the file "
                "write ${NAME} and put NAME in your .env."] if connectors_mod.secret_literals(spec) else []
    warnings += [f"{v} is not set in your environment: put it in your .env before using this connector." for v in (connectors_mod.missing_env(template) if template else [])]
    return {"added": name, "warnings": warnings}


def test_connector(name: str) -> Dict:
    """Starts one registered connector and lists its tools. Spends no model quota. It does run the connector's command."""
    spec = connectors_mod.load().get(name)
    if spec is None:
        raise ValueError(f"no connector named '{name}'")
    client = connectors_mod.make_client(name, spec)
    try:
        client.start()
        tools = connectors_mod.filter_tools(connectors_mod.list_tools(client), spec)
        return {"ok": True, "tools": [t["name"] for t in tools]}
    except (connectors_mod.McpError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        client.close()


def agy_connector(body: Dict) -> Dict:
    code, lines = connectors_mod.agy_register(body.get("install") is True)
    return {"ok": code == 0, "lines": lines}


def change_connector(action: str, name: str) -> Dict:
    ok = connectors_mod.remove(name) if action == "remove" else connectors_mod.set_enabled(name, action == "enable")
    if not ok:
        raise ValueError(f"no connector named '{name}'")
    return {"ok": True}


# ---------- server ----------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"    # one request per connection: a streamed answer simply ends when the socket closes
    server_version = "ia-router-ui"

    def log_message(self, *_args) -> None:   # quiet: the terminal belongs to the user
        pass

    # --- guards ---
    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        port = self.server.server_address[1]
        if host not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin.lower() in (f"http://127.0.0.1:{port}", f"http://localhost:{port}")

    def _authorized(self, query: Dict) -> bool:
        given = self.headers.get("X-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(given, self.server.token)

    def _deny(self, code: int, message: str) -> None:
        self._send(code, json.dumps({"error": message}).encode(), "application/json")

    def _send(self, code: int, data: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    # --- routes ---
    def do_GET(self) -> None:
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if not self._local():
            return self._deny(403, "not a local request")
        if not self._authorized(query):
            return self._deny(401, "missing or wrong token (open the URL that `ia-router ui` printed)")
        if url.path == "/":
            return self._send(200, render_page(self.server.token).encode(), "text/html")
        if url.path == "/api/state":
            return self._json(snapshot(core.load_config()))
        try:
            if url.path == "/api/scores":
                return self._json(scores_view((query.get("category") or [""])[0], core.load_config()))
            if url.path == "/api/metrics":
                return self._json({"sources": scoring.describe_sources(core.load_config())})
            if url.path == "/api/priorities":
                info = priorities_mod.current(core.load_config())
                return self._json(dict(info, preview=priorities_mod.preview(scoring.build(core.load_config(), profile={"priorities": info["answers"]})["table"])))
        except ValueError as exc:
            return self._deny(400, str(exc))
        if url.path == "/api/connectors":
            return self._json(connectors_state())
        if url.path == "/api/sessions":
            return self._json({"enabled": sessions.enabled(), "sessions": sessions.list_sessions(50)})
        if url.path.startswith("/api/sessions/"):
            try:
                data = sessions.load(url.path.rsplit("/", 1)[1])
            except ValueError:
                data = None
            return self._json(data) if data else self._deny(404, "no such conversation")
        self._deny(404, "not found")

    def do_POST(self) -> None:
        url = urlparse(self.path)
        if not self._local():
            return self._deny(403, "not a local request")
        if not self._authorized({}):
            return self._deny(401, "missing or wrong token")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._deny(415, "send JSON")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > (MAX_UPLOAD * 4 // 3 + 4096 if url.path == "/api/upload" else MAX_BODY):
                return self._deny(413, "too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._deny(400, "bad JSON")
        if url.path == "/api/ask":
            return self._ask(body)
        if url.path in ("/api/metrics/refresh",):
            return self._stream(lambda emit: refresh_metrics(body, emit))
        try:
            cfg = None
            if url.path == "/api/upload":
                return self._json(save_upload(body.get("name"), body.get("data")))
            if url.path == "/api/route":
                return self._json(route_preview(body, core.load_config()))
            if url.path == "/api/probe":
                return self._json(probe_logins(core.load_config()))
            if url.path == "/api/reset-cooldowns":
                state.reset_cooldowns()
                return self._json({"ok": True})
            if url.path == "/api/priorities":
                answers = body.get("answers")
                if not isinstance(answers, dict):
                    raise ValueError("answers must be an object like {\"coding\": \"speed\"}")
                return self._json({"preview": priorities_mod.apply(core.load_config(), {str(k): str(v) for k, v in answers.items()}, save=body.get("save") is True),
                                   "saved": body.get("save") is True})
            if url.path.startswith("/api/sessions/") and url.path.rsplit("/", 1)[1] in ("clear", "saving"):
                return self._json(change_sessions(url.path.rsplit("/", 1)[1], body))
            if url.path == "/api/connectors/agy":
                return self._json(agy_connector(body))
        except ValueError as exc:
            return self._deny(400, str(exc))
        if url.path == "/api/sessions/delete":
            try:
                ok = sessions.delete(str(body.get("id") or ""))
            except ValueError:
                ok = False
            return self._json({"deleted": ok})
        if url.path.startswith("/api/connectors/"):
            action = url.path.rsplit("/", 1)[1]
            try:
                if action == "add":
                    return self._json(add_connector(body))
                if action == "test":
                    return self._json(test_connector(str(body.get("name") or "")))
                if action in ("enable", "disable", "remove"):
                    return self._json(change_connector(action, str(body.get("name") or "")))
            except ValueError as exc:
                return self._deny(400, str(exc))
        self._deny(404, "not found")

    def _stream(self, work) -> None:
        """Runs `work(emit)` and streams its events as NDJSON, ending with {"type": "done", ...} or {"type": "error"}."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        alive = [True]

        def emit(event: Dict) -> None:
            if not alive[0]:
                return
            try:
                self.wfile.write(json.dumps(event, ensure_ascii=False).encode() + b"\n")
                self.wfile.flush()
            except OSError:        # the tab was closed: let the task finish quietly
                alive[0] = False

        try:
            emit(dict(work(emit), type="done"))
        except ValueError as exc:
            emit({"type": "error", "text": str(exc)})
        except Exception as exc:   # noqa: BLE001 - the page must always get an answer, whatever went wrong
            emit({"type": "error", "text": f"{type(exc).__name__}: {exc}"})

    def _ask(self, body: Dict) -> None:
        self._stream(lambda emit: run_ask(body, core.load_config(), emit))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), Handler)
        self.token = secrets.token_urlsafe(24)

    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"


def serve(port: int = 0, open_browser: bool = True, say=print) -> int:
    try:
        server = Server(port)
    except OSError as exc:
        say(f"Error: could not listen on 127.0.0.1:{port} ({exc.strerror or exc}).")
        return 1
    prune_uploads()
    say(f"ia-router UI: {server.url()}\n(local only; press Ctrl+C to stop)")
    sys.stdout.flush()       # when piped, the URL must still appear before the server blocks
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(server.url())).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
