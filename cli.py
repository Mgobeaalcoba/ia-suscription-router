#!/usr/bin/env python3
"""CLI del router: doctor | route | ask | mcp | reset-cooldowns."""
from __future__ import annotations

import argparse
import sys

from llm_router import adapters, core, state


def cmd_doctor(cfg, _args) -> int:
    print(f"Config: {len(cfg['models'])} modelos | estado en: {state.home()}\n")
    for name, spec in cfg["models"].items():
        installed = adapters.is_available(name, spec)
        cd = state.cooldown_remaining(name)
        print(f"{name:<8} {'instalado' if installed else 'NO instalado':<13} cooldown={round(cd)}s  cmd={' '.join(spec['cmd'][:3])} ...")
        if not installed:
            print(f"         → instalá el CLI oficial o ajustá 'cmd' en models.json (o ROUTER_CMD_{name.upper()})")
    print("\nVerificá flags reales con: claude --help | codex exec --help | gemini --help")
    return 0


def cmd_route(cfg, args) -> int:
    _, ctx_len, warns = core.build_prompt(args.task, args.context)
    print(core.format_ranking(core.route(args.task, cfg, ctx_len, use_llm=args.llm)))
    for w in warns:
        print("aviso:", w, file=sys.stderr)
    return 0


def cmd_ask(cfg, args) -> int:
    res = core.ask(args.task, cfg, model=args.model, context_files=args.context, dry_run=args.dry_run, use_llm=args.llm)
    print(core.format_ranking(res["decision"]), file=sys.stderr)
    if res.get("dry_run"):
        print(f"(dry-run) orden de intento: {res['order']}", file=sys.stderr)
        return 0
    for a in res["attempts"]:
        print(f"intento {a['model']}: {'OK' if not a['error'] else a['error'][:80]} ({a['seconds']}s)", file=sys.stderr)
    if res["ok"]:
        print(f"\n[{res['model_used']}]\n{res['output']}")
        return 0
    print(f"Falló: {res.get('error')}", file=sys.stderr)
    return 1


def main() -> int:
    p = argparse.ArgumentParser(prog="router", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="verifica qué CLIs están instalados")
    for name in ("route", "ask"):
        sp = sub.add_parser(name, help="rutear" if name == "route" else "rutear y ejecutar")
        sp.add_argument("task")
        sp.add_argument("--context", "-c", action="append", default=[], help="archivo de contexto (repetible)")
        sp.add_argument("--llm", action="store_true", help="usar clasificador LLM barato en vez de solo reglas")
        if name == "ask":
            sp.add_argument("--model", "-m", default="auto", help="auto|claude|codex|gemini")
            sp.add_argument("--dry-run", action="store_true", help="solo mostrar la decisión")
    sub.add_parser("mcp", help="correr el servidor MCP (stdio)")
    sub.add_parser("reset-cooldowns", help="limpiar cooldowns por rate limit")
    args = p.parse_args()

    if args.cmd == "mcp":
        from llm_router import mcp_server
        mcp_server.main()
        return 0
    if args.cmd == "reset-cooldowns":
        state.reset_cooldowns()
        print("cooldowns limpiados")
        return 0
    cfg = core.load_config()
    return {"doctor": cmd_doctor, "route": cmd_route, "ask": cmd_ask}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
