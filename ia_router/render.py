"""Markdown interpretado en la terminal, como se ve un README en GitHub: sin los signos (#, **, `, ```, >, |),
con estilos ANSI, viñetas, tablas alineadas y bloques de código con fondo y colores. Sin dependencias.

Sin color (pipe, NO_COLOR, tests) devuelve el texto tal cual. Es un renderizador por líneas, no un parser
completo: cubre lo que suelen devolver los modelos (títulos, listas, citas, código, tablas, énfasis, links).
"""
from __future__ import annotations

import re
import shutil
from typing import List, Optional

RESET = "\033[0m"
BOLD, BOLD_OFF = "\033[1m", "\033[22m"
ITALIC, ITALIC_OFF = "\033[3m", "\033[23m"
UNDER, UNDER_OFF = "\033[4m", "\033[24m"
STRIKE, STRIKE_OFF = "\033[9m", "\033[29m"
DIM = "\033[2m"
FG_OFF = "\033[39m"
CYAN, YELLOW, BLUE, MAGENTA, GREEN, GRAY = "\033[36m", "\033[33m", "\033[34m", "\033[35m", "\033[32m", "\033[90m"
CODE_BG, CODE_BG_OFF = "\033[48;5;236m", "\033[49m"      # bloque de código
SPAN_ON, SPAN_OFF = "\033[38;5;216m\033[48;5;238m", "\033[39m\033[49m"  # `código en línea`

_ANSI = re.compile(r"\033\[[0-9;]*m|\033\][^\033\007]*(?:\033\\|\007)")  # CSI de estilo y links OSC 8
_FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+#.-]*)\s*$")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
_QUOTE = re.compile(r"^\s*((?:>\s?)+)(.*)$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_TABLE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
_CODE_SPAN = re.compile(r"(`+)(.+?)\1")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
_INLINE = [
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__"), BOLD, BOLD_OFF),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), STRIKE, STRIKE_OFF),
    (re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])"), ITALIC, ITALIC_OFF),
    (re.compile(r"(?<![\w_])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])"), ITALIC, ITALIC_OFF),
]
_LINK = re.compile(r"!?\[([^\]\n]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")

_HASH_COMMENT = {"python", "py", "bash", "sh", "shell", "zsh", "yaml", "yml", "toml", "ruby", "rb", "r", "dockerfile", "makefile", "ini"}
_SLASH_COMMENT = {"js", "javascript", "ts", "typescript", "jsx", "tsx", "java", "c", "cpp", "c++", "cs", "csharp", "go", "rust", "rs", "kotlin", "swift", "php", "scala", "json5", "jsonc"}
_KEYWORDS = (r"def|class|return|if|elif|else|for|while|in|not|and|or|is|import|from|as|with|try|except|finally|raise|lambda|yield|pass|break|continue|"
             r"None|True|False|function|const|let|var|new|this|async|await|export|default|switch|case|typeof|null|undefined|true|false|"
             r"public|private|static|void|int|float|string|bool|struct|fn|func|package|interface|enum|impl|use|mut|select|insert|update|delete|where|join|"
             r"create|table|group|order|by|limit|echo|then|fi|do|done")
_CODE_TOKEN = re.compile(
    r"(?P<str>\"\"\".*?\"\"\"|'''.*?'''|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)"
    rf"|(?P<kw>\b(?:{_KEYWORDS})\b)"
    r"|(?P<num>\b\d+(?:\.\d+)?\b)",
    re.S,
)


def _vlen(s: str) -> int:
    """Largo visible (sin secuencias ANSI)."""
    return len(_ANSI.sub("", s))


# ---------- texto en línea ----------

def _emphasis(s: str) -> str:
    s = _ESCAPE.sub(lambda m: chr(0xE000 + ord(m.group(1))), s)  # \* y similares: se protegen y se restauran al final
    s = _AUTOLINK.sub(lambda m: f"{UNDER}{BLUE}{m.group(1)}{FG_OFF}{UNDER_OFF}", s)

    def link(m):
        text, url = m.group(1), m.group(2)
        label = f"{UNDER}{BLUE}{text or url}{FG_OFF}{UNDER_OFF}"
        return label if not text or text == url else f"{label} {GRAY}({url}){FG_OFF}"
    s = _LINK.sub(link, s)
    for pat, on, off in _INLINE:
        s = pat.sub(lambda m, on=on, off=off: on + next(g for g in m.groups() if g is not None) + off, s)
    return re.sub("[\ue000-\ue0ff]", lambda m: chr(ord(m.group(0)) - 0xE000), s)


def inline(s: str) -> str:
    out, pos = [], 0
    for m in _CODE_SPAN.finditer(s):
        out.append(_emphasis(s[pos:m.start()]))
        out.append(f"{SPAN_ON} {m.group(2).strip()} {SPAN_OFF}")
        pos = m.end()
    out.append(_emphasis(s[pos:]))
    return "".join(out)


def _styled(style: str, off: str, text: str) -> str:
    """Aplica `style` a todo el texto aunque adentro haya estilos anidados que lo apaguen."""
    return style + text.replace(off, off + style) + off


# ---------- código ----------

def _highlight(line: str, lang: str) -> str:
    comment: Optional[str] = "#" if lang in _HASH_COMMENT else "//" if lang in _SLASH_COMMENT else "--" if lang == "sql" else None
    head, tail = line, ""
    if comment:
        # el comentario empieza en el primer marcador que no esté dentro de un string
        pos, quote = 0, None
        while pos < len(line):
            ch = line[pos]
            if quote:
                if ch == "\\":
                    pos += 1
                elif ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
            elif line.startswith(comment, pos):
                head, tail = line[:pos], line[pos:]
                break
            pos += 1

    def paint(m):
        kind = m.lastgroup
        color = {"str": GREEN, "kw": MAGENTA, "num": YELLOW}[kind]
        return f"{color}{m.group(0)}{FG_OFF}"
    out = _CODE_TOKEN.sub(paint, head)
    return out + (f"{GRAY}{tail}{FG_OFF}" if tail else "")


def _code_block(lines: List[str], lang: str, width: int) -> List[str]:
    lang = lang.lower()
    body = [l.expandtabs(4) for l in lines]
    w = min(max([len(l) for l in body] + [len(lang) + 1, 20]) + 2, max(width, 24))
    out = [f"{CODE_BG}{GRAY}{(' ' + lang).ljust(w)}{FG_OFF}{CODE_BG_OFF}" if lang else f"{CODE_BG}{' ' * w}{CODE_BG_OFF}"]
    for l in body:
        out.append(f"{CODE_BG} {_highlight(l, lang)}{' ' * max(0, w - len(l) - 1)}{CODE_BG_OFF}")
    out.append(f"{CODE_BG}{' ' * w}{CODE_BG_OFF}")
    return out


# ---------- tablas ----------

def _cells(line: str) -> List[str]:
    line = line.strip()
    line = line[1:] if line.startswith("|") else line
    line = line[:-1] if line.endswith("|") and not line.endswith("\\|") else line
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def _table(rows: List[str]) -> List[str]:
    has_header = len(rows) > 1 and _TABLE_SEP.match(rows[1]) is not None
    aligns = [("r" if c.endswith(":") and not c.startswith(":") else "c" if c.startswith(":") and c.endswith(":") else "l")
              for c in _cells(rows[1])] if has_header else []
    data = [_cells(r) for i, r in enumerate(rows) if not (has_header and i == 1)]
    n = max(len(r) for r in data)
    data = [r + [""] * (n - len(r)) for r in data]
    rendered = [[inline(c) for c in r] for r in data]
    widths = [max(_vlen(r[i]) for r in rendered) for i in range(n)]

    def pad(cell, i):
        gap = widths[i] - _vlen(cell)
        a = aligns[i] if i < len(aligns) else "l"
        return " " * gap + cell if a == "r" else " " * (gap // 2) + cell + " " * (gap - gap // 2) if a == "c" else cell + " " * gap

    bar = f"{GRAY}│{FG_OFF}"
    line = lambda l, m, r: f"{GRAY}{l}{m.join('─' * (w + 2) for w in widths)}{r}{FG_OFF}"
    out = [line("┌", "┬", "┐")]
    for k, r in enumerate(rendered):
        cells = [f" {BOLD}{pad(c, i)}{BOLD_OFF} " if has_header and k == 0 else f" {pad(c, i)} " for i, c in enumerate(r)]
        out.append(bar + bar.join(cells) + bar)
        if has_header and k == 0:
            out.append(line("├", "┼", "┤"))
    out.append(line("└", "┴", "┘"))
    return out


# ---------- ajuste de líneas ----------

def wrap(s: str, width: int, first: str = "", rest: str = "") -> List[str]:
    """Ajusta por palabras a `width` columnas visibles (los códigos ANSI no cuentan). `first`/`rest` son los prefijos
    de la primera línea y de las siguientes (sangría francesa en listas y citas)."""
    words = s.split(" ")
    lines, cur, cur_len = [], first, _vlen(first)
    fresh = True
    for w in words:
        wl = _vlen(w)
        if not fresh and cur_len + 1 + wl > width:
            lines.append(cur)
            cur, cur_len, fresh = rest, _vlen(rest), True
        if not fresh:
            cur, cur_len = cur + " ", cur_len + 1
        cur, cur_len, fresh = cur + w, cur_len + wl, False
    lines.append(cur)
    return lines


# ---------- documento ----------

def render(text: str, color: bool = True, width: Optional[int] = None) -> str:
    if not color or not text:
        return text
    width = (width or shutil.get_terminal_size((100, 24)).columns) - 1  # un margen para no depender del auto-wrap de la terminal
    lines, out, i = text.split("\n"), [], 0
    while i < len(lines):
        line = lines[i]
        if f := _FENCE.match(line):
            ch, n, lang = f.group(2)[0], len(f.group(2)), f.group(3)
            code, i = [], i + 1
            while i < len(lines) and not (lines[i].strip().startswith(ch * n) and not lines[i].strip().strip(ch)):
                code.append(lines[i])
                i += 1
            out += _code_block(code, lang, width)  # un bloque sin cerrar se muestra igual (típico de una respuesta cortada)
            i += 1
            continue
        if _TABLE.match(line):
            j = i
            while j < len(lines) and _TABLE.match(lines[j]):
                j += 1
            out += _table(lines[i:j])
            i = j
            continue
        if m := _HEADING.match(line):
            level, body = len(m.group(1)), inline(m.group(2)).replace(BOLD_OFF, "")
            body = body.replace(SPAN_OFF, SPAN_OFF + (MAGENTA if level < 4 else CYAN)).replace(FG_OFF, FG_OFF + (MAGENTA if level < 4 else CYAN))
            if level == 1:
                out += [f"{BOLD}{MAGENTA}{UNDER}{body}{RESET}", f"{GRAY}{'═' * min(_vlen(body), width)}{FG_OFF}"]
            elif level == 2:
                out += [f"{BOLD}{MAGENTA}{body}{RESET}", f"{GRAY}{'─' * min(_vlen(body), width)}{FG_OFF}"]
            else:
                out.append(f"{BOLD}{MAGENTA if level == 3 else CYAN}{body}{RESET}")
        elif _HR.match(line):
            out.append(f"{GRAY}{'─' * min(width, 60)}{FG_OFF}")
        elif m := _QUOTE.match(line):
            depth = m.group(1).count(">")
            bar = f"{GRAY}{'▎ ' * depth}{FG_OFF}"
            out += wrap(_styled(ITALIC, ITALIC_OFF, inline(m.group(2).strip())), width, bar, bar)
        elif m := _BULLET.match(line):
            indent, marker, body = m.group(1), m.group(2), m.group(3)
            if marker[0].isdigit():
                bullet = f"{YELLOW}{marker}{FG_OFF}"
            else:
                bullet = f"{YELLOW}{'•' if len(indent) < 2 else '◦' if len(indent) < 4 else '▪'}{FG_OFF}"
            if t := _TASK.match(body):
                bullet, body = (f"{GREEN}☑{FG_OFF}" if t.group(1) in "xX" else f"{GRAY}☐{FG_OFF}"), t.group(2)
            out += wrap(inline(body), width, f"{indent}{bullet} ", indent + " " * (_vlen(bullet) + 1))
        else:
            out += wrap(inline(line), width)
        i += 1
    return "\n".join(out)
