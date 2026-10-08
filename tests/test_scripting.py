import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
FAKE_BIN = str(ROOT / "tests" / "fake_bin")

from ia_router import core, scoring as S  # noqa: E402
import test_scoring  # noqa: E402  (its Home class sets up the toy Arena snapshot and the learned model ids)


def run(args, stdin='', home=None, env=None):
    e = dict(os.environ, PATH=FAKE_BIN + os.pathsep + os.environ["PATH"], ROUTER_HOME=home)
    e.update(env or {})
    return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args], input=stdin, capture_output=True, text=True, env=e, timeout=60, cwd=ROOT)


class ScriptModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def cli(self, args, stdin='', env=None):
        return run(args, stdin, self.tmp.name, env)

    def test_json_is_one_object_and_nothing_else_on_stdout(self):
        r = self.cli(["ask", "Fix this bug in my Python function", "--json"])
        self.assertEqual(r.returncode, 0, r.stderr)
        d = json.loads(r.stdout)
        self.assertTrue(d["ok"])
        self.assertEqual(d["model"], "codex")
        self.assertIn("[fake-codex]", d["output"])
        self.assertEqual(d["routing"]["chosen"], "codex")
        self.assertEqual({"model", "score", "usable"}, set(d["routing"]["ranking"][0]))
        self.assertEqual(d["attempts"][0]["model"], "codex")
        self.assertEqual(d["connectors"], [])
        self.assertIn("estimated_cost_usd", d)
        self.assertEqual(r.stderr.strip(), "")                      # the routing table does not leak anywhere

    def test_json_reports_failure_with_a_nonzero_exit_and_the_error(self):
        r = self.cli(["ask", "Fix this bug", "-m", "codex", "--json"], env={"FAKE_CODEX_MODE": "fail"})
        self.assertEqual(r.returncode, 1)
        d = json.loads(r.stdout)
        self.assertFalse(d["ok"])
        self.assertTrue(d["error"])

    def test_json_for_a_usage_error_is_still_json_with_exit_2(self):
        r = self.cli(["ask", "hello", "-m", "gpt-9", "--json"])
        self.assertEqual(r.returncode, 2)
        self.assertFalse(json.loads(r.stdout)["ok"])

    def test_raw_prints_only_the_answer_on_stdout(self):
        r = self.cli(["ask", "Fix this bug in my Python function", "--raw", "-q"])
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.startswith("[fake-codex]"))
        self.assertNotIn("Classification", r.stdout)
        self.assertIn("codex", r.stderr)                            # who answered goes to stderr
        self.assertNotIn("Classification", r.stderr)

    def test_a_piped_task_is_the_task(self):
        r = self.cli(["ask", "--json"], stdin="Fix this bug in my Python function\n")
        self.assertEqual(json.loads(r.stdout)["model"], "codex")

    def test_piped_text_is_material_and_the_task_decides_the_routing(self):
        r = self.cli(["ask", "Draft a follow-up email for a client", "--json"], stdin="x" * 5000)
        d = json.loads(r.stdout)
        self.assertEqual(d["model"], "claude")                      # routed by the task, not by the 5000 x's
        self.assertIn("received", d["output"])
        n = int(d["output"].split("received ")[1].split()[0])
        self.assertGreater(n, 5000)                                 # the piped text did travel with the task

    def test_no_task_and_no_pipe_is_a_usage_error(self):
        r = self.cli(["ask"], stdin="")
        self.assertEqual(r.returncode, 2)
        self.assertIn("give a task", r.stderr)

    def test_an_open_but_silent_stdin_does_not_hang_a_task_that_never_needed_it(self):
        e = dict(os.environ, PATH=FAKE_BIN + os.pathsep + os.environ["PATH"], ROUTER_HOME=self.tmp.name)
        p = subprocess.Popen([sys.executable, str(ROOT / "cli.py"), "ask", "Fix this bug in my Python function", "--json"], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=e, cwd=ROOT)
        try:
            p.wait(timeout=30)                                   # stdin stays open and silent the whole time: it must still finish
            out = p.stdout.read()
        finally:
            p.stdin.close()
            p.stdout.close()
            p.stderr.close()
        self.assertEqual(json.loads(out)["model"], "codex")

    def test_the_dash_task_waits_for_the_pipe(self):
        r = self.cli(["ask", "-", "--json"], stdin="Fix this bug in my Python function\n")
        self.assertEqual(json.loads(r.stdout)["model"], "codex")

    def test_the_default_output_is_unchanged(self):
        r = self.cli(["ask", "Fix this bug in my Python function"])
        self.assertIn("Classification", r.stderr)
        self.assertIn("[codex", r.stdout)


class CostTests(test_scoring.Home):
    def test_the_estimate_uses_the_list_price_of_the_model_the_cli_uses(self):
        self.with_aa()
        cfg = core.load_config()
        # codex: 3 USD in / 12 USD out per million tokens in the toy Artificial Analysis data
        self.assertEqual(S.price_of(cfg, "codex"), (3.0, 12.0))
        self.assertEqual(S.estimate_cost(cfg, "codex", {"input": 1_000_000, "output": 500_000}), 9.0)

    def test_without_prices_or_tokens_there_is_no_estimate(self):
        cfg = core.load_config()
        self.assertIsNone(S.estimate_cost(cfg, "codex", None))
        self.assertIsNone(S.estimate_cost(cfg, "no-such-model", {"input": 1, "output": 1}))

    def test_result_json_carries_the_estimate(self):
        self.with_aa()
        cfg = core.load_config()
        os.environ["PATH"] = FAKE_BIN + os.pathsep + os.environ["PATH"]
        res = core.ask("Fix this bug in my Python function", cfg, model="codex")
        out = core.result_json(res, cfg)
        self.assertEqual(out["model"], "codex")
        self.assertGreater(out["estimated_cost_usd"], 0)


if __name__ == "__main__":
    unittest.main()
