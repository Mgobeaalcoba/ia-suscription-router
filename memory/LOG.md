# Session log

> New entries at the top. Format: YYYY-MM-DD · agent · title. Append-only history: old entries are never edited.

## 2026-10-27 · Claude · real-CLI guard in tests, docs, 0.10.0

- `adapters.guard_real_cli` refuses `claude`/`codex`/`agy` outside `tests/fake_bin` when `IA_ROUTER_BLOCK_REAL_CLIS` is set; checked at both launches and the probe. Every test module sets it at import. Suite: 452 tests, OK, ~44 s.
- Docs updated for the guard and counts (AGENTS, CONTRIBUTING, README, USAGE). Captured outputs in USAGE stay as they were (2026-10-05 data).
- Released 0.10.0.

## 2026-10-27 · Claude · Arena from the official dataset, background update, 0.9.0

- arena.ai answers a Cloudflare challenge (403, `cf-mitigated: challenge`) to the snapshot script: not bypassed. Source moved to the CC BY 4.0 dataset through datasets-server (`metrics.fetch_arena`): 46 categories, 12,930 rows, ~10 min per full read.
- Validated against the bundled snapshot: same top models, same matching for the user's models. Prices are matched by normalized name (slug vs display name): 282 of 418 carried over.
- Chat update runs in a thread (`metrics.refresh_in_background`) after yes; `active()` picks the new cache on the next call.
- Test isolation bug: `test_setup` restored an import-time PATH and dropped `tests/fake_bin` for later modules; `test_chat` then ran the REAL claude/codex/agy (quota spent: one turn reported ~382k input tokens). Fixed; the full suite now runs in ~44 s instead of 220-490 s. A chat test with a stale fixture date also started a real download: fixed, and network guards added in `test_chat` and `test_setup`.
- 0.9.0 released (see CHANGELOG).

## 2026-10-27 · Claude · agy commands only with explicit approval

- Owner request: agy may run commands, but only with explicit per-command approval in the chat.
- Design: agy never executes. It asks with `RUN: <command>` (`adapters.AGY_PROTOCOL`, `requested_command`); `core.ask(approve=...)` shows the command and runs it once with `core.run_command` (no shell, timeout 60 s, output capped) only on a yes; the model gets the output in a second call (max 2 rounds). No approver (MCP server, scripts) means nothing runs. No persistent grant is written to agy's config.
- Surfaces: chat (`approve_command`, y/N), CLI `ask` on a terminal (`_approve_cli`, y/yes only), UI (`approval` NDJSON event, `POST /api/approve`, Allow once / Deny buttons; 300 s wait).
- Verified: real agy end to end (ROUTER_HOME separate): asked `git branch --show-current`, approval requested, ran, answered `main`. Fake-agy tests for approve, refuse, no approver, no shell.
- Not committed.

## 2026-10-27 · Claude · agy shell-command denials fixed at the source

- Owner authorized real agy calls. Probes (ROUTER_HOME separate, tiny prompts): the denied action is `command`/`RunCommand`; the JSON reports it in `denied_actions` with `status: SUCCESS` and an empty answer.
- Real grants live in `~/.gemini/config/config.json` → `userSettings.globalPermissionGrants.allow` (not in `antigravity-cli/settings.json`). They list some commands (ls, grep, cat, jq…), not git. One entry is multi-line and ignored by agy.
- `--mode plan` does NOT stop the request (denied the same way). An instruction does: "Do not run shell commands. If you would need one, say so in the answer instead." Verified on the real CLI, and end to end through `core.ask`: ok, no denial.
- Code: `adapters.AGY_NO_SHELL` prepended for agy in `run_cli`; `parse_usage` reads `denied_actions` as a permission denial. Tests: `test_core` (3 new).
- Trade-off: agy no longer runs the granted shell commands either; it names them in the answer. MCP connectors are unaffected.
- Not committed.

## 2026-10-27 · Claude · agy permission_denied handling

- Fault: Antigravity (`agy`) in headless mode auto-denied a tool that needed a `command` permission; the model produced no output and the attempt showed a raw error. Intermittent: the same task failed once and succeeded on the next run (about 50 s later, per the router log).
- Change: `adapters.PERMISSION_RE` classifies it as `permission_denied`; `core.ask` falls through with no cooldown and sets `result["hint"]` (`core.PERMISSION_HINT`), shown in chat, CLI stderr and the UI. Fake agy mode `permission`; test in `test_core`.
- Verified: full suite 437 tests OK (python3). No real agy call made: no quota spent.
- Pending: root-cause fix needs owner approval for one tiny real `agy` run to learn the exact command, and to test whether `--mode plan` prevents tool requests.
- Not committed.

## 2026-10-17 · OpenCode · Pre-commit hook active

- Mariano approved option 1 (2026-10-17): resolve findings with an allowlist and install the hook.
- Findings reviewed: template placeholders (`${...}` and `__TOKEN__` styles); no real credentials. Allowed via `scripts/secret_scan_allowlist.txt`.
- Scanner updated to v3.2 (skips binaries and `.b64` dumps; allowlist support).

## 2026-10-17 · OpenCode · initial agentification

- Repo reviewed against the agentification standard; only missing pieces were added.
- Changes: AGENTS.md (if missing), multi-agent bridges, memory/STATE.md + memory/LOG.md, scripts/scan_secrets.py (hook not installed: pre-existing findings).
- Pending: nothing recorded.
