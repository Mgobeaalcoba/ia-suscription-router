import json, os, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import core, state  # noqa: E402


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]
        self.cfg = core.load_config()

    def tearDown(self):
        self.tmp.cleanup()

    def test_debugging_goes_to_codex(self):
        d = core.route("Arreglá este bug en mi función Python, falla el test", self.cfg)
        self.assertEqual(d["chosen"], "codex")

    def test_write_a_function_is_coding_not_writing(self):
        d = core.route("Escribí una función Python que sume los elementos de una lista", self.cfg)
        self.assertNotIn("writing", d["weights"])
        self.assertIn("coding", d["weights"])

    def test_writing_goes_to_claude(self):
        d = core.route("Redactá un mail de seguimiento para un cliente con tono cordial", self.cfg)
        self.assertEqual(d["chosen"], "claude")

    def test_long_context_goes_to_antigravity(self):
        f = Path(self.tmp.name) / "big.txt"
        f.write_text("lorem ipsum " * 15000)  # ~180k chars
        _, ctx_len, _ = core.build_prompt("Resumí este documento", [str(f)])
        d = core.route("Resumí este documento", self.cfg, ctx_len)
        self.assertEqual(d["chosen"], "antigravity")

    def test_cooldown_excludes_model(self):
        state.set_cooldown("codex", 30)
        d = core.route("Arreglá este bug en mi función Python", self.cfg)
        self.assertNotEqual(d["chosen"], "codex")

    def test_ask_runs_fake_cli(self):
        res = core.ask("Arreglá este bug en mi función Python", self.cfg)
        self.assertTrue(res["ok"])
        self.assertEqual(res["model_used"], "codex")
        self.assertIn("[fake-codex]", res["output"])

    def test_fallback_on_rate_limit_sets_cooldown(self):
        os.environ["FAKE_CODEX_MODE"] = "ratelimit"
        res = core.ask("Arreglá este bug en mi función Python", self.cfg)
        self.assertTrue(res["ok"])
        self.assertNotEqual(res["model_used"], "codex")
        self.assertTrue(res["attempts"][0]["rate_limited"])
        self.assertGreater(state.cooldown_remaining("codex"), 0)

    def test_dry_run_does_not_execute(self):
        res = core.ask("hola", self.cfg, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["attempts"], [])

    def test_models_json_lookup_order(self):
        packaged = core.models_path()
        self.assertEqual(packaged, core.PACKAGED_MODELS)
        mine = Path(self.tmp.name) / "models.json"
        mine.write_text(json.dumps({"models": {"x": {"cmd": ["x"], "strengths": {}}}}))
        self.assertEqual(core.models_path(), mine)                      # tu copia en ~/.ia-router gana al incluido
        os.environ["ROUTER_MODELS"] = "/tmp/otro.json"
        try:
            self.assertEqual(core.models_path(), Path("/tmp/otro.json"))   # ROUTER_MODELS gana a tu copia
            self.assertEqual(core.models_path("/tmp/pedido.json"), Path("/tmp/pedido.json"))
        finally:
            del os.environ["ROUTER_MODELS"]

    def test_unknown_model_raises(self):
        with self.assertRaises(ValueError):
            core.ask("hola", self.cfg, model="gpt-9")

    def test_big_prompt_uses_stdin(self):
        f = Path(self.tmp.name) / "big.txt"
        f.write_text("x" * 150_000)
        res = core.ask("Resumí este documento largo", self.cfg, model="claude", context_files=[str(f)])
        self.assertTrue(res["ok"])
        self.assertIn("recibi 15", res["output"])  # llegó el texto grande por stdin


if __name__ == "__main__":
    unittest.main()
