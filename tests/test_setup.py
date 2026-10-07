import json, os, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAKE_BIN = str(ROOT / "tests" / "fake_bin")
REAL_PATH = os.environ["PATH"]

from ia_router import chat, core, setup as S, state  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["ROUTER_HOME"] = self.tmp.name
        self.addCleanup(lambda: os.environ.__setitem__("PATH", REAL_PATH))
        self.out = []

    def path(self, *names):
        """A PATH holding only the named fake CLIs (and python), so the others count as not installed."""
        d = Path(self.tmp.name) / "bin"
        d.mkdir(exist_ok=True)
        for n in names:
            link = d / n
            if not link.exists():
                link.symlink_to(Path(FAKE_BIN) / n)
        os.environ["PATH"] = str(d) + os.pathsep + str(Path(sys.executable).parent)
        return core.load_config(apply_scoring=False)

    def say(self, s=""):
        self.out.append(s)

    @property
    def text(self):
        return "\n".join(self.out)


class StatusTests(Base):
    def test_installed_and_missing_are_told_apart(self):
        rows = S.statuses(self.path("claude"))
        self.assertEqual([(r["name"], r["installed"]) for r in rows], [("claude", True), ("codex", False), ("antigravity", False)])
        self.assertEqual(S.usable(rows), ["claude"])

    def test_a_probe_result_and_a_remembered_failure_both_mark_a_missing_login(self):
        cfg = self.path("claude", "codex")
        rows = S.statuses(cfg, {"claude": {"auth": "missing"}, "codex": {"auth": "ok"}})
        self.assertEqual(S.usable(rows), ["codex"])
        state.set_auth_missing("codex", True)
        self.assertEqual(S.usable(S.statuses(cfg)), ["claude"])      # remembered from a task that failed
        state.set_auth_missing("codex", False)
        self.assertEqual(S.usable(S.statuses(cfg)), ["claude", "codex"])

    def test_the_table_and_the_advice_name_the_exact_step(self):
        rows = S.statuses(self.path("claude"))
        text = "\n".join(S.table(rows) + S.advice(rows))
        self.assertIn("✔ claude", text)
        self.assertIn("✗ codex", text)
        self.assertIn("brew install --cask antigravity-cli", text)     # the one install command we know for sure
        self.assertIn("only one model ready", text)

    def test_with_nothing_usable_it_says_tasks_cannot_run(self):
        text = "\n".join(S.advice(S.statuses(self.path())))
        self.assertIn("No model is ready", text)

    def test_unknown_clis_get_the_generic_guidance(self):
        self.assertIn("models.json", S.guide("my-cli", "install"))


class FlowTests(Base):
    def test_all_present_is_silent_and_marks_the_setup_done(self):
        cfg = self.path("claude", "codex", "agy")
        cfg["models"]["antigravity"]["cmd"] = ["agy", "-p", "{prompt}"]
        S.run(cfg, say=self.say)
        self.assertEqual(self.out, [])
        self.assertTrue(state.flags().get("onboarded"))
        self.assertFalse(S.needs_onboarding(cfg))

    def test_missing_clis_get_a_welcome_the_steps_and_the_connectors_hint(self):
        cfg = self.path("claude")
        S.run(cfg, say=self.say)
        self.assertIn("Welcome to ia-router", self.text)
        self.assertIn("codex:", self.text)
        self.assertIn("ia-router connectors add", self.text)
        self.assertTrue(state.flags().get("onboarded"))               # one CLI is enough to start using it

    def test_with_nothing_installed_it_does_not_mark_the_setup_done_and_asks_again_next_time(self):
        cfg = self.path()
        S.run(cfg, say=self.say)
        self.assertFalse(state.flags().get("onboarded"))
        self.assertTrue(S.needs_onboarding(cfg))

    def test_the_login_check_spends_quota_so_it_is_asked_first_and_declined_by_default(self):
        cfg = self.path("claude")
        calls, asked = [], []
        S.run(cfg, say=self.say, ask_yes=lambda q, default=True: asked.append((q, default)) or default,
              check_login=lambda cfg, only: calls.append(only) or {})
        self.assertEqual(calls, [])
        self.assertIn("spends a pinch of quota", asked[0][0])
        self.assertFalse(asked[0][1])

    def test_an_accepted_login_check_reports_and_remembers_who_is_logged_out(self):
        cfg = self.path("claude", "codex")
        checked = {"claude": {"auth": "ok"}, "codex": {"auth": "missing"}}
        rows = S.run(cfg, say=self.say, ask_yes=lambda q, default=True: True, check_login=lambda cfg, only: checked, explicit=True)
        self.assertEqual(S.usable(rows), ["claude"])
        self.assertIn("not logged in", self.text)
        self.assertIn("Run `codex` once", self.text)
        self.assertEqual(state.auth_missing(), {"codex"})
        S.run(cfg, say=self.say, ask_yes=lambda q, default=True: True, check_login=lambda cfg, only: {"claude": {"auth": "ok"}, "codex": {"auth": "ok"}}, explicit=True)
        self.assertEqual(state.auth_missing(), set())

    def test_the_explicit_command_always_speaks(self):
        cfg = self.path("claude", "codex", "agy")
        S.run(cfg, say=self.say, explicit=True)
        self.assertIn("Where your CLIs stand", self.text)


class ChatIntegrationTests(Base):
    def make(self, cfg_names, answers=()):
        self.path(*cfg_names)
        answers = list(answers)
        c = chat.Chat(read=lambda prompt="": answers.pop(0) if answers else "", write=self.say, color=False)
        return c

    def test_first_open_with_a_missing_cli_shows_the_guidance_before_anything_else(self):
        c = self.make(["claude"], ["n", "n", "n"])
        c.startup()
        self.assertTrue(self.out[0].startswith("Welcome to ia-router"))

    def test_a_failed_task_for_lack_of_login_says_how_to_log_in_and_it_is_remembered(self):
        c = self.make(["claude", "codex"])
        os.environ["FAKE_CODEX_MODE"] = "auth"
        self.addCleanup(os.environ.pop, "FAKE_CODEX_MODE", None)
        c.handle("Fix this bug in my Python function")     # codex is the best model for it, fails, and the router falls back to claude
        self.assertEqual(state.auth_missing(), {"codex"})
        self.out.clear()
        state.set_flag("onboarded", True)
        c.startup()
        self.assertIn("codex was not logged in the last time it ran. Run `codex` once", self.text)
        self.assertNotIn("codex", "".join(l for l in self.out if "minimal query" in l or "I need to know" in l))   # no probe wasted on it

    def test_when_every_attempt_fails_for_lack_of_login_each_one_gets_its_step(self):
        c = self.make(["claude", "codex"])
        for n in ("CLAUDE", "CODEX"):
            os.environ[f"FAKE_{n}_MODE"] = "auth"
            self.addCleanup(os.environ.pop, f"FAKE_{n}_MODE", None)
        c.handle("Fix this bug in my Python function")
        self.assertIn("claude is not logged in. Run `claude` once", self.text)
        self.assertIn("codex is not logged in. Run `codex` once", self.text)

    def test_a_task_with_no_ready_cli_points_to_setup(self):
        c = self.make([])
        c.handle("Fix this bug in my Python function")
        self.assertIn("/setup", self.text)

    def test_the_setup_command_exists_in_help_and_the_palette(self):
        self.assertIn("/setup", chat.HELP)
        self.assertIn("/setup", [x.name for x in chat.COMMANDS])
        c = self.make(["claude"], ["n"])
        state.set_flag("onboarded", True)
        c.handle("/setup")
        self.assertIn("Where your CLIs stand", self.text)

    def test_a_success_clears_the_remembered_logout(self):
        c = self.make(["claude", "codex"])
        state.set_auth_missing("codex", True)
        os.environ["FAKE_CLAUDE_MODE"] = "ok"
        res = core.ask("Fix this bug in my Python function", core.load_config(), model="codex")
        self.assertTrue(res["ok"])
        self.assertEqual(state.auth_missing(), set())


if __name__ == "__main__":
    unittest.main()
