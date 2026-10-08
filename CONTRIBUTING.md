# How to contribute to ia-router

Thanks for wanting to help! ia-router is free software ([Apache-2.0](LICENSE)) and contributions are welcome: bugs, ideas, documentation, tests and code.

Where everything is: [website](https://www.mgatc.com/en/recursos/ia-router/) ([in Spanish](https://www.mgatc.com/recursos/ia-router/)) · [PyPI](https://pypi.org/project/ia-router/) · [Homebrew tap](https://github.com/Mgobeaalcoba/homebrew-tap) · [issues](https://github.com/Mgobeaalcoba/ia-suscription-router/issues) · [changes](CHANGELOG.md).

Before you start, read [AGENTS.md](AGENTS.md): it summarizes the project rules, the code map and the known pitfalls.

## What is easy to accept

- Bug fixes, with a test that fails before and passes after.
- Support for other AI CLIs (see "How to extend" in `AGENTS.md`).
- Improvements to the documentation and to the user-facing messages.
- Tests that cover cases that are not tested today.

## What is worth discussing first

Open an [*issue*](https://github.com/Mgobeaalcoba/ia-suscription-router/issues/new/choose) before starting if you want to change the design (how models are scored, new metric sources, new commands). The project was deliberately simplified: there is no "manager" manifest, no custom calibration, and no natural-language configuration, and they will not be reintroduced without a good reason.

## Set up the environment

```bash
git clone https://github.com/Mgobeaalcoba/ia-suscription-router.git
cd ia-suscription-router
python3 -m unittest discover -s tests     # must end in OK (311 tests, ~15 s)
```

There is nothing to install: the project uses **only the Python standard library** and is compatible with **Python 3.9** (try it with `/usr/bin/python3` on macOS). Do not add dependencies.

## Rules checked on every pull request

1. **Tests:** every behavior change comes with tests. Tests **use no network, call no real CLIs and do not touch `~/.ia-router`** (they use `tests/fake_bin`, a temporary `ROUTER_HOME` and `tests/fixtures.py`).
2. **No secrets:** never push a `.env`, keys, tokens or personal data. The `.env` is ignored by git; only `.env.example` is versioned.
3. **No unrequested spending:** nothing may consume a model's quota or query the network without the user asking.
4. **Documentation up to date:** if a command, a state file or a rule changes, update `README.md`, `docs/USAGE.md` and `AGENTS.md` in the same change. The outputs shown in the guide must be **real**: capture them, do not invent them.
5. **English everywhere:** user-facing text, code comments, docs, tests and commit messages are in English. The only Spanish allowed is the keywords the task classifier deliberately recognizes (`ia_router/router.py`, the intent patterns in `ia_router/chat.py` and the connector keywords in `ia_router/connectors.py`); a test enforces it.
6. **Commit messages** in English with a `feat:`, `fix:`, `docs:` or `test:` prefix, explaining the *why*.

## License of your contributions and commit sign-off (DCO)

By contributing you agree that your contribution is published under the project's [Apache License 2.0](LICENSE) (section 5 of the license). Your copyright over what you write remains yours.

To keep a record we use the [Developer Certificate of Origin](DCO) (DCO 1.1): by **signing off your commits** you certify that you have the right to contribute that code under the project's license. It is one line at the end of the commit message:

```
Signed-off-by: Your Name <you@email.com>
```

Git adds it for you with the `-s` option:

```bash
git commit -s -m "fix: correct the calculation of ..."
# if you forgot to sign off several commits of your branch:
git rebase --signoff main
```

An automatic check on every pull request verifies that all commits are signed off (`tools/check_dco.sh`). Use your real name and an email where you can be reached.

## Attribution

If you redistribute or derive this software, keep `LICENSE` and [NOTICE](NOTICE) with the attribution to the author and to the third-party data, as the license requires. To cite it in a work: [CITATION.cff](CITATION.cff).

## Security

If you find a security problem (for example, a way to leak an API key), **do not open a public issue**: write to the author through the channels on [mgatc.com](https://www.mgatc.com).

## Your pull request

1. Make a *fork* and create a branch with a descriptive name.
2. Small, focused changes: one coherent change per pull request.
3. Run the tests and complete the pull request template checklist.
4. Answer reviews with new commits; there is no need to rewrite the history.
