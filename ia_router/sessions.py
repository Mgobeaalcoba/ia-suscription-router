"""Saved chat sessions, so a conversation survives closing the terminal: `ia-router --continue`, `/sessions`, `/resume`.

Unlike `log.jsonl` (which never stores prompts), a session DOES store what you and the model said, because resuming needs it. So it is
local only, files readable just by you (0600), capped, easy to delete (`ia-router sessions delete ID|clear`) and easy to turn off
(`ROUTER_NO_SESSIONS=1` or `/sessions off`). Nothing leaves your machine.

One JSON file per session in ~/.ia-router/sessions/. Pure helpers plus small file I/O; no terminal code.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from typing import Dict, List, Optional, Tuple

from . import state

MAX_ANSWER_CHARS = 20_000      # a stored answer is cut here (the chat only replays 1,500 characters of each anyway)
MAX_TURNS = 200
_ID = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{4}$")


def sessions_dir():
    return state.home() / "sessions"


def enabled() -> bool:
    return not os.environ.get("ROUTER_NO_SESSIONS") and not state.flags().get("sessions_off")


def new_session(cwd: str = "") -> Dict:
    return {"id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4], "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated": "", "cwd": cwd, "pinned": "auto", "turns": []}


def _path(sid: str):
    if not _ID.match(sid or ""):
        raise ValueError(f"not a session id: {sid}")
    return sessions_dir() / f"{sid}.json"


def save(session: Dict) -> None:
    """Writes the session (readable only by its owner). Never raises: losing a save must not break the chat."""
    if not enabled() or not session.get("turns"):
        return
    session["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    session["turns"] = [dict(t, answer=t.get("answer", "")[:MAX_ANSWER_CHARS]) for t in session["turns"][-MAX_TURNS:]]
    try:
        sessions_dir().mkdir(parents=True, exist_ok=True)
        path = _path(session["id"])
        tmp = path.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(session, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except (OSError, ValueError):
        pass


def load(sid: str) -> Optional[Dict]:
    try:
        d = json.loads(_path(sid).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and isinstance(d.get("turns"), list) else None
    except (OSError, ValueError):
        return None


def list_sessions(limit: int = 20) -> List[Dict]:
    """Newest first: {id, updated, turns, title, cwd}."""
    out = []
    try:
        names = [p for p in sessions_dir().glob("*.json") if _ID.match(p.stem)]
    except OSError:
        return []
    for p in names:
        d = load(p.stem)
        if d and d["turns"]:
            first = str(d["turns"][0].get("user", "")).strip().replace("\n", " ")
            out.append({"id": d["id"], "updated": d.get("updated", ""), "turns": len(d["turns"]), "title": first[:60] + ("…" if len(first) > 60 else ""),
                        "cwd": d.get("cwd", "")})
    return sorted(out, key=lambda s: s["updated"], reverse=True)[:limit]


def latest() -> Optional[Dict]:
    ids = list_sessions(1)
    return load(ids[0]["id"]) if ids else None


def delete(sid: str) -> bool:
    try:
        _path(sid).unlink()
        return True
    except (OSError, ValueError):
        return False


def clear() -> int:
    n = 0
    for s in list_sessions(10_000):
        n += 1 if delete(s["id"]) else 0
    return n


def to_history(session: Dict) -> List[Tuple[str, str, str]]:
    """The (user, model, answer) tuples the chat replays as context."""
    return [(str(t.get("user", "")), str(t.get("model", "")), str(t.get("answer", ""))) for t in session.get("turns", [])]


def add_turn(session: Dict, user: str, model: str, answer: str) -> None:
    session["turns"].append({"user": user, "model": model, "answer": answer, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")})
