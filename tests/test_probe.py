import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import adapters, core, probe, state  # noqa: E402


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        os.environ["CODEX_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith(("FAKE_", "ROUTER_CMD_")):
                del os.environ[k]
        self.cfg = core.load_config(apply_scoring=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_probe_reports_install_login_and_the_real_model(self):
        info = probe.probe(self.cfg)
        self.assertEqual({n: r["auth"] for n, r in info.items()}, {"claude": "ok", "codex": "ok", "antigravity": "ok"})
        self.assertEqual(info["claude"]["model_id"], "claude-fake-1")
        self.assertEqual(info["antigravity"]["model_id"], "Fake Flash (High)")
        self.assertIsNone(info["codex"]["model_id"])                     # codex only reports it in its session file
        self.assertIn("probe_seconds", info["claude"])

    def test_probe_remembers_the_ids_it_finds(self):
        probe.probe(self.cfg)
        self.assertEqual(state.seen_ids(), {"claude": "claude-fake-1", "antigravity": "Fake Flash (High)"})

    def test_missing_ids_and_detect_only_asks_the_ones_it_does_not_know(self):
        self.assertEqual(probe.missing_ids(self.cfg), ["claude", "codex", "antigravity"])
        state.remember_model_id("claude", "claude-sonnet-5-5")
        self.assertEqual(probe.missing_ids(self.cfg), ["codex", "antigravity"])
        called = []
        real = adapters.run_cli
        def spy(name, *a, **k):
            called.append(name)
            return real(name, *a, **k)
        out = []
        with mock.patch.object(adapters, "run_cli", spy):
            found = probe.detect_ids(self.cfg, out.append)
        self.assertEqual(sorted(called), ["antigravity", "codex"])        # claude was already known: no query is spent
        self.assertEqual(found["antigravity"], "Fake Flash (High)")
        self.assertTrue(any("antigravity" in l and "Fake Flash" in l for l in out))
        self.assertEqual(probe.missing_ids(self.cfg), ["codex"])

    def test_a_cli_that_is_not_installed_is_reported_not_called(self):
        self.cfg["models"]["claude"]["cmd"] = ["no-existe-este-cli", "{prompt}"]
        info = probe.probe(self.cfg, only=["claude"])
        self.assertFalse(info["claude"]["installed"])
        self.assertEqual(info["claude"]["auth"], "n/a")
        self.assertNotIn("claude", probe.missing_ids(self.cfg))            # not installed: detection is not attempted

    def test_a_failing_cli_reports_unknown_auth_and_no_id(self):
        os.environ["FAKE_CLAUDE_MODE"] = "fail"
        info = probe.probe(self.cfg, only=["claude"])
        self.assertEqual(info["claude"]["auth"], "unknown")
        self.assertIsNone(info["claude"]["model_id"])
        self.assertEqual(state.seen_ids(), {})

    def test_core_ask_learns_the_model_from_a_normal_answer(self):
        core.ask("hello", self.cfg, model="claude")
        self.assertEqual(state.seen_ids()["claude"], "claude-fake-1")


if __name__ == "__main__":
    unittest.main()
