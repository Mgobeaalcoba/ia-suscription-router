"""Encabezado del chat: marca (tres proveedores que convergen en un nodo), wordmark con degradé,
y un panel con el estado clave (manager, modelos disponibles, carpeta), al estilo de los CLIs de IA.

Sin color devuelve la misma composición en texto plano; con menos de 60 columnas, una versión compacta.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from .render import BOLD, DIM, GRAY, RESET, _vlen

# Wordmark "ia-router" (fuente Calvin S): tres filas por letra.
_GLYPHS = {
    "i": ("╦", "║", "╩"), "a": ("╔═╗", "╠═╣", "╩ ╩"), "r": ("╦═╗", "╠╦╝", "╩╚═"), "o": ("╔═╗", "║ ║", "╚═╝"),
    "u": ("╦ ╦", "║ ║", "╚═╝"), "t": ("╔╦╗", " ║ ", " ╩ "), "e": ("╔═╗", "║╣ ", "╚═╝"), "-": ("   ", " ─ ", "   "),
}
_WORD = "ia-router"
# Colores de marca de los proveedores (claude, codex, antigravity) y degradé del wordmark.
_BRAND = [(217, 119, 87), (99, 168, 248), (52, 168, 83)]
_STOPS = [(217, 119, 87), (200, 90, 160), (66, 133, 244)]
_TAGLINE = "tus suscripciones de IA, ruteadas"


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
    """●─╮ / ●─┼─◉ / ●─╯: los proveedores entran al router por la izquierda."""
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


def _box(lines: List[str], width: int, color: bool) -> List[str]:
    inner = width - 4
    b = (lambda s: f"{GRAY}{s}{RESET}") if color else (lambda s: s)
    out = [b("╭" + "─" * (width - 2) + "╮")]
    for l in lines:
        out.append(b("│") + " " + l + " " * max(0, inner - _vlen(l)) + " " + b("│"))
    out.append(b("╰" + "─" * (width - 2) + "╯"))
    return out


def render(version: str, manager: str, models: List[str], installed: Dict[str, bool], cwd: str,
           color: bool = True, width: int = 80, pinned: Optional[str] = None) -> str:
    dim = (lambda s: f"{DIM}{s}{RESET}") if color else (lambda s: s)
    label = lambda s: dim(f"{s:<8}")
    chips = []
    for i, n in enumerate(models):
        on = installed.get(n, False)
        dot = f"{_rgb(_BRAND[i % 3])}●{RESET}" if color and on else (f"{GRAY}○{RESET}" if color else ("●" if on else "○"))
        chips.append(f"{dot} {n}" if on else f"{dot} {dim(n)}")
    hints = dim("Hablame normal: tareas o preferencias  ·  /help atajos  ·  /exit salir")

    if width < 60:  # versión compacta
        title = f"{BOLD}ia-router{RESET} {dim('v' + version)}" if color else f"ia-router v{version}"
        return "\n".join([title, f"{label('manager')}{manager}", label("modelos") + "  ".join(chips), hints, ""])

    mark, word = _mark(models, installed, color), _wordmark(color)
    ver = dim(f"v{version}")
    head = [f"{mark[0]}   {word[0]}", f"{mark[1]}   {word[1]}  {ver}", f"{mark[2]}   {word[2]}  {dim(_TAGLINE)}"]
    w = min(width - 2, 76)
    room = w - 4 - 8
    info = [f"{label('manager')}{manager}" + (f"   {dim('fijado:')} {pinned}" if pinned and pinned != "auto" else ""),
            f"{label('modelos')}" + "   ".join(chips),
            f"{label('carpeta')}{_short_path(cwd, room)}"]
    return "\n".join([""] + [" " + h for h in head] + [""] + [" " + l for l in _box(info, w, color)] + [" " + hints, ""])
