"""`ia-router ui`: the same router in a browser tab, for people who would rather not live in a terminal.

A small local HTTP server (standard library only) that serves one page and a JSON API on top of the functions the CLI already uses
(core.ask / core.compare, usage, setup, sessions). It listens on 127.0.0.1 only. Because any web page can send requests to localhost, every
request must carry a random per-run token (the page gets it in the URL that `ui` opens), the Host header must be the local one, and a
POST must be JSON (a cross-origin page cannot send that without a preflight, which this server never answers).

The page is static (ui_page.py); this module is the API. Answers stream as NDJSON lines: {"type": "text"|"status"|"reset"|"done"|"error", ...}.
"""
from __future__ import annotations

import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from . import __version__, chat, connectors as connectors_mod, core, router, sessions, setup as setup_mod, state, usage as usage_mod
from .ui_page import PAGE

MAX_BODY = 1_000_000
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
        results = core.compare(task, cfg, models=models, connectors=conn)
        return {"compare": [core.result_json(r, cfg) for r in results]}

    session = sessions.load(body["session"]) if body.get("session") else None
    session = session or sessions.new_session()
    history = sessions.to_history(session)
    follow_up = history and set(router.detect(task)) <= {"quick"}   # same rule as the chat: a short follow-up inherits the previous topic
    res = core.ask(task, cfg, model=str(body.get("model") or "auto"), preamble=chat.build_preamble(history), connectors=conn,
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
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'")
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
            return self._send(200, PAGE.replace("__TOKEN__", self.server.token).encode(), "text/html")
        if url.path == "/api/state":
            return self._json(snapshot(core.load_config()))
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
            if length > MAX_BODY:
                return self._deny(413, "too large")
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._deny(400, "bad JSON")
        if url.path == "/api/ask":
            return self._ask(body)
        if url.path == "/api/sessions/delete":
            try:
                ok = sessions.delete(str(body.get("id") or ""))
            except ValueError:
                ok = False
            return self._json({"deleted": ok})
        self._deny(404, "not found")

    def _ask(self, body: Dict) -> None:
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
            emit(dict(run_ask(body, core.load_config(), emit), type="done"))
        except ValueError as exc:
            emit({"type": "error", "text": str(exc)})
        except Exception as exc:   # noqa: BLE001 - the page must always get an answer, whatever went wrong
            emit({"type": "error", "text": f"{type(exc).__name__}: {exc}"})


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
