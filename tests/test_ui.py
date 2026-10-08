import base64, http.client, json, os, stat, subprocess, sys, tempfile, threading, unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import ui, ui_page  # noqa: E402


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


FAKE_MCP = [sys.executable, str(ROOT / "tests" / "fake_bin" / "fake_mcp")]


class ConnectorTests(UiTests):
    def post(self, path, body):
        status, data = self.call("POST", path, body)
        return status, json.loads(data)

    def test_it_starts_empty_and_offers_the_templates(self):
        st = json.loads(self.call("GET", "/api/connectors")[1])
        self.assertEqual(st["connectors"], [])
        self.assertTrue({"filesystem", "memory", "github"} <= {t["key"] for t in st["templates"]})

    def test_a_free_command_needs_the_confirmation_the_page_collects(self):
        body = {"name": "fake", "command": " ".join(FAKE_MCP)}
        status, out = self.post("/api/connectors/add", body)
        self.assertEqual(status, 400)
        self.assertIn("confirm", out["error"])
        self.assertEqual(json.loads(self.call("GET", "/api/connectors")[1])["connectors"], [])
        self.assertEqual(self.post("/api/connectors/add", dict(body, confirm=True))[0], 200)
        self.assertEqual([c["name"] for c in json.loads(self.call("GET", "/api/connectors")[1])["connectors"]], ["fake"])

    def test_the_command_is_never_run_through_a_shell(self):
        marker = os.path.join(self.tmp.name, "pwned")
        self.post("/api/connectors/add", {"name": "x", "command": f"echo hi; touch {marker} $(touch {marker})", "confirm": True})
        spec = json.loads(self.call("GET", "/api/connectors")[1])["connectors"][0]
        self.assertEqual(spec["kind"], "local")
        self.assertFalse(os.path.exists(marker))                      # saving it ran nothing
        self.post("/api/connectors/test", {"name": "x"})
        self.assertFalse(os.path.exists(marker))                      # and neither did starting it: `;` and `$()` were plain characters of the program's arguments

    def test_test_lists_the_tools_without_spending_quota(self):
        self.post("/api/connectors/add", {"name": "fake", "command": " ".join(FAKE_MCP), "confirm": True})
        status, out = self.post("/api/connectors/test", {"name": "fake"})
        self.assertTrue(out["ok"])
        self.assertEqual(out["tools"], ["echo", "shout"])

    def test_a_connector_that_cannot_start_reports_why(self):
        self.post("/api/connectors/add", {"name": "dead", "command": "/nonexistent/program", "confirm": True})
        out = self.post("/api/connectors/test", {"name": "dead"})[1]
        self.assertFalse(out["ok"])
        self.assertTrue(out["error"])

    def test_turn_off_on_and_remove(self):
        self.post("/api/connectors/add", {"name": "fake", "command": " ".join(FAKE_MCP), "confirm": True})
        self.post("/api/connectors/disable", {"name": "fake"})
        self.assertFalse(json.loads(self.call("GET", "/api/connectors")[1])["connectors"][0]["enabled"])
        self.post("/api/connectors/enable", {"name": "fake"})
        self.assertTrue(json.loads(self.call("GET", "/api/connectors")[1])["connectors"][0]["enabled"])
        self.post("/api/connectors/remove", {"name": "fake"})
        self.assertEqual(json.loads(self.call("GET", "/api/connectors")[1])["connectors"], [])
        self.assertEqual(self.post("/api/connectors/remove", {"name": "fake"})[0], 400)

    def test_secret_values_are_never_sent_to_the_page(self):
        self.post("/api/connectors/add", {"name": "fake", "command": " ".join(FAKE_MCP), "env": "API_KEY=sk-live-123456", "confirm": True})
        self.post("/api/connectors/add", {"name": "remote", "url": "https://example.com/mcp", "headers": "Authorization: Bearer topsecret"})
        raw = self.call("GET", "/api/connectors")[1].decode()
        self.assertNotIn("sk-live-123456", raw)
        self.assertNotIn("topsecret", raw)
        self.assertIn("API_KEY", raw)                                  # the NAME of the variable is fine
        rows = {c["name"]: c for c in json.loads(raw)["connectors"]}
        self.assertEqual(rows["fake"]["literal_secrets"], ["API_KEY"])  # and the page is told a literal secret is stored

    def test_a_template_and_a_url_need_no_confirmation(self):
        self.assertEqual(self.post("/api/connectors/add", {"name": "mem", "template": "memory"})[0], 200)
        self.assertEqual(self.post("/api/connectors/add", {"name": "remote", "url": "https://example.com/mcp"})[0], 200)
        self.assertEqual(self.post("/api/connectors/add", {"name": "bad", "template": "nope"})[0], 400)
        self.assertEqual(self.post("/api/connectors/add", {"name": "BAD NAME", "url": "https://x.io"})[0], 400)
        self.assertEqual(self.post("/api/connectors/add", {"name": "u", "url": "ftp://x.io"})[0], 400)
        self.assertEqual(self.post("/api/connectors/add", {"name": "e", "command": "   ", "confirm": True})[0], 400)

    def test_a_connector_added_in_the_page_is_seen_by_the_cli(self):
        self.post("/api/connectors/add", {"name": "fake", "command": " ".join(FAKE_MCP), "confirm": True})
        out = subprocess.run([sys.executable, str(ROOT / "cli.py"), "connectors", "list"], capture_output=True, text=True, input="", timeout=30,
                             env=dict(os.environ, ROUTER_HOME=self.tmp.name)).stdout
        self.assertIn("fake", out)

    def test_agy_registration_goes_through_the_shared_helper(self):
        with mock.patch("ia_router.connectors.agy_register", return_value=(0, ["Running: agy mcp add"])) as reg:
            out = self.call("POST", "/api/connectors/agy", {"install": True})[1]
        reg.assert_called_once_with(True)
        self.assertTrue(json.loads(out)["ok"])


class ParityTests(UiTests):
    def post(self, path, body=None):
        status, data = self.call("POST", path, body or {})
        return status, json.loads(data)

    def test_route_preview_spends_nothing_and_ranks_the_models(self):
        status, out = self.post("/api/route", {"task": "Fix this bug in my Python function"})
        self.assertEqual(status, 200)
        self.assertEqual({r["model"] for r in out["ranking"]}, {"claude", "codex", "antigravity"})
        self.assertIn("score", out["text"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "log.jsonl")))
        self.assertEqual(self.post("/api/route", {"task": ""})[0], 400)

    def test_scores_for_every_category_and_a_bad_one(self):
        status, data = self.call("GET", "/api/scores?category=coding")
        out = json.loads(data)
        self.assertEqual(status, 200)
        self.assertIn("coding", out["categories"])
        self.assertTrue(out["table"])
        self.assertEqual(self.call("GET", "/api/scores?category=nope")[0], 400)

    def test_priorities_are_validated_previewed_and_saved(self):
        info = json.loads(self.call("GET", "/api/priorities")[1])
        self.assertEqual([g["key"] for g in info["groups"]], ["coding", "writing", "analysis", "math", "quick"])
        self.assertTrue({"precision", "balanced"} <= {o["key"] for o in info["options"]})
        self.assertEqual(self.post("/api/priorities", {"answers": {"nope": "balanced"}})[0], 400)
        self.assertEqual(self.post("/api/priorities", {"answers": {"coding": "teleport"}})[0], 400)
        status, out = self.post("/api/priorities", {"answers": {"coding": "balanced"}})          # preview only
        self.assertEqual((status, out["saved"]), (200, False))
        self.assertEqual(json.loads(self.call("GET", "/api/priorities")[1])["answers"], {})
        self.post("/api/priorities", {"answers": {"coding": "balanced"}, "save": True})
        self.assertEqual(json.loads(self.call("GET", "/api/priorities")[1])["answers"], {"coding": "balanced"})

    def test_login_check_probes_the_installed_clis_and_remembers_a_missing_login(self):
        out = self.post("/api/probe")[1]
        self.assertEqual({n: r["auth"] for n, r in out["checked"].items()}, {"claude": "ok", "codex": "ok", "antigravity": "ok"})
        os.environ["FAKE_CODEX_MODE"] = "auth"
        self.addCleanup(os.environ.pop, "FAKE_CODEX_MODE", None)
        self.assertEqual(self.post("/api/probe")[1]["checked"]["codex"]["auth"], "missing")
        codex = [m for m in json.loads(self.call("GET", "/api/state")[1])["models"] if m["name"] == "codex"][0]
        self.assertEqual(codex["auth"], "missing")
        self.assertEqual(self.post("/api/reset-cooldowns")[0], 200)
        codex = [m for m in json.loads(self.call("GET", "/api/state")[1])["models"] if m["name"] == "codex"][0]
        self.assertNotEqual(codex["auth"], "missing")

    def test_state_has_what_doctor_and_stats_show(self):
        self.ask(task="hello there")
        st = json.loads(self.call("GET", "/api/state")[1])
        self.assertIn("claude", st["cooldowns"])
        self.assertTrue(st["stats"])
        self.assertTrue(st["metrics_line"])

    def test_sessions_can_be_switched_off_and_cleared_from_the_page(self):
        self.ask(task="hello there")
        self.assertEqual(self.post("/api/sessions/saving", {"on": False})[1], {"enabled": False})
        self.ask(task="and again")
        self.assertEqual(len(json.loads(self.call("GET", "/api/sessions")[1])["sessions"]), 1)    # nothing new was stored
        self.post("/api/sessions/saving", {"on": True})
        self.assertEqual(self.post("/api/sessions/clear")[1]["deleted"], 1)
        self.assertEqual(json.loads(self.call("GET", "/api/sessions")[1])["sessions"], [])

    def test_metrics_refresh_streams_its_progress_and_ends_with_the_sources(self):
        def fake(cfg, say=print, force=False, **_):
            say("Downloading Arena…")
            return True
        with mock.patch("ia_router.scoring.refresh_and_report", fake):
            status, data = self.call("POST", "/api/metrics/refresh", {"force": True})
        events = [json.loads(l) for l in data.decode().splitlines()]
        self.assertEqual(events[0], {"type": "line", "text": "Downloading Arena…"})
        self.assertEqual(events[-1]["type"], "done")
        self.assertTrue(events[-1]["ok"])

    def test_files_chosen_in_the_browser_reach_the_model_and_are_private(self):
        up = self.post("/api/upload", {"name": "../../notes.txt", "data": base64.b64encode(b"remember the milk").decode()})[1]
        self.assertTrue(up["path"].startswith(os.path.join(self.tmp.name, "uploads")))
        self.assertEqual(os.path.basename(up["path"]).split("-", 1)[1], "notes.txt")          # the folder part of the name is gone
        self.assertEqual(stat.S_IMODE(os.stat(up["path"]).st_mode), 0o600)
        done = self.ask(task="Summarize the attached file", files=[up["path"]])[-1]
        self.assertTrue(done["ok"])
        sent = int(done["output"].split("received ")[1].split(" chars")[0])
        self.assertGreater(sent, len("Summarize the attached file") + len("remember the milk"))   # the file's text went into the prompt

    def test_the_page_cannot_attach_files_it_did_not_upload(self):
        done = self.ask(task="Summarize it", files=["/etc/hosts"])[-1]
        self.assertEqual(done["type"], "error")
        self.assertIn("uploaded", done["text"])
        self.assertEqual(self.post("/api/upload", {"name": "a.txt", "data": "***not base64***"})[0], 400)

    def test_old_uploads_are_pruned(self):
        up = self.post("/api/upload", {"name": "old.txt", "data": base64.b64encode(b"x").decode()})[1]
        old = os.stat(up["path"]).st_mtime - 8 * 86400
        os.utime(up["path"], (old, old))
        self.assertEqual(ui.prune_uploads(), 1)
        self.assertFalse(os.path.exists(up["path"]))


class CliParityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args], capture_output=True, text=True, input="", timeout=60,
                              env=dict(os.environ, ROUTER_HOME=self.tmp.name))

    def test_sessions_on_off_without_opening_the_chat(self):
        self.assertIn("OFF", self.cli("sessions", "off").stdout)
        self.assertIn("No saved conversations yet. (Saving is off.)", self.cli("sessions").stdout)
        self.assertIn("ON", self.cli("sessions", "on").stdout)

    def test_priorities_set_and_show_work_without_a_terminal(self):
        out = self.cli("priorities", "--set", "coding=balanced")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("Saved.", out.stdout)
        self.assertIn("balanced", self.cli("priorities", "--show").stdout)
        bad = self.cli("priorities", "--set", "coding=teleport")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("not available", bad.stderr)


class ParityMapTests(unittest.TestCase):
    """The rule: whatever the CLI can do, the UI can do, and the other way round. A new subcommand must be mapped to a UI route here (or declared
    not applicable with a reason), so parity is decided on purpose and never forgotten."""
    UI_ROUTE = {"chat": "/api/ask", "ask": "/api/ask", "route": "/api/route", "sessions": "/api/sessions", "doctor": "/api/probe", "setup": "/api/probe",
                "stats": "/api/state", "usage": "/api/state", "scores": "/api/scores", "metrics": "/api/metrics", "priorities": "/api/priorities",
                "connectors": "/api/connectors", "reset-cooldowns": "/api/reset-cooldowns"}
    NOT_APPLICABLE = {"mcp": "serves the router to other programs over stdio", "ui": "is the UI itself"}

    def test_every_cli_subcommand_has_a_ui_route_or_a_reason(self):
        import re
        src = (ROOT / "ia_router" / "cli.py").read_text(encoding="utf-8")
        commands = set(re.findall(r'sub\.add_parser\("([a-z-]+)"', src)) | {"route", "ask"}
        self.assertEqual(sorted(commands - set(self.UI_ROUTE) - set(self.NOT_APPLICABLE)), [], "map the new command to a UI route (and build it) or say why it does not apply")

    def test_the_mapped_routes_exist(self):
        src = (ROOT / "ia_router" / "ui.py").read_text(encoding="utf-8")
        for command, route in self.UI_ROUTE.items():
            self.assertIn(f'"{route}', src, f"{command} -> {route}")

    def test_what_only_the_ui_used_to_do_exists_in_the_cli(self):          # the other direction
        out = subprocess.run([sys.executable, str(ROOT / "cli.py"), "--help"], capture_output=True, text=True, input="", timeout=30).stdout
        self.assertIn("sessions", out)
        help_priorities = subprocess.run([sys.executable, str(ROOT / "cli.py"), "priorities", "--help"], capture_output=True, text=True, input="", timeout=30).stdout
        self.assertIn("--set", help_priorities)


class PageTests(unittest.TestCase):
    page = ui_page.render("TOKEN123")

    def test_the_page_loads_nothing_from_outside(self):
        for bad in ("<script src", "<img", "@import", "url(", 'rel="stylesheet"', 'src="http'):
            self.assertNotIn(bad, self.page)
        self.assertEqual(self.page.count("<link "), 1)          # only the tab icon, which is a data: URI
        self.assertIn('href="data:image/svg+xml,', self.page)

    def test_every_placeholder_is_filled(self):
        import re
        self.assertEqual(re.findall(r"__[A-Z0-9]+__", self.page), [])
        self.assertIn("TOKEN123", self.page)

    def test_the_brand_is_the_terminals(self):               # one identity for the CLI and the UI
        from ia_router import banner
        self.assertIn(banner._TAGLINE, self.page)
        self.assertIn(banner._GITHUB, self.page)
        self.assertIn(banner._URL, self.page)
        self.assertIn("rgb(%d,%d,%d)" % tuple(banner._BRAND[0]), self.page)
        for row in range(3):                                  # the box-drawing wordmark, row by row
            self.assertIn("".join(banner._GLYPHS[c][row] for c in banner._WORD), self.page)

    def test_the_credit_links_open_safely(self):
        self.assertIn('href="https://github.com/Mgobeaalcoba" target="_blank" rel="noopener noreferrer"', self.page)
        self.assertIn('href="https://mgatc.com" target="_blank" rel="noopener noreferrer"', self.page)

    def test_the_markdown_renderer_escapes_before_it_builds_tags(self):
        self.assertIn("esc(src).replace", self.page)


if __name__ == "__main__":
    unittest.main()
