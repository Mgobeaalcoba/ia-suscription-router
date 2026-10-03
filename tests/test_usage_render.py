import os, re, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import adapters, banner, chat, core, render, state  # noqa: E402


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
    def plain(self, s, **kw):
        return re.sub(r"\033\[[0-9;]*m", "", render.render(s, width=80, **kw))

    def test_no_color_returns_text_unchanged(self):
        t = "# Título\n**negrita** y `código`"
        self.assertEqual(render.render(t, color=False), t)

    def test_no_markdown_signs_remain(self):
        t = ("# Título\n\n## Sub\n\nTexto con **negrita**, *itálica*, `código`, ~~tachado~~ y [link](http://x.io).\n"
             "- item\n> cita\n---\n```python\nx = 1\n```\nfin")
        out = self.plain(t)
        for sign in ("#", "**", "`", "~~", "](", "> "):
            self.assertNotIn(sign, out.replace("# Cada", ""), sign)
        self.assertIn("Título", out)
        self.assertIn("negrita", out)
        self.assertIn("• item", out)
        self.assertIn("▎ cita", out)
        self.assertIn("link (http://x.io)", out)

    def test_code_block_hides_fences_keeps_content_and_comments(self):
        out = self.plain("```python\n# comentario\ngrupos = df.groupby([\"X1\"]).ngroup()\n```")
        self.assertNotIn("```", out)
        self.assertIn("# comentario", out)
        self.assertIn('df.groupby(["X1"]).ngroup()', out)
        self.assertIn("python", out)

    def test_code_block_highlights_but_not_inline_markdown(self):
        r = render.render("```python\ndef f(): return '**a**'\n```", width=80)
        self.assertIn(render.MAGENTA + "def", r)
        self.assertIn(render.GREEN + "'**a**'", r)
        self.assertNotIn(render.BOLD, r)

    def test_hash_inside_string_is_not_a_comment(self):
        r = render.render('```python\nx = "a # b"\n```', width=80)
        self.assertNotIn(render.GRAY + "# b", r)

    def test_unclosed_fence_still_renders_code(self):
        self.assertIn("código", self.plain("```\ncódigo"))

    def test_table_is_aligned_box(self):
        out = self.plain("| Modelo | Tokens |\n|:--|--:|\n| claude | 16.5k |\n| codex | **7** |").splitlines()
        self.assertEqual(len({len(l) for l in out}), 1)  # todas las filas del mismo ancho
        self.assertTrue(out[0].startswith("┌") and out[-1].startswith("└"))
        self.assertIn("│ claude │  16.5k │", out[3])
        self.assertNotIn("|", "".join(out))

    def test_lists_tasks_and_numbers(self):
        out = self.plain("- a\n  - b\n- [x] ok\n- [ ] no\n1. uno")
        self.assertEqual(out.splitlines(), ["• a", "  ◦ b", "☑ ok", "☐ no", "1. uno"])

    def test_styles_applied(self):
        self.assertIn(render.BOLD + "hola", render.render("**hola**"))
        self.assertIn(render.SPAN_ON, render.render("`x`"))
        self.assertTrue(render.render("# T").startswith(render.BOLD))

    def test_code_span_protects_markers(self):
        self.assertEqual(self.plain("`**a**`").strip(), "**a**")

    def test_escapes_are_literal(self):
        self.assertEqual(self.plain(r"\*no\* es literal"), "*no* es literal")

    def test_snake_case_and_math_not_italic(self):
        for t in ("usá mi_variable_larga aquí", "2 * 3 * 4"):
            self.assertNotIn(render.ITALIC, render.render(t))

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
            self.assertNotIn(render.BOLD, "\n".join(out[1:]))
        finally:
            del os.environ["FAKE_CLAUDE_OUT"]


class BannerTests(unittest.TestCase):
    OK = {"claude": True, "codex": True, "antigravity": False}
    NAMES = ["claude", "codex", "antigravity"]

    def make(self, **kw):
        args = dict(version="9.9.9", manager="claude", models=self.NAMES, installed=self.OK, cwd="/tmp/x", color=False, width=100)
        args.update(kw)
        return banner.render(**args)

    def test_plain_banner_has_logo_and_status(self):
        out = self.make()
        for piece in ("╦═╗╔═╗╦ ╦╔╦╗╔═╗╦═╗", "v9.9.9", "manager claude", "● claude", "● codex", "○ antigravity", "/tmp/x", "/help", "/exit"):
            self.assertIn(piece, out)
        self.assertNotIn("\033", out)

    def test_wordmark_rows_aligned_and_boxes_closed(self):
        lines = self.make().splitlines()
        box = [l for l in lines if l.lstrip().startswith(("╭", "│", "╰"))]
        self.assertEqual(len(box), 5)
        self.assertEqual(len({len(l) for l in box}), 1)

    def test_color_banner_fits_width_and_uses_brand_colors(self):
        out = banner.render("1", "claude", self.NAMES, self.OK, "/tmp/x", color=True, width=90)
        self.assertIn("38;2;217;119;87", out)
        for l in out.splitlines():
            self.assertLessEqual(len(re.sub(r"\033\[[0-9;]*m", "", l)), 90)

    def test_narrow_terminal_gets_compact_banner(self):
        out = self.make(width=40)
        self.assertNotIn("╦═╗", out)
        self.assertIn("ia-router v9.9.9", out)

    def test_long_path_is_shortened(self):
        out = self.make(cwd="/a/" + "muy-largo/" * 20 + "fin")
        self.assertIn("…", out)
        self.assertIn("fin", out)

    def test_pinned_model_shown(self):
        self.assertIn("fijado: codex", self.make(pinned="codex"))
        self.assertNotIn("fijado", self.make(pinned="auto"))

    def test_chat_prompt_is_readline_safe_only_with_color(self):
        self.assertEqual(chat.Chat(color=False).prompt(), "ia> ")
        p = chat.Chat(color=True).prompt()
        self.assertIn("\001", p)
        self.assertTrue(p.endswith("\002 "))

    def test_chat_banner_renders(self):
        os.environ["ROUTER_HOME"] = tempfile.mkdtemp()
        out = chat.Chat(color=False).banner()
        self.assertIn("╦═╗╔═╗╦ ╦╔╦╗╔═╗╦═╗", out)
        self.assertIn(f"v{__import__('ia_router').__version__}", out)


if __name__ == "__main__":
    unittest.main()
