# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions are published on [PyPI](https://pypi.org/project/ia-router/) and [Homebrew](https://github.com/Mgobeaalcoba/homebrew-tap).

## 0.7.0 — 2026-10-08

### Added
- **`ia-router ui`: the router in a browser tab.** A local chat page (no new dependencies, nothing to install) with the same engine as the terminal chat: conversations grouped by date, a welcome screen with starter prompts, streaming answers rendered as markdown with copy buttons, a model picker, connectors auto/all/none, **Compare**, and a status dialog (which CLIs are ready and the next step if not, usage, connectors). It shares sessions with the chat. Local only: it listens on `127.0.0.1` and every request needs a per-run token. It carries the terminal header's identity (the three-provider mark, the gradient wordmark, the tagline and the "by Mgobeaalcoba · mgatc.com" credit with links), taken from the same source. 
- **CLI and UI do the same things.** The UI also has attachments (the Attach button, drag and drop, or a path in the message), a routing preview and a "Why this model" note on every answer, Compare with two chosen models, a Settings dialog (CLI status and login check, usage, history per model, scores per category, priorities, metrics update with live progress, saved-conversation controls), and a connector manager: add from a template, a free local command (shown to you and confirmed before it is saved; it is never run through a shell) or a URL, test, turn on or off, remove, and register in Antigravity.
- `ia-router priorities --set GROUP=OPTION` and `--show` (no terminal needed), and `ia-router sessions on|off`, so what the UI settings do can also be done from the shell.

## 0.6.0 — 2026-10-08

### Added
- **Connectors only when the task needs them.** In automatic mode a task gets just the connectors it needs (a Gmail connector for an email task, none for a coding task), instead of every connector on every call: less token cost and startup time. Built-in keywords in English and Spanish, per-connector `when` words and `always: true`, and connectors the router cannot classify are always attached. `/connectors auto|on|off` and `ask --all-connectors`/`--no-connectors`.
- **Script mode.** `ask` reads piped input (`cat error.log | ia-router ask "what is wrong?"`, or `ask -`), and has `--json` (answer, model, tokens, estimated list-price cost, routing, attempts), `--raw` (only the answer on stdout), `--quiet` and `--stdin`.
- **Usage meter.** `ia-router usage` and `/usage` show tokens per model over the last 5 hours, 24 hours and 7 days, and an estimated list-price cost. The limit is learned from your own history (the most you had used when a rate limit hit you) and the chat warns at 80% of it. Nothing is guessed until a rate limit has been recorded.
- **Compare.** `ask --compare` (or `--models a,b`) and `/compare` run one task on two models and show both answers, tokens and cost. It spends quota on each, so it is always explicit (the chat asks first).
- **Saved sessions.** `ia-router --continue`, `--resume ID`, `/sessions`, `/resume` and `ia-router sessions list|show|delete|clear`. Unlike the usage log, sessions DO store what you and the model said: local only, files readable just by you, capped, and easy to turn off (`ROUTER_NO_SESSIONS=1` or `/sessions off`).
- **Streaming.** The chat shows an answer as the model writes it and then swaps it for the rendered markdown (it stays raw if it is taller than the screen); `/stream on|off`. `ask --stream` prints it as it arrives. Works with claude and agy (text as written, plus the tools they use) and codex (whole messages and tool progress).
- **Connector templates.** `ia-router connectors templates` and `connectors add NAME --template filesystem|memory|github`. Only servers published by the MCP project or the app's vendor ship built in; your own trusted ones go in `~/.ia-router/templates.json`.
- **CI.** The tests now run on Linux and macOS with Python 3.9 to 3.14 on every push and pull request.

### Changed
- In automatic mode the connectors are no longer attached to every call (see the first item). `ask` prints the same by default; `--json`, `--raw` and `--stream` are opt-in.

## 0.5.0 — 2026-10-07

### Added
- **First-run onboarding.** On the first open the chat checks which official CLIs are installed and tells you, for each missing one, the exact step to install it and log in. If none is installed it says tasks cannot run yet and asks again on the next start; with just one it explains that everything goes to it. It stays silent when all three are present. It never installs a CLI or logs in for you.
- `/setup` in the chat and `ia-router setup` repeat that check at any time. Both can also check the logins: one minimal query per CLI, always asked first and declined by default.
- A task that fails for lack of login now says which CLI and how to log in, and the router remembers it: the next start reminds you and no longer spends a detection query on that CLI. A later success clears it, and `reset-cooldowns` clears it too.
- The first-run guidance also points to the optional MCP connectors.

## 0.4.0 — 2026-10-07

### Added
- **Connectors:** any model can now use your other apps (Gmail, Calendar, Slack, GitHub…) through MCP servers. Register servers with `ia-router connectors add NAME -- COMMAND…` (or `--url` for a remote one) and the router gives them to claude and codex on every call through one proxy MCP server that aggregates all of them. `ia-router connectors list|test|enable|disable|remove`, `/connectors` in the chat, and `ask --no-connectors`.
- Antigravity (`agy`) has no per-call MCP option, so it needs a one-time `ia-router connectors install agy`, which registers the proxy and adds the allow rule `mcp(ia-router-connectors/*)` to agy's settings (without it agy denies MCP tools when run non-interactively); `uninstall agy` removes both.
- Tool calls are logged to `~/.ia-router/connectors.log.jsonl` (server, tool, success, duration; never arguments or results). Per-server `allow`/`deny` tool lists are supported in `connectors.json`.

### Security
- The router never handles OAuth tokens: each MCP server logs in on its own. Secrets in `connectors.json` can be references such as `${GMAIL_TOKEN}`, resolved from your environment or `.env`; the file is created readable only by you.
- Connectors are enabled for reading and writing: a model can send an email if you ask it to. Use `deny` to hide specific tools, or `/connectors off` to turn them off.

## 0.3.0 — 2026-10-07

### Changed
- **The whole interface is now in English:** chat, commands, prompts, selector, error messages, the `/help` text, the MCP tool descriptions and the sample outputs. It was Spanish (Rioplatense) before.
- The documentation is in English: `README.md`, `AGENTS.md`, `CONTRIBUTING.md`, this changelog, the issue and pull request templates, and the usage guide, which moved from `docs/USO.md` to `docs/USAGE.md` (the old path is a stub so published links keep working).
- Package metadata and the Homebrew formula description are in English.
- The website page has an English version at <https://www.mgatc.com/en/recursos/ia-router/>.

### Added
- The task classifier now also recognizes English keywords for analysis, data, research, long-context and quick tasks. Spanish keywords still work, on purpose.
- A test that fails if Spanish text slips into the docs or the source (outside the classifier keywords).

### Compatibility
- The files in `~/.ia-router` keep their format, so existing data, priorities and history keep working.
- Chat answers to yes/no questions still accept `s`, `si`, `sí`, `y` and `yes`.

## 0.2.1 — 2026-10-05

### Added
- Public code on GitHub with `CONTRIBUTING.md`, commit sign-off (DCO) and issue and pull request templates.
- `CHANGELOG.md` and a "Where to find it" table (website, PyPI, Homebrew, code, guide, license) in the README.
- Package metadata with `Source`, `Documentation`, `Issues`, `Changelog` and `Homebrew`.

### Changed
- The README uses absolute links so it renders properly on PyPI, and includes the header screenshot.
- It is made clear that it was only tested on macOS (it should work on Linux, but that was not verified).

## 0.2.0 — 2026-10-05

First public version.

- Routing by objective metrics: accuracy (Arena), speed and cost (Artificial Analysis, with a free key), with the latest Arena snapshot bundled.
- Priority questions per kind of task; the metrics are updated with visibility of what changes.
- Chat with its own input box, dragged files, exact model and tokens in every answer, and rendered markdown.
- Installation with Homebrew and pip/pipx, `ia-router --version`.
- Apache-2.0 license with an attribution `NOTICE`.
