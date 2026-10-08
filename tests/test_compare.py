import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAKE_BIN = str(ROOT / "tests" / "fake_bin")
os.environ["PATH"] = FAKE_BIN + os.pathsep + os.environ["PATH"]

from ia_router import chat, core  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]
        self.cfg = core.load_config()

    def cli(self, *args):
        e = dict(os.environ, ROUTER_HOME=self.tmp.name)
        return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args], capture_output=True, text=True, env=e, timeout=60, cwd=ROOT, input="")


class CoreTests(Base):
    def test_the_default_is_the_two_best_usable_models_each_running_the_task(self):
        res = core.compare("Fix this bug in my Python function", self.cfg)
        self.assertEqual(len(res), 2)
        self.assertEqual(len({r["model_used"] for r in res}), 2)
        self.assertTrue(all(r["ok"] for r in res))

    def test_chosen_models_keep_their_order(self):
        res = core.compare("hello", self.cfg, ["antigravity", "claude"])
        self.assertEqual([r["model_used"] for r in res], ["antigravity", "claude"])

    def test_one_failing_model_does_not_stop_the_other(self):
        os.environ["FAKE_CODEX_MODE"] = "fail"
        res = core.compare("hello", self.cfg, ["codex", "claude"])
        self.assertFalse(res[0]["ok"])
        self.assertTrue(res[1]["ok"])

    def test_it_needs_two_different_known_models(self):
        with self.assertRaises(ValueError):
            core.compare("hello", self.cfg, ["claude"])
        with self.assertRaises(ValueError):
            core.compare("hello", self.cfg, ["claude", "claude"])
        with self.assertRaises(ValueError) as cm:
            core.compare("hello", self.cfg, ["claude", "gpt-9"])
        self.assertIn("gpt-9", str(cm.exception))


class CliTests(Base):
    def test_json_has_one_entry_per_model(self):
        r = self.cli("ask", "Fix this bug", "--compare", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)["compare"]
        self.assertEqual(len(d), 2)
        self.assertTrue(all(x["ok"] for x in d))

    def test_the_text_output_has_a_header_per_model_and_warns_about_quota(self):
        r = self.cli("ask", "Fix this bug", "--models", "claude,codex")
        self.assertIn("spends quota on each", r.stderr)
        self.assertEqual(r.stdout.count("━━"), 2)
        self.assertIn("fake-claude", r.stdout)
        self.assertIn("fake-codex", r.stdout)

    def test_dry_run_sends_nothing_and_says_where_it_would(self):
        r = self.cli("ask", "Fix this bug", "--compare", "--dry-run", "--models", "claude,codex")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("claude, codex", r.stderr)
        self.assertEqual((Path(self.tmp.name) / "log.jsonl").exists(), False)

    def test_a_bad_model_is_a_usage_error(self):
        self.assertEqual(self.cli("ask", "hello", "--models", "claude,gpt-9").returncode, 2)

    def test_the_exit_code_is_1_only_when_every_model_failed(self):
        os.environ["FAKE_CODEX_MODE"] = "fail"
        self.addCleanup(os.environ.pop, "FAKE_CODEX_MODE", None)
        self.assertEqual(self.cli("ask", "hello", "--models", "codex,claude", "--json").returncode, 0)
        os.environ["FAKE_CLAUDE_MODE"] = "fail"
        self.addCleanup(os.environ.pop, "FAKE_CLAUDE_MODE", None)
        self.assertEqual(self.cli("ask", "hello", "--models", "codex,claude", "--json").returncode, 1)


class ChatTests(Base):
    def make(self, answers):
        self.out, self.prompts = [], []
        answers = list(answers)
        return chat.Chat(read=lambda p="": self.prompts.append(p) or (answers.pop(0) if answers else ""), write=self.out.append, color=False)

    def test_it_asks_first_and_declining_sends_nothing(self):
        c = self.make(["n"])
        c.handle("/compare Fix this bug in my Python function")
        self.assertIn("spends quota on each", self.prompts[0])
        self.assertIn("Cancelled", "\n".join(self.out))
        self.assertFalse((Path(self.tmp.name) / "log.jsonl").exists())

    def test_accepting_runs_both_and_shows_each_answer(self):
        c = self.make(["y"])
        c.handle("/compare claude,codex Fix this bug")
        text = "\n".join(self.out)
        self.assertIn("fake-claude", text)
        self.assertIn("fake-codex", text)
        self.assertEqual(text.count("━━"), 2)

    def test_usage_hint_and_palette(self):
        c = self.make([])
        c.handle("/compare")
        self.assertIn("Usage: /compare", "\n".join(self.out))
        self.assertIn("/compare", chat.HELP)
        self.assertIn("/compare", [x.name for x in chat.COMMANDS])


if __name__ == "__main__":
    unittest.main()
