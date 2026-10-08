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
        adapters.run_cli("claude", spec, "hi", usage=True, mcp=["gmail"])
        self.assertIn("--mcp-config", seen["argv"])
        self.assertIn("IA_ROUTER_CONNECTORS", seen["argv"][seen["argv"].index("--mcp-config") + 1])
        seen.clear()
        os.environ["ROUTER_CMD_CLAUDE"] = json.dumps(["claude", "-p", "{prompt}"])
        self.addCleanup(os.environ.pop, "ROUTER_CMD_CLAUDE", None)
        adapters.run_cli("claude", spec, "hi", usage=True, mcp=["gmail"])
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


class SelectionTests(Base):
    SERVERS = {"gmail": {"command": FAKE}, "calendar": {"command": FAKE}, "files": {"command": FAKE}, "crm": {"command": FAKE},
               "mail-archive": {"command": FAKE, "when": ["archive"]}, "always-on": {"command": FAKE, "always": True}}

    def test_a_task_gets_only_the_connectors_it_needs(self):
        sel = lambda t: C.select(t, self.SERVERS)
        self.assertEqual(sel("summarize my unread emails from today"), ["gmail", "crm", "always-on"])
        self.assertEqual(sel("put a 30 minute meeting on my calendar tomorrow"), ["calendar", "crm", "always-on"])
        self.assertEqual(sel("Fix this bug in my Python function"), ["crm", "always-on"])

    def test_spanish_tasks_work_too(self):
        self.assertIn("gmail", C.select("resumí los correos sin leer", self.SERVERS))
        self.assertIn("calendar", C.select("agendá una reunión mañana", self.SERVERS))
        self.assertIn("files", C.select("leé el archivo de la carpeta", self.SERVERS))

    def test_unknown_connectors_are_always_attached_never_silently_dropped(self):
        self.assertIn("crm", C.select("anything at all", self.SERVERS))
        self.assertIsNone(C.family_words("crm", {}))

    def test_when_replaces_the_builtin_words_and_the_name_always_counts(self):
        self.assertNotIn("mail-archive", C.select("check my email", self.SERVERS))      # `when` replaced the mail words
        self.assertIn("mail-archive", C.select("search the archive", self.SERVERS))
        self.assertIn("mail-archive", C.select("open mail-archive please", self.SERVERS))

    def test_whole_words_only(self):
        self.assertEqual(C.select("my profile is nice", {"files": {"command": FAKE}}), [])   # "file" inside "profile" must not match
        self.assertEqual(C.select("read the file", {"files": {"command": FAKE}}), ["files"])

    def test_the_proxy_exposes_only_the_calls_selection(self):
        C.add("one", command=FAKE)
        C.add("two", command=FAKE)
        os.environ[C.ENV_SELECTION] = "two"
        self.addCleanup(os.environ.pop, C.ENV_SELECTION, None)
        self.assertEqual(list(C.active_servers()), ["two"])
        os.environ[C.ENV_SELECTION] = ""
        self.assertEqual(C.active_servers(), {})                                         # an empty selection means none, not all
        del os.environ[C.ENV_SELECTION]
        self.assertEqual(sorted(C.active_servers()), ["one", "two"])                     # no selection at all: everything enabled
        C.write_selection(["one"])
        self.assertEqual(list(C.active_servers()), ["one"])                              # the file used for agy
        C.clear_selection()
        self.assertEqual(sorted(C.active_servers()), ["one", "two"])

    def test_the_selection_travels_in_the_cli_arguments(self):
        claude = C.inject("claude", ["claude", "-p", "x"], ["gmail", "calendar"])
        cfg = json.loads(claude[claude.index("--mcp-config") + 1])
        self.assertEqual(cfg["mcpServers"][C.PROXY_NAME]["env"][C.ENV_SELECTION], "gmail,calendar")
        codex = " ".join(C.inject("codex", ["codex", "exec", "x"], ["gmail"]))
        self.assertIn(C.ENV_SELECTION + ' = "gmail"', codex)


class AskTests(Base):
    def setUp(self):
        super().setUp()
        self.cfg = core.load_config()
        C.add("gmail", command=FAKE)
        C.add("calendar", command=FAKE)

    def test_automatic_mode_attaches_only_what_the_task_needs(self):
        ask = lambda t, **k: core.ask(t, self.cfg, dry_run=True, **k)["connectors"]
        self.assertEqual(ask("read my unread email"), ["gmail"])
        self.assertEqual(ask("Fix this bug in my Python function"), [])
        self.assertEqual(ask("email my calendar to Ana"), ["gmail", "calendar"])

    def test_all_and_none_override_the_selection(self):
        self.assertEqual(core.ask("Fix this bug", self.cfg, dry_run=True, connectors=True)["connectors"], ["gmail", "calendar"])
        self.assertEqual(core.ask("read my email", self.cfg, dry_run=True, connectors=False)["connectors"], [])

    def test_a_disabled_connector_is_never_attached(self):
        C.set_enabled("gmail", False)
        self.assertEqual(core.ask("read my email", self.cfg, dry_run=True)["connectors"], [])
        self.assertEqual(core.ask("read my email", self.cfg, dry_run=True, connectors=True)["connectors"], ["calendar"])

    def test_a_short_followup_is_matched_with_the_topic_it_inherits(self):
        res = core.ask("do it now", self.cfg, dry_run=True, route_text="read my unread email\ndo it now")
        self.assertEqual(res["connectors"], ["gmail"])

    def test_a_real_task_still_runs(self):
        res = core.ask("read my unread email", self.cfg)
        self.assertTrue(res["ok"])
        self.assertEqual(res["connectors"], ["gmail"])


if __name__ == "__main__":
    unittest.main()


class TemplateTests(Base):
    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args], capture_output=True, text=True, env=dict(os.environ, ROUTER_HOME=self.tmp.name),
                              timeout=60, cwd=ROOT, input="")

    def test_the_builtin_templates_are_only_official_servers(self):
        self.assertEqual(sorted(C.TEMPLATES), ["filesystem", "github", "memory"])
        for key, t in C.TEMPLATES.items():
            self.assertTrue(t["source"].startswith("https://github.com/"), key)
            self.assertTrue(t["source"].split("/")[3] in ("modelcontextprotocol", "github"), key)     # published by the MCP project or the vendor
        self.assertFalse(C.TEMPLATES["github"]["verified"])           # we never ran it against a real token: it says so

    def test_a_local_template_builds_the_command_with_the_extra_arguments(self):
        built = C.from_template("filesystem", ["/work", "/docs"])
        self.assertEqual(built["command"][-3:], ["@modelcontextprotocol/server-filesystem", "/work", "/docs"])
        self.assertEqual(built["command"][:2], ["npx", "-y"])
        self.assertEqual(C.from_template("memory")["command"][-1], "@modelcontextprotocol/server-memory")

    def test_a_remote_template_builds_the_url_and_a_header_that_references_the_environment(self):
        built = C.from_template("github")
        self.assertEqual(built["url"], "https://api.githubcopilot.com/mcp/")
        self.assertEqual(built["headers"]["Authorization"], "Bearer ${GITHUB_TOKEN}")       # never a literal secret
        self.assertEqual(C.missing_env("github"), ["GITHUB_TOKEN"] if not os.environ.get("GITHUB_TOKEN") else [])

    def test_errors_are_explained(self):
        for args, needle in ((("nope",), "unknown template"), (("filesystem",), "needs one or more folders"), (("github", ["x"]), "takes no extra arguments")):
            with self.assertRaises(ValueError) as cm:
                C.from_template(*args)
            self.assertIn(needle, str(cm.exception))

    def test_your_own_templates_extend_and_replace_the_builtin_ones(self):
        C.templates_path().write_text(json.dumps({"mail": {"description": "my trusted one", "command": ["my-mail-mcp"]},
                                                  "memory": {"description": "mine", "command": ["my-memory"]}, "broken": {"description": "no command"}}), encoding="utf-8")
        t = C.all_templates()
        self.assertIn("mail", t)
        self.assertNotIn("broken", t)
        self.assertEqual(C.from_template("memory")["command"], ["my-memory"])
        C.templates_path().write_text("{not json", encoding="utf-8")
        self.assertEqual(sorted(C.all_templates()), ["filesystem", "github", "memory"])

    def test_the_cli_lists_adds_and_tests_a_real_template(self):
        listing = self.cli("connectors", "templates").stdout
        self.assertIn("filesystem", listing)
        self.assertIn("not verified", listing)
        self.assertIn("pick them yourself", listing)
        r = self.cli("connectors", "add", "files", "--template", "filesystem", "--", self.tmp.name)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(C.load()["files"]["command"][-1], self.tmp.name)
        self.assertEqual(self.cli("connectors", "add", "x", "--template", "filesystem").returncode, 2)
        r = self.cli("connectors", "add", "gh", "--template", "github")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(C.load()["gh"]["headers"]["Authorization"], "Bearer ${GITHUB_TOKEN}")
