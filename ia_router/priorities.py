"""Multiple-choice questions (↑/↓ selector + Enter) about what you prioritize for each kind of task: accuracy, speed or cost.

Your answers rebuild the routing: each kind of task uses the weights of the chosen option over the objective metrics
(see scoring.PRESETS). No numbers are made up: the result is shown before saving. Without answers, the default weights apply.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from . import scoring
from .select import Option, choose as tty_choose

OPTION_TEXT = {
    "precision": "the best answer, even if it is slower or costs more",
    "balanced": "a bit of everything: accuracy, speed and cost",
    "speed": "the fastest among the good ones (published tokens/s)",
    "cost": "the one that uses the least quota (price per million tokens)",
}


def options_for(available: Dict[str, bool]) -> List[str]:
    """Options offered: speed and cost only if there is objective data for all your models."""
    return ["precision", "balanced"] + (["speed"] if available.get("speed") else []) + (["cost"] if available.get("cost") else [])


def preview(table: Dict[str, Dict[str, Dict]]) -> List[str]:
    """Which model the router would pick per category with that calculation."""
    names = list(table)
    out = []
    for key, title, cats in scoring.GROUPS:
        picks = []
        for c in cats:
            best = max(names, key=lambda n: table[n][c]["score"])
            picks.append(f"{c} → {best}")
        out.append(f"  {title}: " + ", ".join(picks))
    return out


def current(cfg: Dict) -> Dict:
    """What the questions would offer and what is saved now: {groups, options, answers, notes, blocked}. Shared by `priorities --show` and the UI."""
    avail = scoring.build(cfg)["available"]
    keys = options_for(avail)
    notes = [f"{scoring.LABELS[d]} is not available: " + ("your Artificial Analysis key is missing (see .env.example)" if d == "speed" else "no portal publishes the price of all your models")
             for d in ("speed", "cost") if not avail.get(d)]
    return {"groups": [{"key": k, "title": t} for k, t, _ in scoring.GROUPS], "options": [{"key": k, "label": scoring.PRESET_LABELS[k], "text": OPTION_TEXT[k]} for k in keys],
            "answers": {k: v for k, v in (scoring.load_profile().get("priorities") or {}).items() if v in keys}, "notes": notes,
            "blocked": not avail.get("speed") and not avail.get("cost")}


def apply(cfg: Dict, answers: Dict[str, str], save: bool = True) -> List[str]:
    """Validates `answers` ({group: option}) against what is offered, optionally saves them, and returns the preview lines (which model wins per category).
    Groups you leave out keep what was saved. Raises ValueError for an unknown group or an option that is not offered."""
    info = current(cfg)
    valid_groups, valid_options = {g["key"] for g in info["groups"]}, {o["key"] for o in info["options"]}
    for group, option in answers.items():
        if group not in valid_groups:
            raise ValueError(f"unknown group '{group}' (options: {', '.join(sorted(valid_groups))})")
        if option not in valid_options:
            raise ValueError(f"'{option}' is not available (options: {', '.join(sorted(valid_options)) or 'none'}" + (f"; {' '.join(info['notes'])}" if info["notes"] else "") + ")")
    profile = {"priorities": {**info["answers"], **answers}}
    lines = preview(scoring.build(cfg, profile=profile)["table"])
    if save:
        scoring.save_profile(profile)
    return lines


def run(cfg: Dict, choose: Callable = tty_choose, say: Callable[[str], None] = print, color: bool = True) -> Optional[Dict]:
    """Asks the questions. Returns the saved profile, or None if cancelled or discarded. `choose` is injectable for tests."""
    built = scoring.build(cfg)
    avail = built["available"]
    keys = options_for(avail)
    if not avail.get("speed") and not avail.get("cost"):
        say("With the current metrics there is only accuracy data for your models: there is nothing to prioritize between accuracy, speed and cost.\n"
            "Add your free Artificial Analysis key (see .env.example) and update with `metrics refresh`: it brings speed and price.")
        return None
    prev = scoring.load_profile().get("priorities") or {}
    notes = [f"{scoring.LABELS[d]} is not available: " + ("your Artificial Analysis key is missing (see .env.example)" if d == "speed" else "no portal publishes the price of all your models")
             for d in ("speed", "cost") if not avail.get(d)]
    answers: Dict[str, str] = {}
    total = len(scoring.GROUPS) + 1
    for i, (key, title, _) in enumerate(scoring.GROUPS, 1):
        a = choose(f"For {title.lower()}, what do you prioritize?", [Option(scoring.PRESET_LABELS[k], OPTION_TEXT[k]) for k in keys],
                   default=keys.index(prev[key]) if prev.get(key) in keys else 0, subtitle="  ·  ".join(notes) if i == 1 else "",
                   step=f"{i}/{total}", color=color)
        if a is None:
            say("Cancelled: nothing changed.")
            return None
        answers[key] = keys[a]
    profile = {"priorities": answers}
    say("\nThis is the routing with your priorities:\n" + "\n".join(preview(scoring.build(cfg, profile=profile)["table"])))
    final = choose("Apply these priorities?", [Option("Save", "the router uses them right away"), Option("Discard", "change nothing")], default=0, step=f"{total}/{total}", color=color)
    if final != 0:
        say("Discarded: nothing changed.")
        return None
    scoring.save_profile(profile)
    say("Saved. They apply right away (`scores` shows the detail).")
    return profile
