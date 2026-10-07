"""Single-choice selector in the terminal: ↑/↓ (or j/k, or the number), Enter confirms, Esc cancels.

`SelectState` is pure (testable without a terminal); `choose()` drives it over a tty and redraws in place.
Without an interactive terminal it returns the default option, so flows do not hang in pipes or tests.
"""
from __future__ import annotations

import os
import select as _select
import shutil
import sys
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from .banner import _lerp, _rgb
from .editor import Parser, text_width
from .render import BOLD, DIM, GRAY, RESET, _vlen


@dataclass
class Option:
    label: str
    desc: str = ""


class SelectState:
    def __init__(self, n: int, default: int = 0) -> None:
        self.n = n
        self.sel = min(max(default, 0), max(0, n - 1))

    def apply(self, ev: Tuple[str, str]) -> Optional[Tuple[str, ...]]:
        kind, val = ev
        if kind == "text":
            for ch in val:
                if ch in "jJ":
                    self.sel = (self.sel + 1) % self.n
                elif ch in "kK":
                    self.sel = (self.sel - 1) % self.n
                elif ch.isdigit() and 1 <= int(ch) <= self.n:
                    self.sel = int(ch) - 1
            return None
        if kind != "key":
            return None
        if val == "down" or val == "tab":
            self.sel = (self.sel + 1) % self.n
        elif val == "up" or val == "shift_tab":
            self.sel = (self.sel - 1) % self.n
        elif val == "home":
            self.sel = 0
        elif val == "end":
            self.sel = self.n - 1
        elif val == "enter":
            return ("done", str(self.sel))
        elif val in ("esc", "interrupt", "eof"):
            return ("cancel",)
        return None


def render_lines(title: str, options: Sequence[Option], sel: int, subtitle: str = "", color: bool = True, step: str = "", width: int = 100) -> List[str]:
    dim = (lambda s: f"{DIM}{s}{RESET}") if color else (lambda s: s)
    head = f"{BOLD}{title}{RESET}" if color else title
    lines = ["", f"  {dim(step + '  ') if step else ''}{head}"]
    if subtitle:
        lines.append("  " + dim(subtitle))
    lines.append("")
    pad = max(text_width(o.label) for o in options) + 2
    for i, o in enumerate(options):
        on = i == sel
        mark = "◉" if on else "○"
        label = o.label + " " * (pad - text_width(o.label))
        if color and on:
            accent = _rgb(_lerp(0.5), True)
            lines.append(f"  {accent}❯ {mark} {label}{RESET}{o.desc}")
        elif color:
            lines.append(f"    {GRAY}{mark}{RESET} {label}{dim(o.desc)}")
        else:
            lines.append(f"  {'>' if on else ' '} {mark} {label}{o.desc}")
    lines += ["", "  " + dim("↑/↓ choose · 1-9 jump · ⏎ confirm · esc cancel")]
    return [l if _vlen(l) < width else l[: width - 1] for l in lines]


def summary_line(title: str, label: str, step: str = "", color: bool = True) -> str:
    """How an already answered question looks: ✔ 1/6 What do you prioritize…  › Balanced"""
    if not color:
        return f"  ✔ {step + '  ' if step else ''}{title} › {label}"
    return f"  {_rgb((52, 168, 83), True)}✔{RESET} {DIM}{step + '  ' if step else ''}{title}{RESET} {GRAY}›{RESET} {BOLD}{label}{RESET}"


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "") not in ("", "dumb")


def choose(title: str, options: Sequence[Option], default: int = 0, subtitle: str = "", step: str = "", color: bool = True) -> Optional[int]:
    """Chosen index, or None if cancelled. Without a tty, the default option."""
    if not interactive():
        return default
    import termios
    fd = sys.stdin.fileno()
    old, new = termios.tcgetattr(fd), termios.tcgetattr(fd)
    new[0] &= ~(termios.IXON | termios.ICRNL | termios.INLCR | termios.IGNCR)
    new[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
    new[6][termios.VMIN], new[6][termios.VTIME] = 1, 0
    st, parser, drawn = SelectState(len(options), default), Parser(), 0

    def draw() -> None:
        nonlocal drawn
        cols = shutil.get_terminal_size((80, 24)).columns
        lines = render_lines(title, options, st.sel, subtitle, color, step, cols)
        sys.stdout.write((f"\033[{drawn - 1}A" if drawn > 1 else "") + "\r\033[J" + "\r\n".join(lines))
        sys.stdout.flush()
        drawn = len(lines)

    try:
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        sys.stdout.write("\033[?25l")
        draw()
        while True:
            ready = _select.select([fd], [], [], 0.05 if parser.buf else None)[0]
            events = parser.feed(os.read(fd, 4096)) if ready else parser.flush()
            for ev in events:
                res = st.apply(ev)
                if res:
                    done = res[0] == "done"
                    sys.stdout.write((f"\033[{drawn - 1}A" if drawn > 1 else "") + "\r\033[J")  # erase the question
                    if done:  # and leave it summarized in one line
                        sys.stdout.write(summary_line(title, options[int(res[1])].label, step, color) + "\r\n")
                    return int(res[1]) if done else None
            draw()
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
