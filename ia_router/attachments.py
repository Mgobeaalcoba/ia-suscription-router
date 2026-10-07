"""File attachments: when a file is dragged onto the terminal, it pastes its path as text (with escaped spaces,
quoted or as file://). This module recognizes those paths, classifies them and normalizes them for display.

The message text is not modified: the paths stay there, just like in Claude Code and Codex. What changes is that
the router knows which ones are real files so it can attach them to the task (text) or reference them (images, PDFs, etc.).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote, urlparse

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif", ".tif", ".tiff", ".svg"}
PDF_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".html",
            ".css", ".js", ".ts", ".tsx", ".jsx", ".py", ".ipynb", ".sql", ".sh", ".zsh", ".bash", ".go", ".rs", ".java", ".c", ".h", ".cpp",
            ".rb", ".php", ".swift", ".kt", ".r", ".log", ".env", ".tex", ".lock"}
MAX_INLINE_BYTES = 200_000  # per text file; the total limit is set by core.MAX_CONTEXT_CHARS


def safe_cwd() -> str:
    """The current folder; if it was deleted while the chat was open, the user's home."""
    try:
        return os.getcwd()
    except OSError:
        return os.path.expanduser("~")


@dataclass
class Attachment:
    path: Path
    kind: str  # image | pdf | text | dir | binary
    size: int

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

    def label(self) -> str:
        kinds = {"image": "image", "pdf": "PDF", "text": "text", "dir": "folder", "binary": "binary"}
        return f"{self.name} · {kinds[self.kind]}" + (f" · {human_size(self.size)}" if self.kind != "dir" else "")


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def classify(p: Path) -> str:
    if p.is_dir():
        return "dir"
    ext = p.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    try:
        with open(p, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return "binary"
    if b"\0" in head:
        return "binary"
    try:
        head.decode("utf-8")
        return "text"
    except UnicodeDecodeError:
        return "binary"


# ---------- path recognition ----------

def tokens(text: str) -> List[Tuple[int, int, str]]:
    """Splits `text` into shell-style words: (start, end, value) respecting quotes and `\\ ` (escaped space)."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        start, buf, quote = i, [], None
        while i < n:
            c = text[i]
            if quote:
                if c == quote:
                    quote = None
                else:
                    buf.append(c)
            elif c in "\"'":
                quote = c
            elif c == "\\" and i + 1 < n:
                i += 1
                buf.append(text[i])
            elif c.isspace():
                break
            else:
                buf.append(c)
            i += 1
        out.append((start, i, "".join(buf)))
    return out


def resolve(value: str, cwd: Optional[str] = None) -> Optional[Path]:
    """Returns the existing path that `value` points to (absolute, ~, file:// or explicit relative), or None."""
    v = value.strip()
    if not v or len(v) > 4096:
        return None
    if v.startswith("file://"):
        v = unquote(urlparse(v).path)
    elif v.startswith("@"):  # @path-style mentions
        v = v[1:]
    if not (v.startswith(("/", "~", "./", "../")) or re.match(r"^[A-Za-z]:[\\/]", v)):
        return None  # a bare word like "hello" is not a path even if a file with that name exists
    p = Path(os.path.expanduser(v))
    if not p.is_absolute():
        p = Path(cwd or safe_cwd()) / p
    try:
        return Path(os.path.abspath(p)) if p.exists() else None  # without resolving symlinks: the path is shown exactly as it was dragged
    except OSError:
        return None


def find(text: str, cwd: Optional[str] = None) -> List[Attachment]:
    """Real files or folders mentioned in `text`, without duplicates and in order of appearance."""
    seen, found = set(), []
    for _, _, value in tokens(text):
        p = resolve(value, cwd)
        if p and p not in seen:
            seen.add(p)
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            found.append(Attachment(p, classify(p), size))
    return found


def spans(text: str, cwd: Optional[str] = None) -> List[Tuple[int, int]]:
    """Ranges (start, end) of the text that are existing paths; the editor highlights them."""
    return [(s, e) for s, e, v in tokens(text) if resolve(v, cwd)]


def normalize_paste(text: str, cwd: Optional[str] = None) -> str:
    """If everything pasted is existing paths (what happens when dragging files), it leaves them clean:
    absolute, unescaped, quoted only if they contain spaces and with a trailing space. Otherwise it returns `text` unchanged."""
    toks = tokens(text.strip())
    if not toks:
        return text
    paths = [resolve(v, cwd) for _, _, v in toks]
    if any(p is None for p in paths):
        return text
    return " ".join(quote(str(p)) for p in paths) + " "


def quote(path: str) -> str:
    return f'"{path}"' if re.search(r"\s", path) else path


# ---------- task assembly ----------

def split_for_prompt(atts: List[Attachment]) -> Tuple[List[str], List[Attachment]]:
    """(text files to inline as context, the rest to reference by path)."""
    text = [str(a.path) for a in atts if a.kind == "text" and a.size <= MAX_INLINE_BYTES]
    rest = [a for a in atts if not (a.kind == "text" and a.size <= MAX_INLINE_BYTES)]
    return text, rest


def reference_block(atts: List[Attachment]) -> str:
    """Text added to the prompt for the attachments that are not inlined (the model opens them by path)."""
    if not atts:
        return ""
    lines = ["", "--- ATTACHED FILES (open them by path) ---"]
    lines += [f"- {a.path}  [{a.kind}, {human_size(a.size)}]" for a in atts]
    return "\n".join(lines)
