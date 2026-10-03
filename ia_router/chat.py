"""Modo conversacional: abrís `ia-router` y hablás. Cada mensaje es una tarea (se rutea y ejecuta),
una instrucción de configuración (el manager actualiza el manifiesto) o una consulta sobre el estado.

Los CLIs no tienen memoria entre llamadas, así que el chat antepone un resumen de los últimos turnos
a cada tarea. La interpretación de intención es por reglas (rápida, sin gastar cuota); si es ambigua,
se pregunta. Los comandos con "/" siempre funcionan como atajos.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__, adapters, attachments, banner, core, editor as editor_mod, manifest, render, router, state

HISTORY_TURNS = 6
HISTORY_ANSWER_CHARS = 1500
HISTORY_TOTAL_CHARS = 8000

HELP = """Hablame normal: si es una tarea la rutea al mejor modelo; si es una preferencia ("usá Codex para todo lo de código") actualizo tu manifiesto.

Atajos:
  /manifest          ver tus preferencias        /models      estado de los CLIs (/models probe = con llamada real)
  /stats             éxito y latencia por modelo /model X     fijar un modelo (X = claude|codex|... o auto)
  /manager X         cambiar el modelo barato    /llm on|off  clasificar tareas con el manager
  /explain on|off    mostrar la tabla de ruteo   /setup       rearmar el manifiesto desde cero
  /md on|off         markdown interpretado o texto crudo
  /ask texto         forzar "es una tarea"       /config texto forzar "es configuración"
  /clear             olvidar la conversación     /help        esta ayuda      /exit  salir"""

COMMANDS = [
    editor_mod.Command("/help", "ver los atajos"), editor_mod.Command("/manifest", "ver tus preferencias"),
    editor_mod.Command("/models", "estado de los CLIs (/models probe = llamada real)"), editor_mod.Command("/model", "fijar un modelo o auto"),
    editor_mod.Command("/stats", "éxito, latencia y tokens por modelo"), editor_mod.Command("/manager", "cambiar el modelo barato"),
    editor_mod.Command("/llm", "clasificar tareas con el manager (on|off)"), editor_mod.Command("/explain", "mostrar la tabla de ruteo (on|off)"),
    editor_mod.Command("/md", "markdown interpretado o crudo (on|off)"), editor_mod.Command("/setup", "rearmar el manifiesto desde cero"),
    editor_mod.Command("/ask", "forzar: es una tarea"), editor_mod.Command("/config", "forzar: es configuración"),
    editor_mod.Command("/clear", "olvidar la conversación"), editor_mod.Command("/exit", "salir"),
]

# ---------- interpretación de mensajes ----------

_CATEGORY_WORDS = (r"c[oó]digo|programar|debug\w*|\bbugs?\b|escritur\w+|redacci[oó]n|an[aá]lisis|datos|investigaci[oó]n|"
                   r"matem[aá]tic\w+|multimodal|im[aá]genes|contexto largo|documentos? largos?|tareas? r[aá]pidas?")
_STRONG_PREF = (r"\bprefiero\b|\bsiempre\b|\bnunca\b|por defecto|de ahora en (m[aá]s|adelante)|a partir de ahora|"
                r"no quiero|desactiv\w+|deshabilit\w+|para todo\b|para todas? las|para cualquier")
_WEAK_VERB = r"\b(us[aá]r?|us[ae]n?|pon[eé]|poner|cambi[aá]|prioriz\w+|dej[aá]|mand[aá]|ruteá)\b"
_ROUTER_WORDS = r"manifiesto|\bmanager\b|\bpreferencias\b"
_SHOW = [
    (r"(mostr\w+|ver|cu[aá]l es|c[oó]mo (est[aá]|quedó)).{0,30}(manifiesto|configuraci[oó]n|preferencias)|c[oó]mo (estoy|est[aá]) configurad", "manifest"),
    (r"estad[ií]sticas?|\bstats\b", "stats"),
    (r"qu[eé] modelos|cu[aá]les modelos|modelos disponibles|qu[eé] clis", "models"),
]


def detect_intent(text: str, model_names: List[str]) -> str:
    """Devuelve 'manifest'|'stats'|'models' (consulta), 'config', 'task' o 'ambiguous'."""
    t = text.strip()
    low = t.lower()
    if len(t) > 400 or "\n" in t or "```" in t:
        return "task"
    for pat, kind in _SHOW:
        if re.search(pat, low):
            return kind
    has_model = any(re.search(rf"\b{re.escape(m)}\b", low) for m in model_names)
    strong = bool(re.search(_STRONG_PREF, low))
    if re.search(_ROUTER_WORDS, low) and (strong or re.search(_WEAK_VERB, low) or has_model):
        return "config"
    if has_model and strong:
        return "config"
    if has_model and re.search(_WEAK_VERB, low):
        return "ambiguous"
    if strong and re.search(_CATEGORY_WORDS, low):
        return "ambiguous"
    return "task"


def build_preamble(history: List[Tuple[str, str, str]]) -> str:
    """Resumen de los últimos turnos (usuario, modelo, respuesta) para anteponer a la próxima tarea."""
    if not history:
        return ""
    lines: List[str] = []
    for user, model, answer in history[-HISTORY_TURNS:]:
        a = answer if len(answer) <= HISTORY_ANSWER_CHARS else answer[:HISTORY_ANSWER_CHARS] + " […]"
        lines.append(f"Usuario: {user}\nAsistente ({model}): {a}")
    body = "\n\n".join(lines)
    body = body[-HISTORY_TOTAL_CHARS:]
    return f"[Conversación previa, solo como contexto]\n{body}\n[Fin del contexto]\n\nMensaje actual del usuario:\n"


# ---------- sesión ----------

class Chat:
    def __init__(self, read: Callable[[str], str] = input, write: Callable[[str], None] = print, color: bool = False,
                 editor: Optional[editor_mod.LineEditor] = None):
        self.read, self.write, self.color, self.editor = read, write, color, editor
        self.history: List[Tuple[str, str, str]] = []
        self.pinned = "auto"
        self.use_llm = False
        self.explain = False
        self.markdown = True

    # --- utilidades ---
    def dim(self, s: str) -> str:
        return f"\033[2m{s}\033[0m" if self.color else s

    def say(self, s: str = "") -> None:
        self.write(s)

    def ask_yes(self, question: str, default: bool = True) -> bool:
        try:
            ans = self.read(f"{question} [{'S/n' if default else 's/N'}]: ").strip().lower()
        except EOFError:
            return default
        return default if not ans else ans in ("s", "si", "sí", "y", "yes")

    # --- entrada principal ---
    def handle(self, text: str) -> bool:
        """Procesa un mensaje. Devuelve False si hay que salir."""
        text = text.strip()
        if not text:
            return True
        first = attachments.tokens(text)[0][2]
        if text.startswith("/") and not attachments.resolve(first):  # "/Users/x/foto.png" es un archivo, no un comando
            return self.command(text)
        if text.lower() in ("exit", "salir", "quit", "chau"):
            return False
        cfg = core.load_config()
        intent = detect_intent(text, list(cfg["models"]))
        if intent == "ambiguous":
            intent = "config" if self.read("¿Es una instrucción de configuración (c) o una tarea para ejecutar (t)? [c/T]: ").strip().lower().startswith("c") else "task"
        if intent in ("manifest", "stats", "models"):
            return self.command("/" + intent)
        if intent == "config":
            self.configure(text)
        else:
            self.run_task(text)
        return True

    # --- tarea ---
    def run_task(self, text: str, files: Optional[List[str]] = None) -> None:
        cfg = core.load_config()
        atts = attachments.find(text)
        if atts and self.editor is None:  # con el editor, los adjuntos ya se mostraron al enviar
            for a in atts:
                self.say(self.dim(f"⎘ {a.label()}"))
        self.say(self.dim("… ruteando"))
        try:
            res = core.ask(text, cfg, model=self.pinned, context_files=files, use_llm=self.use_llm, attachments=atts,
                           preamble=build_preamble(self.history), route_text=self.route_text(text))
        except ValueError as exc:
            self.say(f"Error: {exc}")
            return
        dec = res["decision"]
        if self.explain:
            self.say(core.format_ranking(dec))
        for w in res.get("warnings", []):
            self.say(f"aviso: {w}")
        if not res["ok"]:
            trace = "; ".join(f"{a['model']}: {(a['error'] or 'ok')[:60]}" for a in res["attempts"]) or "sin intentos"
            self.say(f"No pude resolverlo: {res.get('error')} ({trace}). Probá /models o /models probe.")
            return
        used = res["model_used"]
        why = ", ".join(f"{c}×{w:g}" for c, w in dec["weights"].items()) or "general"
        secs = sum(a["seconds"] for a in res["attempts"])
        fb = f" · fallback tras {', '.join(a['model'] for a in res['attempts'][:-1])}" if len(res["attempts"]) > 1 else ""
        self.say(self.dim(f"── {core.format_usage(res)} · {why} · {secs:.1f}s{fb}"))
        self.say(render.render(res["output"], color=self.color and self.markdown) + "\n")
        self.history.append((text, used, res["output"]))

    def route_text(self, text: str) -> Optional[str]:
        """Un seguimiento corto sin tema propio ("ahora hacela recursiva") hereda el del mensaje anterior."""
        if self.history and set(router.detect(text)) <= {"quick"}:
            return self.history[-1][0] + "\n" + text
        return None

    # --- configuración por lenguaje natural ---
    def configure(self, text: str) -> None:
        cfg = core.load_config(apply_manifest=False)
        current = manifest.load()
        if current is None:
            self.say("Todavía no hay manifiesto. Lo armo con lo que me dijiste como preferencia.")
            self.setup(notes=text)
            return
        names = list(cfg["models"])
        mgr = re.search(r"\bmanager\b", text.lower()) and next((m for m in names if re.search(rf"\b{re.escape(m)}\b", text.lower())), None)
        if mgr:
            current["manager"] = mgr
            manifest.save(current, f"manager → {mgr}")
            self.say(f"Listo: el manager ahora es {mgr}.")
            return
        self.say(self.dim(f"… consultando al manager ({current.get('manager')})"))
        new = manifest.refine(cfg, core.manager_runner(cfg, current.get("manager")), current, text)
        if new is None:
            self.say("El manager no devolvió un manifiesto válido; no cambié nada. Probá decirlo de otra forma.")
            return
        d = manifest.diff(current, new)
        self.say("Cambios propuestos:\n" + d)
        if d.strip().startswith("(sin cambios"):
            return
        if self.ask_yes("¿Aplico?"):
            manifest.save(new, f"chat: {text}")
            self.say("Aplicado.")

    def setup(self, notes: str = "", probe: bool = True) -> None:
        cfg = core.load_config(apply_manifest=False)
        mgr = (manifest.load() or {}).get("manager") or core.manager_name(cfg)
        probed = None
        if probe:
            self.say(self.dim("… probando cada CLI con un prompt mínimo"))
            probed = manifest.probe(cfg)
            for n, p in probed.items():
                self.say(f"  {n:<12} auth={p['auth']} {p.get('probe_seconds', '-')}s")
        self.say(self.dim(f"… armando el manifiesto con {mgr}"))
        m = manifest.generate(cfg, core.manager_runner(cfg, mgr), mgr, notes, probed)
        manifest.save(m, "setup (chat)")
        self.say(manifest.render(m))

    # --- comandos ---
    def command(self, line: str) -> bool:
        cmd, _, arg = line[1:].partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        cfg = core.load_config()
        if cmd in ("exit", "quit", "salir"):
            return False
        if cmd == "help":
            self.say(HELP)
        elif cmd == "manifest":
            m = manifest.load()
            self.say(manifest.render(m) if m else "Todavía no hay manifiesto: escribí /setup o contame tus preferencias.")
        elif cmd == "models":
            self.models(cfg, probe=(arg == "probe"))
        elif cmd == "stats":
            st = state.stats()
            self.say("\n".join([f"{n:<12} corridas={m['runs']} éxito={m['ok_rate']:.0%} medio={m['avg_seconds']}s rate_limits={m['rate_limits']} tokens in/out={m['tokens_in']}/{m['tokens_out']}" for n, m in st.items()]) or "Sin historial todavía.")
        elif cmd == "model":
            if arg in ("auto", *cfg["models"]):
                self.pinned = arg
                self.say(f"Modelo: {'automático (rutea el manifiesto)' if arg == 'auto' else 'fijado en ' + arg}.")
            else:
                self.say(f"Uso: /model auto|{'|'.join(cfg['models'])}")
        elif cmd == "manager":
            m = manifest.load()
            if arg in cfg["models"] and m:
                m["manager"] = arg
                manifest.save(m, f"manager → {arg}")
                self.say(f"Manager: {arg}.")
            else:
                self.say(f"Uso: /manager {'|'.join(cfg['models'])} (requiere manifiesto: /setup)")
        elif cmd == "llm":
            self.use_llm = arg != "off"
            self.say(f"Clasificación con el manager: {'activada' if self.use_llm else 'desactivada (solo reglas)'}.")
        elif cmd == "explain":
            self.explain = arg != "off"
            self.say(f"Tabla de ruteo: {'visible' if self.explain else 'oculta'}.")
        elif cmd == "md":
            self.markdown = arg != "off"
            self.say(f"Markdown: {'interpretado (como un README en GitHub, sin signos)' if self.markdown else 'texto crudo'}.")
        elif cmd == "setup":
            self.setup(notes=arg or (manifest.load() or {}).get("user_notes", ""))
        elif cmd == "clear":
            self.history.clear()
            self.say("Conversación olvidada.")
        elif cmd == "ask":
            self.run_task(arg) if arg else self.say("Uso: /ask <tarea>")
        elif cmd == "config":
            self.configure(arg) if arg else self.say("Uso: /config <lo que quieras cambiar>")
        else:
            self.say(f"No conozco /{cmd}. Escribí /help.")
        return True

    def models(self, cfg: Dict, probe: bool = False) -> None:
        probed = manifest.probe(cfg) if probe else {}
        for n, spec in cfg["models"].items():
            extra = f" auth={probed[n]['auth']} {probed[n].get('probe_seconds', '-')}s" if n in probed else ""
            off = " [desactivado]" if not spec.get("enabled", True) else ""
            cd = round(state.cooldown_remaining(n))
            self.say(f"{n:<12} {'instalado' if adapters.is_available(n, spec) else 'NO instalado':<13} cooldown={cd}s{extra}{off}")

    # --- bucle ---
    def banner(self) -> str:
        cfg = core.load_config()
        mgr = (manifest.load() or {}).get("manager") or core.manager_name(cfg)
        names = list(cfg["models"])
        ok = {n: cfg["models"][n].get("enabled", True) and adapters.is_available(n, cfg["models"][n]) for n in names}
        return banner.render(__version__, mgr, names, ok, attachments.safe_cwd(), color=self.color, width=shutil.get_terminal_size((80, 24)).columns, pinned=self.pinned)

    def status_line(self) -> str:
        cfg = core.load_config()
        return f"manager {(manifest.load() or {}).get('manager') or core.manager_name(cfg)}"

    def prompt(self) -> str:
        # \001..\002 marcan los códigos de color como no imprimibles para que readline calcule bien el cursor
        return f"\001\033[1;38;2;200;90;160m\002ia ❯\001\033[0m\002 " if self.color else "ia> "

    def loop(self) -> int:
        self.say(self.banner())
        if manifest.load() is None:
            self.say("Todavía no tenés manifiesto de preferencias.")
            if self.ask_yes("¿Lo armo ahora?"):
                notes = self.read("Contame tus preferencias (ej: 'Codex para código, Claude para escribir'; enter para omitir): ").strip()
                self.setup(notes=notes)
        while True:
            try:
                line = self.editor.read() if self.editor else self.read(self.prompt())
            except EOFError:
                self.say()
                return 0
            except KeyboardInterrupt:
                self.say("\n(Ctrl-C otra vez o /exit para salir)")
                try:
                    self.read("")
                except (EOFError, KeyboardInterrupt):
                    return 0
                continue
            try:
                if not self.handle(line):
                    return 0
            except KeyboardInterrupt:
                self.say("\n(interrumpido)")


def run() -> int:
    try:
        import readline  # noqa: F401  (edición de línea e historial con flechas)
    except ImportError:
        pass
    color = sys.stdout.isatty() and "NO_COLOR" not in os.environ
    chat = Chat(color=color)
    if editor_mod.supported():
        chat.editor = editor_mod.LineEditor(
            COMMANDS, model=lambda: chat.pinned, status=lambda: chat.status_line(),
            history_path=state.home() / "history.jsonl", color=color)
    return chat.loop()
