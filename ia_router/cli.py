"""Router de suscripciones de IA con ruteo por métricas objetivas. Sin argumentos abre el chat; también: doctor | route | ask | stats | scores | metrics | priorities | mcp | reset-cooldowns."""
from __future__ import annotations

import argparse
import os
import sys

from . import adapters, core, envfile, metrics, priorities, probe, render, scoring, state


def _color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def cmd_doctor(cfg, args) -> int:
    print(f"Config: {len(cfg['models'])} modelos | estado en: {state.home()} | métricas: {metrics.status_line()}\n")
    probed = probe.probe(cfg) if args.probe else {}
    ids = metrics.known_model_ids(list(cfg["models"]))
    for name, spec in cfg["models"].items():
        installed = adapters.is_available(name, spec)
        cd = state.cooldown_remaining(name)
        extra = ""
        if name in probed:
            p = probed[name]
            extra = f"  auth={p['auth']} {p.get('probe_seconds', '-')}s {p.get('version', '')}"
        flags = "" if spec.get("enabled", True) else " [desactivado en models.json]"
        mid = (probed.get(name) or {}).get("model_id") or ids.get(name)
        print(f"{name:<12} {'instalado' if installed else 'NO instalado':<13} cooldown={round(cd)}s{extra}{flags}  modelo: {mid or 'desconocido'}")
        if not installed:
            print(f"             → instalá el CLI oficial o ajustá 'cmd' en models.json (o ROUTER_CMD_{name.upper()})")
        elif name in probed and probed[name]["auth"] == "missing":
            print(f"             → instalado pero SIN autenticar: iniciá sesión o configurá la API key de {name}")
        elif name in probed and probed[name]["auth"] == "unknown":
            print(f"             → la sonda falló: {probed[name].get('probe_error')}")
    if not args.probe:
        print("\nPara verificar login, latencia y qué modelo usa cada CLI (gasta 1 llamada corta por modelo): cli.py doctor --probe")
    return 0


def cmd_route(cfg, args) -> int:
    _, ctx_len, warns = core.build_prompt(args.task, args.context)
    print(core.format_ranking(core.route(args.task, cfg, ctx_len)))
    for w in warns:
        print("aviso:", w, file=sys.stderr)
    return 0


def cmd_ask(cfg, args) -> int:
    try:
        res = core.ask(args.task, cfg, model=args.model, context_files=args.context, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(core.format_ranking(res["decision"]), file=sys.stderr)
    if res.get("dry_run"):
        print(f"(dry-run) orden de intento: {res['order']}", file=sys.stderr)
        return 0
    for a in res["attempts"]:
        print(f"intento {a['model']}: {'OK' if not a['error'] else a['error'][:80]} ({a['seconds']}s)", file=sys.stderr)
    if res["ok"]:
        print(f"\n[{core.format_usage(res)}]\n{render.render(res['output'], color=_color())}")
        return 0
    print(f"Falló: {res.get('error')}", file=sys.stderr)
    return 1


def cmd_stats(cfg, _args) -> int:
    st = state.stats()
    if not st:
        print("Sin historial todavía (se arma con cada `ask`).")
        return 0
    print(f"{'modelo':<12} {'corridas':>8} {'éxito':>6} {'seg. medio':>10} {'rate limits':>11} {'auth':>5} {'tokens in':>10} {'tokens out':>10}")
    for n, m in st.items():
        print(f"{n:<12} {m['runs']:>8} {m['ok_rate']:>6.0%} {m['avg_seconds']:>10} {m['rate_limits']:>11} {m['auth_errors']:>5} {m['tokens_in']:>10} {m['tokens_out']:>10}")
    return 0


def cmd_scores(cfg, args) -> int:
    print(scoring.render_table(cfg, [args.category] if args.category else None))
    if args.category:
        print("\n" + scoring.explain(cfg, args.category))
    return 0


def cmd_metrics(cfg, args) -> int:
    if args.action == "refresh":
        if not scoring.refresh_and_report(core.load_config(apply_scoring=False), say=lambda s: print(s, flush=True), force=args.force):
            return 1
        cfg = core.load_config()
        print()
    print(scoring.describe_sources(cfg))
    return 0


def cmd_priorities(cfg, _args) -> int:
    if not sys.stdin.isatty():
        print("`priorities` necesita una terminal interactiva (usa un selector con las flechas).", file=sys.stderr)
        return 1
    return 0 if priorities.run(cfg, color=_color()) else 1


def main() -> int:
    envfile.load()  # .env (p. ej. ARTIFICIAL_ANALYSIS_API_KEY); nunca pisa lo ya definido en el entorno
    p = argparse.ArgumentParser(prog="ia-router", description=__doc__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("chat", help="modo conversacional (es lo que se abre sin argumentos)")
    dp = sub.add_parser("doctor", help="verifica qué CLIs están instalados y qué modelo usa cada uno")
    dp.add_argument("--probe", action="store_true", help="llamada mínima real a cada CLI: login, latencia, versión y modelo")
    for name in ("route", "ask"):
        sp = sub.add_parser(name, help="rutear" if name == "route" else "rutear y ejecutar")
        sp.add_argument("task")
        sp.add_argument("--context", "-c", action="append", default=[], help="archivo de contexto (repetible)")
        if name == "ask":
            sp.add_argument("--model", "-m", default="auto", help="auto|claude|codex|antigravity")
            sp.add_argument("--dry-run", action="store_true", help="solo mostrar la decisión")
    sub.add_parser("stats", help="éxito, latencia, rate limits y tokens por modelo (del log)")
    sp = sub.add_parser("scores", help="puntaje por modelo y categoría (con desglose si indicás una)")
    sp.add_argument("category", nargs="?", help="ej: coding")
    sp = sub.add_parser("metrics", help="de dónde salen las métricas; `refresh` las actualiza (Arena y, con clave, Artificial Analysis)")
    sp.add_argument("action", nargs="?", choices=["show", "refresh"], default="show")
    sp.add_argument("--force", action="store_true", help="(refresh) consultar aunque tus datos tengan menos de 12 horas")
    sub.add_parser("priorities", help="preguntas: qué priorizás en cada tipo de tarea (precisión, velocidad o costo)")
    sub.add_parser("mcp", help="correr el servidor MCP (stdio)")
    sub.add_parser("reset-cooldowns", help="limpiar cooldowns (rate limit y auth)")
    args = p.parse_args()

    if args.cmd in (None, "chat"):
        from . import chat
        return chat.run()
    if args.cmd == "mcp":
        from . import mcp_server
        mcp_server.main()
        return 0
    if args.cmd == "reset-cooldowns":
        state.reset_cooldowns()
        print("cooldowns limpiados")
        return 0
    cfg = core.load_config()
    return {"doctor": cmd_doctor, "route": cmd_route, "ask": cmd_ask, "stats": cmd_stats, "scores": cmd_scores,
            "metrics": cmd_metrics, "priorities": cmd_priorities}[args.cmd](cfg, args)
