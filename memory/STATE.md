# STATE — ia-suscription-router

> Current snapshot. Updated at every close; history lives in LOG.md.

**Updated:** 2026-10-27 · Arena dataset + background update, 0.9.0

## State

- Local-first router across AI subscription CLIs (Arena metrics, stdlib-only).
- Agentified on 2026-10-17: AGENTS.md, multi-agent bridges (CLAUDE.md, GEMINI.md, .github/copilot-instructions.md), this memory (STATE.md + LOG.md) and scripts/scan_secrets.py (pre-commit hook installed on 2026-10-17; findings reviewed: placeholders/template tokens, not real credentials). History in `LOG.md`.

## Next step

1. Consider a guard that makes any real CLI call fail during tests (the isolation bug went unnoticed for a while).

## Pending

- [x] Secret-scan findings reviewed (placeholders/template tokens) and pre-commit hook installed — 2026-10-17. Allowlist: `scripts/secret_scan_allowlist.txt`.

## Blockers

- (none for agy: the shell-command denial is avoided by the instruction and handled by fallback + hint.)
