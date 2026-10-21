#!/usr/bin/env python3
"""Scan the repository for credential-like patterns. Exit with 1 on findings.

v3.2: skips binaries (by extension and by content) and `.b64` dumps; supports
an optional allowlist in `scripts/secret_scan_allowlist.txt` (one regular
expression per line, `#` for comments): matching findings are ignored and
documented in that file."""
import pathlib
import re
import sys

PATTERNS = [
    r"ghp_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"sk-[A-Za-z0-9]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"(?i)(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]",
    r"\b\d{2}\.\d{3}\.\d{3}\b.*(DNI|CUIL)",
    r"\b\d{22}\b",
]

SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__", "site-packages",
    ".next", "dist", "build", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
}
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".mp4", ".mov",
    ".pptx", ".docx", ".xlsx", ".woff", ".woff2", ".ttf", ".b64",
    ".dylib", ".so", ".dll", ".exe", ".jar", ".class", ".pyc", ".o", ".a", ".bin",
}
SKIP_FILES = {"scan_secrets.py", "install_hooks.sh", "secret_scan_allowlist.txt"}


def read_text(path):
    """Return file text, or None when the file looks binary."""
    try:
        with path.open("rb") as fh:
            head = fh.read(4096)
            if b"\x00" in head:
                return None
            rest = fh.read()
    except OSError:
        return None
    return (head + rest).decode("utf-8", errors="ignore")


def load_allowlist(root):
    """Allowlist rules (regex per line; plain text when regex compile fails)."""
    path = root / "scripts" / "secret_scan_allowlist.txt"
    rules = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            try:
                rules.append(re.compile(entry))
            except re.error:
                rules.append(re.compile(re.escape(entry)))
    return rules


def main() -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    allow = load_allowlist(root)
    bad = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.name in SKIP_FILES:
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        rel_dirs = path.relative_to(root).parts[:-1]
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel_dirs):
            continue
        text = read_text(path)
        if text is None:
            continue
        for pattern in PATTERNS:
            for match in re.finditer(pattern, text):
                token = match.group(0)
                if any(rule.search(token) for rule in allow):
                    continue
                print(f"[!] {path.relative_to(root)}: {token[:60]}")
                bad += 1
    print("OK: no secrets detected" if not bad else f"{bad} finding(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
