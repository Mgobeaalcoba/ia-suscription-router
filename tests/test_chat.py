import json, os, re, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

import fixtures as F  # noqa: E402
from ia_router import chat, core, metrics as M, priorities, probe, scoring as S, state  # noqa: E402


class IntentTests(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(chat.detect_intent(text), expected, text)

    def test_tasks(self):
        for t in ("Fix this bug in my Python function", "Draft a follow-up email for a client", "Summarize this document\nwith several lines",
                  "I prefer the email to be short, draft one for Juan", "use Codex for everything code related"):   # there is no spoken configuration anymore: all of this is a task
            self.check(t, "task")

    def test_queries(self):
        self.check("which models do I have available", "models")
        self.check("show me the stats", "stats")
        self.check("show me the scores", "scores")
        self.check("how does the router decide?", "scores")

    def test_long_or_code_is_always_a_task(self):
        self.check("show me the stats " + "x" * 400, "task")
        self.check("which models do I have ```print(1)```", "task")


class Base(unittest.TestCase):
    ids = False       # if True, the state already knows each CLI's models
    stale = False     # if True, the bundled snapshot is old

    def setUp(self):
        self._init()

    def restart(self, ids=None, stale=None):
        """Rebuilds the state under other conditions, clearing the previous one."""
        self.tmp.cleanup()
        self.ids = self.ids if ids is None else ids
        self.stale = self.stale if stale is None else stale
        self._init()

    def _init(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        os.environ["CODEX_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith(("FAKE_", "ROUTER_CMD_", "ARTIFICIAL_")):
                del os.environ[k]
        date = "2020-01-01T00:00:00" if self.stale else time.strftime("%Y-%m-%dT%H:%M:%S")
        self.snap = Path(self.tmp.name) / "snapshot.json"
        self.snap.write_text(json.dumps(F.snapshot(date)))
        p = mock.patch.object(M, "SNAPSHOT", self.snap)
        p.start()
        self.addCleanup(p.stop)
        M._memo.clear()
        if self.ids:
            for n, mid in F.IDS.items():
                state.remember_model_id(n, mid)
        self.out, self.inputs = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def chat(self, *answers):
        self.inputs = list(answers)
        def read(prompt):
            if not self.inputs:
                raise EOFError
            self.out.append(f"<{prompt}>")
            return self.inputs.pop(0)
        return chat.Chat(read=read, write=self.out.append)

    def text(self):
        return "\n".join(self.out)


class TaskTests(Base):
    def test_task_is_routed_and_answer_shown(self):
        c = self.chat()
        self.assertTrue(c.handle("Fix this bug in my Python function, the test fails"))
        self.assertIn("[fake-codex]", self.text())                  # with no known ids the models.json estimate applies
        self.assertIn("codex", self.text().split("──")[1])
        self.assertEqual(len(c.history), 1)

    def test_with_metrics_the_task_follows_the_scores(self):
        self.restart(ids=True)
        S.save_profile({"priorities": {"coding": "cost", "debugging": "cost"}})   # without AA there is only cost; the cheapest is antigravity
        M._memo.clear()
        c = self.chat()
        c.explain = True
        c.handle("Fix this bug in my Python function, the test fails")
        self.assertIn("metrics: yes", self.text())
        self.assertIn("[fake-agy]", self.text())

    def test_history_is_prepended_to_next_task(self):
        c = self.chat()
        c.pinned = "codex"
        c.handle("first question")
        c.handle("second question")
        sizes = [int(n) for n in re.findall(r"received (\d+) chars", self.text())]
        self.assertEqual(len(sizes), 2)
        self.assertGreater(sizes[1], sizes[0] + len("second question") - 1)

    def test_short_followup_inherits_previous_topic(self):
        c = self.chat()
        c.explain = True
        c.handle("Write a Python function that reverses a string")
        self.out.clear()
        c.handle("Now make it recursive")                          # on its own it would go to 'quick'; it inherits 'coding'
        self.assertIn("coding×", self.text())
        c.handle("/clear")
        self.assertIsNone(c.route_text("Now make it recursive"))

    def test_followup_with_own_topic_does_not_inherit(self):
        c = self.chat()
        c.handle("Write a Python function that reverses a string")
        self.assertIsNone(c.route_text("Draft a follow-up email for a client"))

    def test_failed_task_reports_error(self):
        os.environ["FAKE_CODEX_MODE"] = "fail"
        c = self.chat()
        c.pinned = "codex"
        c.handle("hello")
        self.assertIn("Could not resolve it", self.text())
        self.assertEqual(c.history, [])

    def test_a_path_alone_is_a_task_not_a_command(self):
        f = Path(self.tmp.name) / "x.png"
        f.write_bytes(b"\x89PNG")
        c = self.chat()
        c.pinned = "claude"
        c.handle(str(f))
        self.assertNotIn("Unknown command", self.text())
        self.assertIn("⎘ x.png · image", self.text())


class CommandTests(Base):
    ids = True

    def test_model_pinning_and_unknown_commands(self):
        c = self.chat()
        c.handle("/model codex")
        self.assertEqual(c.pinned, "codex")
        c.handle("/model gpt-9")
        self.assertEqual(c.pinned, "codex")
        c.handle("/model auto")
        self.assertEqual(c.pinned, "auto")
        c.handle("/foo")
        self.assertIn("Unknown command /foo", self.text())
        self.assertFalse(c.handle("/exit"))
        self.assertFalse(c.handle("salir"))

    def test_removed_commands_are_gone(self):
        c = self.chat()
        for cmd in ("/manifest", "/manager claude", "/setup", "/config x", "/calibrate", "/criteria", "/benchmarks", "/llm on"):
            c.handle(cmd)
        self.assertEqual(self.text().count("Unknown command"), 8)

    def test_help_and_palette_list_the_new_commands_only(self):
        names = {c.name for c in chat.COMMANDS}
        self.assertTrue({"/scores", "/metrics", "/priorities", "/models", "/model", "/stats"} <= names)
        self.assertFalse(names & {"/manifest", "/manager", "/setup", "/calibrate", "/criteria", "/benchmarks", "/llm", "/config"})
        self.assertIn("/priorities", chat.HELP)
        self.assertNotIn("manifiesto", chat.HELP.lower())

    def test_scores_and_breakdown(self):
        c = self.chat()
        c.handle("/scores")
        self.assertIn("category", self.text())
        c.handle("/scores coding")
        self.assertIn("Σ weight × value", self.text())

    def test_metrics_shows_where_the_data_comes_from(self):
        c = self.chat()
        c.handle("/metrics")
        self.assertIn("claude-sonnet-5.5-xhigh", self.text())
        self.assertIn("bundled with this version", self.text())

    def test_metrics_refresh_goes_through_the_visible_report(self):
        c = self.chat()
        with mock.patch.object(S, "refresh_and_report", return_value=True) as ref:
            c.handle("/metrics refresh")
            self.assertFalse(ref.call_args.kwargs["force"])
            c.handle("/metrics force")
            self.assertTrue(ref.call_args.kwargs["force"])
        self.assertIn("CLI model", self.text())

    def test_priorities_runs_the_questionnaire_and_shows_the_new_routing(self):
        c = self.chat()
        with mock.patch.object(priorities, "run", return_value={"priorities": {}}) as run:
            c.handle("/priorities")
            run.assert_called_once()
        self.assertIn("category", self.text())                       # after saving it shows the table
        self.out.clear()
        with mock.patch.object(priorities, "run", return_value=None):
            c.handle("/priorities")
        self.assertNotIn("category", self.text())

    def test_models_lists_the_model_each_cli_uses(self):
        c = self.chat()
        c.handle("/models")
        self.assertIn("model: claude-sonnet-5-5", self.text())
        self.assertIn("model: Gemini 3.8 Flash (High)", self.text())

    def test_models_probe_learns_ids(self):
        state.remember_model_id("claude", "viejo")
        c = self.chat()
        c.handle("/models probe")
        self.assertIn("model: claude-fake-1", self.text())
        self.assertEqual(state.seen_ids()["claude"], "claude-fake-1")


class StartupTests(Base):
    def run_startup(self, *answers, **kw):
        c = self.chat(*answers)
        c.startup()
        return c

    def test_unknown_models_are_detected_after_asking(self):
        c = self.run_startup("s", "n")                                # detect: yes; (if it asks something else) no
        self.assertIn("I need to know which model each CLI uses", self.text())
        self.assertIn("minimal query", self.text())
        self.assertEqual(state.seen_ids()["claude"], "claude-fake-1")

    def test_declining_detection_spends_nothing(self):
        with mock.patch.object(probe, "probe", side_effect=AssertionError("should not have queried")):
            self.run_startup("n")
        self.assertIn("the models.json estimate applies", self.text())
        self.assertEqual(state.seen_ids(), {})

    def test_known_models_skip_the_detection_question(self):
        self.restart(ids=True)
        self.run_startup()
        self.assertNotIn("I need to know", self.text())
        self.assertEqual(self.inputs, [])

    def test_stale_metrics_offer_an_update_once_per_day(self):
        self.restart(ids=True, stale=True)
        with mock.patch.object(S, "refresh_and_report", return_value=True) as ref:
            c = self.run_startup("s")
            self.assertIn("The metrics are from 2020-01-01", self.text())
            self.assertIn("bundled with this version", self.text())
            ref.assert_called_once()
            self.out.clear()
            self.run_startup()                                         # same day: it does not ask again
            self.assertNotIn("The metrics are from", self.text())
            self.assertEqual(ref.call_count, 1)

    def test_declining_the_update_changes_nothing_and_does_not_nag(self):
        self.restart(ids=True, stale=True)
        with mock.patch.object(S, "refresh_and_report") as ref:
            self.run_startup("n")
            ref.assert_not_called()
            self.run_startup()
            ref.assert_not_called()
        self.assertEqual(state.flags()["asked_refresh"], time.strftime("%Y-%m-%d"))

    def test_fresh_metrics_are_not_questioned(self):
        self.restart(ids=True)
        with mock.patch.object(S, "refresh_and_report") as ref:
            self.run_startup("n")
            ref.assert_not_called()
        self.assertNotIn("The metrics are from", self.text())

    def test_priorities_are_offered_once_and_default_to_no(self):
        self.restart(ids=True)
        with mock.patch.object(priorities, "run") as run:
            self.run_startup("")                                      # Enter = no
            self.assertIn("6 questions", self.text())
            run.assert_not_called()
            self.out.clear()
            self.run_startup()                                        # already offered: it does not insist
            self.assertNotIn("6 questions", self.text())
        self.assertTrue(state.flags()["asked_priorities"])

    def test_accepting_runs_the_questionnaire(self):
        self.restart(ids=True)
        with mock.patch.object(priorities, "run") as run:
            self.run_startup("s")
            run.assert_called_once()

    def test_priorities_are_not_offered_when_they_already_exist_or_there_are_no_metrics_for_the_models(self):
        self.restart(ids=True)
        S.save_profile({"priorities": {"coding": "precision"}})
        with mock.patch.object(priorities, "run") as run:
            self.run_startup()
            run.assert_not_called()
        self.assertNotIn("6 questions", self.text())
        os.remove(Path(self.tmp.name) / "models_seen.json")
        os.remove(S.profile_path())
        M._memo.clear()
        self.run_startup("n")                                          # without ids: it asks to detect them, but not about priorities
        self.assertNotIn("6 questions", self.text())

    def test_without_speed_or_cost_the_questions_are_not_offered_and_a_hint_appears_once(self):
        self.restart(ids=True)
        self.snap.write_text(json.dumps(F.snapshot(no_price=("gpt-6.1-sol-high", "gpt-6.1-sol-max"))))   # Arena does not publish codex's price
        M._memo.clear()
        with mock.patch.object(priorities, "run") as run:
            self.run_startup()
            self.assertIn("Tip: with your free Artificial Analysis key", self.text())
            self.assertNotIn("6 questions", self.text())
            self.out.clear()
            self.run_startup()
            self.assertNotIn("Consejo", self.text())                      # it is not repeated
            run.assert_not_called()

    def test_no_metrics_at_all_is_explained(self):
        self.restart(ids=True)
        self.snap.unlink()
        M._memo.clear()
        self.run_startup()
        self.assertIn("There are no bundled or downloaded metrics", self.text())


class LoopTests(Base):
    def test_banner_shows_where_the_metrics_come_from(self):
        out = self.chat().banner()
        self.assertIn("metrics", out)
        self.assertIn("Arena", out)
        self.assertIn("(bundled)", out)
        self.assertNotIn("manager", out)

    def test_loop_runs_startup_then_exits_on_eof(self):
        self.restart(ids=True)
        c = self.chat()
        self.assertEqual(c.loop(), 0)
        self.assertIn("╦═╗╔═╗╦ ╦╔╦╗╔═╗╦═╗", self.text())

    def test_status_line_for_the_editor(self):
        self.assertTrue(self.chat().status_line().startswith("metrics "))
        self.snap.unlink()
        M._memo.clear()
        self.assertEqual(self.chat().status_line(), "no metrics")

    def test_ctrl_c_during_startup_does_not_kill_the_session(self):
        c = self.chat()
        with mock.patch.object(c, "startup", side_effect=KeyboardInterrupt):
            self.assertEqual(c.loop(), 0)
        self.assertIn("startup interrupted", self.text())


if __name__ == "__main__":
    unittest.main()
