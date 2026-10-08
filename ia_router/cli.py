"""AI subscription router that routes by objective metrics. With no arguments it opens the chat; also: setup | usage | doctor | route | ask | stats | scores | metrics | priorities | connectors | mcp | reset-cooldowns."""
from __future__ import annotations

import argparse
import json
import os
import sys

import subprocess

from . import __version__, adapters, connectors, core, setup as setup_mod, envfile, metrics, priorities, probe, render, scoring, sessions, state, usage


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


def _read_stdin(force: bool = False) -> str:
    """What was piped in (`cat error.log | ia-router ask "what is wrong?"`), capped like any context. Empty on a terminal.
    A stdin that is open but silent (some tools leave one attached) must not hang a task that never needed it, so unless `force`
    (--stdin, or the task `-`) it is read only if data shows up within a moment."""
    import select
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return ""
        if not force and not select.select([sys.stdin], [], [], 0.5)[0]:
            return ""
        return sys.stdin.read(core.MAX_CONTEXT_CHARS)
    except (OSError, ValueError):
        return ""


def _compare(cfg, args, task, route_text) -> int:
    names = [m for m in (args.models or "").split(",") if m] or None
    if args.dry_run:
        print("(dry-run) it would run the task on: " + ", ".join(names or [r["name"] for r in core.route(route_text or task, cfg)["ranking"] if r["usable"]][:2]), file=sys.stderr)
        return 0
    if not (args.json or args.raw):
        print("Comparing: the task runs on every model below and spends quota on each.", file=sys.stderr)
    try:
        results = core.compare(task, cfg, names, context_files=args.context, route_text=route_text,
                               connectors=False if args.no_connectors else True if args.all_connectors else None)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}) if args.json else f"Error: {exc}", file=None if args.json else sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"compare": [core.result_json(r, cfg) for r in results]}, ensure_ascii=False))
    else:
        for r in results:
            j = core.result_json(r, cfg)
            cost = f" · ~${j['estimated_cost_usd']}" if j["estimated_cost_usd"] is not None else ""
            who = core.format_usage(r) if r["ok"] else (r["order"][0] if r.get("order") else "?")
            print(f"\n━━ {who} · {j['seconds']}s{cost}" + ("" if r["ok"] else f" · FAILED: {r.get('error')}"))
            if r["ok"]:
                print(r["output"] if args.raw else render.render(r["output"], color=_color()))
    return 0 if any(r["ok"] for r in results) else 1


def cmd_ask(cfg, args) -> int:
    task = (args.task or "").strip()
    piped = _read_stdin(force=args.stdin or task == "-").strip()
    task = "" if task == "-" else task
    if not task and not piped:
        print("Error: give a task, or pipe one in: echo 'explain this' | ia-router ask", file=sys.stderr)
        return 2
    route_text = None
    if task and piped:   # the task decides the routing; the piped text is only material for it
        route_text, task = task, f"{task}\n\n--- INPUT (from stdin) ---\n{piped}"
    else:
        task = task or piped
    quiet = args.json or args.raw or args.quiet
    if args.compare or args.models:
        return _compare(cfg, args, task, route_text)
    try:
        res = core.ask(task, cfg, model=args.model, context_files=args.context, dry_run=args.dry_run, route_text=route_text,
                       connectors=False if args.no_connectors else True if args.all_connectors else None)
    except ValueError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(core.result_json(res, cfg), ensure_ascii=False))
        return 0 if res["ok"] or res.get("dry_run") else 1
    if not quiet:
        print(core.format_ranking(res["decision"]), file=sys.stderr)
    if res.get("dry_run"):
        print(f"(dry-run) attempt order: {res['order']} · connectors: {', '.join(res['connectors']) or 'none'}", file=sys.stderr)
        return 0
    if not quiet:
        for a in res["attempts"]:
            print(f"attempt {a['model']}: {'OK' if not a['error'] else a['error'][:80]} ({a['seconds']}s)", file=sys.stderr)
    if res["ok"]:
        if args.raw:   # only the answer on stdout; who answered goes to stderr
            print(res["output"])
            print(f"[{core.format_usage(res)}]", file=sys.stderr)
        elif args.quiet:
            print(f"[{core.format_usage(res)}]\n{render.render(res['output'], color=_color())}")
        else:
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


def cmd_sessions(args) -> int:
    if args.action == "clear":
        print(f"Deleted {sessions.clear()} saved conversation(s).")
        return 0
    if args.action in ("show", "delete"):
        if not args.id:
            print(f"Error: `sessions {args.action}` needs an id (see `ia-router sessions`).", file=sys.stderr)
            return 2
        if args.action == "delete":
            ok = sessions.delete(args.id)
            print("Deleted." if ok else "No such saved conversation.", file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
        data = sessions.load(args.id)
        if not data:
            print("No such saved conversation.", file=sys.stderr)
            return 1
        for t in data["turns"]:
            print(f"you> {t['user']}\n[{t['model']}] {t['answer']}\n")
        return 0
    rows = sessions.list_sessions(30)
    if not rows:
        print("No saved conversations yet." + ("" if sessions.enabled() else " (Saving is off.)"))
        return 0
    for r in rows:
        print(f"{r['id']}  {r['updated'][:16].replace('T', ' ')}  {r['turns']:>3} turns  {r['title']}")
    return 0


def cmd_usage(cfg, _args) -> int:
    summary = usage.summarize(usage.read_log(), cfg=cfg)
    print("\n".join(usage.table(summary)))
    for w in usage.warnings(summary):
        print("\n⚠ " + w)
    return 0


def cmd_setup(cfg, _args) -> int:
    def ask_yes(question: str, default: bool = True) -> bool:
        try:
            ans = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        except EOFError:
            return default
        return default if not ans else ans in ("y", "yes", "s", "si")
    rows = setup_mod.run(cfg, say=print, ask_yes=ask_yes, explicit=True)
    return 0 if setup_mod.usable(rows) else 1


def cmd_priorities(cfg, _args) -> int:
    if not sys.stdin.isatty():
        print("`priorities` needs an interactive terminal (it uses an arrow-key selector).", file=sys.stderr)
        return 1
    return 0 if priorities.run(cfg, color=_color()) else 1


def _connector_line(name: str, spec: dict) -> str:
    kind = spec.get("url") or " ".join(spec.get("command") or [])
    state_ = "on " if spec.get("enabled", True) else "off"
    extra = (f"  allow={','.join(spec['allow'])}" if spec.get("allow") else "") + (f"  deny={','.join(spec['deny'])}" if spec.get("deny") else "")
    return f"{name:<14} {state_}  {kind[:70]}{extra}"


def _test_connectors(names) -> int:
    servers = {n: s for n, s in connectors.load().items() if not names or n in names}
    if not servers:
        print("No connectors to test. Add one with `ia-router connectors add NAME -- COMMAND…`.")
        return 1
    bad = 0
    for name, spec in servers.items():
        client = connectors.make_client(name, spec)
        try:
            client.start()
            tools = connectors.filter_tools(connectors.list_tools(client), spec)
            print(f"{name:<14} OK    {len(tools)} tools: " + ", ".join(t["name"] for t in tools[:8]) + (" …" if len(tools) > 8 else ""))
        except (connectors.McpError, OSError) as exc:
            bad += 1
            print(f"{name:<14} FAIL  {exc}")
        finally:
            client.close()
    return 1 if bad else 0


def cmd_connectors(args) -> int:
    act = args.action or "list"
    if act == "serve":
        return connectors.serve()
    servers = connectors.load()
    if act == "list":
        if not servers:
            print("No connectors yet. Example: ia-router connectors add gmail -- npx -y @your/gmail-mcp-server")
            return 0
        print("\n".join(_connector_line(n, s) for n, s in servers.items()))
        print(f"\nEvery model gets them through one proxy ({connectors.PROXY_NAME}). `ia-router connectors test` checks them without spending quota.")
        return 0
    if act == "add":
        command = args.command
        try:
            spec = connectors.add(args.name, command=command or None, url=args.url, env=connectors.parse_pairs(args.env, "="),
                                  headers=connectors.parse_pairs(args.header, ":"))
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        print(f"Added connector '{args.name}'. Check it with: ia-router connectors test {args.name}")
        literal = connectors.secret_literals(spec)
        if literal:
            print(f"Note: {', '.join(literal)} is stored in {connectors.registry_path()} (readable only by you). "
                  "To keep secrets out of the file use ${NAME}, e.g. --env TOKEN='${MY_TOKEN}', and put MY_TOKEN in your .env.")
        return 0
    if act in ("remove", "enable", "disable"):
        ok = connectors.remove(args.name) if act == "remove" else connectors.set_enabled(args.name, act == "enable")
        print(f"{act}: {args.name}" if ok else f"No connector named '{args.name}'.", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    if act == "test":
        return _test_connectors([args.name] if args.name else [])
    if act in ("install", "uninstall"):
        if args.cli != "agy":
            print("Only agy needs a one-time registration; claude and codex get the connectors on every call.", file=sys.stderr)
            return 2
        installing = act == "install"
        cmd = connectors.install_agy() if installing else connectors.uninstall_agy()
        print("Running: " + " ".join(cmd))
        try:
            code = subprocess.run(cmd).returncode
        except OSError as exc:
            print(f"Could not run agy: {exc.strerror}", file=sys.stderr)
            return 1
        try:
            done = connectors.agy_allow_rule(installing)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"agy settings ({connectors.agy_settings_path()}): rule {connectors.AGY_RULE} {done}. "
              + ("It lets agy use ONLY this proxy's tools in non-interactive mode; nothing else is allowed." if installing else ""))
        return code
    return 2


def main() -> int:
    envfile.load()  # .env (e.g. ARTIFICIAL_ANALYSIS_API_KEY); never overrides what is already set in the environment
    p = argparse.ArgumentParser(prog="ia-router", description=__doc__)
    p.add_argument("--version", action="version", version=f"ia-router {__version__}")
    p.add_argument("--continue", dest="cont", action="store_true", help="open the chat and resume the most recent saved conversation")
    p.add_argument("--resume", metavar="ID", help="open the chat and resume a saved conversation (see `ia-router sessions`)")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("chat", help="conversational mode (what opens with no arguments)")
    ss = sub.add_parser("sessions", help="saved conversations: list, show, delete or clear (they are stored only on this machine)")
    ss.add_argument("action", nargs="?", choices=["list", "show", "delete", "clear"], default="list")
    ss.add_argument("id", nargs="?")
    dp = sub.add_parser("doctor", help="check which CLIs are installed and which model each one uses")
    dp.add_argument("--probe", action="store_true", help="minimal real call to each CLI: login, latency, version and model")
    for name in ("route", "ask"):
        sp = sub.add_parser(name, help="route" if name == "route" else "route and run")
        sp.add_argument("task", nargs="?" if name == "ask" else None, default="", help="what to do; for `ask` it can also come from stdin")
        sp.add_argument("--context", "-c", action="append", default=[], help="context file (repeatable)")
        if name == "ask":
            sp.add_argument("--model", "-m", default="auto", help="auto|claude|codex|antigravity")
            sp.add_argument("--dry-run", action="store_true", help="only show the decision")
            sp.add_argument("--compare", action="store_true", help="run the same task on the two best models and show both answers (spends quota on each)")
            sp.add_argument("--models", help="with --compare: which models to compare, e.g. claude,codex")
            sp.add_argument("--stdin", action="store_true", help="wait for piped input even if it is slow to start (a task of - does the same)")
            sp.add_argument("--json", action="store_true", help="print one JSON object (answer, model, tokens, estimated cost, routing, attempts) and nothing else")
            sp.add_argument("--raw", action="store_true", help="print only the answer on stdout, unrendered (who answered goes to stderr): for pipes")
            sp.add_argument("--quiet", "-q", action="store_true", help="hide the routing table and the attempts")
            sp.add_argument("--no-connectors", action="store_true", help="do not give the model any MCP connector for this task")
            sp.add_argument("--all-connectors", action="store_true", help="give the model every enabled MCP connector, not only the ones the task needs")
    sub.add_parser("stats", help="success, latency, rate limits and tokens per model (from the log)")
    sp = sub.add_parser("scores", help="score per model and category (with a breakdown if you give one)")
    sp.add_argument("category", nargs="?", help="e.g. coding")
    sp = sub.add_parser("metrics", help="where the metrics come from; `refresh` updates them (Arena and, with a key, Artificial Analysis)")
    sp.add_argument("action", nargs="?", choices=["show", "refresh"], default="show")
    sp.add_argument("--force", action="store_true", help="(refresh) query even if your data is less than 12 hours old")
    sub.add_parser("usage", help="how much each model was used lately and how close it is to the rate limit you already hit")
    sub.add_parser("setup", help="check which official CLIs are installed and logged in, and what to do about the missing ones")
    sub.add_parser("priorities", help="questions: what you prioritize for each kind of task (accuracy, speed or cost)")
    cp = sub.add_parser("connectors", help="MCP connectors (Gmail, Calendar, Slack…) that every model can use")
    cs = cp.add_subparsers(dest="action")
    cs.add_parser("list", help="show the registered connectors")
    ca = cs.add_parser("add", help="register an MCP server: add NAME [--env K=V] [--header 'K: V'] (--url URL | -- COMMAND…)")
    ca.add_argument("name")
    ca.add_argument("--url", help="URL of a remote (Streamable HTTP) MCP server")
    ca.add_argument("--env", action="append", default=[], help="environment variable for a local server, KEY=VALUE (repeatable; use ${NAME} to reference your environment)")
    ca.add_argument("--header", action="append", default=[], help="HTTP header for a remote server, 'Key: value' (repeatable)")
    for verb, text in (("remove", "forget a connector"), ("enable", "turn a connector on"), ("disable", "turn a connector off without forgetting it")):
        cs.add_parser(verb, help=text).add_argument("name")
    ct = cs.add_parser("test", help="start the connectors and list their tools (spends no model quota)")
    ct.add_argument("name", nargs="?")
    for verb, text in (("install", "register the proxy once in a CLI that has no per-call option (agy)"), ("uninstall", "remove that registration")):
        cs.add_parser(verb, help=text).add_argument("cli", choices=["agy", "claude", "codex"])
    cs.add_parser("serve", help="run the proxy MCP server (stdio); the CLIs start it themselves")
    sub.add_parser("mcp", help="run the MCP server (stdio)")
    sub.add_parser("reset-cooldowns", help="clear cooldowns (rate limit and auth)")
    argv, tail = sys.argv[1:], []
    if argv[:1] == ["connectors"] and "--" in argv:  # everything after -- is the server command, options included
        argv, tail = argv[: argv.index("--")], argv[argv.index("--") + 1:]
    args = p.parse_args(argv)
    args.command = tail

    if args.cmd in (None, "chat"):
        from . import chat
        return chat.run(resume=args.resume, continue_last=args.cont)
    if args.cmd == "sessions":
        return cmd_sessions(args)
    if args.cmd == "mcp":
        from . import mcp_server
        mcp_server.main()
        return 0
    if args.cmd == "connectors":
        return cmd_connectors(args)
    if args.cmd == "reset-cooldowns":
        state.reset_cooldowns()
        print("cooldowns cleared")
        return 0
    cfg = core.load_config()
    return {"doctor": cmd_doctor, "route": cmd_route, "ask": cmd_ask, "stats": cmd_stats, "scores": cmd_scores,
            "metrics": cmd_metrics, "priorities": cmd_priorities, "setup": cmd_setup, "usage": cmd_usage}[args.cmd](cfg, args)
