import http.client, json, os, sys, tempfile, threading, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import ui  # noqa: E402


class UiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]
        self.server = ui.Server(0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self, method, path, body=None, token=True, host=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            h["X-Token"] = self.server.token
        if body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, json.dumps(body) if body is not None else None, h)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def ask(self, **body):
        status, data = self.call("POST", "/api/ask", body)
        self.assertEqual(status, 200)
        return [json.loads(line) for line in data.decode().splitlines() if line]


class GuardTests(UiTests):
    def test_the_page_needs_the_token_and_embeds_it(self):
        self.assertEqual(self.call("GET", "/", token=False)[0], 401)
        status, data = self.call("GET", "/?t=" + self.server.token, token=False)
        self.assertEqual(status, 200)
        self.assertIn(self.server.token.encode(), data)
        self.assertNotIn(b"__TOKEN__", data)

    def test_a_wrong_token_is_refused(self):
        self.assertEqual(self.call("GET", "/api/state", headers={"X-Token": "nope"})[0], 401)

    def test_a_foreign_host_is_refused_even_with_the_token(self):   # DNS rebinding
        self.assertEqual(self.call("GET", "/api/state", host="evil.example:80")[0], 403)

    def test_a_foreign_origin_is_refused(self):
        self.assertEqual(self.call("POST", "/api/ask", {"task": "hi"}, headers={"Origin": "https://evil.example"})[0], 403)

    def test_posts_must_be_json(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/ask", "task=hi", {"Host": f"127.0.0.1:{self.port}", "X-Token": self.server.token, "Content-Type": "text/plain"})
        self.assertEqual(conn.getresponse().status, 415)

    def test_it_only_listens_locally(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_unknown_paths_and_bad_ids(self):
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        self.assertEqual(self.call("GET", "/api/sessions/not-an-id")[0], 404)


class ApiTests(UiTests):
    def test_state_lists_the_models_and_usage(self):
        status, data = self.call("GET", "/api/state")
        st = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual({m["name"] for m in st["models"]}, {"claude", "codex", "antigravity"})
        self.assertTrue(st["ready"])
        self.assertIn("connectors", st)

    def test_an_answer_streams_text_then_finishes_with_the_result(self):
        events = self.ask(task="Fix this bug in my Python function")
        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue(events[-1]["ok"])
        self.assertTrue(events[-1]["output"])
        self.assertTrue(events[-1]["model"])

    def test_the_conversation_is_saved_and_continues(self):
        first = self.ask(task="Fix this bug in my Python function")[-1]
        sid = first["session"]
        self.assertTrue(sid)
        self.ask(task="now make it recursive", session=sid)
        status, data = self.call("GET", f"/api/sessions/{sid}")
        self.assertEqual(len(json.loads(data)["turns"]), 2)
        listed = json.loads(self.call("GET", "/api/sessions")[1])["sessions"]
        self.assertEqual([s["id"] for s in listed], [sid])
        self.assertEqual(json.loads(self.call("POST", "/api/sessions/delete", {"id": sid})[1]), {"deleted": True})
        self.assertEqual(self.call("GET", f"/api/sessions/{sid}")[0], 404)

    def test_no_session_is_saved_when_saving_is_off(self):
        os.environ["ROUTER_NO_SESSIONS"] = "1"
        self.addCleanup(os.environ.pop, "ROUTER_NO_SESSIONS", None)
        done = self.ask(task="hello there")[-1]
        self.assertIsNone(done["session"])
        self.assertEqual(json.loads(self.call("GET", "/api/sessions")[1])["sessions"], [])

    def test_compare_returns_one_result_per_model(self):
        done = self.ask(task="hello", compare=True, models=["claude", "codex"])[-1]
        self.assertEqual([r["model"] for r in done["compare"]], ["claude", "codex"])

    def test_an_empty_task_or_bad_mode_is_an_error_event_not_a_crash(self):
        self.assertEqual(self.ask(task="   ")[-1]["type"], "error")
        self.assertEqual(self.ask(task="hi", connectors="maybe")[-1]["type"], "error")
        self.assertEqual(self.ask(task="hi", model="gpt-9")[-1]["type"], "error")

    def test_a_failing_model_reports_the_failure(self):
        os.environ["FAKE_CLAUDE_MODE"] = os.environ["FAKE_CODEX_MODE"] = os.environ["FAKE_AGY_MODE"] = "fail"
        done = self.ask(task="hello")[-1]
        self.assertEqual(done["type"], "done")
        self.assertFalse(done["ok"])


class PageTests(unittest.TestCase):
    def test_the_page_loads_nothing_from_outside(self):
        for bad in ("<script src", "<link ", "<img", "@import", "url("):
            self.assertNotIn(bad, ui.PAGE)

    def test_the_markdown_renderer_escapes_before_it_builds_tags(self):
        self.assertIn("esc(src).replace", ui.PAGE)


if __name__ == "__main__":
    unittest.main()
