"""Markdown en la terminal: estilos ANSI que CONSERVAN los signos (#, **, `, ```, >, -), sin dependencias.

Sin color (pipe, NO_COLOR, tests) devuelve el texto tal cual. Es un renderizador por líneas, no un parser
completo: cubre lo que suelen devolver los modelos (títulos, listas, citas, código, tablas, énfasis, links).
"""
from __future__ import annotations

import re

RESET = "\033[0m"
BOLD, ITALIC, UNDER, STRIKE, DIM = "\033[1m", "\033[3m", "\033[4m", "\033[9m", "\033[2m"
CYAN, YELLOW, BLUE, MAGENTA = "\033[36m", "\033[33m", "\033[34m", "\033[35m"

_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^(\s{0,3})(#{1,6})(\s+.*)$")
_BULLET = re.compile(r"^(\s*)([-*+]|\d+[.)])(\s+)(.*)$")
_QUOTE = re.compile(r"^(\s*>+)(.*)$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE = re.compile(r"^\s*\|.*\|\s*$")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_INLINE = [
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__"), BOLD, "\033[22m"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), STRIKE, "\033[29m"),
    (re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])"), ITALIC, "\033[23m"),
    (re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])"), ITALIC, "\033[23m"),
]
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")


def _inline_text(s: str) -> str:
    """Énfasis y links sobre texto sin code spans. Los marcadores quedan visibles pero atenuados."""
    s = _LINK.sub(lambda m: f"{DIM}[{RESET}{UNDER}{BLUE}{m.group(1)}{RESET}{DIM}]({m.group(2)}){RESET}", s)
    for pat, on, off in _INLINE:
        def sub(m, on=on, off=off):
            body = next(g for g in m.groups() if g is not None)
            mark = m.group(0)[: (len(m.group(0)) - len(body)) // 2]
            return f"{DIM}{mark}{RESET}{on}{body}{off}{DIM}{mark}{RESET}"
        s = pat.sub(sub, s)
    return s


def inline(s: str) -> str:
    out, pos = [], 0
    for m in _CODE_SPAN.finditer(s):
        out.append(_inline_text(s[pos:m.start()]))
        tick = m.group(1)
        out.append(f"{DIM}{tick}{RESET}{CYAN}{m.group(2)}{RESET}{DIM}{tick}{RESET}")
        pos = m.end()
    out.append(_inline_text(s[pos:]))
    return "".join(out)


def _table_row(line: str) -> str:
    if re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", line) and "-" in line:
        return f"{DIM}{line}{RESET}"  # fila separadora |---|---|
    return re.sub(r"\|", f"{DIM}|{RESET}", inline(line))


def render(text: str, color: bool = True) -> str:
    if not color or not text:
        return text
    out, fence = [], None
    for line in text.split("\n"):
        f = _FENCE.match(line)
        if fence:
            if f and f.group(2)[0] == fence[0] and len(f.group(2)) >= len(fence) and not f.group(3).strip():
                fence = None
                out.append(f"{DIM}{line}{RESET}")
            else:
                out.append(f"{CYAN}{line}{RESET}")
            continue
        if f:
            fence = f.group(2)
            out.append(f"{DIM}{f.group(1)}{f.group(2)}{RESET}{YELLOW}{f.group(3)}{RESET}")
            continue
        if m := _HEADING.match(line):
            out.append(f"{m.group(1)}{BOLD}{MAGENTA}{m.group(2)}{m.group(3)}{RESET}")
        elif _HR.match(line):
            out.append(f"{DIM}{line}{RESET}")
        elif m := _QUOTE.match(line):
            out.append(f"{DIM}{m.group(1)}{RESET}{ITALIC}{inline(m.group(2))}{RESET}")
        elif m := _BULLET.match(line):
            out.append(f"{m.group(1)}{YELLOW}{m.group(2)}{RESET}{m.group(3)}{inline(m.group(4))}")
        elif _TABLE.match(line):
            out.append(_table_row(line))
        else:
            out.append(inline(line))
    return "\n".join(out) + (RESET if fence else "")
