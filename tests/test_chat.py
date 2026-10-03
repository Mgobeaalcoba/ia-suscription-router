import json, os, re, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import chat, core, manifest  # noqa: E402

NAMES = ["claude", "codex", "antigravity"]
CLAUDE_FIRST = json.dumps({"tasks": {"coding": {"prefer": ["claude", "codex"], "why": "pref"}}, "disabled": []})


class IntentTests(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(chat.detect_intent(text, NAMES), expected, text)

    def test_tasks(self):
        self.check("Arreglá este bug en mi función Python", "task")
        self.check("Redactá un mail de seguimiento para un cliente", "task")
        self.check("Prefiero que el mail sea corto, redactá uno para Juan", "task")
        self.check("Resumí este documento\ncon varias líneas", "task")
        self.check("Siempre que escribo código me da error, arreglalo", "ambiguous")  # mejor preguntar que adivinar

    def test_config(self):
        self.check("Prefiero Codex para código", "config")
        self.check("usá Codex para todo lo de código", "config")
        self.check("nunca uses antigravity para matemática", "config")
        self.check("cambiá el manifiesto: claude primero en escritura", "config")
        self.check("quiero que el manager sea codex", "config")
        self.check("desactivá antigravity", "config")

    def test_ambiguous(self):
        self.check("usá Codex para revisar este bug", "ambiguous")

    def test_queries(self):
        self.check("mostrame el manifiesto", "manifest")
        self.check("¿cómo estoy configurado?", "manifest")
        self.check("qué modelos tengo disponibles", "models")
        self.check("mostrame las estadísticas", "stats")

    def test_long_or_code_is_task(self):
        self.check("Prefiero Codex para código " + "x" * 400, "task")
        self.check("Usá Codex ```print(1)```", "task")


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith("FAKE_"):
                del os.environ[k]
        self.out, self.inputs = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def chat(self, *answers):
        self.inputs = list(answers)
        return chat.Chat(read=lambda _p: self.inputs.pop(0), write=self.out.append)

    def text(self):
        return "\n".join(self.out)

    def make_manifest(self):
        cfg = core.load_config(apply_manifest=False)
        manifest.save(manifest.generate(cfg, None, "claude"), "base")

    def test_task_is_routed_and_answer_shown(self):
        c = self.chat()
        self.assertTrue(c.handle("Arreglá este bug en mi función Python, falla el test"))
        self.assertIn("[fake-codex]", self.text())
        self.assertIn("codex", self.text().split("──")[1])
        self.assertEqual(len(c.history), 1)

    def test_history_is_prepended_to_next_task(self):
        c = self.chat()
        c.pinned = "codex"
        c.handle("primera pregunta")
        c.handle("segunda pregunta")
        sizes = [int(n) for n in re.findall(r"recibi (\d+) chars", self.text())]
        self.assertEqual(len(sizes), 2)
        self.assertGreater(sizes[1], sizes[0] + len("segunda pregunta") - 1)  # incluye el turno anterior

    def test_short_followup_inherits_previous_topic(self):
        cfg = core.load_config(apply_manifest=False)
        prefs = {"tasks": {"coding": {"prefer": ["codex", "claude"]}, "quick": {"prefer": ["claude", "codex"]}}}
        manifest.save(manifest.generate(cfg, lambda _p: json.dumps(prefs), "claude"), "t")
        c = self.chat()
        c.handle("Escribí una función Python que invierta un string")
        self.assertEqual(c.history[-1][1], "codex")
        c.handle("Ahora hacela recursiva")  # sola iría a 'quick' → claude; hereda 'coding' → codex
        self.assertEqual(c.history[-1][1], "codex")
        c.handle("/clear")
        self.assertIsNone(c.route_text("Ahora hacela recursiva"))  # sin historial no hay a qué heredar

    def test_followup_with_own_topic_does_not_inherit(self):
        c = self.chat()
        c.handle("Escribí una función Python que invierta un string")
        self.assertIsNone(c.route_text("Redactá un mail de seguimiento para un cliente"))

    def test_clear_forgets_history(self):
        c = self.chat()
        c.pinned = "codex"
        c.handle("hola uno")
        c.handle("/clear")
        self.assertEqual(c.history, [])

    def test_config_applies_after_confirmation(self):
        self.make_manifest()
        os.environ["FAKE_CLAUDE_OUT"] = CLAUDE_FIRST
        c = self.chat("s")
        c.handle("Prefiero Claude para código")
        self.assertIn("Cambios propuestos", self.text())
        self.assertEqual(manifest.load()["tasks"]["coding"]["prefer"][0], "claude")

    def test_config_rejected_changes_nothing(self):
        self.make_manifest()
        before = manifest.load()["tasks"]["coding"]["prefer"]
        os.environ["FAKE_CLAUDE_OUT"] = CLAUDE_FIRST
        c = self.chat("n")
        c.handle("Prefiero Claude para código")
        self.assertEqual(manifest.load()["tasks"]["coding"]["prefer"], before)

    def test_config_with_invalid_manager_reply_changes_nothing(self):
        self.make_manifest()
        before = manifest.load()
        os.environ["FAKE_CLAUDE_OUT"] = "no soy json"
        c = self.chat()
        c.handle("Prefiero Claude para código")
        self.assertIn("no cambié nada", self.text())
        self.assertEqual(manifest.load()["tasks"], before["tasks"])

    def test_ambiguous_asks_and_defaults_to_task(self):
        c = self.chat("")
        c.handle("usá Codex para revisar este bug")
        self.assertEqual(len(c.history), 1)  # se ejecutó como tarea

    def test_config_without_manifest_creates_one(self):
        os.environ["FAKE_CLAUDE_OUT"] = CLAUDE_FIRST
        c = self.chat()
        c.handle("Prefiero Claude para código")
        self.assertIsNotNone(manifest.load())

    def test_manager_change_by_text(self):
        self.make_manifest()
        c = self.chat()
        c.handle("quiero que el manager sea codex")
        self.assertEqual(manifest.load()["manager"], "codex")
        c.handle("/manager claude")
        self.assertEqual(manifest.load()["manager"], "claude")

    def test_commands(self):
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

    def test_failed_task_reports_error(self):
        os.environ["FAKE_CODEX_MODE"] = "fail"
        c = self.chat()
        c.pinned = "codex"
        c.handle("hola")
        self.assertIn("No pude resolverlo", self.text())
        self.assertEqual(c.history, [])

    def test_loop_exits_on_eof_and_offers_setup(self):
        os.environ["FAKE_CLAUDE_OUT"] = CLAUDE_FIRST
        answers = iter(["s", "Claude para código"])

        def read(_p):
            try:
                return next(answers)
            except StopIteration:
                raise EOFError

        self.assertEqual(chat.Chat(read=read, write=self.out.append).loop(), 0)
        self.assertIsNotNone(manifest.load())


if __name__ == "__main__":
    unittest.main()
