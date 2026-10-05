"""Modo conversacional: abrís `ia-router` y hablás. Cada mensaje es una tarea (se rutea al mejor modelo según las métricas y se
ejecuta) o una consulta sobre el estado. Al iniciar, el chat ofrece actualizar las métricas (mostrando cada paso) y, una sola vez,
hacerte las preguntas sobre qué priorizás en cada tipo de tarea.

Los CLIs no tienen memoria entre llamadas, así que el chat antepone un resumen de los últimos turnos a cada tarea. La
interpretación de intención es por reglas (rápida, sin gastar cuota). Los comandos con "/" siempre funcionan como atajos.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import __version__, adapters, attachments, banner, core, editor as editor_mod, metrics, priorities, probe as probe_mod, render, router, scoring, state

HISTORY_TURNS = 6
HISTORY_ANSWER_CHARS = 1500
HISTORY_TOTAL_CHARS = 8000

HELP = """Hablame normal: cada mensaje es una tarea y se rutea al mejor modelo según las métricas. Para ver o fijar cosas:

  /models [probe]    estado de los CLIs y qué modelo usa cada uno (probe = consulta mínima real)
  /scores [cat]      puntaje por modelo y categoría; con una categoría, el desglose
  /metrics [refresh] de dónde salen los datos; refresh = actualizarlos (force = aunque sean recientes)
  /priorities        preguntas: qué priorizás en cada tipo de tarea (precisión, velocidad, costo)
  /model X           fijar un modelo (X = claude|codex|... o auto)      /stats   éxito, latencia y tokens
  /explain on|off    mostrar la tabla de ruteo en cada mensaje          /md on|off   markdown interpretado o crudo
  /ask texto         forzar "es una tarea"                              /clear   olvidar la conversación
  /help              esta ayuda                                         /exit    salir"""

COMMANDS = [
    editor_mod.Command("/help", "ver los atajos"), editor_mod.Command("/models", "estado de los CLIs y qué modelo usa cada uno"),
    editor_mod.Command("/scores", "puntaje por modelo y categoría"), editor_mod.Command("/metrics", "de dónde salen los datos; refresh = actualizarlos"),
    editor_mod.Command("/priorities", "qué priorizás en cada tipo de tarea (preguntas)"), editor_mod.Command("/model", "fijar un modelo o auto"),
    editor_mod.Command("/stats", "éxito, latencia y tokens por modelo"), editor_mod.Command("/explain", "mostrar la tabla de ruteo (on|off)"),
    editor_mod.Command("/md", "markdown interpretado o crudo (on|off)"), editor_mod.Command("/ask", "forzar: es una tarea"),
    editor_mod.Command("/clear", "olvidar la conversación"), editor_mod.Command("/exit", "salir"),
]

# ---------- interpretación de mensajes ----------

_SHOW = [
    (r"estad[ií]sticas?|\bstats\b", "stats"),
    (r"qu[eé] modelos|cu[aá]les modelos|modelos disponibles|qu[eé] clis", "models"),
    (r"\bpuntajes?\b|c[oó]mo (rutea|decide|elige)|\branking\b", "scores"),
]


def detect_intent(text: str) -> str:
    """Devuelve 'stats'|'models'|'scores' (consulta) o 'task'. Un mensaje largo, con saltos de línea o con código es siempre una tarea."""
    t = text.strip()
    low = t.lower()
    if len(t) > 400 or "\n" in t or "```" in t:
        return "task"
    for pat, kind in _SHOW:
        if re.search(pat, low):
            return kind
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
        intent = detect_intent(text)
        if intent in ("stats", "models", "scores"):
            return self.command("/" + intent)
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
            res = core.ask(text, cfg, model=self.pinned, context_files=files, attachments=atts,
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

    # --- comandos ---
    def command(self, line: str) -> bool:
        cmd, _, arg = line[1:].partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        cfg = core.load_config()
        if cmd in ("exit", "quit", "salir"):
            return False
        if cmd == "help":
            self.say(HELP)
        elif cmd == "models":
            self.models(cfg, probe=(arg == "probe"))
        elif cmd == "stats":
            st = state.stats()
            self.say("\n".join([f"{n:<12} corridas={m['runs']} éxito={m['ok_rate']:.0%} medio={m['avg_seconds']}s rate_limits={m['rate_limits']} tokens in/out={m['tokens_in']}/{m['tokens_out']}" for n, m in st.items()]) or "Sin historial todavía.")
        elif cmd == "model":
            if arg in ("auto", *cfg["models"]):
                self.pinned = arg
                self.say(f"Modelo: {'automático (rutea por métricas)' if arg == 'auto' else 'fijado en ' + arg}.")
            else:
                self.say(f"Uso: /model auto|{'|'.join(cfg['models'])}")
        elif cmd == "explain":
            self.explain = arg != "off"
            self.say(f"Tabla de ruteo: {'visible' if self.explain else 'oculta'}.")
        elif cmd == "md":
            self.markdown = arg != "off"
            self.say(f"Markdown: {'interpretado (como un README en GitHub, sin signos)' if self.markdown else 'texto crudo'}.")
        elif cmd == "scores":
            self.say(scoring.render_table(cfg, [arg] if arg else None) + (("\n\n" + scoring.explain(cfg, arg)) if arg else ""))
        elif cmd == "metrics":
            if arg in ("refresh", "force"):
                scoring.refresh_and_report(core.load_config(apply_scoring=False), self.say, force=arg == "force")
                cfg = core.load_config()
            self.say(("\n" if arg else "") + scoring.describe_sources(cfg))
        elif cmd == "priorities":
            if priorities.run(cfg, say=self.say, color=self.color):
                self.say("\n" + scoring.render_table(core.load_config()))
        elif cmd == "clear":
            self.history.clear()
            self.say("Conversación olvidada.")
        elif cmd == "ask":
            self.run_task(arg) if arg else self.say("Uso: /ask <tarea>")
        else:
            self.say(f"No conozco /{cmd}. Escribí /help.")
        return True

    def models(self, cfg: Dict, probe: bool = False) -> None:
        probed = probe_mod.probe(cfg) if probe else {}
        ids = metrics.known_model_ids(list(cfg["models"]))
        for n, spec in cfg["models"].items():
            extra = f" auth={probed[n]['auth']} {probed[n].get('probe_seconds', '-')}s" if n in probed else ""
            off = " [desactivado]" if not spec.get("enabled", True) else ""
            cd = round(state.cooldown_remaining(n))
            mid = (probed.get(n) or {}).get("model_id") or ids.get(n)
            self.say(f"{n:<12} {'instalado' if adapters.is_available(n, spec) else 'NO instalado':<13} cooldown={cd}s{extra}{off}  modelo: {mid or 'desconocido'}")

    # --- inicio de la sesión ---
    def startup(self) -> None:
        """Lo primero que pasa al abrir: saber qué modelo usa cada CLI, ofrecer métricas al día y (una vez) las preguntas de prioridad.
        Todo lo que gasta algo (una consulta mínima, ~1 minuto de lectura web) se explica y se pregunta antes."""
        cfg = core.load_config(apply_scoring=False)
        todo = probe_mod.missing_ids(cfg)
        if todo:
            self.say(f"Para rutear con métricas necesito saber qué modelo usa cada CLI ({', '.join(todo)}).")
            self.say(self.dim("Hago una consulta mínima a cada uno (unos 12k tokens de entrada en codex y agy; pocos en claude)."))
            if self.ask_yes("¿La hago ahora?"):
                probe_mod.detect_ids(cfg, self.say)
            else:
                self.say("Mientras tanto rige la estimación de models.json. `/models probe` lo detecta cuando quieras.")
        data = metrics.active()
        today = time.strftime("%Y-%m-%d")
        if not data.get("pages"):
            self.say("No hay métricas incluidas ni descargadas. `/metrics refresh` las baja.")
        elif metrics.is_stale(data) and state.flags().get("asked_refresh") != today:
            days = int(metrics.age_days(data.get("arena_at")) or 0)
            self.say(f"Las métricas son del {(data.get('arena_at') or '?')[:10]} (hace {days} días, {data.get('arena_origin')}).")
            state.set_flag("asked_refresh", today)
            if self.ask_yes("¿Las actualizo? (lee páginas públicas de arena.ai, alrededor de un minuto)"):
                scoring.refresh_and_report(cfg, self.say)
        cfg = core.load_config()
        if not cfg.get("_scored"):
            return
        avail = cfg.get("_available", {})
        if not (avail.get("speed") or avail.get("cost")):
            if not state.flags().get("hinted_aa"):  # una sola vez: sin más datos las preguntas de prioridad no cambiarían nada
                state.set_flag("hinted_aa", True)
                self.say(self.dim("Consejo: con tu clave gratuita de Artificial Analysis (ver .env.example) se suman velocidad y costo, y podés priorizarlos por tipo de tarea."))
            return
        if not scoring.load_profile() and not state.flags().get("asked_priorities"):
            state.set_flag("asked_priorities", True)
            self.say("\nPuedo ajustar el ruteo a lo que priorizás en cada tipo de tarea (precisión, velocidad o costo): son 6 preguntas.")
            if self.ask_yes("¿Querés responderlas ahora? (después: /priorities)", default=False):
                priorities.run(cfg, say=self.say, color=self.color)

    # --- bucle ---
    def banner(self) -> str:
        cfg = core.load_config()
        names = list(cfg["models"])
        ok = {n: cfg["models"][n].get("enabled", True) and adapters.is_available(n, cfg["models"][n]) for n in names}
        return banner.render(__version__, metrics.status_line(), names, ok, attachments.safe_cwd(), color=self.color,
                             width=shutil.get_terminal_size((80, 24)).columns, pinned=self.pinned)

    def status_line(self) -> str:
        d = metrics.active()
        return f"métricas {(d.get('arena_at') or '?')[:10]}" if d.get("pages") else "sin métricas"

    def prompt(self) -> str:
        # \001..\002 marcan los códigos de color como no imprimibles para que readline calcule bien el cursor
        return f"\001\033[1;38;2;200;90;160m\002ia ❯\001\033[0m\002 " if self.color else "ia> "

    def loop(self) -> int:
        self.say(self.banner())
        try:
            self.startup()
        except KeyboardInterrupt:
            self.say("\n(inicio interrumpido)")
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
