import http.server, json, os, stat, subprocess, sys, tempfile, threading, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import adapters, connectors as C, core  # noqa: E402

FAKE = [sys.executable, str(ROOT / "tests" / "fake_bin" / "fake_mcp")]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]

    def tearDown(self):
        self.tmp.cleanup()


class RegistryTests(Base):
    def test_add_list_disable_remove(self):
        C.add("gmail", command=FAKE, env={"FAKE_TOKEN": "${MY_TOKEN}"})
        C.add("crm", url="https://example.com/mcp", headers={"Authorization": "Bearer ${CRM_KEY}"})
        self.assertEqual(sorted(C.load()), ["crm", "gmail"])
        self.assertTrue(C.has_connectors())
        C.set_enabled("gmail", False)
        self.assertEqual(list(C.enabled_servers()), ["crm"])
        self.assertTrue(C.remove("crm"))
        self.assertFalse(C.remove("crm"))
        self.assertFalse(C.has_connectors())

    def test_the_file_is_private_and_a_damaged_one_is_empty(self):
        C.add("gmail", command=FAKE)
        self.assertEqual(stat.S_IMODE(C.registry_path().stat().st_mode), 0o600)
        C.registry_path().write_text("{not json", encoding="utf-8")
        self.assertEqual(C.load(), {})

    def test_validation(self):
        for bad in ("Gmail", "my_crm", "", "-x", "a" * 40):
            with self.assertRaises(ValueError, msg=bad):
                C.add(bad, command=FAKE)
        with self.assertRaises(ValueError):
            C.add("both", command=FAKE, url="https://x.io")
        with self.assertRaises(ValueError):
            C.add("none")
        with self.assertRaises(ValueError):
            C.add("badurl", url="ftp://x.io")

    def test_pairs_and_env_expansion(self):
        self.assertEqual(C.parse_pairs(["A=1", "B=x=y"], "="), {"A": "1", "B": "x=y"})
        self.assertEqual(C.parse_pairs(["Authorization: Bearer t"], ":"), {"Authorization": "Bearer t"})
        with self.assertRaises(ValueError):
            C.parse_pairs(["novalue"], "=")
        self.assertEqual(C.expand("Bearer ${K}!", {"K": "abc"}), "Bearer abc!")
        self.assertEqual(C.expand("${MISSING}", {}), "")          # never leaks the literal reference
        self.assertEqual(C.secret_literals({"env": {"A": "plain", "B": "${B}"}, "headers": {"H": "x"}}), ["A", "H"])

    def test_exposed_names_are_valid_unique_and_short(self):
        taken = set()
        a = C.exposed_name("gmail", "search messages", taken)
        b = C.exposed_name("gmail", "search messages", taken)
        self.assertEqual(a, "gmail__search_messages")
        self.assertNotEqual(a, b)
        long = C.exposed_name("x", "t" * 100, set())
        self.assertLessEqual(len(long), 64)

    def test_allow_and_deny(self):
        tools = [{"name": "read"}, {"name": "send"}, {"name": "delete"}]
        self.assertEqual([t["name"] for t in C.filter_tools(tools, {})], ["read", "send", "delete"])
        self.assertEqual([t["name"] for t in C.filter_tools(tools, {"deny": ["delete"]})], ["read", "send"])
        self.assertEqual([t["name"] for t in C.filter_tools(tools, {"allow": ["read"]})], ["read"])


class ProxyTests(Base):
    def proxy(self, servers):
        p = C.Proxy(servers)
        self.addCleanup(p.close)
        return p

    def rpc(self, p, method, params=None, mid=1):
        return p.handle({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})

    def test_aggregates_tools_from_several_servers_with_prefixes(self):
        p = self.proxy({"one": {"command": FAKE}, "two": {"command": FAKE, "env": {"FAKE_MCP_TOOLS": "extra"}}})
        names = [t["name"] for t in self.rpc(p, "tools/list")["result"]["tools"]]
        self.assertEqual(names, ["one__echo", "one__shout", "two__echo", "two__shout", "two__extra"])
        self.assertTrue(self.rpc(p, "tools/list")["result"]["tools"][0]["description"].startswith("[one]"))

    def test_calls_are_forwarded_to_the_right_server_with_its_own_env(self):
        p = self.proxy({"one": {"command": FAKE, "env": {"FAKE_TOKEN": "t1"}}, "two": {"command": FAKE, "env": {"FAKE_TOKEN": "${T2}"}}})
        os.environ["T2"] = "from-env"
        self.addCleanup(os.environ.pop, "T2", None)
        r1 = self.rpc(p, "tools/call", {"name": "one__shout", "arguments": {"text": "hi"}})["result"]
        r2 = self.rpc(p, "tools/call", {"name": "two__echo", "arguments": {"text": "hi"}})["result"]
        self.assertEqual(r1["content"][0]["text"], "HI|token=t1")
        self.assertEqual(r2["content"][0]["text"], "hi|token=from-env")

    def test_a_failing_server_is_skipped_not_fatal(self):
        p = self.proxy({"good": {"command": FAKE}, "bad": {"command": FAKE, "env": {"FAKE_MCP_MODE": "die"}}, "missing": {"command": ["/no/such/binary"]}})
        names = [t["name"] for t in self.rpc(p, "tools/list")["result"]["tools"]]
        self.assertEqual(names, ["good__echo", "good__shout"])
        self.assertEqual(sorted(p.errors), ["bad", "missing"])

    def test_deny_hides_a_tool_and_it_cannot_be_called(self):
        p = self.proxy({"one": {"command": FAKE, "deny": ["shout"]}})
        self.assertEqual([t["name"] for t in self.rpc(p, "tools/list")["result"]["tools"]], ["one__echo"])
        r = self.rpc(p, "tools/call", {"name": "one__shout", "arguments": {}})["result"]
        self.assertTrue(r["isError"])

    def test_protocol_basics(self):
        p = self.proxy({})
        init = self.rpc(p, "initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(init["serverInfo"]["name"], C.PROXY_NAME)
        self.assertEqual(self.rpc(p, "ping")["result"], {})
        self.assertIn("error", self.rpc(p, "nope"))
        self.assertIsNone(p.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_calls_are_logged_without_arguments(self):
        events = []
        p = C.Proxy({"one": {"command": FAKE}}, log=events.append)
        self.addCleanup(p.close)
        self.rpc(p, "tools/call", {"name": "one__echo", "arguments": {"text": "secret body"}})
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["server"], events[0]["tool"], events[0]["ok"]), ("one", "echo", True))
        self.assertNotIn("secret", json.dumps(events))

    def test_the_real_serve_loop_over_stdio(self):
        C.add("one", command=FAKE)
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
        out = subprocess.run([sys.executable, "-m", "ia_router", "connectors", "serve"], cwd=ROOT, input="\n".join(json.dumps(m) for m in msgs) + "\n",
                             capture_output=True, text=True, timeout=60, env=dict(os.environ, ROUTER_HOME=self.tmp.name))
        replies = [json.loads(line) for line in out.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])               # stdout carries only MCP messages
        self.assertEqual([t["name"] for t in replies[1]["result"]["tools"]], ["one__echo", "one__shout"])
        self.assertIn("ready", out.stderr)


class HttpTests(Base):
    def test_streamable_http_json_and_event_stream_replies(self):
        seen = []

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append((body.get("method"), self.headers.get("Authorization"), self.headers.get("Mcp-Session-Id")))
                if "id" not in body:
                    self.send_response(202); self.end_headers(); return
                res = {"tools": [{"name": "ping", "description": "p"}]} if body["method"] == "tools/list" else {"protocolVersion": "x"}
                payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": res})
                self.send_response(200)
                if body["method"] == "tools/list":
                    self.send_header("Content-Type", "text/event-stream"); data = f"event: message\ndata: {payload}\n\n".encode()
                else:
                    self.send_header("Content-Type", "application/json"); self.send_header("Mcp-Session-Id", "s1"); data = payload.encode()
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        os.environ["CRM_KEY"] = "k1"
        self.addCleanup(os.environ.pop, "CRM_KEY", None)
        c = C.make_client("crm", {"url": f"http://127.0.0.1:{srv.server_port}/mcp", "headers": {"Authorization": "Bearer ${CRM_KEY}"}})
        c.start()
        self.assertEqual([t["name"] for t in C.list_tools(c)], ["ping"])
        self.assertTrue(all(auth == "Bearer k1" for _, auth, _ in seen))
        self.assertEqual(seen[-1][2], "s1")                                 # the session id is echoed back

    def test_http_errors_are_reported_not_raised_raw(self):
        c = C.make_client("down", {"url": "http://127.0.0.1:9/mcp"})
        with self.assertRaises(C.McpError):
            c.start(timeout=2)


class InjectionTests(Base):
    def test_claude_gets_one_server_allowed_never_allow_everything(self):
        args = C.inject("claude", ["claude", "-p", "task"])
        self.assertEqual(args[0], "claude")
        cfg = json.loads(args[args.index("--mcp-config") + 1])
        self.assertEqual(list(cfg["mcpServers"]), [C.PROXY_NAME])
        self.assertEqual(cfg["mcpServers"][C.PROXY_NAME]["env"]["ROUTER_HOME"], self.tmp.name)   # CLIs trim the environment of MCP servers
        self.assertEqual(args[args.index("--allowedTools") + 1], f"mcp__{C.PROXY_NAME}")
        self.assertEqual(args[-2:], ["-p", "task"])                        # variadic flags must not swallow the prompt
        self.assertFalse(any("dangerously" in a for a in args))

    def test_codex_overrides_are_per_call_and_scoped_to_the_proxy(self):
        args = C.inject("codex", ["codex", "exec", "--skip-git-repo-check", "task"])
        self.assertEqual(args[:2], ["codex", "exec"])
        joined = " ".join(args)
        self.assertIn(f"mcp_servers.{C.PROXY_NAME}.command=", joined)
        self.assertIn('default_tools_approval_mode="approve"', joined)
        self.assertIn("env={ROUTER_HOME = " + json.dumps(self.tmp.name) + "}", joined)   # a TOML inline table, not JSON
        self.assertEqual(args[-2:], ["--skip-git-repo-check", "task"])
        self.assertFalse(any("dangerously" in a for a in args))

    def test_agy_has_no_per_call_option_and_a_registration_command(self):
        self.assertEqual(C.inject("agy", ["agy", "-p", "x"]), ["agy", "-p", "x"])
        self.assertEqual(C.install_agy()[:6], ["agy", "mcp", "add", "--env", f"ROUTER_HOME={self.tmp.name}", C.PROXY_NAME])
        del os.environ["ROUTER_HOME"]
        self.assertEqual(C.install_agy()[:5], ["agy", "mcp", "add", C.PROXY_NAME, "--"])
        self.assertEqual(C.uninstall_agy(), ["agy", "mcp", "remove", C.PROXY_NAME])

    def test_run_cli_adds_the_flags_only_when_asked_and_not_for_custom_commands(self):
        cfg = core.load_config(apply_scoring=False)
        spec = cfg["models"]["claude"]
        seen = {}
        real = adapters._run
        adapters._run = lambda name, spec, argv, *a, **k: seen.setdefault("argv", argv) and real(name, spec, argv, *a, **k)
        self.addCleanup(setattr, adapters, "_run", real)
        adapters.run_cli("claude", spec, "hi", usage=True)
        self.assertNotIn("--mcp-config", seen["argv"])
        seen.clear()
        adapters.run_cli("claude", spec, "hi", usage=True, mcp=True)
        self.assertIn("--mcp-config", seen["argv"])
        seen.clear()
        os.environ["ROUTER_CMD_CLAUDE"] = json.dumps(["claude", "-p", "{prompt}"])
        self.addCleanup(os.environ.pop, "ROUTER_CMD_CLAUDE", None)
        adapters.run_cli("claude", spec, "hi", usage=True, mcp=True)
        self.assertNotIn("--mcp-config", seen["argv"])


class AgySettingsTests(Base):
    def setUp(self):
        super().setUp()
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        old = os.environ.get("HOME")
        os.environ["HOME"] = self.home.name
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old) if old is not None else os.environ.pop("HOME", None))
        self.path = C.agy_settings_path()

    def test_the_rule_is_added_once_and_everything_else_is_kept(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"trustedWorkspaces": ["/x"], "permissions": {"allow": ["shell(ls)"]}}), encoding="utf-8")
        self.assertEqual(C.agy_allow_rule(True), "added")
        self.assertEqual(C.agy_allow_rule(True), "unchanged")
        d = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(d["permissions"]["allow"], ["shell(ls)", C.AGY_RULE])
        self.assertEqual(d["trustedWorkspaces"], ["/x"])
        self.assertEqual(C.agy_allow_rule(False), "removed")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["permissions"]["allow"], ["shell(ls)"])

    def test_removing_leaves_no_empty_permissions_behind(self):
        self.assertEqual(C.agy_allow_rule(True), "added")        # the file did not exist
        self.assertEqual(C.agy_allow_rule(False), "removed")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {})
        self.assertEqual(C.agy_allow_rule(False), "unchanged")

    def test_the_rule_never_allows_more_than_the_proxy(self):
        self.assertEqual(C.AGY_RULE, "mcp(ia-router-connectors/*)")

    def test_a_broken_settings_file_is_never_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{oops", encoding="utf-8")
        with self.assertRaises(ValueError):
            C.agy_allow_rule(True)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{oops")


class AskTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg = core.load_config()

    def test_automatic_mode_follows_the_registry(self):
        self.assertFalse(core.ask("hello", self.cfg, dry_run=True)["connectors"])
        C.add("gmail", command=FAKE)
        self.assertTrue(core.ask("hello", self.cfg, dry_run=True)["connectors"])
        self.assertFalse(core.ask("hello", self.cfg, dry_run=True, connectors=False)["connectors"])
        C.set_enabled("gmail", False)
        self.assertFalse(core.ask("hello", self.cfg, dry_run=True)["connectors"])
        self.assertTrue(core.ask("hello", self.cfg, dry_run=True, connectors=True)["connectors"])

    def test_a_real_task_still_runs_with_connectors_on(self):
        C.add("gmail", command=FAKE)
        res = core.ask("Fix this bug in my Python function", self.cfg)
        self.assertTrue(res["ok"])
        self.assertTrue(res["connectors"])


if __name__ == "__main__":
    unittest.main()
