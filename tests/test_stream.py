import io, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAKE_BIN = str(ROOT / "tests" / "fake_bin")
os.environ["PATH"] = FAKE_BIN + os.pathsep + os.environ["PATH"]

from ia_router import adapters as A, core, stream as S  # noqa: E402


class ParseTests(unittest.TestCase):
    def test_claude_events(self):
        delta = json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hel"}}})
        tool = json.dumps({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "gmail__search"}}})
        res = json.dumps({"type": "result", "result": "hello"})
        self.assertEqual(A.parse_stream_line("claude", delta), ("text", "hel"))
        self.assertEqual(A.parse_stream_line("claude", tool), ("status", "using gmail__search"))
        self.assertEqual(A.parse_stream_line("claude", res), ("final", {"type": "result", "result": "hello"}))
        self.assertIsNone(A.parse_stream_line("claude", json.dumps({"type": "system"})))
        self.assertIsNone(A.parse_stream_line("claude", "not json"))
        self.assertIsNone(A.parse_stream_line("claude", "[1]"))

    def test_agy_events(self):
        text = json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "hi"}})
        tool = json.dumps({"event": "step_update", "step_update": {"step_type": "tool", "state": "ACTIVE", "tool_name": "view_file"}})
        done = json.dumps({"event": "step_update", "step_update": {"step_type": "tool", "state": "DONE", "tool_name": "view_file"}})
        res = json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": "hi"}})
        self.assertEqual(A.parse_stream_line("agy", text), ("text", "hi"))
        self.assertEqual(A.parse_stream_line("agy", tool), ("status", "using view_file"))
        self.assertIsNone(A.parse_stream_line("agy", done))
        self.assertEqual(A.parse_stream_line("agy", res), ("final", {"status": "SUCCESS", "response": "hi"}))

    def test_codex_gives_whole_messages_and_tool_progress(self):
        msg = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}})
        cmd = json.dumps({"type": "item.started", "item": {"type": "command_execution"}})
        self.assertEqual(A.parse_stream_line("codex", msg), ("text", "done"))
        self.assertEqual(A.parse_stream_line("codex", cmd), ("status", "using command_execution"))
        self.assertIsNone(A.parse_stream_line("codex", json.dumps({"type": "item.started", "item": {"type": "reasoning"}})))

    def test_the_command_is_switched_to_streaming_json(self):
        c = A.stream_argv("claude", ["claude", "--output-format", "json", "-p", "x"])
        self.assertEqual(c[:6], ["claude", "--output-format", "stream-json", "--verbose", "--include-partial-messages", "-p"])
        self.assertEqual(A.stream_argv("agy", ["agy", "-p", "x", "--output-format", "json"])[-1], "stream-json")
        self.assertEqual(A.stream_argv("codex", ["codex", "exec", "--json", "x"]), ["codex", "exec", "--json", "x"])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]
        self.cfg = core.load_config()

    def run_stream(self, model, **env):
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in env])
        text, status = [], []
        res = core.ask("Fix this bug in my Python function", self.cfg, model=model, stream={"on_text": text.append, "on_status": status.append})
        return res, text, status

    def test_claude_text_arrives_in_pieces_and_the_result_is_identical(self):
        res, text, _ = self.run_stream("claude")
        self.assertTrue(res["ok"])
        self.assertGreater(len(text), 1)                                       # several deltas, not one block
        self.assertEqual("".join(text), res["output"])
        self.assertEqual(res["tokens"]["input"], 152)                          # the same token accounting as the non-streaming mode
        self.assertEqual(res["model_id"], "claude-fake-1")

    def test_antigravity_streams_too_and_keeps_the_model_from_its_log(self):
        res, text, _ = self.run_stream("antigravity")
        self.assertTrue(res["ok"])
        self.assertEqual("".join(text), res["output"])
        self.assertEqual(res["model_id"], "Fake Flash (High)")

    def test_codex_delivers_the_whole_message(self):
        res, text, _ = self.run_stream("codex")
        self.assertTrue(res["ok"])
        self.assertEqual(text, [res["output"]])

    def test_tool_use_is_reported_as_status(self):
        _, text, status = self.run_stream("claude", FAKE_STREAM_TOOL="1")
        self.assertEqual(status, ["using files__read_text_file"])
        _, _, status = self.run_stream("antigravity", FAKE_STREAM_TOOL="1")
        self.assertEqual(status, ["using files__read_text_file"])

    def test_failures_are_still_classified_and_the_next_model_is_tried(self):
        os.environ["FAKE_CODEX_MODE"] = "ratelimit"
        self.addCleanup(os.environ.pop, "FAKE_CODEX_MODE", None)
        resets = []
        res = core.ask("Fix this bug in my Python function", self.cfg, stream={"on_text": lambda s: None, "on_reset": lambda: resets.append(1)})
        self.assertTrue(res["ok"])
        self.assertNotEqual(res["model_used"], "codex")
        self.assertTrue(res["attempts"][0]["rate_limited"])
        self.assertEqual(resets, [1])

    def test_a_timeout_kills_the_process(self):
        os.environ["FAKE_CLAUDE_OUT"] = "x"
        spec = dict(self.cfg["models"]["claude"], cmd=[sys.executable, "-c", "import time; time.sleep(30)"], usage={"parser": "claude", "args": []})
        res = A.run_cli("claude", spec, "hi", timeout=1, usage=True, stream={"on_text": lambda s: None})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "timeout")
        os.environ.pop("FAKE_CLAUDE_OUT", None)


class LiveOutputTests(unittest.TestCase):
    def live(self, columns=20, rows=10):
        out = io.StringIO()
        return S.LiveOutput(out=out, columns=columns, rows=rows, dim=lambda s: f"<{s}>"), out

    def test_rows_account_for_newlines_and_wrapping(self):
        self.assertEqual(S.advance(0, 0, "abc\nde", 20), (1, 2))
        self.assertEqual(S.advance(0, 0, "a" * 45, 20), (2, 5))
        self.assertEqual(S.advance(0, 0, "日本", 4), (0, 4))                  # wide characters take two cells
        self.assertEqual(S.advance(0, 0, "日本語", 4), (1, 2))

    def test_a_short_answer_is_erased_to_make_room_for_the_rendered_one(self):
        live, out = self.live()
        live.on_text("one\ntwo\n")
        live.on_text("thr")
        self.assertEqual(live.row, 2)
        self.assertTrue(live.erase())
        self.assertTrue(out.getvalue().endswith("\r\033[2A\033[J"))
        self.assertEqual((live.row, live.col), (0, 0))

    def test_an_answer_taller_than_the_screen_is_left_alone(self):
        live, out = self.live(rows=4)
        live.on_text("1\n2\n3\n4\n5\n")
        before = out.getvalue()
        self.assertFalse(live.erase())
        self.assertEqual(out.getvalue(), before)                              # nothing was cleared

    def test_status_goes_on_its_own_line_and_reset_clears_a_failed_attempt(self):
        live, out = self.live()
        live.on_text("partial")
        live.on_status("using gmail__search")
        self.assertIn("\n<⚙ using gmail__search>\n", out.getvalue())
        live.reset()
        self.assertEqual(live.text, "")
        self.assertIn("\033[J", out.getvalue())


class CliTests(unittest.TestCase):
    def test_ask_stream_prints_the_answer_once_and_who_answered_on_stderr(self):
        with tempfile.TemporaryDirectory() as home:
            r = subprocess.run([sys.executable, str(ROOT / "cli.py"), "ask", "Fix this bug in my Python function", "--stream", "-q"], capture_output=True, text=True,
                               env=dict(os.environ, ROUTER_HOME=home), timeout=60, cwd=ROOT, input="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("[fake-codex]"), 1)
        self.assertIn("[codex", r.stderr)


if __name__ == "__main__":
    unittest.main()
