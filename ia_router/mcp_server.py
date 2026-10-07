"""MCP server (stdio) with no external dependencies. Exposes the router as tools.

Tools: route_task, ask_model, list_models.
Transport: JSON-RPC 2.0, one message per line. Logs go to stderr (stdout is the MCP channel).
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from . import __version__, adapters, core, state

PROTOCOL_DEFAULT = "2025-06-18"

TOOLS = [
    {
        "name": "route_task",
        "description": "Decides which model (claude/codex/antigravity) suits a task WITHOUT running it. Returns a ranking with reasons.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Description of the task"},
                "context_files": {"type": "array", "items": {"type": "string"}, "description": "Paths of context files (optional)"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "ask_model",
        "description": (
            "Runs a task on another model through its official CLI. model='auto' routes on its own and falls back on rate limit. "
            "Use it to delegate subtasks (e.g. long-context analysis to antigravity, debugging to codex)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "model": {"type": "string", "description": "auto or the name of a model from list_models", "default": "auto"},
                "context_files": {"type": "array", "items": {"type": "string"}},
                "timeout_seconds": {"type": "number", "description": "Maximum time per attempt"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "list_models",
        "description": "Lists configured models, whether their CLI is installed and whether they are in rate-limit cooldown.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _text(text: str, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call_tool(name: str, args: Dict[str, Any], cfg: Dict) -> Dict[str, Any]:
    if name == "list_models":
        rows = []
        for n, spec in cfg["models"].items():
            cd = state.cooldown_remaining(n)
            rows.append(f"{n}: installed={adapters.is_available(n, spec)} cooldown_s={round(cd)} ({spec.get('label', '')})")
        return _text("\n".join(rows))
    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        return _text("Missing the 'task' parameter.", True)
    files = args.get("context_files") or []
    if name == "route_task":
        _, ctx_len, warns = core.build_prompt(task, files)
        text = core.format_ranking(core.route(task, cfg, ctx_len))
        return _text(text + ("\nWarnings: " + "; ".join(warns) if warns else ""))
    if name == "ask_model":
        try:
            res = core.ask(task, cfg, model=args.get("model", "auto"), context_files=files, timeout=args.get("timeout_seconds"))
        except ValueError as exc:
            return _text(str(exc), True)
        trace = " → ".join(f"{a['model']}({'ok' if not a['error'] else a['error'][:40]})" for a in res["attempts"]) or "no attempts"
        if res["ok"]:
            return _text(f"[{res['model_used']}] (path: {trace})\n\n{res['output']}")
        return _text(f"Failed: {res.get('error')} (path: {trace})", True)
    return _text(f"Unknown tool: {name}", True)


def handle(msg: Dict[str, Any], cfg: Dict) -> Optional[Dict[str, Any]]:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification (e.g. notifications/initialized): no response
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_DEFAULT
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ia-suscription-router", "version": __version__},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        try:
            result = call_tool(params.get("name", ""), params.get("arguments") or {}, cfg)
        except Exception as exc:  # do not take the server down over a tool error
            result = _text(f"Internal error: {type(exc).__name__}: {exc}", True)
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unsupported method: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    cfg = core.load_config()
    print("ia-suscription-router MCP ready (stdio)", file=sys.stderr)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        resp = handle(msg, cfg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
