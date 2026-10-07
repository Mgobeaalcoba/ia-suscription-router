import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class McpTests(unittest.TestCase):
    def test_roundtrip(self):
        env = dict(os.environ, ROUTER_HOME=tempfile.mkdtemp(),
                   PATH=str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"])
        msgs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "route_task", "arguments": {"task": "Fix this bug in my Python function"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "ask_model", "arguments": {"task": "Draft an email", "model": "claude"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "ask_model", "arguments": {"task": ""}}},
        ]
        proc = subprocess.run([sys.executable, str(ROOT / "cli.py"), "mcp"], input="\n".join(map(json.dumps, msgs)) + "\n",
                              capture_output=True, text=True, env=env, timeout=30)
        out = {json.loads(l)["id"]: json.loads(l) for l in proc.stdout.splitlines()}
        self.assertEqual(set(out), {1, 2, 3, 4, 5})  # the notification gets no response
        self.assertEqual(out[1]["result"]["serverInfo"]["name"], "ia-suscription-router")
        self.assertEqual({t["name"] for t in out[2]["result"]["tools"]}, {"route_task", "ask_model", "list_models"})
        self.assertIn("chosen: codex", out[3]["result"]["content"][0]["text"])
        self.assertIn("[fake-claude]", out[4]["result"]["content"][0]["text"])
        self.assertTrue(out[5]["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
