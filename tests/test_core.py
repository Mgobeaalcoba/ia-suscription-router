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
        d = core.route("Fix this bug in my Python function, the test fails", self.cfg)
        self.assertEqual(d["chosen"], "codex")

    def test_write_a_function_is_coding_not_writing(self):
        d = core.route("Write a Python function that adds up the elements of a list", self.cfg)
        self.assertNotIn("writing", d["weights"])
        self.assertIn("coding", d["weights"])

    def test_writing_goes_to_claude(self):
        d = core.route("Draft a follow-up email for a client in a friendly tone", self.cfg)
        self.assertEqual(d["chosen"], "claude")

    def test_tasks_are_classified_in_english_and_in_spanish(self):
        # the interface is English, but the rules deliberately understand tasks written in either language
        pairs = [("Fix this bug in my Python function", "Arreglá este bug en mi función Python", "debugging"),
                 ("Draft a follow-up email for a client", "Redactá un mail de seguimiento para un cliente", "writing"),
                 ("Analyze the architecture and the trade-offs", "Analizá la arquitectura y los trade-offs", "analysis"),
                 ("Run a regression on this dataset", "Hacé una regresión sobre este dataset", "data"),
                 ("Summarize this transcript, a quick one-liner", "Resumí esta transcripción, algo rápido", "long_context")]
        for en, es, cat in pairs:
            self.assertIn(cat, core.route(en, self.cfg)["weights"], en)
            self.assertIn(cat, core.route(es, self.cfg)["weights"], es)

    def test_long_context_goes_to_antigravity(self):
        f = Path(self.tmp.name) / "big.txt"
        f.write_text("lorem ipsum " * 15000)  # ~180k chars
        _, ctx_len, _ = core.build_prompt("Summarize this document", [str(f)])
        d = core.route("Summarize this document", self.cfg, ctx_len)
        self.assertEqual(d["chosen"], "antigravity")

    def test_cooldown_excludes_model(self):
        state.set_cooldown("codex", 30)
        d = core.route("Fix this bug in my Python function", self.cfg)
        self.assertNotEqual(d["chosen"], "codex")

    def test_ask_runs_fake_cli(self):
        res = core.ask("Fix this bug in my Python function", self.cfg)
        self.assertTrue(res["ok"])
        self.assertEqual(res["model_used"], "codex")
        self.assertIn("[fake-codex]", res["output"])

    def test_fallback_on_rate_limit_sets_cooldown(self):
        os.environ["FAKE_CODEX_MODE"] = "ratelimit"
        res = core.ask("Fix this bug in my Python function", self.cfg)
        self.assertTrue(res["ok"])
        self.assertNotEqual(res["model_used"], "codex")
        self.assertTrue(res["attempts"][0]["rate_limited"])
        self.assertGreater(state.cooldown_remaining("codex"), 0)

    def test_permission_denial_is_not_a_cooldown_and_says_what_to_do(self):
        # agy auto-denies a tool that needs a permission it cannot ask for: the task depends on what the model tried,
        # so the model must not be parked for an hour and the user gets the real cause, not a bare "all attempts failed"
        os.environ["FAKE_AGY_MODE"] = "permission"
        res = core.ask("hello", self.cfg, model="antigravity")
        self.assertFalse(res["ok"])
        self.assertTrue(res["attempts"][0]["permission_denied"])
        self.assertEqual(res["attempts"][0]["error"], "permission_denied")
        self.assertEqual(state.cooldown_remaining("antigravity"), 0)
        self.assertNotIn("antigravity", state.auth_missing())
        self.assertIn("approve", res["hint"])
        self.assertNotIn("dangerously", res["hint"])

    def test_denied_json_with_success_status_is_a_permission_denial(self):
        # the real agy reports a denied shell command with status SUCCESS and an empty answer; that must not look like "empty output"
        os.environ["FAKE_AGY_MODE"] = "denied_json"
        res = core.ask("hello", self.cfg, model="antigravity")
        self.assertFalse(res["ok"])
        self.assertEqual(res["attempts"][0]["error"], "permission_denied")
        self.assertIn("approve", res["hint"])

    def test_agy_gets_the_run_protocol(self):
        # headless agy cannot run commands itself: it is told to ask with a RUN line, which the router only runs after approval
        os.environ["FAKE_AGY_ECHO"] = "1"
        from ia_router import adapters
        res = adapters.run_cli("antigravity", self.cfg["models"]["antigravity"], "list the files", usage=True)
        self.assertTrue(res["output"].startswith(adapters.AGY_PROTOCOL))
        self.assertTrue(res["output"].endswith("list the files"))

    def test_approved_command_runs_once_and_its_output_reaches_the_model(self):
        os.environ["FAKE_AGY_MODE"] = "run"
        asked = []
        res = core.ask("say hello", self.cfg, model="antigravity", approve=lambda c: asked.append(c) or True)
        self.assertEqual(asked, ["echo hello"])
        self.assertTrue(res["ok"])
        self.assertIn("GOT_OUTPUT hello", res["output"])
        self.assertEqual(res["commands"], [{"command": "echo hello", "approved": True, "returncode": 0}])
        self.assertEqual(len(res["attempts"]), 2)

    def test_refused_command_is_not_run_and_the_model_is_not_asked_again(self):
        os.environ["FAKE_AGY_MODE"] = "run"
        res = core.ask("say hello", self.cfg, model="antigravity", approve=lambda c: False)
        self.assertTrue(res["ok"])
        self.assertIn("was not approved", res["output"])
        self.assertNotIn("RUN:", res["output"])
        self.assertEqual(res["commands"], [{"command": "echo hello", "approved": False}])
        self.assertEqual(len(res["attempts"]), 1)

    def test_without_an_approver_nothing_runs(self):
        os.environ["FAKE_AGY_MODE"] = "run"
        res = core.ask("say hello", self.cfg, model="antigravity")
        self.assertIn("RUN: echo hello", res["output"])
        self.assertEqual(res["commands"], [])
        self.assertEqual(len(res["attempts"]), 1)

    def test_commands_have_no_shell(self):
        out = core.run_command("echo a && echo b", None)
        self.assertEqual(out, (0, "a && echo b\n"))
        self.assertEqual(core.run_command("definitely-not-a-binary-xyz", None)[0], 127)

    def test_dry_run_does_not_execute(self):
        res = core.ask("hello", self.cfg, dry_run=True)
        self.assertTrue(res["dry_run"])
        self.assertEqual(res["attempts"], [])

    def test_models_json_lookup_order(self):
        packaged = core.models_path()
        self.assertEqual(packaged, core.PACKAGED_MODELS)
        mine = Path(self.tmp.name) / "models.json"
        mine.write_text(json.dumps({"models": {"x": {"cmd": ["x"], "strengths": {}}}}))
        self.assertEqual(core.models_path(), mine)                      # your copy in ~/.ia-router wins over the bundled one
        os.environ["ROUTER_MODELS"] = "/tmp/otro.json"
        try:
            self.assertEqual(core.models_path(), Path("/tmp/otro.json"))   # ROUTER_MODELS wins over your copy
            self.assertEqual(core.models_path("/tmp/pedido.json"), Path("/tmp/pedido.json"))
        finally:
            del os.environ["ROUTER_MODELS"]

    def test_unknown_model_raises(self):
        with self.assertRaises(ValueError):
            core.ask("hello", self.cfg, model="gpt-9")

    def test_big_prompt_uses_stdin(self):
        f = Path(self.tmp.name) / "big.txt"
        f.write_text("x" * 150_000)
        res = core.ask("Summarize this long document", self.cfg, model="claude", context_files=[str(f)])
        self.assertTrue(res["ok"])
        self.assertIn("received 15", res["output"])  # the large text arrived through stdin


if __name__ == "__main__":
    unittest.main()
