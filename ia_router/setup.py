"""First-run onboarding and `ia-router setup`: which of the official CLIs are installed and logged in, and what to do about the ones that are not.

The router never installs a CLI or logs in for you (that would mean handling their credentials): it tells you the exact step and checks again.
Checking installation spends nothing; checking the login makes one minimal query per CLI, so it is always asked first.

Pure parts (`statuses`, `usable`, `table`, `advice`) are separated from the flow (`run`), which takes `say`/`ask_yes` so it is testable.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from . import adapters, connectors, probe, state

# What to tell the user for each CLI the router knows. A CLI added by the user in models.json gets the generic text.
GUIDE: Dict[str, Dict[str, str]] = {
    "claude": {"install": "Install Claude Code (see its documentation).", "login": "Run `claude` once and follow the login."},
    "codex": {"install": "Install the Codex CLI (see its documentation).", "login": "Run `codex` once and follow the login."},
    "antigravity": {"install": "Install it with `brew install --cask antigravity-cli`.",
                    "login": "Run `agy` with no arguments in a terminal and choose to sign in with Google."},
}
GENERIC = {"install": "Install the official CLI, or point 'cmd' at it in models.json.", "login": "Log in to that CLI (run it once with no arguments)."}


def guide(name: str, key: str) -> str:
    return (GUIDE.get(name) or GENERIC)[key]


# ---------- pure ----------

def statuses(cfg: Dict, checked: Optional[Dict[str, Dict]] = None) -> List[Dict]:
    """One row per enabled model: {name, installed, auth}. `auth` is 'ok' | 'missing' | 'unknown' from a probe (`checked`),
    'missing' if a task already failed for lack of login, and None when it has not been checked."""
    known_missing = state.auth_missing()
    rows = []
    for name, spec in cfg["models"].items():
        if not spec.get("enabled", True):
            continue
        installed = adapters.is_available(name, spec)
        auth = (checked or {}).get(name, {}).get("auth") if installed else None
        if auth in (None, "n/a") and installed and name in known_missing:
            auth = "missing"
        rows.append({"name": name, "installed": installed, "auth": auth if installed else None})
    return rows


def usable(rows: List[Dict]) -> List[str]:
    """Models that can take a task: installed and not known to be logged out."""
    return [r["name"] for r in rows if r["installed"] and r["auth"] != "missing"]


def table(rows: List[Dict]) -> List[str]:
    out = []
    for r in rows:
        if not r["installed"]:
            mark, note = "✗", "not installed"
        elif r["auth"] == "missing":
            mark, note = "✗", "installed, but not logged in"
        elif r["auth"] == "ok":
            mark, note = "✔", "installed and logged in"
        elif r["auth"] == "unknown":
            mark, note = "?", "installed; the login check failed"
        else:
            mark, note = "✔", "installed (login not checked)"
        out.append(f"  {mark} {r['name']:<12} {note}")
    return out


def advice(rows: List[Dict]) -> List[str]:
    """What to do next, one line per problem, plus what it means for the routing."""
    out = []
    for r in rows:
        if not r["installed"]:
            out.append(f"  {r['name']}: {guide(r['name'], 'install')} {guide(r['name'], 'login')}")
        elif r["auth"] == "missing":
            out.append(f"  {r['name']}: {guide(r['name'], 'login')}")
    n = len(usable(rows))
    if n == 0:
        out.append("No model is ready yet, so tasks cannot run. Fix at least one of the above, then run /setup (or `ia-router setup`) again.")
    elif n == 1:
        out.append("With only one model ready everything goes to it: the router has nothing to choose between. It works; add another CLI whenever you want the split.")
    return out


# ---------- flow ----------

def needs_onboarding(cfg: Dict) -> bool:
    """First run (never completed), or nothing usable yet. A completed setup with all CLIs present stays silent."""
    rows = statuses(cfg)
    return not state.flags().get("onboarded") or not usable(rows)


def run(cfg: Dict, say: Callable[[str], None] = print, ask_yes: Callable[..., bool] = lambda q, default=True: default,
        check_login: Callable[..., Dict] = probe.probe, explicit: bool = False) -> List[Dict]:
    """Shows where things stand and what to do. With `explicit` (the user asked for /setup) it always speaks and offers the login check;
    on the automatic first run it only speaks when something needs attention. Returns the final rows."""
    rows = statuses(cfg)
    problems = [r for r in rows if not r["installed"] or r["auth"] == "missing"]
    if not explicit and not problems:
        state.set_flag("onboarded", True)
        return rows
    say("Welcome to ia-router. It routes your tasks across the official AI CLIs you already pay for. Here is where you stand:"
        if not explicit and not state.flags().get("onboarded") else "Where your CLIs stand:")
    say("\n".join(table(rows)))
    installed = [r["name"] for r in rows if r["installed"]]
    if installed and (explicit or problems) and ask_yes(f"Check the login of {', '.join(installed)} now? (one minimal query each; spends a pinch of quota)", default=False):
        checked = check_login(cfg, only=installed)
        rows = statuses(cfg, checked)
        for name, row in checked.items():
            state.set_auth_missing(name, row.get("auth") == "missing")
        say("\n".join(table(rows)))
    tips = advice(rows)
    if tips:
        say("\n".join(tips))
    if not connectors.has_connectors():
        say("Optional: let the models use your other apps (Gmail, Calendar, Slack…) with MCP connectors: ia-router connectors add NAME -- COMMAND (see /connectors).")
    if any(r["installed"] for r in rows):
        state.set_flag("onboarded", True)
    return rows
