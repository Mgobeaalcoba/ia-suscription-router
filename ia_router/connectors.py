"""Connectors: let any model reach other apps (Gmail, Calendar, Slack, GitHub…) through MCP servers.

The router keeps ONE registry of MCP servers (`~/.ia-router/connectors.json`) and runs ONE proxy MCP server
(`ia-router connectors serve`) that aggregates all of them. Every official CLI is pointed at that single proxy, so
permissions, logging and credentials live in one place and it works the same with claude, codex and agy.

The router never handles OAuth tokens: each MCP server does its own login. Secrets in the registry should be
references (`${NAME}`) that are expanded from the environment or `.env` when the server starts.

Design pattern kept from the rest of the code: the registry, the naming rules and the argument builders are pure;
only `StdioClient`/`HttpClient` and `serve` touch processes, the network or the terminal.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__, state

PROXY_NAME = "ia-router-connectors"
PROTOCOL = "2025-06-18"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")   # no underscores: "__" separates server and tool in exposed names
START_TIMEOUT = 60.0                                  # npx-style servers can take a while to download and boot
CALL_TIMEOUT = 120.0
SEP = "__"


# ---------- registry ----------

def registry_path():
    return state.home() / "connectors.json"


def load() -> Dict[str, Dict]:
    """{name: server spec}. A missing or damaged file is an empty registry."""
    try:
        d = json.loads(registry_path().read_text(encoding="utf-8"))
        servers = d.get("servers") if isinstance(d, dict) else None
        return {k: v for k, v in servers.items() if isinstance(v, dict)} if isinstance(servers, dict) else {}
    except (OSError, ValueError):
        return {}


def save(servers: Dict[str, Dict]) -> None:
    """Writes the registry readable only by its owner: it may hold API keys."""
    state.home().mkdir(parents=True, exist_ok=True)
    tmp = registry_path().with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"servers": servers}, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, registry_path())


def parse_pairs(items: List[str], sep: str) -> Dict[str, str]:
    """['A=1', 'B=x=y'] -> {'A': '1', 'B': 'x=y'} (sep '=') or ['K: v'] -> {'K': 'v'} (sep ':')."""
    out: Dict[str, str] = {}
    for item in items:
        key, found, value = item.partition(sep)
        if not found or not key.strip():
            raise ValueError(f"expected KEY{sep}VALUE, got: {item}")
        out[key.strip()] = value.strip()
    return out


def add(name: str, command: Optional[List[str]] = None, url: Optional[str] = None, env: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None) -> Dict:
    """Registers (or replaces) a server: a local command (stdio) or an http(s) URL. Returns the stored spec."""
    if not NAME_RE.match(name):
        raise ValueError("the name must be lowercase letters, digits and hyphens (max 31 characters), e.g. gmail or my-crm")
    if bool(command) == bool(url):
        raise ValueError("give either a command to run or --url, not both")
    if url and not re.match(r"^https?://", url):
        raise ValueError("--url must start with http:// or https://")
    spec: Dict[str, Any] = {"enabled": True}
    if command:
        spec["command"] = list(command)
        if env:
            spec["env"] = dict(env)
    else:
        spec["url"] = url
        if headers:
            spec["headers"] = dict(headers)
    servers = load()
    servers[name] = spec
    save(servers)
    return spec


def remove(name: str) -> bool:
    servers = load()
    if name not in servers:
        return False
    del servers[name]
    save(servers)
    return True


def set_enabled(name: str, enabled: bool) -> bool:
    servers = load()
    if name not in servers:
        return False
    servers[name]["enabled"] = enabled
    save(servers)
    return True


def enabled_servers() -> Dict[str, Dict]:
    return {n: s for n, s in load().items() if s.get("enabled", True)}


def has_connectors() -> bool:
    return bool(enabled_servers())


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand(value: str, environ: Optional[Dict[str, str]] = None) -> str:
    """Replaces ${NAME} with the environment variable (an unset one becomes empty, never the literal text)."""
    env = os.environ if environ is None else environ
    return _VAR.sub(lambda m: env.get(m.group(1), ""), value)


def secret_literals(spec: Dict) -> List[str]:
    """Names of env vars / headers whose value is typed in the file instead of referenced as ${NAME}."""
    pairs = list((spec.get("env") or {}).items()) + list((spec.get("headers") or {}).items())
    return [k for k, v in pairs if v and not _VAR.search(str(v))]


# ---------- names exposed to the models ----------

def exposed_name(server: str, tool: str, taken: Optional[set] = None) -> str:
    """'gmail' + 'search messages' -> 'gmail__search_messages'. Valid for every CLI ([A-Za-z0-9_-], max 64) and unique."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", tool)
    base = (server + SEP + safe)[:64]
    taken = taken if taken is not None else set()
    name, n = base, 2
    while name in taken:
        suffix = f"_{n}"
        name, n = base[: 64 - len(suffix)] + suffix, n + 1
    taken.add(name)
    return name


def filter_tools(tools: List[Dict], spec: Dict) -> List[Dict]:
    """Applies the optional per-server `allow` (only these) and `deny` (never these) lists of tool names."""
    allow, deny = spec.get("allow"), set(spec.get("deny") or [])
    return [t for t in tools if t.get("name") and t["name"] not in deny and (not allow or t["name"] in allow)]


# ---------- MCP clients (downstream servers) ----------

class McpError(RuntimeError):
    pass


class StdioClient:
    """Talks JSON-RPC (one message per line) to a local MCP server process."""

    def __init__(self, name: str, command: List[str], env: Optional[Dict[str, str]] = None) -> None:
        self.name, self.command, self.extra_env = name, command, env or {}
        self.proc: Optional[subprocess.Popen] = None
        self.lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self.next_id = 1

    def start(self, timeout: float = START_TIMEOUT) -> None:
        env = dict(os.environ)
        env.update({k: expand(str(v)) for k, v in self.extra_env.items()})
        try:
            self.proc = subprocess.Popen([expand(c) for c in self.command], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         text=True, encoding="utf-8", env=env, bufsize=1)
        except OSError as exc:
            raise McpError(f"could not start {self.command[0]}: {exc.strerror or exc}") from exc
        threading.Thread(target=self._pump, daemon=True).start()
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "ia-router", "version": __version__}}, timeout)
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, msg: Dict) -> None:
        assert self.proc and self.proc.stdin
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError(f"{self.name} closed its connection") from exc

    def request(self, method: str, params: Optional[Dict] = None, timeout: float = CALL_TIMEOUT) -> Dict:
        rid, self.next_id = self.next_id, self.next_id + 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.01, deadline - time.time()))
            except queue.Empty:
                raise McpError(f"{self.name} did not answer {method} in {timeout:.0f}s") from None
            if line is None:
                raise McpError(f"{self.name} exited unexpectedly")
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # servers sometimes print banners on stdout
            if msg.get("id") == rid and ("result" in msg or "error" in msg):
                if "error" in msg:
                    raise McpError(str((msg["error"] or {}).get("message", msg["error"])))
                return msg.get("result") or {}
            if msg.get("method") == "ping" and "id" in msg:
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for pipe in (getattr(self.proc, "stdin", None), getattr(self.proc, "stdout", None)):
            try:
                if pipe:
                    pipe.close()
            except OSError:
                pass


class HttpClient:
    """Talks to a remote MCP server over Streamable HTTP (JSON or event-stream replies, static headers)."""

    def __init__(self, name: str, url: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.name, self.url, self.headers = name, url, {k: expand(str(v)) for k, v in (headers or {}).items()}
        self.session: Optional[str] = None
        self.next_id = 1

    def start(self, timeout: float = START_TIMEOUT) -> None:
        self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "ia-router", "version": __version__}}, timeout)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, timeout)

    def _post(self, body: Dict, timeout: float) -> Tuple[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                self.session = r.headers.get("Mcp-Session-Id") or self.session
                return r.headers.get("Content-Type", ""), r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            exc.close()
            raise McpError(f"{self.name}: HTTP {exc.code}") from None
        except (urllib.error.URLError, OSError) as exc:
            raise McpError(f"{self.name}: {getattr(exc, 'reason', exc)}") from None

    def request(self, method: str, params: Optional[Dict] = None, timeout: float = CALL_TIMEOUT) -> Dict:
        rid, self.next_id = self.next_id, self.next_id + 1
        ctype, text = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}, timeout)
        candidates = [text]
        if "event-stream" in ctype:
            candidates = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        for raw in candidates:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("id") == rid:
                if "error" in msg:
                    raise McpError(str((msg["error"] or {}).get("message", msg["error"])))
                return msg.get("result") or {}
        raise McpError(f"{self.name}: no answer to {method}")

    def close(self) -> None:
        pass


def make_client(name: str, spec: Dict):
    if spec.get("url"):
        return HttpClient(name, spec["url"], spec.get("headers"))
    return StdioClient(name, spec.get("command") or [], spec.get("env"))


def list_tools(client) -> List[Dict]:
    tools: List[Dict] = []
    cursor = None
    for _ in range(50):  # a server that never ends its pages must not hang the proxy
        res = client.request("tools/list", {"cursor": cursor} if cursor else {}, 30)
        tools += [t for t in res.get("tools", []) if isinstance(t, dict)]
        cursor = res.get("nextCursor")
        if not cursor:
            break
    return tools


# ---------- the proxy server ----------

class Proxy:
    """One MCP server that exposes the tools of every enabled connector, named `<server>__<tool>`."""

    def __init__(self, servers: Optional[Dict[str, Dict]] = None, factory: Callable = make_client, log: Optional[Callable[[Dict], None]] = None) -> None:
        self.servers = servers if servers is not None else enabled_servers()
        self.factory, self.log = factory, log or (lambda event: None)
        self.clients: Dict[str, Any] = {}
        self.routes: Dict[str, Tuple[str, str]] = {}
        self.tools: List[Dict] = []
        self.errors: Dict[str, str] = {}
        self.ready = False

    def _boot(self, name: str, spec: Dict, out: Dict[str, List[Dict]]) -> None:
        client = self.factory(name, spec)
        try:
            client.start()
            out[name] = filter_tools(list_tools(client), spec)
            self.clients[name] = client
        except (McpError, OSError) as exc:
            self.errors[name] = str(exc)
            client.close()

    def load_tools(self) -> None:
        """Starts every server in parallel (the first run of an npx server downloads it) and collects the tools. A failing server is skipped."""
        if self.ready:
            return
        found: Dict[str, List[Dict]] = {}
        threads = [threading.Thread(target=self._boot, args=(n, s, found), daemon=True) for n, s in self.servers.items()]
        for t in threads:
            t.start()
        for t in threads:
            t.join(START_TIMEOUT + 30)
        taken: set = set()
        for name in self.servers:  # registry order keeps the exposed names stable between runs
            for tool in found.get(name, []):
                exposed = exposed_name(name, tool["name"], taken)
                self.routes[exposed] = (name, tool["name"])
                self.tools.append(dict(tool, name=exposed, description=f"[{name}] {tool.get('description', '')}".strip()))
        self.ready = True

    def call(self, exposed: str, args: Dict) -> Dict:
        if exposed not in self.routes:
            return {"content": [{"type": "text", "text": f"Unknown tool: {exposed}"}], "isError": True}
        server, tool = self.routes[exposed]
        t0, ok = time.time(), False
        try:
            res = self.clients[server].request("tools/call", {"name": tool, "arguments": args or {}}, CALL_TIMEOUT)
            ok = not res.get("isError")
            return res
        except McpError as exc:
            return {"content": [{"type": "text", "text": f"{server} failed: {exc}"}], "isError": True}
        finally:
            self.log({"server": server, "tool": tool, "ok": ok, "seconds": round(time.time() - t0, 2)})  # never the arguments or the result

    def handle(self, msg: Dict) -> Optional[Dict]:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None
        if method == "initialize":
            result: Dict = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {}},
                            "serverInfo": {"name": PROXY_NAME, "version": __version__}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            self.load_tools()
            result = {"tools": self.tools}
        elif method == "tools/call":
            self.load_tools()
            p = msg.get("params") or {}
            result = self.call(p.get("name", ""), p.get("arguments") or {})
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unsupported method: {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def close(self) -> None:
        for c in self.clients.values():
            c.close()


def log_call(event: Dict) -> None:
    try:
        state.home().mkdir(parents=True, exist_ok=True)
        with open(state.home() / "connectors.log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(event, ts=time.strftime("%Y-%m-%dT%H:%M:%S")), ensure_ascii=False) + "\n")
    except OSError:
        pass


def serve() -> int:
    """stdio entry point: `ia-router connectors serve`. stdout is the MCP channel, so messages go to stderr."""
    from . import envfile
    envfile.load()  # so ${NAME} references resolve from .env too
    proxy = Proxy(log=log_call)
    print(f"{PROXY_NAME} ready (stdio): {', '.join(proxy.servers) or 'no connectors'}", file=sys.stderr)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            resp = proxy.handle(msg)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    finally:
        for name, err in proxy.errors.items():
            print(f"connector {name} was skipped: {err}", file=sys.stderr)
        proxy.close()
    return 0


# ---------- pointing the official CLIs at the proxy ----------

def proxy_command() -> List[str]:
    return [sys.executable, "-m", "ia_router", "connectors", "serve"]


def _toml(value: Any) -> str:
    """A string, list of strings or {KEY: string} table as TOML (a JSON string or list is valid TOML; a JSON object is not)."""
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k} = {json.dumps(v)}" for k, v in value.items()) + "}"
    return json.dumps(value)


def cli_args(parser: str) -> Tuple[List[str], int]:
    """(arguments to insert, position in the command) that make a CLI load the proxy for ONE call, without touching its config files.
    claude gets only this server allowed (never a blanket 'allow everything'); codex auto-approves only this server's tools.
    agy has no per-call option: it uses the one-time registration done by `connectors install agy`."""
    cmd = proxy_command()
    env = {"ROUTER_HOME": str(state.home())}  # the CLIs start MCP servers with a trimmed environment: say where the registry lives
    if parser == "claude":
        cfg = {"mcpServers": {PROXY_NAME: {"command": cmd[0], "args": cmd[1:], "env": env}}}
        return ["--mcp-config", json.dumps(cfg), "--allowedTools", f"mcp__{PROXY_NAME}"], 1
    if parser == "codex":
        key = f"mcp_servers.{PROXY_NAME}"
        return ["-c", f"{key}.command={_toml(cmd[0])}", "-c", f"{key}.args={_toml(cmd[1:])}", "-c", f"{key}.env={_toml(env)}",
                "-c", f'{key}.default_tools_approval_mode="approve"', "-c", f"{key}.startup_timeout_sec={int(START_TIMEOUT)}",
                "-c", f"{key}.tool_timeout_sec={int(CALL_TIMEOUT)}"], 2
    return [], 1


def inject(parser: str, template: List[str]) -> List[str]:
    extra, at = cli_args(parser)
    return template[:at] + extra + template[at:]


def install_agy() -> List[str]:
    """The command that registers the proxy once in Antigravity."""
    env = ["--env", f"ROUTER_HOME={os.environ['ROUTER_HOME']}"] if os.environ.get("ROUTER_HOME") else []  # only when the state folder is not the default
    return ["agy", "mcp", "add"] + env + [PROXY_NAME, "--"] + proxy_command()


def uninstall_agy() -> List[str]:
    return ["agy", "mcp", "remove", PROXY_NAME]


AGY_RULE = f"mcp({PROXY_NAME}/*)"   # agy's headless mode auto-denies MCP tools unless an allow rule names the server


def agy_settings_path() -> Path:
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def agy_allow_rule(add: bool) -> str:
    """Adds or removes the allow rule for the proxy (and only for the proxy) in agy's settings.json, keeping everything else in it.
    Returns what happened: 'added', 'removed', 'unchanged'. Raises ValueError if the file is not valid JSON (it is never overwritten)."""
    path = agy_settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON; fix it or add the rule {AGY_RULE} under permissions.allow by hand") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    perms = data.setdefault("permissions", {}) if add else data.get("permissions", {})
    allow = list(perms.get("allow") or []) if isinstance(perms, dict) else []
    if add == (AGY_RULE in allow):
        return "unchanged"
    allow = allow + [AGY_RULE] if add else [r for r in allow if r != AGY_RULE]
    if add:
        perms["allow"] = allow
    elif allow:
        perms["allow"] = allow
    else:
        perms.pop("allow", None)
        if not perms:
            data.pop("permissions", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return "added" if add else "removed"
