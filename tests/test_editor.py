import os, pty, re, select, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import adapters, attachments as att, chat, core, editor as E, render  # noqa: E402

CMDS = [E.Command("/help", "ayuda"), E.Command("/manifest", "ver"), E.Command("/model", "fijar"), E.Command("/models", "estado")]


def feed(st, *events):
    res = None
    for ev in events:
        res = st.apply(ev) or res
    return res


def keys(*names):
    return [("key", n) for n in names]


class ParserTests(unittest.TestCase):
    def parse(self, *chunks):
        p, out = E.Parser(), []
        for c in chunks:
            out += p.feed(c if isinstance(c, bytes) else c.encode())
        return out

    def test_text_is_coalesced_and_enter_is_key(self):
        self.assertEqual(self.parse("hola\r"), [("text", "hola"), ("key", "enter")])

    def test_arrows_home_end_delete(self):
        self.assertEqual(self.parse("\x1b[A\x1b[B\x1b[C\x1b[D\x1b[H\x1b[F\x1b[3~"),
                         keys("up", "down", "right", "left", "home", "end", "delete"))

    def test_alt_enter_and_ctrl_j_are_newline(self):
        self.assertEqual(self.parse("\x1b\r\n"), keys("newline", "newline"))

    def test_alt_arrows_jump_words(self):
        self.assertEqual(self.parse("\x1b[1;3D\x1b[1;5C\x1bb\x1bf"), keys("word_left", "word_right", "word_left", "word_right"))

    def test_bracketed_paste(self):
        self.assertEqual(self.parse("\x1b[200~/tmp/a b\nc\x1b[201~x"), [("paste", "/tmp/a b\nc"), ("text", "x")])

    def test_paste_split_across_reads(self):
        self.assertEqual(self.parse("\x1b[20", "0~hola ", "mundo\x1b[2", "01~"), [("paste", "hola mundo")])

    def test_lone_escape_needs_flush(self):
        p = E.Parser()
        self.assertEqual(p.feed(b"\x1b"), [])
        self.assertEqual(p.flush(), [("key", "esc")])

    def test_utf8_split_across_reads(self):
        raw = "é".encode()
        self.assertEqual(self.parse(raw[:1], raw[1:]), [("text", "é")])

    def test_ctrl_keys(self):
        self.assertEqual(self.parse("\x01\x05\x03\x04\x7f\x15\x17\x0b"), keys("home", "end", "interrupt", "eof", "backspace", "kill_bol", "kill_word", "kill_eol"))

    def test_kitty_shift_enter(self):
        self.assertEqual(self.parse("\x1b[13;2u"), keys("newline"))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.png = Path(self.dir) / "foto con espacios.png"
        self.png.write_bytes(b"\x89PNG")

    def tearDown(self):
        self.tmp.cleanup()

    def st(self, text="", **kw):
        s = E.State(commands=CMDS, cwd=self.dir, **kw)
        if text:
            feed(s, ("text", text))
        return s

    def test_typing_and_editing_in_the_middle(self):
        s = self.st("hola mundo")
        feed(s, *keys("left", "left", "left", "left", "left", "left"), ("text", ","))
        self.assertEqual(s.text, "hola, mundo")
        feed(s, ("key", "backspace"))
        self.assertEqual(s.text, "hola mundo")
        feed(s, *keys("delete"))
        self.assertEqual(s.text, "holamundo")

    def test_word_motion_and_kills(self):
        s = self.st("uno dos tres")
        feed(s, ("key", "word_left"))
        self.assertEqual(s.cur, 8)
        feed(s, ("key", "kill_word"))
        self.assertEqual(s.text, "uno tres")
        feed(s, ("key", "kill_bol"))
        self.assertEqual((s.text, s.cur), ("tres", 0))
        s = self.st("abc def")
        feed(s, *keys("home", "kill_eol"))
        self.assertEqual(s.text, "")

    def test_backslash_enter_makes_a_newline(self):
        s = self.st("línea 1\\")
        self.assertIsNone(feed(s, ("key", "enter")))
        feed(s, ("text", "línea 2"))
        self.assertEqual(s.text, "línea 1\nlínea 2")

    def test_enter_submits_text(self):
        self.assertEqual(feed(self.st("hola"), ("key", "enter")), ("submit", "hola"))

    def test_history_with_draft(self):
        s = E.State(history=["uno", "dos"], cwd=self.dir)
        feed(s, ("text", "borrador"))
        feed(s, ("key", "up"))
        self.assertEqual(s.text, "dos")
        feed(s, ("key", "up"))
        self.assertEqual(s.text, "uno")
        feed(s, *keys("down", "down"))
        self.assertEqual(s.text, "borrador")

    def test_up_down_move_between_lines_before_history(self):
        s = E.State(history=["viejo"], cwd=self.dir)
        feed(s, ("text", "abc\ndef"))
        feed(s, ("key", "up"))
        self.assertEqual((s.text, s.cur), ("abc\ndef", 3))
        feed(s, ("key", "down"))
        self.assertEqual(s.cur, 7)

    def test_palette_enter_completes_instead_of_sending(self):
        s = self.st("/m")
        self.assertEqual([c.name for c in s.palette()], ["/manifest", "/model", "/models"])
        self.assertIsNone(feed(s, ("key", "enter")))
        self.assertEqual(s.text, "/manifest ")

    def test_palette_exact_command_submits_and_tab_selects(self):
        self.assertEqual(feed(self.st("/help"), ("key", "enter")), ("submit", "/help"))
        s = self.st("/m")
        feed(s, *keys("down", "tab"))
        self.assertEqual(s.text, "/model ")

    def test_slash_path_is_not_a_command_palette(self):
        self.assertEqual(self.st(str(self.png)).palette(), [])

    def test_dragged_file_is_normalized(self):
        s = self.st()
        escaped = str(self.png).replace(" ", "\\ ")
        feed(s, ("paste", escaped + "\n"))
        self.assertEqual(s.text, f'"{self.png}" ')
        self.assertEqual([a.kind for a in s.attachments()], ["image"])

    def test_normal_paste_is_untouched_and_crlf_fixed(self):
        s = self.st()
        feed(s, ("paste", "hola\r\nmundo /no/existe"))
        self.assertEqual(s.text, "hola\nmundo /no/existe")

    def test_eof_and_double_interrupt(self):
        self.assertEqual(feed(self.st(), ("key", "eof")), ("eof",))
        s = self.st("algo")
        feed(s, ("key", "interrupt"))
        self.assertEqual(s.text, "")
        self.assertIsNone(feed(s, ("key", "interrupt")))
        self.assertTrue(s.armed)
        self.assertEqual(feed(s, ("key", "interrupt")), ("eof",))

    def test_interrupt_is_disarmed_by_other_keys(self):
        s = self.st()
        feed(s, ("key", "interrupt"), ("text", "a"))
        self.assertFalse(s.armed)


class FrameTests(unittest.TestCase):
    def frame(self, text="", cols=100, rows=30, color=False, cur=None, **kw):
        s = E.State(commands=CMDS, cwd="/nonexistent")
        s.text, s.cur = text, len(text) if cur is None else cur
        return s, E.render_frame(s, cols, rows, "auto", "manager claude", color)

    def test_box_geometry(self):
        _, fr = self.frame("hola")
        box = fr.lines[:3]
        self.assertTrue(box[0].startswith("╭") and box[0].endswith("╮") and box[2].startswith("╰"))
        self.assertEqual(len({render._vlen(l) for l in box}), 1)
        self.assertEqual(fr.cursor, (1, 4 + 4))

    def test_placeholder_when_empty_and_cursor_at_start(self):
        _, fr = self.frame("")
        self.assertIn("arrastrá archivos", fr.lines[1])
        self.assertEqual(fr.cursor, (1, 4))

    def test_long_text_wraps_and_cursor_follows(self):
        _, fr = self.frame("x" * 200, cols=60)
        body = [l for l in fr.lines if l.startswith("│")]
        self.assertGreaterEqual(len(body), 4)
        self.assertTrue(all(render._vlen(l) == render._vlen(body[0]) for l in body))
        self.assertEqual(fr.cursor[0], len(body))

    def test_text_exactly_filling_a_row_moves_cursor_to_next_row(self):
        area = E.box_width(60) - 4 - len(E.PROMPT)
        _, fr = self.frame("y" * area, cols=60)
        self.assertEqual(fr.cursor, (2, 4))

    def test_wide_chars_count_two_cells(self):
        _, fr = self.frame("日本語")
        self.assertEqual(fr.cursor[1], 4 + 6)

    def test_every_line_fits_the_terminal(self):
        for cols in (40, 80, 120, 200):
            _, fr = self.frame("texto " * 30, cols=cols)
            self.assertTrue(all(render._vlen(l) <= cols - 1 for l in fr.lines), cols)

    def test_tall_input_scrolls_with_marker(self):
        _, fr = self.frame("\n".join(f"l{i}" for i in range(30)), rows=24)
        body = [l for l in fr.lines if l.startswith("│")]
        self.assertEqual(len(body), E.MAX_BODY_ROWS)
        self.assertIn("↑", fr.lines[0])

    def test_attachments_listed_under_box(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.txt"
            f.write_text("x")
            s = E.State(cwd=d)
            s.text, s.cur = str(f), len(str(f))
            fr = E.render_frame(s, 100, 30, "auto", "", False)
            self.assertTrue(any("⎘ a.txt · texto" in l for l in fr.lines))

    def test_palette_and_hints_rendered(self):
        _, fr = self.frame("/mo")
        text = "\n".join(fr.lines)
        self.assertIn("▸ /model", text)
        self.assertIn("nueva línea", text)
        s = E.State(); s.armed = True
        self.assertIn("Ctrl-C otra vez", "\n".join(E.render_frame(s, 100, 30, "auto", "", False).lines))

    def test_color_frame_has_gradient_border_and_path_highlight(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.txt"
            f.write_text("x")
            s = E.State(cwd=d)
            s.text = f"mirá {f}"
            s.cur = len(s.text)
            fr = E.render_frame(s, 100, 30, "auto", "", True)
            self.assertIn("38;2;217;119;87", fr.lines[0])
            self.assertIn(E.UNDER, fr.lines[1])

    def test_echo_lines(self):
        out = E.echo_lines("hola\nmundo", [], [], 80, color=False)
        self.assertEqual(out, ["❯ hola", "  mundo"])


class WrapTests(unittest.TestCase):
    def test_wraps_on_words_with_hanging_indent(self):
        lines = render.wrap("uno dos tres cuatro cinco", 12, "• ", "  ")
        self.assertEqual(lines, ["• uno dos", "  tres", "  cuatro", "  cinco"])

    def test_ansi_does_not_count_as_width(self):
        word = f"{render.BOLD}negrita{render.BOLD_OFF}"
        self.assertEqual(len(render.wrap(f"{word} {word}", 16)), 1)

    def test_long_word_is_not_split(self):
        self.assertEqual(render.wrap("a " + "x" * 30, 10), ["a", "x" * 30])

    def test_render_wraps_paragraphs_lists_and_quotes_but_not_code(self):
        md = "palabra " * 12 + "\n- " + "item " * 12 + "\n> " + "cita " * 12 + "\n```\n" + "c" * 80 + "\n```"
        plain = re.sub(r"\033\[[0-9;]*m", "", render.render(md, width=40))
        for l in plain.splitlines():
            if not l.startswith(" c") and "ccc" not in l:
                self.assertLessEqual(len(l), 39, l)
        self.assertIn("  item", plain)  # continuación alineada al texto de la viñeta
        self.assertGreaterEqual(plain.count("▎"), 2)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.txt = self.d / "nota con espacios.txt"
        self.txt.write_text("hola")
        self.png = self.d / "img.png"
        self.png.write_bytes(b"\x89PNG")
        self.bin = self.d / "datos.bin"
        self.bin.write_bytes(b"\x00\x01\x02")

    def tearDown(self):
        self.tmp.cleanup()

    def test_tokens_respect_escapes_and_quotes(self):
        vals = [v for _, _, v in att.tokens(r'a\ b "c d" e')]
        self.assertEqual(vals, ["a b", "c d", "e"])

    def test_find_escaped_quoted_and_file_url_without_duplicates(self):
        esc = str(self.txt).replace(" ", "\\ ")
        found = att.find(f'mirá {esc} y "{self.txt}" y file://{self.png} fin')
        self.assertEqual([a.name for a in found], ["nota con espacios.txt", "img.png"])

    def test_plain_words_are_never_paths(self):
        self.assertEqual(att.find("img.png datos.bin hola", cwd=str(self.d)), [])
        self.assertEqual([a.name for a in att.find("./img.png", cwd=str(self.d))], ["img.png"])

    def test_deleted_cwd_does_not_crash(self):
        old = os.getcwd()
        gone = tempfile.mkdtemp()
        os.chdir(gone)
        os.rmdir(gone)
        try:
            self.assertEqual(att.safe_cwd(), os.path.expanduser("~"))
            self.assertEqual(att.find("hola"), [])
        finally:
            os.chdir(old)

    def test_tilde_and_missing(self):
        self.assertIsNone(att.resolve("/no/existe/seguro.png"))
        self.assertIsNotNone(att.resolve("~"))

    def test_classify(self):
        kinds = {a.name: a.kind for a in att.find(" ".join(att.quote(str(p)) for p in (self.txt, self.png, self.bin, self.d)))}
        self.assertEqual(kinds["nota con espacios.txt"], "text")
        self.assertEqual(kinds["img.png"], "image")
        self.assertEqual(kinds["datos.bin"], "binary")
        self.assertEqual(kinds[self.d.name], "dir")

    def test_symlinks_are_not_resolved(self):
        link = self.d / "enlace.txt"
        link.symlink_to(self.txt)
        self.assertEqual(att.resolve(str(link)), link)

    def test_normalize_paste(self):
        self.assertEqual(att.normalize_paste(str(self.txt).replace(" ", "\\ ")), f'"{self.txt}" ')
        self.assertEqual(att.normalize_paste(f"{self.png} {self.bin}"), f"{self.png} {self.bin} ")
        self.assertEqual(att.normalize_paste("hola mundo"), "hola mundo")
        self.assertEqual(att.normalize_paste(f"{self.png} y más"), f"{self.png} y más")

    def test_spans_cover_whole_token(self):
        text = f'ver "{self.txt}" ok'
        (s, e), = att.spans(text)
        self.assertEqual(text[s:e], f'"{self.txt}"')

    def test_labels_and_sizes(self):
        self.assertEqual(att.human_size(2048), "2.0 KB")
        self.assertEqual(att.find(str(self.png))[0].label(), "img.png · imagen · 4 B")


class CoreAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith(("FAKE_", "ROUTER_CMD_")):
                del os.environ[k]
        self.cfg = core.load_config(apply_manifest=False)
        self.png = Path(self.tmp.name) / "x.png"
        self.png.write_bytes(b"\x89PNG")
        self.txt = Path(self.tmp.name) / "x.txt"
        self.txt.write_text("contenido " * 100)

    def tearDown(self):
        self.tmp.cleanup()

    def test_image_excludes_models_that_cannot_open_files(self):
        res = core.ask(f"describí {self.png}", self.cfg, dry_run=True, attachments=att.find(str(self.png)))
        self.assertNotIn("antigravity", res["order"])
        self.assertIn("multimodal", res["decision"]["weights"])
        row = next(r for r in res["decision"]["ranking"] if r["name"] == "antigravity")
        self.assertFalse(row["usable"])
        self.assertIn("no abre archivos", row["why"])

    def test_pinned_model_that_cannot_open_files_is_an_error(self):
        with self.assertRaises(ValueError):
            core.ask("mirá", self.cfg, model="antigravity", attachments=att.find(str(self.png)))

    def test_text_attachment_is_inlined_and_does_not_restrict_models(self):
        plain = core.ask("resumí esto", self.cfg, model="antigravity")
        with_file = core.ask("resumí esto", self.cfg, model="antigravity", attachments=att.find(str(self.txt)))
        self.assertTrue(with_file["ok"])
        n = lambda r: int(re.search(r"recibi (\d+)", r["output"]).group(1))
        self.assertGreater(n(with_file), n(plain) + 900)

    def test_reference_block_lists_non_text_files(self):
        block = att.reference_block(att.find(str(self.png)))
        self.assertIn(str(self.png), block)
        self.assertIn("image", block)

    def test_claude_gets_add_dir_for_attachment_folders(self):
        spec = self.cfg["models"]["claude"]
        argv = adapters._with_dirs("claude", spec, ["claude", "-p", "{prompt}"], ["/a", "/b"])
        self.assertEqual(argv, ["claude", "--add-dir", "/a", "--add-dir", "/b", "-p", "{prompt}"])
        self.assertEqual(adapters._with_dirs("codex", self.cfg["models"]["codex"], ["codex", "exec"], ["/a"]), ["codex", "exec"])

    def test_chat_path_only_message_is_a_task_not_a_command(self):
        out = []
        c = chat.Chat(read=lambda p: "", write=out.append)
        c.pinned = "claude"
        c.handle(str(self.png))
        text = "\n".join(out)
        self.assertNotIn("No conozco", text)
        self.assertIn("⎘ x.png · imagen", text)
        self.assertIn("claude", text)

    def test_chat_unknown_slash_is_still_a_command(self):
        out = []
        chat.Chat(read=lambda p: "", write=out.append).handle("/nada")
        self.assertIn("No conozco /nada", "\n".join(out))


@unittest.skipUnless(hasattr(os, "fork") and sys.platform != "win32", "necesita pty")
class PtySmokeTests(unittest.TestCase):
    """El editor real sobre un pseudo-terminal: teclas -> resultado devuelto por read()."""

    def run_editor(self, steps, timeout=10):
        code = ("import sys; sys.path.insert(0, %r)\nfrom ia_router import editor as E\n"
                "ed = E.LineEditor([E.Command('/help','ayuda')], color=False)\n"
                "try:\n    r = ed.read()\n    print('RESULT=' + repr(r))\nexcept EOFError:\n    print('EOF')\n") % str(ROOT)
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update(TERM="xterm-256color", COLUMNS="100", LINES="30")
            os.execvp(sys.executable, [sys.executable, "-c", code])
        import fcntl, struct, termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        buf, end = b"", time.time() + timeout

        def pump(t):
            nonlocal buf
            stop = time.time() + t
            while time.time() < stop:
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        d = os.read(fd, 65536)
                    except OSError:
                        return
                    if not d:
                        return
                    buf += d
        pump(1.0)
        for s in steps:
            os.write(fd, s.encode())
            pump(0.3)
        pump(1.0)
        try:
            os.close(fd)
            os.waitpid(pid, 0)
        except OSError:
            pass
        return buf.decode(errors="replace")

    def test_type_edit_and_submit(self):
        out = self.run_editor(["hola mundx", "\x7f", "o", "\r"])
        self.assertIn("RESULT='hola mundo'", out)
        self.assertIn("╭", out)

    def test_dragged_file_arrives_as_clean_path_and_multiline(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "mi foto.png"
            f.write_bytes(b"\x89PNG")
            esc = str(f).replace(" ", "\\ ")
            out = self.run_editor(["\x1b[200~" + esc + "\x1b[201~", "qué es?", "\x1b\r", "otra", "\r"])
            self.assertIn(f"RESULT='\"{f}\" qué es?\\notra'", out)
            self.assertIn("⎘ mi foto.png · imagen", out)

    def test_ctrl_d_on_empty_raises_eof(self):
        self.assertIn("EOF", self.run_editor(["\x04"]))

    def test_terminal_restored_after_read(self):
        out = self.run_editor(["x\r"])
        self.assertIn("\x1b[?2004l", out)


if __name__ == "__main__":
    unittest.main()
