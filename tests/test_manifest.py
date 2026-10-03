import json, os, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import core, manifest, state  # noqa: E402

GOOD = json.dumps({"tasks": {"coding": {"prefer": ["claude", "codex", "ghost"], "why": "x"},
                             "writing": {"prefer": ["antigravity"], "why": "y"}, "bogus": {"prefer": ["claude"]}},
                   "disabled": ["antigravity", "ghost"]})


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]
        self.cfg = core.load_config()
        self.names = list(self.cfg["models"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_filters_unknown_models_and_categories(self):
        m = manifest.parse("texto previo " + GOOD + " texto posterior", self.names)
        self.assertEqual(m["tasks"]["coding"]["prefer"], ["claude", "codex"])
        self.assertNotIn("bogus", m["tasks"])
        self.assertEqual(m["disabled"], ["antigravity"])

    def test_parse_rejects_garbage(self):
        self.assertIsNone(manifest.parse("no es json", self.names))
        self.assertIsNone(manifest.parse('{"tasks": {"bogus": {"prefer": ["claude"]}}}', self.names))

    def test_generate_with_llm_fills_missing_categories(self):
        m = manifest.generate(self.cfg, lambda _p: GOOD, "claude", "notas")
        self.assertEqual(set(m["tasks"]), set(manifest.CATEGORIES))
        self.assertEqual(m["tasks"]["coding"]["prefer"][0], "claude")
        self.assertEqual(m["source"], "manager LLM")

    def test_generate_falls_back_when_manager_fails(self):
        m = manifest.generate(self.cfg, lambda _p: None, "claude")
        self.assertIn("reglas", m["source"])
        self.assertEqual(m["tasks"]["debugging"]["prefer"][0], "codex")

    def test_manifest_changes_routing_and_disables_models(self):
        task = "Arreglá este bug en mi función Python"
        self.assertEqual(core.route(task, self.cfg)["chosen"], "codex")
        manifest.save(manifest.generate(self.cfg, lambda _p: GOOD, "claude"), "test")
        cfg2 = core.load_config()
        d = core.route(task, cfg2)
        self.assertEqual(d["chosen"], "claude")
        self.assertTrue(d["manifest"])
        gem = [r for r in d["ranking"] if r["name"] == "antigravity"][0]
        self.assertFalse(gem["usable"])  # desactivado por el manifiesto

    def test_refine_applies_feedback_and_save_keeps_backup(self):
        base = manifest.generate(self.cfg, lambda _p: None, "claude")
        manifest.save(base, "base")
        new = manifest.refine(self.cfg, lambda _p: GOOD, base, "usá claude para código")
        self.assertEqual(new["tasks"]["coding"]["prefer"], ["claude", "codex"])
        self.assertIn("usá claude para código", new["user_notes"])
        self.assertIsNone(manifest.refine(self.cfg, lambda _p: "nada útil", base, "x"))
        manifest.save(new, "feedback")
        self.assertTrue((Path(self.tmp.name) / "manifest.prev.json").exists())
        self.assertEqual(len(manifest.load()["history"]), 2)

    def test_manager_runner_uses_fake_cli(self):
        os.environ["FAKE_CLAUDE_OUT"] = GOOD
        run = core.manager_runner(self.cfg, "claude")
        self.assertEqual(manifest.parse(run("x"), self.names)["tasks"]["coding"]["prefer"][0], "claude")

    def test_auth_error_sets_cooldown_and_falls_back(self):
        os.environ["FAKE_AGY_MODE"] = "auth"
        res = core.ask("Resumí este documento largo", self.cfg, model="auto")
        first = res["attempts"][0]
        if first["model"] == "antigravity":
            self.assertTrue(first["auth_required"])
            self.assertGreater(state.cooldown_remaining("antigravity"), 0)
        self.assertTrue(res["ok"])

    def test_stats_from_log(self):
        core.ask("hola", self.cfg, model="claude")
        st = state.stats()
        self.assertEqual(st["claude"]["runs"], 1)
        self.assertEqual(st["claude"]["ok_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
