"""Servidor MCP (stdio) sin dependencias externas. Expone el router como herramientas.

Herramientas: route_task, ask_model, list_models.
Transporte: JSON-RPC 2.0, un mensaje por línea. Los logs van a stderr (stdout es el canal MCP).
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
        "description": "Decide qué modelo (claude/codex/gemini) conviene para una tarea SIN ejecutarla. Devuelve ranking con motivos.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Descripción de la tarea"},
                "context_files": {"type": "array", "items": {"type": "string"}, "description": "Rutas de archivos de contexto (opcional)"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "ask_model",
        "description": (
            "Ejecuta una tarea en otro modelo vía su CLI oficial. model='auto' rutea solo y hace fallback si hay rate limit. "
            "Usalo para delegar subtareas (p. ej. análisis de contexto largo a gemini, depuración a codex)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "model": {"type": "string", "enum": ["auto", "claude", "codex", "gemini"], "default": "auto"},
                "context_files": {"type": "array", "items": {"type": "string"}},
                "timeout_seconds": {"type": "number", "description": "Tiempo máximo por intento"},
            },
            "required": ["task"],
        },
    },
    {
        "name": "list_models",
        "description": "Lista modelos configurados, si su CLI está instalado y si están en cooldown por rate limit.",
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
            rows.append(f"{n}: instalado={adapters.is_available(n, spec)} cooldown_s={round(cd)} ({spec.get('label', '')})")
        return _text("\n".join(rows))
    task = args.get("task")
    if not isinstance(task, str) or not task.strip():
        return _text("Falta el parámetro 'task'.", True)
    files = args.get("context_files") or []
    if name == "route_task":
        _, ctx_len, warns = core.build_prompt(task, files)
        text = core.format_ranking(core.route(task, cfg, ctx_len))
        return _text(text + ("\nAvisos: " + "; ".join(warns) if warns else ""))
    if name == "ask_model":
        try:
            res = core.ask(task, cfg, model=args.get("model", "auto"), context_files=files, timeout=args.get("timeout_seconds"))
        except ValueError as exc:
            return _text(str(exc), True)
        trace = " → ".join(f"{a['model']}({'ok' if not a['error'] else a['error'][:40]})" for a in res["attempts"]) or "sin intentos"
        if res["ok"]:
            return _text(f"[{res['model_used']}] (ruta: {trace})\n\n{res['output']}")
        return _text(f"Falló: {res.get('error')} (ruta: {trace})", True)
    return _text(f"Herramienta desconocida: {name}", True)


def handle(msg: Dict[str, Any], cfg: Dict) -> Optional[Dict[str, Any]]:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notificación (p. ej. notifications/initialized): sin respuesta
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL_DEFAULT
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "llm-router-poc", "version": __version__},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        try:
            result = call_tool(params.get("name", ""), params.get("arguments") or {}, cfg)
        except Exception as exc:  # no tumbar el servidor por un error de una herramienta
            result = _text(f"Error interno: {type(exc).__name__}: {exc}", True)
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"método no soportado: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    cfg = core.load_config()
    print("llm-router-poc MCP listo (stdio)", file=sys.stderr)
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
