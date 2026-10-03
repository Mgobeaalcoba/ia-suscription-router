"""Archivos adjuntos: al arrastrar un archivo a la terminal, ésta pega su ruta como texto (con espacios escapados,
entre comillas o como file://). Este módulo reconoce esas rutas, las clasifica y las normaliza para mostrarlas.

El texto del mensaje no se modifica: las rutas siguen ahí, igual que en Claude Code y Codex. Lo que cambia es que
el router sabe cuáles son archivos reales para anexarlos a la tarea (texto) o referenciarlos (imágenes, PDF, etc.).
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
MAX_INLINE_BYTES = 200_000  # por archivo de texto; el límite total lo pone core.MAX_CONTEXT_CHARS


def safe_cwd() -> str:
    """La carpeta actual; si fue borrada mientras el chat estaba abierto, la del usuario."""
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
        kinds = {"image": "imagen", "pdf": "PDF", "text": "texto", "dir": "carpeta", "binary": "binario"}
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


# ---------- reconocimiento de rutas ----------

def tokens(text: str) -> List[Tuple[int, int, str]]:
    """Parte `text` en palabras estilo shell: (inicio, fin, valor) respetando comillas y `\\ ` (espacio escapado)."""
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
    """Devuelve la ruta existente a la que apunta `value` (absoluta, ~, file:// o relativa explícita), o None."""
    v = value.strip()
    if not v or len(v) > 4096:
        return None
    if v.startswith("file://"):
        v = unquote(urlparse(v).path)
    elif v.startswith("@"):  # menciones estilo @ruta
        v = v[1:]
    if not (v.startswith(("/", "~", "./", "../")) or re.match(r"^[A-Za-z]:[\\/]", v)):
        return None  # una palabra suelta como "hola" no es una ruta aunque exista un archivo con ese nombre
    p = Path(os.path.expanduser(v))
    if not p.is_absolute():
        p = Path(cwd or safe_cwd()) / p
    try:
        return Path(os.path.abspath(p)) if p.exists() else None  # sin resolver symlinks: se ve la ruta tal cual se arrastró
    except OSError:
        return None


def find(text: str, cwd: Optional[str] = None) -> List[Attachment]:
    """Archivos o carpetas reales mencionados en `text`, sin repetidos y en orden de aparición."""
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
    """Rangos (inicio, fin) del texto que son rutas existentes; el editor los resalta."""
    return [(s, e) for s, e, v in tokens(text) if resolve(v, cwd)]


def normalize_paste(text: str, cwd: Optional[str] = None) -> str:
    """Si todo lo pegado son rutas existentes (lo que pasa al arrastrar archivos), las deja limpias:
    absolutas, sin escapes, entre comillas solo si tienen espacios y con un espacio al final. Si no, devuelve `text` igual."""
    toks = tokens(text.strip())
    if not toks:
        return text
    paths = [resolve(v, cwd) for _, _, v in toks]
    if any(p is None for p in paths):
        return text
    return " ".join(quote(str(p)) for p in paths) + " "


def quote(path: str) -> str:
    return f'"{path}"' if re.search(r"\s", path) else path


# ---------- armado de la tarea ----------

def split_for_prompt(atts: List[Attachment]) -> Tuple[List[str], List[Attachment]]:
    """(archivos de texto para inlinear como contexto, el resto para referenciar por ruta)."""
    text = [str(a.path) for a in atts if a.kind == "text" and a.size <= MAX_INLINE_BYTES]
    rest = [a for a in atts if not (a.kind == "text" and a.size <= MAX_INLINE_BYTES)]
    return text, rest


def reference_block(atts: List[Attachment]) -> str:
    """Texto que se agrega al prompt para los adjuntos que no se inlinean (el modelo los abre por ruta)."""
    if not atts:
        return ""
    lines = ["", "--- ARCHIVOS ADJUNTOS (abrilos por su ruta) ---"]
    lines += [f"- {a.path}  [{a.kind}, {human_size(a.size)}]" for a in atts]
    return "\n".join(lines)
