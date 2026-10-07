"""AI subscription router that routes by objective metrics. With no arguments it opens the chat; also: doctor | route | ask | stats | scores | metrics | priorities | mcp | reset-cooldowns."""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__, adapters, core, envfile, metrics, priorities, probe, render, scoring, state


def _color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def cmd_doctor(cfg, args) -> int:
    print(f"Config: {len(cfg['models'])} models | state in: {state.home()} | metrics: {metrics.status_line()}\n")
    probed = probe.probe(cfg) if args.probe else {}
    ids = metrics.known_model_ids(list(cfg["models"]))
    for name, spec in cfg["models"].items():
        installed = adapters.is_available(name, spec)
        cd = state.cooldown_remaining(name)
        extra = ""
        if name in probed:
            p = probed[name]
            extra = f"  auth={p['auth']} {p.get('probe_seconds', '-')}s {p.get('version', '')}"
        flags = "" if spec.get("enabled", True) else " [disabled in models.json]"
        mid = (probed.get(name) or {}).get("model_id") or ids.get(name)
        print(f"{name:<12} {'installed' if installed else 'NOT installed':<13} cooldown={round(cd)}s{extra}{flags}  model: {mid or 'unknown'}")
        if not installed:
            print(f"             → install the official CLI or adjust 'cmd' in models.json (or ROUTER_CMD_{name.upper()})")
        elif name in probed and probed[name]["auth"] == "missing":
            print(f"             → installed but NOT authenticated: log in or set the API key for {name}")
        elif name in probed and probed[name]["auth"] == "unknown":
            print(f"             → the probe failed: {probed[name].get('probe_error')}")
    if not args.probe:
        print("\nTo check login, latency and which model each CLI uses (spends 1 short call per model): cli.py doctor --probe")
    return 0


def cmd_route(cfg, args) -> int:
    _, ctx_len, warns = core.build_prompt(args.task, args.context)
    print(core.format_ranking(core.route(args.task, cfg, ctx_len)))
    for w in warns:
        print("warning:", w, file=sys.stderr)
    return 0


def cmd_ask(cfg, args) -> int:
    try:
        res = core.ask(args.task, cfg, model=args.model, context_files=args.context, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(core.format_ranking(res["decision"]), file=sys.stderr)
    if res.get("dry_run"):
        print(f"(dry-run) attempt order: {res['order']}", file=sys.stderr)
        return 0
    for a in res["attempts"]:
        print(f"attempt {a['model']}: {'OK' if not a['error'] else a['error'][:80]} ({a['seconds']}s)", file=sys.stderr)
    if res["ok"]:
        print(f"\n[{core.format_usage(res)}]\n{render.render(res['output'], color=_color())}")
        return 0
    print(f"Failed: {res.get('error')}", file=sys.stderr)
    return 1


def cmd_stats(cfg, _args) -> int:
    st = state.stats()
    if not st:
        print("No history yet (it builds up with every `ask`).")
        return 0
    print(f"{'model':<12} {'runs':>8} {'success':>7} {'avg secs':>10} {'rate limits':>11} {'auth':>5} {'tokens in':>10} {'tokens out':>10}")
    for n, m in st.items():
        print(f"{n:<12} {m['runs']:>8} {m['ok_rate']:>7.0%} {m['avg_seconds']:>10} {m['rate_limits']:>11} {m['auth_errors']:>5} {m['tokens_in']:>10} {m['tokens_out']:>10}")
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
        print("`priorities` needs an interactive terminal (it uses an arrow-key selector).", file=sys.stderr)
        return 1
    return 0 if priorities.run(cfg, color=_color()) else 1


def main() -> int:
    envfile.load()  # .env (e.g. ARTIFICIAL_ANALYSIS_API_KEY); never overrides what is already set in the environment
    p = argparse.ArgumentParser(prog="ia-router", description=__doc__)
    p.add_argument("--version", action="version", version=f"ia-router {__version__}")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("chat", help="conversational mode (what opens with no arguments)")
    dp = sub.add_parser("doctor", help="check which CLIs are installed and which model each one uses")
    dp.add_argument("--probe", action="store_true", help="minimal real call to each CLI: login, latency, version and model")
    for name in ("route", "ask"):
        sp = sub.add_parser(name, help="route" if name == "route" else "route and run")
        sp.add_argument("task")
        sp.add_argument("--context", "-c", action="append", default=[], help="context file (repeatable)")
        if name == "ask":
            sp.add_argument("--model", "-m", default="auto", help="auto|claude|codex|antigravity")
            sp.add_argument("--dry-run", action="store_true", help="only show the decision")
    sub.add_parser("stats", help="success, latency, rate limits and tokens per model (from the log)")
    sp = sub.add_parser("scores", help="score per model and category (with a breakdown if you give one)")
    sp.add_argument("category", nargs="?", help="e.g. coding")
    sp = sub.add_parser("metrics", help="where the metrics come from; `refresh` updates them (Arena and, with a key, Artificial Analysis)")
    sp.add_argument("action", nargs="?", choices=["show", "refresh"], default="show")
    sp.add_argument("--force", action="store_true", help="(refresh) query even if your data is less than 12 hours old")
    sub.add_parser("priorities", help="questions: what you prioritize for each kind of task (accuracy, speed or cost)")
    sub.add_parser("mcp", help="run the MCP server (stdio)")
    sub.add_parser("reset-cooldowns", help="clear cooldowns (rate limit and auth)")
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
        print("cooldowns cleared")
        return 0
    cfg = core.load_config()
    return {"doctor": cmd_doctor, "route": cmd_route, "ask": cmd_ask, "stats": cmd_stats, "scores": cmd_scores,
            "metrics": cmd_metrics, "priorities": cmd_priorities}[args.cmd](cfg, args)
