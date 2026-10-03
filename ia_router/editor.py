"""Caja de entrada del chat: un editor de línea propio (sin dependencias) con marco, placeholder, varias líneas,
historial, paleta de comandos "/" y soporte de archivos arrastrados (llegan como texto pegado con corchetes).

Piezas separadas para poder testearlas sin terminal:
  Parser  : bytes de la terminal -> eventos ("text", s) | ("key", nombre) | ("paste", s)
  State   : texto, cursor, historial, paleta; `apply(evento)` -> None | ("submit", texto) | ("eof",)
  layout/render_frame : estado + ancho -> líneas ya pintadas y posición del cursor
  LineEditor : el bucle real sobre un tty (modo raw, redibujado)
"""
from __future__ import annotations

import codecs
import json
import os
import re
import select
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import attachments as att_mod
from .banner import _lerp, _rgb
from .render import BOLD, DIM, GRAY, RESET, UNDER, _vlen

PROMPT = "❯ "
PLACEHOLDER = "Escribí una tarea o una preferencia · arrastrá archivos acá"
MAX_BODY_ROWS = 10
MAX_PALETTE = 6
HISTORY_MAX = 500


def cell_width(ch: str) -> int:
    if unicodedata.combining(ch) or ch in "​‍️":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(s: str) -> int:
    return sum(cell_width(c) for c in s)


# ---------- teclas ----------

_CSI_KEYS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "Z": "shift_tab"}
_TILDE_KEYS = {"1": "home", "3": "delete", "4": "end", "7": "home", "8": "end"}
_CTRL = {1: "home", 2: "left", 4: "eof", 5: "end", 6: "right", 8: "backspace", 9: "tab", 10: "newline", 11: "kill_eol", 12: "clear_screen",
         13: "enter", 14: "down", 16: "up", 21: "kill_bol", 23: "kill_word", 127: "backspace", 3: "interrupt"}


class Parser:
    """Convierte bytes en eventos. Mantiene estado entre llamadas (UTF-8 y secuencias de escape partidas)."""

    def __init__(self) -> None:
        self.dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.buf = ""
        self.paste: Optional[List[str]] = None

    def feed(self, data: bytes) -> List[Tuple[str, str]]:
        self.buf += self.dec.decode(data)
        return self._drain(final=False)

    def flush(self) -> List[Tuple[str, str]]:
        """Un ESC solo (sin nada detrás tras una pausa) es la tecla Esc."""
        return self._drain(final=True)

    def _drain(self, final: bool) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        text: List[str] = []

        def flush_text() -> None:
            if text:
                out.append(("text", "".join(text)))
                text.clear()

        while self.buf:
            if self.paste is not None:
                end = self.buf.find("\x1b[201~")
                if end < 0:
                    # puede venir cortado justo en el marcador: se guarda lo que no es prefijo del cierre
                    keep = max((k for k in range(1, 6) if "\x1b[201~".startswith(self.buf[-k:])), default=0)
                    self.paste.append(self.buf[: len(self.buf) - keep])
                    self.buf = self.buf[len(self.buf) - keep:]
                    break
                self.paste.append(self.buf[:end])
                self.buf = self.buf[end + 6:]
                flush_text()
                out.append(("paste", "".join(self.paste)))
                self.paste = None
                continue
            c = self.buf[0]
            if c == "\x1b":
                ev, used = self._escape(final)
                if used == 0:
                    break  # secuencia incompleta: esperar más bytes
                self.buf = self.buf[used:]
                if ev == ("paste_start", ""):
                    flush_text()
                    self.paste = []
                elif ev:
                    flush_text()
                    out.append(ev)
                continue
            self.buf = self.buf[1:]
            code = ord(c)
            if code < 32 or code == 127:
                flush_text()
                if code in _CTRL:
                    out.append(("key", _CTRL[code]))
            else:
                text.append(c)
        flush_text()
        return out

    def _escape(self, final: bool) -> Tuple[Optional[Tuple[str, str]], int]:
        b = self.buf
        if len(b) == 1:
            return (("key", "esc"), 1) if final else (None, 0)
        n = b[1]
        if n == "[":
            m = re.match(r"\x1b\[([0-9;?<>=]*)([ -/]*)([@-~])", b)
            if not m:
                return (None, 0) if not final else (None, len(b))
            params, final_ch, used = m.group(1), m.group(3), m.end()
            if final_ch == "~":
                if params == "200":
                    return ("paste_start", ""), used
                return (("key", _TILDE_KEYS[params.split(";")[0]]), used) if params.split(";")[0] in _TILDE_KEYS else (None, used)
            if final_ch == "u":  # protocolo kitty/CSI u: Enter con modificador = nueva línea
                p = params.split(";")
                if p[0] == "13":
                    return ("key", "newline" if len(p) > 1 and p[1] != "1" else "enter"), used
                return None, used
            if final_ch in _CSI_KEYS:
                mods = params.split(";")[1] if ";" in params else ""
                if final_ch in "CD" and mods in ("3", "5", "9"):  # alt/ctrl + flechas: salto de palabra
                    return ("key", "word_right" if final_ch == "C" else "word_left"), used
                return ("key", _CSI_KEYS[final_ch]), used
            return None, used
        if n == "O" and len(b) >= 3:
            return (("key", _CSI_KEYS.get(b[2], "")), 3) if b[2] in _CSI_KEYS else (None, 3)
        if n == "O":
            return (None, 0) if not final else (None, 2)
        alt = {"b": "word_left", "f": "word_right", "\r": "newline", "\n": "newline", "\x7f": "kill_word", "d": "kill_word_fwd"}
        if n in alt:
            return ("key", alt[n]), 2
        return ("key", "esc"), 1


# ---------- estado ----------

@dataclass
class Command:
    name: str  # con la barra: "/help"
    desc: str


class State:
    def __init__(self, history: Sequence[str] = (), commands: Sequence[Command] = (), cwd: Optional[str] = None) -> None:
        self.text = ""
        self.cur = 0
        self.history: List[str] = list(history)
        self.hidx: Optional[int] = None
        self.draft = ""
        self.commands = list(commands)
        self.sel = 0
        self.armed = False  # Ctrl-C con el campo vacío: el siguiente sale
        self.cwd = cwd
        self._att_key: Optional[str] = None
        self._att: List[att_mod.Attachment] = []
        self._spans: List[Tuple[int, int]] = []

    # --- derivados ---
    def attachments(self) -> List[att_mod.Attachment]:
        self._scan()
        return self._att

    def path_spans(self) -> List[Tuple[int, int]]:
        self._scan()
        return self._spans

    def _scan(self) -> None:
        if self._att_key != self.text:
            self._att_key = self.text
            self._att = att_mod.find(self.text, self.cwd)
            self._spans = att_mod.spans(self.text, self.cwd)

    def palette(self) -> List[Command]:
        if not self.commands or not self.text.startswith("/") or "\n" in self.text or " " in self.text:
            return []
        if att_mod.resolve(self.text, self.cwd):  # es la ruta de un archivo, no un comando
            return []
        return [c for c in self.commands if c.name.startswith(self.text.lower())]

    # --- edición ---
    def _set(self, text: str, cur: Optional[int] = None) -> None:
        self.text = text
        self.cur = len(text) if cur is None else cur
        self.sel = 0

    def insert(self, s: str) -> None:
        self._set(self.text[: self.cur] + s + self.text[self.cur:], self.cur + len(s))
        self.hidx = None

    def _line_start(self) -> int:
        return self.text.rfind("\n", 0, self.cur) + 1

    def _line_end(self) -> int:
        i = self.text.find("\n", self.cur)
        return len(self.text) if i < 0 else i

    def _word_left(self) -> int:
        i = self.cur
        while i > 0 and self.text[i - 1].isspace():
            i -= 1
        while i > 0 and not self.text[i - 1].isspace():
            i -= 1
        return i

    def _word_right(self) -> int:
        i, n = self.cur, len(self.text)
        while i < n and self.text[i].isspace():
            i += 1
        while i < n and not self.text[i].isspace():
            i += 1
        return i

    def _history(self, step: int) -> None:
        if not self.history:
            return
        if self.hidx is None:
            if step > 0:
                return
            self.draft, self.hidx = self.text, len(self.history)
        idx = self.hidx + step
        if idx >= len(self.history):
            self._set(self.draft)
            self.hidx = None
            return
        self.hidx = max(0, idx)
        self._set(self.history[self.hidx])

    def apply(self, ev: Tuple[str, str]) -> Optional[Tuple[str, ...]]:
        kind, val = ev
        if kind != "key" or val != "interrupt":
            self.armed = False
        if kind == "text":
            self.insert(val)
        elif kind == "paste":
            val = val.replace("\r\n", "\n").replace("\r", "\n")
            self.insert(att_mod.normalize_paste(val, self.cwd))
        elif kind == "key":
            return self._key(val)
        return None

    def _key(self, k: str) -> Optional[Tuple[str, ...]]:
        t, c = self.text, self.cur
        if k == "enter":
            if t[:c].endswith("\\"):  # "\" + Enter = nueva línea (como Claude Code)
                self._set(t[: c - 1] + "\n" + t[c:], c)
                return None
            pal = self.palette()
            if pal and t.lower() not in [p.name for p in pal]:
                self._set(pal[self.sel % len(pal)].name + " ")
                return None
            return ("submit", t)
        if k == "newline":
            self.insert("\n")
        elif k == "backspace":
            if c:
                self._set(t[: c - 1] + t[c:], c - 1)
        elif k == "delete":
            if c < len(t):
                self._set(t[:c] + t[c + 1:], c)
        elif k == "left":
            self.cur = max(0, c - 1)
        elif k == "right":
            self.cur = min(len(t), c + 1)
        elif k == "home":
            self.cur = self._line_start()
        elif k == "end":
            self.cur = self._line_end()
        elif k == "word_left":
            self.cur = self._word_left()
        elif k == "word_right":
            self.cur = self._word_right()
        elif k == "kill_eol":
            e = self._line_end()
            self._set(t[:c] + (t[e + 1:] if e == c and e < len(t) else t[e:]), c)
        elif k == "kill_bol":
            start = self._line_start()
            self._set(t[:start] + t[c:], start)
        elif k == "kill_word":
            w = self._word_left()
            self._set(t[:w] + t[c:], w)
        elif k == "kill_word_fwd":
            self._set(t[:c] + t[self._word_right():], c)
        elif k in ("up", "down"):
            pal = self.palette()
            if pal:
                self.sel = (self.sel + (1 if k == "down" else -1)) % len(pal)
            elif "\n" in t and ((k == "up" and t.find("\n", 0, c) >= 0) or (k == "down" and t.find("\n", c) >= 0)):
                self._move_line(-1 if k == "up" else 1)
            else:
                self._history(-1 if k == "up" else 1)
        elif k == "tab":
            pal = self.palette()
            if pal:
                self._set(pal[self.sel % len(pal)].name + " ")
        elif k == "eof":
            if not t:
                return ("eof",)
            self._key("delete")
        elif k == "interrupt":
            if t:
                self._set("")
                self.hidx = None
            elif self.armed:
                return ("eof",)
            else:
                self.armed = True
        elif k == "esc":
            self.sel = 0
        return None

    def _move_line(self, step: int) -> None:
        t = self.text
        start = self._line_start()
        col = self.cur - start
        if step < 0:
            pe = start - 1
            ps = t.rfind("\n", 0, pe) + 1
            self.cur = min(ps + col, pe)
        else:
            ns = self._line_end() + 1
            ne = t.find("\n", ns)
            ne = len(t) if ne < 0 else ne
            self.cur = min(ns + col, ne)


# ---------- dibujo ----------

@dataclass
class Frame:
    lines: List[str]
    cursor: Tuple[int, int]  # (fila, columna) dentro del frame


def _grad(s: str, x0: int, total: int, color: bool) -> str:
    if not color:
        return s
    return "".join(f"{_rgb(_lerp((x0 + i) / max(1, total - 1)))}{ch}" for i, ch in enumerate(s)) + RESET


def layout(text: str, cur: int, area: int, spans: Sequence[Tuple[int, int]]):
    """Parte el texto en filas de `area` celdas. Devuelve (filas, (fila, col) del cursor); cada fila = [(char, en_ruta)]."""
    marked = set()
    for s, e in spans:
        marked.update(range(s, e))
    rows: List[List[Tuple[str, bool]]] = [[]]
    col, cursor = 0, (0, 0)
    for i, ch in enumerate(text):
        if ch == "\n":
            if i == cur:
                cursor = (len(rows) - 1, col)
            rows.append([])
            col = 0
            continue
        w = cell_width(ch)
        if col + w > area:
            rows.append([])
            col = 0
        if i == cur:
            cursor = (len(rows) - 1, col)
        rows[-1].append((ch, i in marked))
        col += w
    if cur >= len(text):
        if col >= area:
            rows.append([])
            col = 0
        cursor = (len(rows) - 1, col)
    return rows, cursor


def _row_text(row: Sequence[Tuple[str, bool]], color: bool) -> str:
    out, on = [], False
    for ch, in_path in row:
        if color and in_path != on:
            out.append(f"{UNDER}{_rgb((99, 168, 248))}" if in_path else RESET)
            on = in_path
        out.append(ch)
    if on:
        out.append(RESET)
    return "".join(out)


def box_width(cols: int) -> int:
    return max(30, min(cols - 2, 110))


def render_frame(st: State, cols: int, rows_avail: int = 30, model: str = "auto", status: str = "", color: bool = True) -> Frame:
    W = box_width(cols)
    area = W - 4 - len(PROMPT)
    spans = st.path_spans()
    rows, (crow, ccol) = layout(st.text, st.cur, area, spans)
    max_rows = max(1, min(MAX_BODY_ROWS, rows_avail - 8))
    top = 0
    if len(rows) > max_rows:
        top = min(max(0, crow - max_rows + 1), len(rows) - max_rows)
        if crow < top:
            top = crow
    window = rows[top: top + max_rows]
    dim = (lambda s: f"{DIM}{s}{RESET}") if color else (lambda s: s)

    label = f" {model} " if model else ""
    if top:
        label = f" ↑{top} " + label
    hidden_below = len(rows) - (top + len(window))
    if hidden_below > 0:
        label = f" ↓{hidden_below} " + label
    fill = max(1, W - 2 - 1 - len(label))
    top_line = _grad("╭" + "─" * fill, 0, W, color) + (dim(label) if label else "") + _grad("─╮", W - 2, W, color)
    left, right = _grad("│", 0, W, color), _grad("│", W - 1, W, color)
    marker = f"{_rgb(_lerp(0.5), True)}{PROMPT}{RESET}" if color else PROMPT

    body = []
    for k, row in enumerate(window):
        first = top + k == 0
        if first and not st.text:
            content = dim(PLACEHOLDER[:area])
            used = min(len(PLACEHOLDER), area)
        else:
            content = _row_text(row, color)
            used = sum(cell_width(ch) for ch, _ in row)
        lead = marker if first else "  "
        body.append(f"{left} {lead}{content}{' ' * max(0, area - used)} {right}")
    bottom = _grad("╰" + "─" * (W - 2) + "╯", 0, W, color)

    under: List[str] = []
    atts = st.attachments()
    for a in atts[:3]:
        under.append("  " + dim(f"⎘ {a.label()}"))
    if len(atts) > 3:
        under.append("  " + dim(f"⎘ +{len(atts) - 3} más"))
    pal = st.palette()
    for i, c in enumerate(pal[:MAX_PALETTE]):
        on = i == st.sel % len(pal)
        name = f"{c.name:<12}"
        if color:
            under.append("  " + (f"{BOLD}{_rgb(_lerp(0.5))}▸ {name}{RESET} {c.desc}" if on else f"  {dim(name)} {dim(c.desc)}"))
        else:
            under.append("  " + ("▸ " if on else "  ") + f"{name} {c.desc}")
    if st.armed:
        hint = dim("Ctrl-C otra vez para salir")
    else:
        full = "⏎ enviar · ⌥⏎ o \\⏎ nueva línea · / comandos · ⌃D salir"
        short = "⏎ enviar · / comandos"
        room = W - 2 - (len(status) + 2 if status else 0)
        hint = dim(full if len(full) <= room else short if len(short) <= room else "")
    gap = max(1, W - 2 - _vlen(hint) - len(status)) if status else 0
    under.append("  " + hint + (" " * gap + dim(status) if status else ""))

    lines = [top_line] + body + [bottom] + under
    return Frame(lines, (1 + (crow - top), 2 + len(PROMPT) + ccol))


def echo_lines(text: str, spans: Sequence[Tuple[int, int]], atts: Sequence[att_mod.Attachment], cols: int, color: bool = True) -> List[str]:
    """Cómo queda el mensaje enviado en el historial de la terminal."""
    area = max(10, box_width(cols) - len(PROMPT))
    rows, _ = layout(text, 0, area, spans)
    marker = f"{_rgb(_lerp(0.5), True)}{PROMPT}{RESET}" if color else PROMPT
    out = [(marker if i == 0 else "  ") + _row_text(r, color) for i, r in enumerate(rows)]
    dim = (lambda s: f"{DIM}{s}{RESET}") if color else (lambda s: s)
    out += ["  " + dim(f"⎘ {a.label()}") for a in atts]
    return out


# ---------- historial en disco ----------

def load_history(path: Path) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for l in lines[-HISTORY_MAX:]:
        try:
            v = json.loads(l)
            if isinstance(v, str) and v:
                out.append(v)
        except ValueError:
            pass
    return out


def save_history(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(text, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------- terminal real ----------

def supported() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get("TERM", "") not in ("", "dumb") and shutil.get_terminal_size((80, 24)).columns >= 40


class LineEditor:
    def __init__(self, commands: Sequence[Command] = (), model: Callable[[], str] = lambda: "auto", status: Callable[[], str] = lambda: "",
                 history_path: Optional[Path] = None, color: bool = True) -> None:
        self.commands, self.model, self.status = list(commands), model, status
        self.history_path, self.color = history_path, color
        self.history = load_history(history_path) if history_path else []
        self._rows = 0  # filas del último frame y fila del cursor, para borrarlo al redibujar
        self._crow = 0

    # --- salida ---
    def _write(self, s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    def _clear(self) -> None:
        if self._rows:
            self._write((f"\033[{self._crow}A" if self._crow else "") + "\r\033[J")
        self._rows = self._crow = 0

    def _draw(self, st: State) -> None:
        size = shutil.get_terminal_size((80, 24))
        fr = render_frame(st, size.columns, size.lines, self.model(), self.status(), self.color)
        self._clear()
        self._write("\r\n".join(fr.lines))
        up = len(fr.lines) - 1 - fr.cursor[0]
        self._write((f"\033[{up}A" if up else "") + f"\r\033[{fr.cursor[1] + 1}G")
        self._rows, self._crow = len(fr.lines), fr.cursor[0]

    # --- lectura ---
    def read(self) -> str:
        """Devuelve el mensaje. Lanza EOFError con Ctrl-D (campo vacío) o Ctrl-C dos veces."""
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        new[0] &= ~(termios.IXON | termios.ICRNL | termios.INLCR | termios.IGNCR)
        new[3] &= ~(termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN)
        new[6][termios.VMIN], new[6][termios.VTIME] = 1, 0
        st, parser = State(self.history, self.commands, att_mod.safe_cwd()), Parser()
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, new)
            self._write("\033[?2004h")  # paste con corchetes: así llegan los archivos arrastrados
            self._draw(st)
            while True:
                ready = select.select([fd], [], [], 0.05 if parser.buf else None)[0]
                events = parser.feed(os.read(fd, 65536)) if ready else parser.flush()
                if not ready and not events:
                    continue
                for ev in events:
                    res = st.apply(ev)
                    if ev == ("key", "clear_screen"):
                        self._write("\033[2J\033[H")
                        self._rows = self._crow = 0
                    if res and res[0] == "eof":
                        self._clear()
                        raise EOFError
                    if res and res[0] == "submit":
                        return self._finish(st)
                self._draw(st)
        finally:
            self._write("\033[?2004l")
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def _finish(self, st: State) -> str:
        self._clear()
        cols = shutil.get_terminal_size((80, 24)).columns
        self._write("\r\n".join(echo_lines(st.text, st.path_spans(), st.attachments(), cols, self.color)) + "\r\n\r\n")
        text = st.text.strip()
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
            if self.history_path:
                save_history(self.history_path, text)
        return st.text
