"""Chat header: brand mark (three providers converging on a node), gradient wordmark,
and a panel with the key state (manager, available models, folder), in the style of AI CLIs.

Without color it returns the same composition as plain text; under 60 columns, a compact version.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .render import BOLD, DIM, GRAY, RESET, UNDER, _vlen

# "ia-router" wordmark (Calvin S font): three rows per letter.
_GLYPHS = {
    "i": ("╦", "║", "╩"), "a": ("╔═╗", "╠═╣", "╩ ╩"), "r": ("╦═╗", "╠╦╝", "╩╚═"), "o": ("╔═╗", "║ ║", "╚═╝"),
    "u": ("╦ ╦", "║ ║", "╚═╝"), "t": ("╔╦╗", " ║ ", " ╩ "), "e": ("╔═╗", "║╣ ", "╚═╝"), "-": ("   ", " ─ ", "   "),
}
_WORD = "ia-router"
# Provider brand colors (claude, codex, antigravity) and wordmark gradient.
_BRAND = [(217, 119, 87), (99, 168, 248), (52, 168, 83)]
_STOPS = [(217, 119, 87), (200, 90, 160), (66, 133, 244)]
_TAGLINE = "your AI subscriptions, routed"
_AUTHOR, _SITE, _URL = "Mgobeaalcoba", "mgatc.com", "https://mgatc.com"
_GITHUB = "https://github.com/Mgobeaalcoba"


def _rgb(c, bold: bool = False) -> str:
    return f"\033[{'1;' if bold else ''}38;2;{c[0]};{c[1]};{c[2]}m"


def _lerp(t: float):
    t = max(0.0, min(1.0, t)) * (len(_STOPS) - 1)
    i = min(int(t), len(_STOPS) - 2)
    f = t - i
    a, b = _STOPS[i], _STOPS[i + 1]
    return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))


def _wordmark(color: bool) -> List[str]:
    rows = ["".join(_GLYPHS[ch][r] for ch in _WORD) for r in range(3)]
    if not color:
        return rows
    width = len(rows[0])
    return ["".join(f"{_rgb(_lerp(x / max(1, width - 1)), True)}{ch}" if ch != " " else " " for x, ch in enumerate(row)) + RESET for row in rows]


def _mark(names: List[str], ok: Dict[str, bool], color: bool) -> List[str]:
    """●─╮ / ●─┼─◉ / ●─╯: the providers enter the router from the left."""
    dots = []
    for i in range(3):
        n = names[i] if i < len(names) else None
        c = _rgb(_BRAND[i % 3]) if n and ok.get(n) else GRAY
        dots.append(f"{c}●{RESET}" if color else "●")
    g = (lambda s: f"{GRAY}{s}{RESET}") if color else (lambda s: s)
    hub = f"{BOLD}◉{RESET}" if color else "◉"
    return [f"{dots[0]}{g('─╮')}  ", f"{dots[1]}{g('─┼─')}{hub}", f"{dots[2]}{g('─╯')}  "]


def _short_path(path: str, room: int) -> str:
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        path = "~" + path[len(home):]
    return path if len(path) <= room else "…" + path[-(room - 1):]


def _credit(color: bool) -> str:
    """'by Mgobeaalcoba · mgatc.com' with the site as a clickable link (OSC 8) in terminals that support it."""
    if not color:
        return f"by {_AUTHOR} · {_SITE}"
    osc = lambda url, text: f"\033]8;;{url}\033\\{text}\033]8;;\033\\"
    author = osc(_GITHUB, f"{_rgb(_STOPS[1], True)}{UNDER}{_AUTHOR}{RESET}")
    site = osc(_URL, f"{UNDER}{_rgb((66, 133, 244))}{_SITE}{RESET}")
    return f"{DIM}by{RESET} {author} {GRAY}·{RESET} {site}"


def _box(lines: List[str], width: int, color: bool, footer: str = "") -> List[str]:
    inner = width - 4
    b = (lambda s: f"{GRAY}{s}{RESET}") if color else (lambda s: s)
    out = [b("╭" + "─" * (width - 2) + "╮")]
    for l in lines:
        out.append(b("│") + " " + l + " " * max(0, inner - _vlen(l)) + " " + b("│"))
    if footer:  # signature right-aligned on the bottom border
        seg = f" {footer} "
        out.append(b("╰" + "─" * max(1, width - 3 - _vlen(seg))) + seg + b("─╯"))
    else:
        out.append(b("╰" + "─" * (width - 2) + "╯"))
    return out


def render(version: str, metrics_line: str, models: List[str], installed: Dict[str, bool], cwd: str,
           color: bool = True, width: int = 80, pinned: Optional[str] = None) -> str:
    dim = (lambda s: f"{DIM}{s}{RESET}") if color else (lambda s: s)
    label = lambda s: dim(f"{s:<9}")
    chips = []
    for i, n in enumerate(models):
        on = installed.get(n, False)
        dot = f"{_rgb(_BRAND[i % 3])}●{RESET}" if color and on else (f"{GRAY}○{RESET}" if color else ("●" if on else "○"))
        chips.append(f"{dot} {n}" if on else f"{dot} {dim(n)}")
    hints = dim("Just talk normally: tasks or preferences  ·  /help shortcuts  ·  /exit quit")

    if width < 60:  # compact version
        title = f"{BOLD}ia-router{RESET} {dim('v' + version)}" if color else f"ia-router v{version}"
        return "\n".join([title, f"{label('metrics')}{metrics_line}", label("models") + "  ".join(chips), hints, dim(_credit(False)), ""])

    mark, word = _mark(models, installed, color), _wordmark(color)
    ver = dim(f"v{version}")
    head = [f"{mark[0]}   {word[0]}", f"{mark[1]}   {word[1]}  {ver}", f"{mark[2]}   {word[2]}  {dim(_TAGLINE)}"]
    w = min(width - 2, 76)
    room = w - 4 - 8
    info = [f"{label('metrics')}{metrics_line}" + (f"   {dim('pinned:')} {pinned}" if pinned and pinned != "auto" else ""),
            f"{label('models')}" + "   ".join(chips),
            f"{label('folder')}{_short_path(cwd, room)}"]
    return "\n".join([""] + [" " + h for h in head] + [""] + [" " + l for l in _box(info, w, color, _credit(color))] + [" " + hints, ""])
