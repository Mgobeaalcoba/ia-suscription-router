"""Conversational mode: you open `ia-router` and talk. Each message is either a task (routed to the best model according to the
metrics and run) or a query about the state. On startup the chat offers to update the metrics (showing every step) and, only once,
to ask you what you prioritize for each kind of task.

The CLIs have no memory between calls, so the chat prepends a summary of the last turns to each task. Intent detection is
rule-based (fast, spends no quota). Commands starting with "/" always work as shortcuts.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__, adapters, attachments, banner, connectors as connectors_mod, core, sessions, setup as setup_mod, stream as stream_mod, usage as usage_mod, editor as editor_mod, metrics, priorities, probe as probe_mod, render, router, scoring, state

HISTORY_TURNS = 6
HISTORY_ANSWER_CHARS = 1500
HISTORY_TOTAL_CHARS = 8000

HELP = """Just talk normally: each message is a task and is routed to the best model according to the metrics. To view or pin things:

  /models [probe]    status of the CLIs and which model each one uses (probe = real minimal query)
  /scores [cat]      score per model and category; with a category, the breakdown
  /metrics [refresh] where the data comes from; refresh = update it (force = even if recent)
  /usage             how much each model was used lately and how close it is to a rate limit
  /setup             which CLIs are installed and logged in, and what to do about the missing ones
  /priorities        questions: what you prioritize for each kind of task (accuracy, speed, cost)
  /model X           pin a model (X = claude|codex|... or auto)         /stats   success, latency and tokens
  /explain on|off    show the routing table on every message            /md on|off   rendered or raw markdown
  /connectors [auto|on|off]  MCP connectors (Gmail, Calendar…): auto = only the ones a task needs, on = all, off = none
  /compare [a,b] task  run the same task on two models and show both (spends quota on each)
  /stream on|off     show answers as they are written (a live raw view, then the rendered one)
  /sessions [off|on]  saved conversations; /resume [id] continues one (they are stored locally, readable only by you)
  /ask text          force "this is a task"                             /clear   forget the conversation
  /help              this help                                          /exit    quit"""

COMMANDS = [
    editor_mod.Command("/help", "show the shortcuts"), editor_mod.Command("/models", "status of the CLIs and which model each one uses"),
    editor_mod.Command("/scores", "score per model and category"), editor_mod.Command("/metrics", "where the data comes from; refresh = update it"),
    editor_mod.Command("/usage", "usage per model and distance to the rate limit"), editor_mod.Command("/setup", "check your CLIs and get the steps for the missing ones"), editor_mod.Command("/priorities", "what you prioritize for each kind of task (questions)"), editor_mod.Command("/model", "pin a model or auto"),
    editor_mod.Command("/stats", "success, latency and tokens per model"), editor_mod.Command("/explain", "show the routing table (on|off)"),
    editor_mod.Command("/md", "rendered or raw markdown (on|off)"), editor_mod.Command("/compare", "run a task on two models and compare (spends quota on each)"), editor_mod.Command("/stream", "show answers as they are written (on|off)"), editor_mod.Command("/sessions", "list saved conversations (off|on)"), editor_mod.Command("/resume", "continue a saved conversation"), editor_mod.Command("/ask", "force: this is a task"),
    editor_mod.Command("/connectors", "MCP connectors the models can use (auto|on|off)"), editor_mod.Command("/clear", "forget the conversation"), editor_mod.Command("/exit", "quit"),
]

# ---------- message interpretation ----------

_SHOW = [
    (r"estad[ií]sticas?|\bstats\b|^(?:show|see|list)(?: me)?(?: the| my)? (?:stats|statistics)\b", "stats"),
    (r"qu[eé] modelos|cu[aá]les modelos|modelos disponibles|qu[eé] clis|\b(?:which|what) (?:models|clis)\b|\bavailable models\b", "models"),
    (r"\bpuntajes?\b|c[oó]mo (rutea|decide|elige)|\branking\b|^(?:show|see|list)(?: me)?(?: the)? (?:scores|ranking)\b|\bhow (?:do|does) (?:you|it|the router) (?:route|decide|choose)\b", "scores"),
]


def detect_intent(text: str) -> str:
    """Returns 'stats'|'models'|'scores' (a query) or 'task'. A long message, one with line breaks or with code is always a task."""
    t = text.strip()
    low = t.lower()
    if len(t) > 400 or "\n" in t or "```" in t:
        return "task"
    for pat, kind in _SHOW:
        if re.search(pat, low):
            return kind
    return "task"


def build_preamble(history: List[Tuple[str, str, str]]) -> str:
    """Summary of the last turns (user, model, answer) to prepend to the next task."""
    if not history:
        return ""
    lines: List[str] = []
    for user, model, answer in history[-HISTORY_TURNS:]:
        a = answer if len(answer) <= HISTORY_ANSWER_CHARS else answer[:HISTORY_ANSWER_CHARS] + " […]"
        lines.append(f"User: {user}\nAssistant ({model}): {a}")
    body = "\n\n".join(lines)
    body = body[-HISTORY_TOTAL_CHARS:]
    return f"[Previous conversation, for context only]\n{body}\n[End of context]\n\nUser's current message:\n"


# ---------- session ----------

class Chat:
    def __init__(self, read: Callable[[str], str] = input, write: Callable[[str], None] = print, color: bool = False,
                 editor: Optional[editor_mod.LineEditor] = None):
        self.read, self.write, self.color, self.editor = read, write, color, editor
        self.history: List[Tuple[str, str, str]] = []
        self.pinned = "auto"
        self.explain = False
        self.markdown = True
        self.streaming = True
        self.session = sessions.new_session(attachments.safe_cwd())
        self.connectors: Optional[bool] = None   # None = automatic (only the connectors a task needs), True = all, False = none

    # --- utilities ---
    def dim(self, s: str) -> str:
        return f"\033[2m{s}\033[0m" if self.color else s

    def say(self, s: str = "") -> None:
        self.write(s)

    def ask_yes(self, question: str, default: bool = True) -> bool:
        try:
            ans = self.read(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
        except EOFError:
            return default
        return default if not ans else ans in ("s", "si", "sí", "y", "yes")

    # --- main entry ---
    def handle(self, text: str) -> bool:
        """Processes a message. Returns False if it is time to quit."""
        text = text.strip()
        if not text:
            return True
        first = attachments.tokens(text)[0][2]
        if text.startswith("/") and not attachments.resolve(first):  # "/Users/x/photo.png" is a file, not a command
            return self.command(text)
        if text.lower() in ("exit", "salir", "quit", "bye", "chau"):
            return False
        intent = detect_intent(text)
        if intent in ("stats", "models", "scores"):
            return self.command("/" + intent)
        self.run_task(text)
        return True

    # --- task ---
    def run_task(self, text: str, files: Optional[List[str]] = None) -> None:
        cfg = core.load_config()
        atts = attachments.find(text)
        if atts and self.editor is None:  # with the editor, attachments were already shown on send
            for a in atts:
                self.say(self.dim(f"⎘ {a.label()}"))
        self.say(self.dim("… routing"))
        live = stream_mod.LiveOutput(dim=self.dim) if self.streaming and self.color and sys.stdout.isatty() else None
        try:
            res = core.ask(text, cfg, model=self.pinned, context_files=files, attachments=atts,
                           preamble=build_preamble(self.history), route_text=self.route_text(text), connectors=self.connectors,
                           stream={"on_text": live.on_text, "on_status": live.on_status, "on_reset": live.reset} if live else None)
        except ValueError as exc:
            self.say(f"Error: {exc}")
            return
        dec = res["decision"]
        if self.explain:
            self.say(core.format_ranking(dec))
        for w in res.get("warnings", []):
            self.say(f"warning: {w}")
        if not res["ok"]:
            trace = "; ".join(f"{a['model']}: {(a['error'] or 'ok')[:60]}" for a in res["attempts"]) or "no attempts"
            self.say(f"Could not resolve it: {res.get('error')} ({trace}). Try /models or /models probe.")
            for a in res["attempts"]:
                if a.get("error") == "auth_required":
                    self.say(f"{a['model']} is not logged in. {setup_mod.guide(a['model'], 'login')} Then run /setup to check.")
            if not res["attempts"]:
                self.say("No CLI is ready. Run /setup to see which ones are missing and how to fix them.")
            return
        used = res["model_used"]
        why = ", ".join(f"{c}×{w:g}" for c, w in dec["weights"].items()) or "general"
        secs = sum(a["seconds"] for a in res["attempts"])
        fb = f" · fallback after {', '.join(a['model'] for a in res['attempts'][:-1])}" if len(res["attempts"]) > 1 else ""
        if live and live.text and not live.erase():
            self.say("")                                    # taller than the screen: the raw text stays, the header goes after it
            self.say(self.dim(f"── {core.format_usage(res)} · {why} · {secs:.1f}s{fb}"))
        else:
            self.say(self.dim(f"── {core.format_usage(res)} · {why} · {secs:.1f}s{fb}"))
            self.say(render.render(res["output"], color=self.color and self.markdown) + "\n")
        self.history.append((text, used, res["output"]))
        sessions.add_turn(self.session, text, used, res["output"])
        self.session["pinned"] = self.pinned
        sessions.save(self.session)
        for w in usage_mod.warnings(usage_mod.summarize(usage_mod.read_log()), [used]):
            self.say(self.dim("⚠ " + w))

    def route_text(self, text: str) -> Optional[str]:
        """A short follow-up with no topic of its own ("now make it recursive") inherits the previous message's."""
        if self.history and set(router.detect(text)) <= {"quick"}:
            return self.history[-1][0] + "\n" + text
        return None

    # --- commands ---
    def command(self, line: str) -> bool:
        cmd, _, arg = line[1:].partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        cfg = core.load_config()
        if cmd in ("exit", "quit", "quit"):
            return False
        if cmd == "help":
            self.say(HELP)
        elif cmd == "models":
            self.models(cfg, probe=(arg == "probe"))
        elif cmd == "stats":
            st = state.stats()
            self.say("\n".join([f"{n:<12} runs={m['runs']} success={m['ok_rate']:.0%} avg={m['avg_seconds']}s rate_limits={m['rate_limits']} tokens in/out={m['tokens_in']}/{m['tokens_out']}" for n, m in st.items()]) or "No history yet.")
        elif cmd == "model":
            if arg in ("auto", *cfg["models"]):
                self.pinned = arg
                self.say(f"Model: {'automatic (routes by metrics)' if arg == 'auto' else 'pinned to ' + arg}.")
            else:
                self.say(f"Usage: /model auto|{'|'.join(cfg['models'])}")
        elif cmd == "explain":
            self.explain = arg != "off"
            self.say(f"Routing table: {'visible' if self.explain else 'hidden'}.")
        elif cmd == "md":
            self.markdown = arg != "off"
            self.say(f"Markdown: {'rendered (like a README on GitHub, without markup characters)' if self.markdown else 'raw text'}.")
        elif cmd == "scores":
            self.say(scoring.render_table(cfg, [arg] if arg else None) + (("\n\n" + scoring.explain(cfg, arg)) if arg else ""))
        elif cmd == "metrics":
            if arg in ("refresh", "force"):
                scoring.refresh_and_report(core.load_config(apply_scoring=False), self.say, force=arg == "force")
                cfg = core.load_config()
            self.say(("\n" if arg else "") + scoring.describe_sources(cfg))
        elif cmd == "priorities":
            if priorities.run(cfg, say=self.say, color=self.color):
                self.say("\n" + scoring.render_table(core.load_config()))
        elif cmd == "stream":
            if arg in ("on", "off"):
                self.streaming = arg == "on"
            self.say(f"Streaming: {'on (live raw text, then the rendered answer)' if self.streaming else 'off'}.")
        elif cmd == "usage":
            summary = usage_mod.summarize(usage_mod.read_log(), cfg=cfg)
            self.say("\n".join(usage_mod.table(summary) + [""] + ["⚠ " + w for w in usage_mod.warnings(summary)]).rstrip())
        elif cmd == "setup":
            setup_mod.run(cfg, say=self.say, ask_yes=self.ask_yes, explicit=True)
        elif cmd == "connectors":
            if arg in ("auto", "on", "off"):
                self.connectors = None if arg == "auto" else arg == "on"
            servers = connectors_mod.load()
            mode = "auto: each task gets only the connectors it needs" if self.connectors is None else "on: every enabled connector on every task" if self.connectors else "off"
            if not servers:
                self.say("No connectors yet. Add an MCP server from your shell, e.g.: ia-router connectors add NAME -- COMMAND…")
            else:
                self.say("\n".join(f"{n:<14} {'on ' if s.get('enabled', True) else 'off'}  {s.get('url') or ' '.join(s.get('command') or [])[:60]}" for n, s in servers.items()))
                self.say(f"Mode: {mode} (/connectors auto|on|off). Manage them with `ia-router connectors`.")
        elif cmd == "clear":
            self.history.clear()
            self.session = sessions.new_session(attachments.safe_cwd())
            self.say("Conversation forgotten here; a new one starts. (The saved one is still listed in /sessions.)" if sessions.enabled() else "Conversation forgotten.")
        elif cmd == "sessions":
            self.sessions_command(arg)
        elif cmd == "resume":
            self.resume(arg)
        elif cmd == "compare":
            self.compare(cfg, arg)
        elif cmd == "ask":
            self.run_task(arg) if arg else self.say("Usage: /ask <task>")
        else:
            self.say(f"Unknown command /{cmd}. Type /help.")
        return True

    def sessions_command(self, arg: str) -> None:
        if arg in ("off", "on"):
            state.set_flag("sessions_off", arg == "off")
            self.say("Saving conversations is " + ("OFF: nothing new is stored (delete old ones with `ia-router sessions clear`)." if arg == "off" else "ON."))
            return
        rows = sessions.list_sessions(10)
        if not rows:
            self.say("No saved conversations yet." + ("" if sessions.enabled() else " (Saving is off: /sessions on)"))
            return
        for r in rows:
            self.say(f"{r['id']}  {r['updated'][:16].replace('T', ' ')}  {r['turns']:>3} turns  {r['title']}")
        self.say("/resume <id> continues one; `ia-router sessions delete <id>` removes it. They are stored only on this machine.")

    def resume(self, arg: str) -> None:
        sid = arg.strip()
        data = sessions.load(sid) if sid else sessions.latest()
        if not data:
            self.say("No such saved conversation. See /sessions." if sid else "No saved conversations yet.")
            return
        self.session, self.history = data, sessions.to_history(data)
        if data.get("pinned") in ("auto", *core.load_config()["models"]):
            self.pinned = data["pinned"]
        last = self.history[-1] if self.history else None
        self.say(f"Resumed {data['id']} ({len(self.history)} turns)." + (f" Last you asked: {last[0][:70]}" if last else ""))

    def compare(self, cfg: Dict, arg: str) -> None:
        names = None
        head, _, rest = arg.partition(" ")
        if head and "," in head and all(p in cfg["models"] for p in head.split(",")):
            names, arg = head.split(","), rest.strip()
        if not arg:
            self.say("Usage: /compare [claude,codex] <task>")
            return
        shown = names or [r["name"] for r in core.route(arg, cfg)["ranking"] if r["usable"]][:2]
        if len(shown) < 2:
            self.say("Comparing needs at least two models ready. Run /setup.")
            return
        if not self.ask_yes(f"Run this on {' and '.join(shown)}? It spends quota on each.", default=False):
            self.say("Cancelled: nothing was sent.")
            return
        self.say(self.dim("… running on " + ", ".join(shown)))
        try:
            results = core.compare(arg, cfg, shown, connectors=self.connectors)
        except ValueError as exc:
            self.say(f"Error: {exc}")
            return
        for r in results:
            j = core.result_json(r, cfg)
            cost = f" · ~${j['estimated_cost_usd']}" if j["estimated_cost_usd"] is not None else ""
            self.say(self.dim(f"━━ {core.format_usage(r) if r['ok'] else r['order'][0]} · {j['seconds']}s{cost}"))
            self.say(render.render(r["output"], color=self.color and self.markdown) + "\n" if r["ok"] else f"Failed: {r.get('error')}\n")

    def models(self, cfg: Dict, probe: bool = False) -> None:
        probed = probe_mod.probe(cfg) if probe else {}
        ids = metrics.known_model_ids(list(cfg["models"]))
        for n, spec in cfg["models"].items():
            extra = f" auth={probed[n]['auth']} {probed[n].get('probe_seconds', '-')}s" if n in probed else ""
            off = " [disabled]" if not spec.get("enabled", True) else ""
            cd = round(state.cooldown_remaining(n))
            mid = (probed.get(n) or {}).get("model_id") or ids.get(n)
            self.say(f"{n:<12} {'installed' if adapters.is_available(n, spec) else 'NOT installed':<13} cooldown={cd}s{extra}{off}  model: {mid or 'unknown'}")

    # --- session startup ---
    def startup(self) -> None:
        """The first thing that happens on open: learn which model each CLI uses, offer up-to-date metrics and (once) the priority questions.
        Anything that spends something (a minimal query, ~1 minute of web reading) is explained and asked about first."""
        cfg = core.load_config(apply_scoring=False)
        if setup_mod.needs_onboarding(cfg):
            setup_mod.run(cfg, say=self.say, ask_yes=self.ask_yes)
        else:
            for name in sorted(state.auth_missing()):
                if name in cfg["models"] and adapters.is_available(name, cfg["models"][name]):
                    self.say(f"{name} was not logged in the last time it ran. {setup_mod.guide(name, 'login')} (/setup checks again.)")
        todo = probe_mod.missing_ids(cfg)
        if todo:
            self.say(f"To route by metrics I need to know which model each CLI uses ({', '.join(todo)}).")
            self.say(self.dim("I make a minimal query to each one (about 12k input tokens on codex and agy; few on claude)."))
            if self.ask_yes("Run it now?"):
                probe_mod.detect_ids(cfg, self.say)
            else:
                self.say("Meanwhile the models.json estimate applies. `/models probe` detects it whenever you want.")
        data = metrics.active()
        today = time.strftime("%Y-%m-%d")
        if not data.get("pages"):
            self.say("There are no bundled or downloaded metrics. `/metrics refresh` downloads them.")
        elif metrics.is_stale(data) and state.flags().get("asked_refresh") != today:
            days = int(metrics.age_days(data.get("arena_at")) or 0)
            self.say(f"The metrics are from {(data.get('arena_at') or '?')[:10]} ({days} days ago, {data.get('arena_origin')}).")
            state.set_flag("asked_refresh", today)
            if self.ask_yes("Update them? (reads public arena.ai pages, about a minute)"):
                scoring.refresh_and_report(cfg, self.say)
        cfg = core.load_config()
        if not cfg.get("_scored"):
            return
        avail = cfg.get("_available", {})
        if not (avail.get("speed") or avail.get("cost")):
            if not state.flags().get("hinted_aa"):  # only once: without more data the priority questions would change nothing
                state.set_flag("hinted_aa", True)
                self.say(self.dim("Tip: with your free Artificial Analysis key (see .env.example) speed and cost are added, and you can prioritize them per kind of task."))
            return
        if not scoring.load_profile() and not state.flags().get("asked_priorities"):
            state.set_flag("asked_priorities", True)
            self.say("\nI can tune the routing to what you prioritize for each kind of task (accuracy, speed or cost): it is 6 questions.")
            if self.ask_yes("Want to answer them now? (later: /priorities)", default=False):
                priorities.run(cfg, say=self.say, color=self.color)

    # --- loop ---
    def banner(self) -> str:
        cfg = core.load_config()
        names = list(cfg["models"])
        ok = {n: cfg["models"][n].get("enabled", True) and adapters.is_available(n, cfg["models"][n]) for n in names}
        return banner.render(__version__, metrics.status_line(), names, ok, attachments.safe_cwd(), color=self.color,
                             width=shutil.get_terminal_size((80, 24)).columns, pinned=self.pinned)

    def status_line(self) -> str:
        d = metrics.active()
        return f"metrics {(d.get('arena_at') or '?')[:10]}" if d.get("pages") else "no metrics"

    def prompt(self) -> str:
        # \001..\002 mark the color codes as non-printing so readline computes the cursor correctly
        return f"\001\033[1;38;2;200;90;160m\002ia ❯\001\033[0m\002 " if self.color else "ia> "

    def loop(self, resume: Optional[str] = None, continue_last: bool = False) -> int:
        self.say(self.banner())
        try:
            self.startup()
        except KeyboardInterrupt:
            self.say("\n(startup interrupted)")
        if resume or continue_last:
            self.resume(resume or "")
        while True:
            try:
                line = self.editor.read() if self.editor else self.read(self.prompt())
            except EOFError:
                self.say()
                return 0
            except KeyboardInterrupt:
                self.say("\n(Ctrl-C again or /exit to quit)")
                try:
                    self.read("")
                except (EOFError, KeyboardInterrupt):
                    return 0
                continue
            try:
                if not self.handle(line):
                    return 0
            except KeyboardInterrupt:
                self.say("\n(interrupted)")


def run(resume: Optional[str] = None, continue_last: bool = False) -> int:
    try:
        import readline  # noqa: F401  (line editing and arrow-key history)
    except ImportError:
        pass
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    chat = Chat(color=color)
    if editor_mod.supported():
        chat.editor = editor_mod.LineEditor(
            COMMANDS, model=lambda: chat.pinned, status=lambda: chat.status_line(),
            history_path=state.home() / "history.jsonl", color=color)
    return chat.loop(resume=resume, continue_last=continue_last)
