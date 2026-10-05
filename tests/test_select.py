import os, pty, select, sys, time, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ia_router import select as S  # noqa: E402


class SelectTests(unittest.TestCase):
    def test_state_navigation_wraps_and_confirms(self):
        st = S.SelectState(3, 0)
        st.apply(("key", "up"))
        self.assertEqual(st.sel, 2)
        st.apply(("key", "down"))
        self.assertEqual(st.sel, 0)
        st.apply(("text", "2"))
        self.assertEqual(st.sel, 1)
        st.apply(("text", "j"))
        self.assertEqual(st.sel, 2)
        st.apply(("text", "k"))
        self.assertEqual(st.sel, 1)
        self.assertEqual(st.apply(("key", "enter")), ("done", "1"))
        self.assertEqual(st.apply(("key", "esc")), ("cancel",))
        self.assertEqual(st.apply(("key", "interrupt")), ("cancel",))
        st.apply(("text", "9"))
        self.assertEqual(st.sel, 1)  # fuera de rango: se ignora

    def test_default_is_clamped(self):
        self.assertEqual(S.SelectState(2, 9).sel, 1)
        self.assertEqual(S.SelectState(2, -3).sel, 0)

    def test_render_marks_the_selected_option(self):
        lines = S.render_lines("¿Qué?", [S.Option("Uno", "a"), S.Option("Dos", "b")], 1, "ayuda", color=False, step="2/5")
        text = "\n".join(lines)
        self.assertIn("2/5", text)
        self.assertIn("> ◉ Dos", text)
        self.assertIn("  ○ Uno", text.replace("    ", "  "))
        self.assertIn("confirmar", text)

    def test_summary_line(self):
        self.assertEqual(S.summary_line("¿Qué?", "Dos", "2/5", color=False), "  ✔ 2/5  ¿Qué? › Dos")
        self.assertIn("Dos", S.summary_line("¿Qué?", "Dos", "2/5", color=True))

    def test_choose_without_a_tty_returns_default(self):
        with mock.patch.object(S, "interactive", return_value=False):
            self.assertEqual(S.choose("x", [S.Option("a"), S.Option("b")], default=1), 1)

    def run_pty(self, keys):
        code = ("import sys; sys.path.insert(0, %r)\nfrom ia_router import select as S\n"
                "r = S.choose('Pregunta', [S.Option('Uno','a'), S.Option('Dos','b'), S.Option('Tres','c')], color=False)\nprint('RESULT=%%r' %% (r,))\n") % str(ROOT)
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update(TERM="xterm-256color", COLUMNS="90", LINES="30")
            os.execvp(sys.executable, [sys.executable, "-c", code])
        buf = b""

        def pump(t):
            nonlocal buf
            end = time.time() + t
            while time.time() < end:
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        d = os.read(fd, 65536)
                    except OSError:
                        return
                    if not d:
                        return
                    buf += d
        pump(1.0)
        for k in keys:
            os.write(fd, k.encode())
            pump(0.3)
        pump(0.8)
        try:
            os.close(fd)
            os.waitpid(pid, 0)
        except OSError:
            pass
        return buf.decode(errors="replace")

    def test_pty_arrows_and_enter(self):
        out = self.run_pty(["\x1b[B", "\x1b[B", "\x1b[A", "\r"])
        self.assertIn("RESULT=1", out)
        self.assertIn("Pregunta", out)
        self.assertIn("Pregunta › Dos", out)  # la pregunta contestada queda resumida en una línea

    def test_pty_escape_cancels(self):
        self.assertIn("RESULT=None", self.run_pty(["\x1b"]))

    def test_pty_number_jumps(self):
        self.assertIn("RESULT=2", self.run_pty(["3", "\r"]))


if __name__ == "__main__":
    unittest.main()
