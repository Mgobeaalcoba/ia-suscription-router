import os, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import adapters, chat, core, render, state  # noqa: E402


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        os.environ["CODEX_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith("FAKE_") or k.startswith("ROUTER_CMD_"):
                del os.environ[k]
        self.cfg = core.load_config(apply_manifest=False)

    def tearDown(self):
        self.tmp.cleanup()

    def run_model(self, name, prompt="hola"):
        return adapters.run_cli(name, self.cfg["models"][name], prompt, usage=True)

    def test_claude_model_and_tokens_include_cache(self):
        r = self.run_model("claude")
        self.assertTrue(r["ok"])
        self.assertEqual(r["model_id"], "claude-fake-1")
        self.assertEqual(r["tokens"], {"input": 152, "output": 7, "cached": 50, "reasoning": 0})
        self.assertTrue(r["output"].startswith("[fake-claude]"))

    def test_codex_tokens_and_model_from_session_file(self):
        d = Path(self.tmp.name) / "sessions" / "2026" / "10" / "03"
        d.mkdir(parents=True)
        (d / "rollout-2026-10-03T00-00-00-t-fake.jsonl").write_text('{"type":"turn_context","payload":{"model":"gpt-fake"}}\n')
        r = self.run_model("codex")
        self.assertEqual(r["model_id"], "gpt-fake")
        self.assertEqual(r["tokens"], {"input": 1000, "output": 12, "cached": 600, "reasoning": 3})

    def test_codex_without_session_file_has_tokens_but_no_model(self):
        r = self.run_model("codex")
        self.assertIsNone(r["model_id"])
        self.assertEqual(r["tokens"]["output"], 12)

    def test_antigravity_model_read_from_log_and_temp_file_removed(self):
        r = self.run_model("antigravity")
        self.assertEqual(r["model_id"], "Fake Flash (High)")
        self.assertEqual(r["tokens"], {"input": 500, "output": 40, "cached": 0, "reasoning": 30})
        self.assertEqual([f for f in os.listdir(tempfile.gettempdir()) if f.startswith("ia-router-agy-")], [])

    def test_explicit_model_flag_wins(self):
        self.assertEqual(adapters.parse_usage("agy", '{"status":"SUCCESS","response":"x"}', ["agy", "--model", "m1"])[1]["model"], "m1")

    def test_plain_output_falls_back_without_usage(self):
        os.environ["ROUTER_CMD_CLAUDE"] = '["claude","-p","{prompt}"]'  # comando propio: no se le agregan flags
        r = self.run_model("claude")
        self.assertTrue(r["ok"])
        self.assertIsNone(r["tokens"])

    def test_garbled_json_keeps_raw_text(self):
        text, info, err = adapters.parse_usage("claude", "no soy json", ["claude"])
        self.assertIsNone(text)

    def test_json_error_flag_marks_failure(self):
        text, _, err = adapters.parse_usage("claude", '{"is_error":true,"result":"Please run /login"}', ["claude"])
        self.assertTrue(err)

    def test_ask_returns_and_logs_usage(self):
        res = core.ask("Arreglá este bug en Python", self.cfg, model="claude")
        self.assertEqual(res["model_id"], "claude-fake-1")
        self.assertEqual(core.format_usage(res), "claude (claude-fake-1) · in 152 (50 caché) · out 7")
        st = state.stats()["claude"]
        self.assertEqual((st["tokens_in"], st["tokens_out"]), (152, 7))

    def test_fmt_tokens(self):
        self.assertEqual(adapters.fmt_tokens(None), "tokens n/d")
        self.assertEqual(adapters.fmt_tokens({"input": 15681, "output": 5, "cached": 13184, "reasoning": 0}), "in 15.7k (13.2k caché) · out 5")

    def test_ask_yes_survives_eof(self):
        def eof(_):
            raise EOFError
        self.assertTrue(chat.Chat(read=eof, write=lambda s: None).ask_yes("¿ok?"))
        self.assertFalse(chat.Chat(read=eof, write=lambda s: None).ask_yes("¿ok?", default=False))

    def test_chat_shows_model_and_tokens(self):
        out = []
        c = chat.Chat(read=lambda p: "", write=out.append)
        c.pinned = "claude"
        c.run_task("Resumí esto")
        self.assertIn("claude (claude-fake-1) · in 152 (50 caché) · out 7", "\n".join(out))


class RenderTests(unittest.TestCase):
    def strip(self, s):
        import re
        return re.sub(r"\033\[[0-9;]*m", "", s)

    def test_no_color_returns_text_unchanged(self):
        t = "# Título\n**negrita** y `código`"
        self.assertEqual(render.render(t, color=False), t)

    def test_signs_are_preserved(self):
        t = ("# Título\n\nTexto con **negrita**, *itálica*, `código` y ~~tachado~~ y [link](http://x.io).\n"
             "- item uno\n1. item dos\n> cita\n---\n| a | b |\n|---|---|\n| 1 | 2 |\n```python\nx = **no_es_negrita**\n```\nfin")
        self.assertEqual(self.strip(render.render(t)), t)

    def test_styles_applied(self):
        r = render.render("**hola**")
        self.assertIn(render.BOLD + "hola", r)
        self.assertIn(render.CYAN + "x", render.render("`x`"))
        self.assertTrue(render.render("# T").startswith(render.BOLD))

    def test_code_block_content_not_styled_inline(self):
        r = render.render("```\n**a**\n```")
        self.assertNotIn(render.BOLD, r)

    def test_code_span_protects_markers(self):
        self.assertNotIn(render.BOLD, render.render("`**a**`"))

    def test_snake_case_and_math_not_italic(self):
        for t in ("usá mi_variable_larga aquí", "2 * 3 * 4"):
            self.assertNotIn(render.ITALIC, render.render(t))

    def test_unclosed_fence_resets_style(self):
        self.assertTrue(render.render("```\ncódigo").endswith(render.RESET))

    def test_chat_md_toggle(self):
        out = []
        c = chat.Chat(read=lambda p: "", write=out.append, color=True)
        c.pinned = "claude"
        os.environ["ROUTER_HOME"] = tempfile.mkdtemp()
        os.environ["FAKE_CLAUDE_OUT"] = "**ok**"
        try:
            c.run_task("hola")
            self.assertIn(render.BOLD + "ok", "\n".join(out))
            out.clear()
            c.command("/md off")
            c.run_task("hola")
            self.assertIn("**ok**", "\n".join(out))
            self.assertNotIn(render.BOLD + "ok", "\n".join(out[1:]))
        finally:
            del os.environ["FAKE_CLAUDE_OUT"]


if __name__ == "__main__":
    unittest.main()
