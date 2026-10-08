"""Live output for a streaming answer: shows the text as the model writes it, then (in the chat) swaps it for the rendered markdown.

While streaming, the text is shown raw. When the answer is complete, if everything still fits on the screen the raw rows are erased and
the rendered version takes their place; if the answer is taller than the terminal it cannot be erased, so it is left as it is.
Pure row accounting (`advance`) plus a small writer; tested with a fake terminal.
"""
from __future__ import annotations

import shutil
import sys
from typing import Callable, Optional, TextIO, Tuple

from .editor import text_width


def advance(row: int, col: int, text: str, columns: int) -> Tuple[int, int]:
    """Where the cursor ends up (rows below the start, column) after printing `text`, with wrapping at `columns`."""
    for ch in text:
        if ch == "\n":
            row, col = row + 1, 0
            continue
        w = max(1, text_width(ch))
        if col + w > columns:
            row, col = row + 1, 0
        col += w
    return row, col


class LiveOutput:
    def __init__(self, out: Optional[TextIO] = None, columns: Optional[int] = None, rows: Optional[int] = None, dim: Callable[[str], str] = lambda s: s) -> None:
        size = shutil.get_terminal_size((100, 30))
        self.out, self.columns, self.rows, self.dim = out or sys.stdout, columns or size.columns, rows or size.lines, dim
        self.row = self.col = 0
        self.text = ""

    def _write(self, s: str) -> None:
        self.out.write(s)
        self.out.flush()
        self.row, self.col = advance(self.row, self.col, s, self.columns)

    def on_text(self, delta: str) -> None:
        self.text += delta
        self._write(delta)

    def on_status(self, what: str) -> None:
        """What the model is doing (a tool call), on its own line."""
        if self.col:
            self._write("\n")
        self._write(self.dim(f"⚙ {what}") + "\n")

    def fits(self) -> bool:
        return self.row + 2 <= self.rows

    def erase(self) -> bool:
        """Removes everything printed so far, if it is still on screen. Returns whether it did."""
        if not self.fits():
            return False
        self.out.write("\r" + (f"\033[{self.row}A" if self.row else "") + "\033[J")
        self.out.flush()
        self.row = self.col = 0
        return True

    def reset(self) -> None:
        """An attempt failed after writing: drop its text (the next model starts clean)."""
        if self.text or self.row or self.col:
            if not self.erase():
                self._write("\n")
            self.text = ""
