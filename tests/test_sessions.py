import json, os, stat, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAKE_BIN = str(ROOT / "tests" / "fake_bin")
os.environ["PATH"] = FAKE_BIN + os.pathsep + os.environ["PATH"]

from ia_router import chat, core, sessions as S, state  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["ROUTER_HOME"] = self.tmp.name
        os.environ.pop("ROUTER_NO_SESSIONS", None)
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]

    def session(self, *turns):
        s = S.new_session("/work")
        for u in turns:
            S.add_turn(s, u, "claude", "answer to " + u)
        S.save(s)
        return s


class StoreTests(Base):
    def test_save_load_and_list(self):
        s = self.session("first question", "second question")
        self.assertEqual(S.load(s["id"])["turns"][1]["user"], "second question")
        rows = S.list_sessions()
        self.assertEqual([(r["id"], r["turns"], r["title"]) for r in rows], [(s["id"], 2, "first question")])

    def test_files_are_private_and_an_empty_session_is_not_written(self):
        s = self.session("hello")
        self.assertEqual(stat.S_IMODE(S._path(s["id"]).stat().st_mode), 0o600)
        empty = S.new_session()
        S.save(empty)
        self.assertEqual(len(list(S.sessions_dir().glob("*.json"))), 1)

    def test_newest_first_and_latest(self):
        a = self.session("older")
        b = S.new_session("/x")
        S.add_turn(b, "newer", "codex", "ok")
        S.save(b)
        d = json.loads(S._path(a["id"]).read_text())
        d["updated"] = "2020-01-01T00:00:00"
        S._path(a["id"]).write_text(json.dumps(d))
        self.assertEqual([r["title"] for r in S.list_sessions()], ["newer", "older"])
        self.assertEqual(S.latest()["id"], b["id"])

    def test_long_answers_and_long_conversations_are_capped(self):
        s = S.new_session()
        for i in range(S.MAX_TURNS + 5):
            S.add_turn(s, f"q{i}", "claude", "x" * (S.MAX_ANSWER_CHARS + 100))
        S.save(s)
        d = S.load(s["id"])
        self.assertEqual(len(d["turns"]), S.MAX_TURNS)
        self.assertEqual(len(d["turns"][0]["answer"]), S.MAX_ANSWER_CHARS)
        self.assertEqual(d["turns"][-1]["user"], f"q{S.MAX_TURNS + 4}")

    def test_delete_clear_and_unsafe_ids(self):
        a, b = self.session("a"), self.session("b")
        self.assertTrue(S.delete(a["id"]))
        self.assertFalse(S.delete(a["id"]))
        self.assertEqual(S.clear(), 1)
        self.assertEqual(S.list_sessions(), [])
        for bad in ("../../etc/passwd", "x", ""):
            self.assertFalse(S.delete(bad))
            self.assertIsNone(S.load(bad))

    def test_it_can_be_turned_off_by_environment_or_flag_and_nothing_is_written(self):
        os.environ["ROUTER_NO_SESSIONS"] = "1"
        self.addCleanup(os.environ.pop, "ROUTER_NO_SESSIONS", None)
        self.session("secret")
        self.assertFalse(S.sessions_dir().exists())
        del os.environ["ROUTER_NO_SESSIONS"]
        state.set_flag("sessions_off", True)
        self.session("secret")
        self.assertFalse(S.sessions_dir().exists())

    def test_damaged_files_are_skipped(self):
        s = self.session("fine")
        (S.sessions_dir() / "20200101-000000-abcd.json").write_text("{broken")
        self.assertEqual([r["id"] for r in S.list_sessions()], [s["id"]])


class ChatTests(Base):
    def make(self, answers=()):
        self.out = []
        answers = list(answers)

        def read(prompt=""):
            if not answers:
                raise EOFError                       # like Ctrl-D: ends the loop and makes yes/no questions take their default
            return answers.pop(0)
        return chat.Chat(read=read, write=self.out.append, color=False)

    def test_every_answered_task_is_saved_and_the_model_pin_with_it(self):
        c = self.make()
        c.pinned = "codex"
        c.handle("Fix this bug in my Python function")
        d = S.load(c.session["id"])
        self.assertEqual(d["turns"][0]["user"], "Fix this bug in my Python function")
        self.assertEqual(d["pinned"], "codex")

    def test_a_failed_task_is_not_saved(self):
        os.environ["FAKE_CODEX_MODE"] = "fail"
        os.environ["FAKE_CLAUDE_MODE"] = "fail"
        os.environ["FAKE_AGY_MODE"] = "fail"
        c = self.make()
        c.handle("hello")
        self.assertEqual(S.list_sessions(), [])

    def test_resume_restores_the_history_that_is_replayed_as_context(self):
        old = self.session("what is a mutex?")
        c = self.make()
        c.resume(old["id"])
        self.assertEqual(c.history[0][0], "what is a mutex?")
        self.assertIn("Resumed", "\n".join(self.out))
        self.assertIn("what is a mutex?", chat.build_preamble(c.history))
        c.handle("and a semaphore?")
        self.assertEqual(len(S.load(old["id"])["turns"]), 2)             # the same session keeps growing

    def test_continue_picks_the_latest_and_a_missing_id_says_so(self):
        c = self.make()
        c.resume("")
        self.assertIn("No saved conversations yet", "\n".join(self.out))
        self.session("one")
        c.resume("")
        self.assertEqual(len(c.history), 1)
        self.out.clear()
        c.resume("20200101-000000-abcd")
        self.assertIn("No such saved conversation", "\n".join(self.out))

    def test_clear_starts_a_new_session_and_keeps_the_old_one_listed(self):
        c = self.make()
        c.pinned = "claude"
        c.handle("Fix this bug in my Python function")
        old = c.session["id"]
        c.handle("/clear")
        self.assertNotEqual(c.session["id"], old)
        self.assertEqual([r["id"] for r in S.list_sessions()], [old])

    def test_the_sessions_command_lists_and_switches_saving(self):
        c = self.make()
        c.handle("/sessions")
        self.assertIn("No saved conversations yet", "\n".join(self.out))
        self.session("hello there")
        self.out.clear()
        c.handle("/sessions")
        self.assertIn("hello there", "\n".join(self.out))
        c.handle("/sessions off")
        self.assertFalse(S.enabled())
        c.handle("/sessions on")
        self.assertTrue(S.enabled())
        self.assertIn("/resume", chat.HELP)
        self.assertEqual({"/sessions", "/resume"} - {x.name for x in chat.COMMANDS}, set())

    def test_the_loop_resumes_on_request(self):
        s = self.session("earlier topic")
        c = self.make()
        c.loop(resume=s["id"])
        self.assertIn("Resumed", "\n".join(self.out))
        c2 = self.make()
        c2.loop(continue_last=True)
        self.assertEqual(len(c2.history), 1)


class CliTests(Base):
    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "cli.py"), *args], capture_output=True, text=True, env=dict(os.environ, ROUTER_HOME=self.tmp.name),
                              timeout=60, cwd=ROOT, input="")

    def test_list_show_delete_clear(self):
        s = self.session("remember this")
        self.assertIn("remember this", self.cli("sessions").stdout)
        shown = self.cli("sessions", "show", s["id"]).stdout
        self.assertIn("you> remember this", shown)
        self.assertIn("answer to remember this", shown)
        self.assertEqual(self.cli("sessions", "delete").returncode, 2)
        self.assertEqual(self.cli("sessions", "delete", s["id"]).returncode, 0)
        self.assertEqual(self.cli("sessions", "delete", s["id"]).returncode, 1)
        self.session("a")
        self.session("b")
        self.assertIn("Deleted 2", self.cli("sessions", "clear").stdout)

    def test_continue_flag_resumes_the_latest_in_a_real_run(self):
        self.session("a topic from yesterday")
        r = self.cli("--continue")
        self.assertIn("Resumed", r.stdout)
        self.assertIn("a topic from yesterday", r.stdout)


if __name__ == "__main__":
    unittest.main()
