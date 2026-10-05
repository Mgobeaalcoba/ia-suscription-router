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
        for t in ("Arreglá este bug en mi función Python", "Redactá un mail de seguimiento para un cliente", "Resumí este documento\ncon varias líneas",
                  "Prefiero que el mail sea corto, redactá uno para Juan", "usá Codex para todo lo de código"):   # ya no hay configuración hablada: todo esto es una tarea
            self.check(t, "task")

    def test_queries(self):
        self.check("qué modelos tengo disponibles", "models")
        self.check("mostrame las estadísticas", "stats")
        self.check("mostrame los puntajes", "scores")
        self.check("¿cómo decide el router?", "scores")

    def test_long_or_code_is_always_a_task(self):
        self.check("mostrame las estadísticas " + "x" * 400, "task")
        self.check("qué modelos tengo ```print(1)```", "task")


class Base(unittest.TestCase):
    ids = False       # si es True, el estado ya conoce los modelos de cada CLI
    stale = False     # si es True, la foto incluida es vieja

    def setUp(self):
        self._init()

    def restart(self, ids=None, stale=None):
        """Vuelve a armar el estado con otras condiciones, limpiando el anterior."""
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
        self.assertTrue(c.handle("Arreglá este bug en mi función Python, falla el test"))
        self.assertIn("[fake-codex]", self.text())                  # sin ids conocidos rige la estimación de models.json
        self.assertIn("codex", self.text().split("──")[1])
        self.assertEqual(len(c.history), 1)

    def test_with_metrics_the_task_follows_the_scores(self):
        self.restart(ids=True)
        S.save_profile({"priorities": {"coding": "cost", "debugging": "cost"}})   # sin AA solo hay costo; el más barato es antigravity
        M._memo.clear()
        c = self.chat()
        c.explain = True
        c.handle("Arreglá este bug en mi función Python, falla el test")
        self.assertIn("métricas: sí", self.text())
        self.assertIn("[fake-agy]", self.text())

    def test_history_is_prepended_to_next_task(self):
        c = self.chat()
        c.pinned = "codex"
        c.handle("primera pregunta")
        c.handle("segunda pregunta")
        sizes = [int(n) for n in re.findall(r"recibi (\d+) chars", self.text())]
        self.assertEqual(len(sizes), 2)
        self.assertGreater(sizes[1], sizes[0] + len("segunda pregunta") - 1)

    def test_short_followup_inherits_previous_topic(self):
        c = self.chat()
        c.explain = True
        c.handle("Escribí una función Python que invierta un string")
        self.out.clear()
        c.handle("Ahora hacela recursiva")                          # sola iría a 'quick'; hereda 'coding'
        self.assertIn("coding×", self.text())
        c.handle("/clear")
        self.assertIsNone(c.route_text("Ahora hacela recursiva"))

    def test_followup_with_own_topic_does_not_inherit(self):
        c = self.chat()
        c.handle("Escribí una función Python que invierta un string")
        self.assertIsNone(c.route_text("Redactá un mail de seguimiento para un cliente"))

    def test_failed_task_reports_error(self):
        os.environ["FAKE_CODEX_MODE"] = "fail"
        c = self.chat()
        c.pinned = "codex"
        c.handle("hola")
        self.assertIn("No pude resolverlo", self.text())
        self.assertEqual(c.history, [])

    def test_a_path_alone_is_a_task_not_a_command(self):
        f = Path(self.tmp.name) / "x.png"
        f.write_bytes(b"\x89PNG")
        c = self.chat()
        c.pinned = "claude"
        c.handle(str(f))
        self.assertNotIn("No conozco", self.text())
        self.assertIn("⎘ x.png · imagen", self.text())


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
        self.assertIn("No conozco /foo", self.text())
        self.assertFalse(c.handle("/exit"))
        self.assertFalse(c.handle("salir"))

    def test_removed_commands_are_gone(self):
        c = self.chat()
        for cmd in ("/manifest", "/manager claude", "/setup", "/config x", "/calibrate", "/criteria", "/benchmarks", "/llm on"):
            c.handle(cmd)
        self.assertEqual(self.text().count("No conozco"), 8)

    def test_help_and_palette_list_the_new_commands_only(self):
        names = {c.name for c in chat.COMMANDS}
        self.assertTrue({"/scores", "/metrics", "/priorities", "/models", "/model", "/stats"} <= names)
        self.assertFalse(names & {"/manifest", "/manager", "/setup", "/calibrate", "/criteria", "/benchmarks", "/llm", "/config"})
        self.assertIn("/priorities", chat.HELP)
        self.assertNotIn("manifiesto", chat.HELP.lower())

    def test_scores_and_breakdown(self):
        c = self.chat()
        c.handle("/scores")
        self.assertIn("categoría", self.text())
        c.handle("/scores coding")
        self.assertIn("Σ peso × valor", self.text())

    def test_metrics_shows_where_the_data_comes_from(self):
        c = self.chat()
        c.handle("/metrics")
        self.assertIn("claude-sonnet-5.5-xhigh", self.text())
        self.assertIn("incluida en esta versión", self.text())

    def test_metrics_refresh_goes_through_the_visible_report(self):
        c = self.chat()
        with mock.patch.object(S, "refresh_and_report", return_value=True) as ref:
            c.handle("/metrics refresh")
            self.assertFalse(ref.call_args.kwargs["force"])
            c.handle("/metrics force")
            self.assertTrue(ref.call_args.kwargs["force"])
        self.assertIn("modelo del CLI", self.text())

    def test_priorities_runs_the_questionnaire_and_shows_the_new_routing(self):
        c = self.chat()
        with mock.patch.object(priorities, "run", return_value={"priorities": {}}) as run:
            c.handle("/priorities")
            run.assert_called_once()
        self.assertIn("categoría", self.text())                       # tras guardar muestra la tabla
        self.out.clear()
        with mock.patch.object(priorities, "run", return_value=None):
            c.handle("/priorities")
        self.assertNotIn("categoría", self.text())

    def test_models_lists_the_model_each_cli_uses(self):
        c = self.chat()
        c.handle("/models")
        self.assertIn("modelo: claude-sonnet-5-5", self.text())
        self.assertIn("modelo: Gemini 3.8 Flash (High)", self.text())

    def test_models_probe_learns_ids(self):
        state.remember_model_id("claude", "viejo")
        c = self.chat()
        c.handle("/models probe")
        self.assertIn("modelo: claude-fake-1", self.text())
        self.assertEqual(state.seen_ids()["claude"], "claude-fake-1")


class StartupTests(Base):
    def run_startup(self, *answers, **kw):
        c = self.chat(*answers)
        c.startup()
        return c

    def test_unknown_models_are_detected_after_asking(self):
        c = self.run_startup("s", "n")                                # detectar: sí; (si pregunta algo más) no
        self.assertIn("necesito saber qué modelo usa cada CLI", self.text())
        self.assertIn("consulta mínima", self.text())
        self.assertEqual(state.seen_ids()["claude"], "claude-fake-1")

    def test_declining_detection_spends_nothing(self):
        with mock.patch.object(probe, "probe", side_effect=AssertionError("no debía consultar")):
            self.run_startup("n")
        self.assertIn("rige la estimación de models.json", self.text())
        self.assertEqual(state.seen_ids(), {})

    def test_known_models_skip_the_detection_question(self):
        self.restart(ids=True)
        self.run_startup()
        self.assertNotIn("necesito saber", self.text())
        self.assertEqual(self.inputs, [])

    def test_stale_metrics_offer_an_update_once_per_day(self):
        self.restart(ids=True, stale=True)
        with mock.patch.object(S, "refresh_and_report", return_value=True) as ref:
            c = self.run_startup("s")
            self.assertIn("Las métricas son del 2020-01-01", self.text())
            self.assertIn("incluida en esta versión", self.text())
            ref.assert_called_once()
            self.out.clear()
            self.run_startup()                                         # mismo día: no vuelve a preguntar
            self.assertNotIn("Las métricas son del", self.text())
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
        self.assertNotIn("Las métricas son del", self.text())

    def test_priorities_are_offered_once_and_default_to_no(self):
        self.restart(ids=True)
        with mock.patch.object(priorities, "run") as run:
            self.run_startup("")                                      # Enter = no
            self.assertIn("6 preguntas", self.text())
            run.assert_not_called()
            self.out.clear()
            self.run_startup()                                        # ya se ofreció: no insiste
            self.assertNotIn("6 preguntas", self.text())
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
        self.assertNotIn("6 preguntas", self.text())
        os.remove(Path(self.tmp.name) / "models_seen.json")
        os.remove(S.profile_path())
        M._memo.clear()
        self.run_startup("n")                                          # sin ids: pregunta por detectarlos, pero no por prioridades
        self.assertNotIn("6 preguntas", self.text())

    def test_without_speed_or_cost_the_questions_are_not_offered_and_a_hint_appears_once(self):
        self.restart(ids=True)
        self.snap.write_text(json.dumps(F.snapshot(no_price=("gpt-6.1-sol-high", "gpt-6.1-sol-max"))))   # Arena no publica el precio de codex
        M._memo.clear()
        with mock.patch.object(priorities, "run") as run:
            self.run_startup()
            self.assertIn("Consejo: con tu clave gratuita de Artificial Analysis", self.text())
            self.assertNotIn("6 preguntas", self.text())
            self.out.clear()
            self.run_startup()
            self.assertNotIn("Consejo", self.text())                      # no se repite
            run.assert_not_called()

    def test_no_metrics_at_all_is_explained(self):
        self.restart(ids=True)
        self.snap.unlink()
        M._memo.clear()
        self.run_startup()
        self.assertIn("No hay métricas incluidas ni descargadas", self.text())


class LoopTests(Base):
    def test_banner_shows_where_the_metrics_come_from(self):
        out = self.chat().banner()
        self.assertIn("métricas", out)
        self.assertIn("Arena", out)
        self.assertIn("(incluida)", out)
        self.assertNotIn("manager", out)

    def test_loop_runs_startup_then_exits_on_eof(self):
        self.restart(ids=True)
        c = self.chat()
        self.assertEqual(c.loop(), 0)
        self.assertIn("╦═╗╔═╗╦ ╦╔╦╗╔═╗╦═╗", self.text())

    def test_status_line_for_the_editor(self):
        self.assertTrue(self.chat().status_line().startswith("métricas "))
        self.snap.unlink()
        M._memo.clear()
        self.assertEqual(self.chat().status_line(), "sin métricas")

    def test_ctrl_c_during_startup_does_not_kill_the_session(self):
        c = self.chat()
        with mock.patch.object(c, "startup", side_effect=KeyboardInterrupt):
            self.assertEqual(c.loop(), 0)
        self.assertIn("inicio interrumpido", self.text())


if __name__ == "__main__":
    unittest.main()
