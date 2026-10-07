# Changelog

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions are published on [PyPI](https://pypi.org/project/ia-router/) and [Homebrew](https://github.com/Mgobeaalcoba/homebrew-tap).

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
