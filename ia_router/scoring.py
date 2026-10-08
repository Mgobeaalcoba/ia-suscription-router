"""Score of each model per kind of task, computed from objective metrics and your priorities.

    score(model, category) = Σ weight × value      (values from 0 to 10, relative to YOUR models)

  accuracy    Arena Elo in the category (human preference, with margin of error) and, if there is a key, the correct-answer
              benchmarks from Artificial Analysis. With no data, the `models.json` estimate (marked as estimated).
  speed       tokens/s published by Artificial Analysis. Only exists with its key.
  cost        list price per million tokens (Arena or Artificial Analysis). Proxy for quota consumption.
              Speed and cost use a logarithmic scale: 2 points less for every doubling versus the best of your models.

A dimension without data for ALL your models carries no weight (the weights are shared among those that do have it). The weights per kind of
task come from your answers in `priorities`; without answers there are defaults. This score feeds the router (`strengths`).
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import metrics, router, state

DIMS = ("precision", "speed", "cost")
LABELS = {"precision": "accuracy", "speed": "speed", "cost": "cost"}
DEFAULT_WEIGHTS = {"precision": 0.70, "speed": 0.15, "cost": 0.15}
QUICK_WEIGHTS = {"precision": 0.40, "speed": 0.50, "cost": 0.10}
PRESETS: Dict[str, Dict[str, float]] = {
    "precision": {"precision": 0.80, "speed": 0.10, "cost": 0.10},
    "balanced": {"precision": 0.50, "speed": 0.25, "cost": 0.25},
    "speed": {"precision": 0.40, "speed": 0.50, "cost": 0.10},
    "cost": {"precision": 0.40, "speed": 0.10, "cost": 0.50},
}
PRESET_LABELS = {"precision": "Accuracy", "balanced": "Balanced", "speed": "Speed", "cost": "Cost"}
# task groups the questions are about: (key, title, router categories)
GROUPS: List[Tuple[str, str, List[str]]] = [
    ("coding", "Code and debugging", ["coding", "debugging"]),
    ("writing", "Writing and drafting", ["writing"]),
    ("analysis", "Analysis, data and research", ["analysis", "data", "research"]),
    ("math", "Math", ["math"]),
    ("quick", "Quick tasks", ["quick"]),
]
CATEGORIES: List[str] = list(router.PATTERNS) + ["quick", "general"]


# ---------- profile: your priorities ----------

def profile_path():
    return state.home() / "profile.json"


def load_profile() -> Dict:
    try:
        d = json.loads(profile_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_profile(profile: Dict) -> None:
    state.home().mkdir(parents=True, exist_ok=True)
    profile["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = profile_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, profile_path())


def group_of(cat: str) -> Optional[str]:
    return next((k for k, _, cats in GROUPS if cat in cats), None)


def weights_for(profile: Dict, cat: str) -> Dict[str, float]:
    choice = (profile.get("priorities") or {}).get(group_of(cat) or "")
    if choice in PRESETS:
        return PRESETS[choice]
    return QUICK_WEIGHTS if cat == "quick" else DEFAULT_WEIGHTS


# ---------- computation ----------

def enabled_models(cfg: Dict) -> List[str]:
    return [n for n, s in cfg["models"].items() if s.get("enabled", True)]


def build(cfg: Dict, data: Optional[Dict] = None, profile: Optional[Dict] = None, ids: Optional[Dict[str, Optional[str]]] = None) -> Dict:
    """{"table": {model: {category: breakdown}}, "available": {dim: bool}, "arena": …, "aa": …, "missing_ids": [models]}.
    The breakdown: score, values {dim: 0-10 | None}, srcs {dim: text}, weights {dim: weight used}, basis ('metrics' | 'estimated')."""
    data = data if data is not None else metrics.active()
    profile = profile if profile is not None else load_profile()
    names = enabled_models(cfg)
    ids = ids if ids is not None else metrics.known_model_ids(names)
    overrides = {n: cfg["models"][n].get("external") or {} for n in names}
    arena, aa = metrics.matches(names, ids, overrides, data)
    prec = metrics.precision_values(names, arena, aa)
    speed, cost = metrics.speed_values(names, aa), metrics.cost_values(names, arena, aa)
    table: Dict[str, Dict[str, Dict]] = {n: {} for n in names}
    for n in names:
        est = cfg["models"][n].get("_prior_strengths") or cfg["models"][n].get("strengths", {})
        for cat in CATEGORIES:
            p = prec.get(cat, {}).get(n)
            vals: Dict[str, Optional[float]] = {"precision": p[0] if p else float(est.get(cat, est.get("general", 5))),
                                                "speed": speed[n][0] if n in speed else None, "cost": cost[n][0] if n in cost else None}
            srcs = {"precision": p[1] if p else "hand-estimated (models.json)"}
            if n in speed:
                srcs["speed"] = speed[n][1]
            if n in cost:
                srcs["cost"] = cost[n][1]
            w = {d: x for d, x in weights_for(profile, cat).items() if vals[d] is not None}
            total = sum(w.values()) or 1.0
            w = {d: x / total for d, x in w.items()}
            table[n][cat] = {"score": round(sum(w[d] * vals[d] for d in w), 2), "values": {d: (round(v, 2) if v is not None else None) for d, v in vals.items()},
                             "srcs": srcs, "weights": {d: round(x, 2) for d, x in w.items()}, "basis": "metrics" if p else "estimated"}
    return {"table": table, "available": {"speed": bool(speed), "cost": bool(cost)}, "arena": arena, "aa": aa,
            "missing_ids": [n for n in names if not ids.get(n)]}


def apply_to_config(cfg: Dict) -> Dict:
    """Replaces `strengths` with the score (if there are metrics covering your models). Stores the breakdown in spec['_scoring']."""
    if len(enabled_models(cfg)) < 2:
        return cfg
    b = build(cfg)
    if not any(d["basis"] == "metrics" for n in b["table"] for d in b["table"][n].values()):
        return cfg  # no model ids or no coverage: the models.json estimate applies
    for n, spec in cfg["models"].items():
        if n not in b["table"]:
            continue
        spec["_prior_strengths"] = dict(spec.get("strengths", {}))
        spec["_scoring"] = b["table"][n]
        spec["strengths"] = {cat: d["score"] for cat, d in b["table"][n].items()}
    cfg["_scored"], cfg["_available"] = True, b["available"]
    return cfg


# ---------- presentation ----------

def render_table(cfg: Dict, cats: Optional[List[str]] = None) -> str:
    if not cfg.get("_scored"):
        return ("There are no metrics covering your models yet: the models.json estimate applies.\n"
                "I need to know which model each CLI uses: run `doctor --probe` (or let it detect them when the chat starts).")
    names = enabled_models(cfg)
    table = {n: cfg["models"][n]["_scoring"] for n in names}
    cats = cats or [c for c in CATEGORIES if c != "general"]
    head = f"{'category':<14}" + "".join(f"{n:>12}   " for n in names)   # each cell: score (12) + * mark + e mark + space
    lines = [head, "─" * len(head)]
    for cat in cats:
        best = max(table[n][cat]["score"] for n in names)
        row = f"{cat:<14}"
        for n in names:
            d = table[n][cat]
            row += f"{d['score']:>12.1f}{'*' if d['score'] == best else ' '}{'e' if d['basis'] == 'estimated' else ' '} "
        lines.append(row)
    lines += ["", "* = the one the router picks · e = hand-estimated accuracy (Arena does not cover that category for all your models)"]
    a = cfg.get("_available", {})
    lines.append("Dimensions with data: accuracy" + (", speed" if a.get("speed") else "") + (", cost" if a.get("cost") else "")
                 + ("" if a.get("speed") else "  ·  speed: your Artificial Analysis key is missing (.env)"))
    d = metrics.active()
    lines.append("Data: " + metrics.status_line(d) + "  ·  Attribution: " + " · ".join(metrics.ATTRIBUTION[k] for k in (("arena", "aa") if d.get("aa") else ("arena",))))
    return "\n".join(lines)


def explain(cfg: Dict, cat: str) -> str:
    names = enabled_models(cfg)
    if not cfg.get("_scored") or cat not in cfg["models"][names[0]].get("_scoring", {}):
        return f"No breakdown for '{cat}'."
    lines = [f"Score of «{cat}» = Σ weight × value (0-10)"]
    for n in names:
        d = cfg["models"][n]["_scoring"][cat]
        parts = [f"{LABELS[k]} {d['values'][k]:.1f}×{w:.2f}" for k, w in d["weights"].items()]
        lines.append(f"{n:<13}= {d['score']:>5.2f}   " + "  ".join(parts))
        lines.append(f"{'':<13}  " + " · ".join(f"{LABELS[k]}: {v}" for k, v in d["srcs"].items()))
    return "\n".join(lines)


def describe_sources(cfg: Dict) -> str:
    """Which entry of each portal was matched to each of your models, and where the data comes from."""
    names = enabled_models(cfg)
    b = build(cfg)
    d = metrics.active()
    lines = [f"Metrics: {metrics.status_line(d)}  ·  Arena {d.get('arena_origin', '')}", ""]
    for n in names:
        mid = metrics.known_model_ids([n])[n]
        a, x = b["arena"].get(n), b["aa"].get(n)
        lines.append(f"{n:<12} CLI model: {mid or 'unknown (run /models probe)'}")
        if a:
            warn = "" if a["exact"] else "  ⚠ Arena does not publish the same effort level your CLI uses: approximate data (" + ", ".join(
                f"{sub}: {v['effort'] or 'no level'}" for sub, v in a["variants"].items() if not v["exact"]) + ")"
            lines.append(f"{'':<12} Arena → {a['name']}{warn}")
        elif mid:
            lines.append(f"{'':<12} Arena → no match for that model")
        if x:
            warn = "" if x["exact"] else "  ⚠ approximate: your CLI does not report its effort level"
            lines.append(f"{'':<12} Artificial Analysis → {x['name']}" + (f" · {x['tps']:.0f} tok/s" if x.get("tps") else "") + warn)
    if not d.get("aa"):
        lines += ["", "Artificial Analysis is not active: without its key there is no speed nor, in general, cost. See .env.example."]
    lines.append("Sources: " + " · ".join(metrics.ATTRIBUTION[k] for k in (("arena", "aa") if d.get("aa") else ("arena",))))
    return "\n".join(lines)


def price_of(cfg: Dict, name: str) -> Optional[Tuple[float, float]]:
    """(input, output) list price in USD per million tokens for the model a CLI uses, from Artificial Analysis or Arena; None if unknown."""
    try:
        b = build(cfg)
    except (KeyError, ValueError):
        return None
    aa, arena = b["aa"].get(name) or {}, b["arena"].get(name) or {}
    pin, pout = aa.get("price_in"), aa.get("price_out")
    if not (isinstance(pin, (int, float)) and isinstance(pout, (int, float))):
        price = arena.get("price") or {}
        pin, pout = price.get("in"), price.get("out")
    return (float(pin), float(pout)) if isinstance(pin, (int, float)) and isinstance(pout, (int, float)) else None


def estimate_cost(cfg: Dict, name: str, tokens: Optional[Dict]) -> Optional[float]:
    """Estimated USD of one answer at LIST price (a proxy: a subscription does not bill per token). Cached input is counted as input."""
    price = price_of(cfg, name)
    if not price or not tokens:
        return None
    return round((tokens.get("input", 0) * price[0] + tokens.get("output", 0) * price[1]) / 1_000_000, 4)


def diff_tables(old: Optional[Dict], new: Dict, threshold: float = 0.3) -> List[str]:
    """What changed between two computations: the model chosen per category and the scores that moved at least `threshold`."""
    if not old:
        return []
    out = []
    for cat in CATEGORIES:
        if cat == "general":
            continue
        o = {n: old[n][cat]["score"] for n in old if cat in old[n]}
        w = {n: new[n][cat]["score"] for n in new if cat in new[n]}
        if not o or not w:
            continue
        bo, bw = max(o, key=o.get), max(w, key=w.get)
        moved = [f"{n} {o[n]:.1f}→{w[n]:.1f}" for n in w if n in o and abs(w[n] - o[n]) >= threshold]
        if bo != bw:
            out.append(f"{cat}: now picks {bw} (was {bo})" + (f"  [{', '.join(moved)}]" if moved else ""))
        elif moved:
            out.append(f"{cat}: {', '.join(moved)}")
    return out


def refresh_and_report(cfg: Dict, say: Callable[[str], None] = print, force: bool = False, get=metrics.http_get, sleep=time.sleep) -> bool:
    """Updates the metrics showing every step and, at the end, what changed in the routing. True if it was updated."""
    first = build(cfg)
    before, ids_known = first["table"], not first["missing_ids"]
    try:
        metrics.refresh(cfg, say=say, get=get, force=force, sleep=sleep)
    except (OSError, ValueError) as exc:
        say(f"Could not update the metrics: {exc}. Continuing with the ones you had ({metrics.status_line()}).")
        return False
    after = build(cfg)["table"]
    changes = diff_tables(before, after) if ids_known else []
    say("Metrics up to date: " + metrics.status_line())
    if changes:
        say("What changed in the routing:\n" + "\n".join("  · " + c for c in changes))
    elif ids_known:
        say("The routing does not change with this data.")
    return True
