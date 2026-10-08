"""Quota meter: how much each model has been used lately and how close it is to the limit that you have already hit.

Providers do not publish their subscription limits, so nothing is guessed: the router learns a limit from YOUR history. Every time a
model answered with a rate limit, the tokens it had used in the window before that moment are an observed ceiling; the largest one is the
reference. Without a recorded rate limit there is no reference and no warning (just the numbers).

The window is an assumption (5 hours, the usual rolling window of coding subscriptions), shown as such. Costs are list prices, a proxy.
Everything is computed from `log.jsonl`, which never holds prompts. Pure functions with an injectable `now`.
"""
from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

from . import scoring, state

WINDOW_HOURS = 5.0
WARN_AT = 0.8
_FMT = "%Y-%m-%dT%H:%M:%S"


def _epoch(ts: Optional[str]) -> Optional[float]:
    try:
        return time.mktime(time.strptime(ts or "", _FMT))
    except ValueError:
        return None


def _tokens(ev: Dict) -> int:
    t = ev.get("tokens") or {}
    return int(t.get("input", 0) or 0) + int(t.get("output", 0) or 0)


def read_log() -> List[Dict]:
    events = []
    try:
        for line in (state.home() / "log.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("model") and _epoch(ev.get("ts")) is not None:
                events.append(ev)
    except OSError:
        pass
    return events


def summarize(events: List[Dict], now: Optional[float] = None, window_hours: float = WINDOW_HOURS, cfg: Optional[Dict] = None) -> Dict[str, Dict]:
    """{model: {tokens_window, tokens_24h, tokens_7d, runs_7d, rate_limits_7d, last_rate_limit, observed_limit, used_ratio, cost_7d}}.
    `cfg` (optional) adds an estimated list-price cost of the last 7 days."""
    now = time.time() if now is None else now
    out: Dict[str, Dict] = {}
    by_model: Dict[str, List[Dict]] = {}
    for ev in events:
        by_model.setdefault(ev["model"], []).append(ev)
    w = window_hours * 3600
    for model, evs in by_model.items():
        evs.sort(key=lambda e: _epoch(e["ts"]))
        price = scoring.price_of(cfg, model) if cfg else None
        row = {"tokens_window": 0, "tokens_24h": 0, "tokens_7d": 0, "runs_7d": 0, "rate_limits_7d": 0, "last_rate_limit": None,
               "observed_limit": None, "used_ratio": None, "cost_7d": 0.0 if price else None}
        for ev in evs:
            t = _epoch(ev["ts"])
            age = now - t
            if age < 0:
                continue
            tok = _tokens(ev)
            if age <= 7 * 86400:
                row["runs_7d"] += 1
                row["tokens_7d"] += tok
                if price:
                    t_ = ev.get("tokens") or {}
                    row["cost_7d"] += (t_.get("input", 0) * price[0] + t_.get("output", 0) * price[1]) / 1_000_000
                row["rate_limits_7d"] += 1 if ev.get("error") == "rate_limited" else 0
            if age <= 86400:
                row["tokens_24h"] += tok
            if age <= w:
                row["tokens_window"] += tok
            if ev.get("error") == "rate_limited":
                row["last_rate_limit"] = ev["ts"]
                used = sum(_tokens(e) for e in evs if 0 <= t - _epoch(e["ts"]) <= w)   # what was spent in the window that ended in this limit
                if used > 0 and (row["observed_limit"] is None or used > row["observed_limit"]):
                    row["observed_limit"] = used
        if row["observed_limit"]:
            row["used_ratio"] = round(row["tokens_window"] / row["observed_limit"], 2)
        out[model] = row
    return out


def warnings(summary: Dict[str, Dict], names: Optional[List[str]] = None) -> List[str]:
    """One line per model that is close to the limit it has already hit."""
    out = []
    for model, row in summary.items():
        if names is not None and model not in names:
            continue
        r = row["used_ratio"]
        if r is not None and r >= WARN_AT:
            out.append(f"{model} is at {r:.0%} of the {row['observed_limit']:,} tokens it had used when it last hit a rate limit "
                       f"(last {WINDOW_HOURS:g} h). Consider /model to send this one to another model.")
    return out


def table(summary: Dict[str, Dict]) -> List[str]:
    if not summary:
        return ["No usage recorded yet (it builds up with every `ask`)."]
    head = f"{'model':<12} {'last 5h':>10} {'last 24h':>10} {'last 7d':>10} {'runs':>5} {'limits':>6} {'vs limit':>9} {'est. USD 7d':>12}"
    lines = [head.replace("last 5h", f"last {WINDOW_HOURS:g}h"), "─" * len(head)]
    for model, r in sorted(summary.items()):
        cost = r["cost_7d"]
        ratio = f"{r['used_ratio']:.0%}" if r["used_ratio"] is not None else "n/a"
        lines.append(f"{model:<12} {r['tokens_window']:>10,} {r['tokens_24h']:>10,} {r['tokens_7d']:>10,} {r['runs_7d']:>5} {r['rate_limits_7d']:>6} {ratio:>9} "
                     f"{('$' + format(cost, '.2f')) if cost is not None else 'n/a':>12}")
    lines += ["", f"Tokens = input + output (cached input included). 'vs limit' compares the last {WINDOW_HOURS:g} h with the most you had used when a "
                  "rate limit hit you: n/a until one is recorded. The window is an assumption; providers do not publish their limits.",
              "Est. USD is a list-price proxy (input and output at their own prices), not what your subscription charges."]
    return lines
