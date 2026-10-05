#!/usr/bin/env python3
"""Router de suscripciones de IA. Sin argumentos abre el chat; también: setup | manifest | doctor | route | ask | stats | calibrate | scores | criteria | benchmarks | mcp | reset-cooldowns."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, Optional

from ia_router import adapters, calibrate, core, criteria, external, manifest, render, scoring, state


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def cmd_doctor(cfg, args) -> int:
    print(f"Config: {len(cfg['models'])} modelos | estado en: {state.home()} | manifiesto: {'sí' if cfg.get('_manifest') else 'no (corré: cli.py setup)'}\n")
    probed = manifest.probe(cfg) if args.probe else {}
    for name, spec in cfg["models"].items():
        installed = adapters.is_available(name, spec)
        cd = state.cooldown_remaining(name)
        extra = ""
        if name in probed:
            p = probed[name]
            extra = f"  auth={p['auth']} {p.get('probe_seconds', '-')}s {p.get('version', '')}"
        flags = ("" if spec.get("enabled", True) else " [desactivado en manifiesto]")
        print(f"{name:<8} {'instalado' if installed else 'NO instalado':<13} cooldown={round(cd)}s{extra}{flags}")
        if not installed:
            print(f"         → instalá el CLI oficial o ajustá 'cmd' en models.json (o ROUTER_CMD_{name.upper()})")
        elif name in probed and probed[name]["auth"] == "missing":
            print(f"         → instalado pero SIN autenticar: iniciá sesión o configurá la API key de {name}")
        elif name in probed and probed[name]["auth"] == "unknown":
            print(f"         → la sonda falló: {probed[name].get('probe_error')}")
    if not args.probe:
        print("\nPara verificar login y latencia reales (gasta 1 llamada corta por modelo): cli.py doctor --probe")
    return 0


def cmd_route(cfg, args) -> int:
    _, ctx_len, warns = core.build_prompt(args.task, args.context)
    print(core.format_ranking(core.route(args.task, cfg, ctx_len, use_llm=args.llm)))
    for w in warns:
        print("aviso:", w, file=sys.stderr)
    return 0


def cmd_ask(cfg, args) -> int:
    try:
        res = core.ask(args.task, cfg, model=args.model, context_files=args.context, dry_run=args.dry_run, use_llm=args.llm)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print(core.format_ranking(res["decision"]), file=sys.stderr)
    if res.get("dry_run"):
        print(f"(dry-run) orden de intento: {res['order']}", file=sys.stderr)
        return 0
    for a in res["attempts"]:
        print(f"intento {a['model']}: {'OK' if not a['error'] else a['error'][:80]} ({a['seconds']}s)", file=sys.stderr)
    if res["ok"]:
        print(f"\n[{core.format_usage(res)}]\n{render.render(res['output'], color=_color())}")
        return 0
    print(f"Falló: {res.get('error')}", file=sys.stderr)
    return 1


def _pick_manager(cfg, wanted: Optional[str]) -> str:
    if wanted:
        if wanted not in cfg["models"]:
            raise SystemExit(f"manager desconocido: {wanted} (opciones: {', '.join(cfg['models'])})")
        return wanted
    current = core.manager_name(cfg)
    if not _interactive():
        return current
    installed = [n for n, s in cfg["models"].items() if adapters.is_available(n, s)] or list(cfg["models"])
    print("El manager es el modelo barato que clasifica tus tareas y arma/actualiza el manifiesto.")
    ans = _input(f"¿Cuál usar? [{'/'.join(installed)}] (enter = {current}): ")
    return ans if ans in cfg["models"] else current


def _generate_and_save(cfg, manager_model: str, notes: str, probed: Optional[Dict], reason: str) -> int:
    print(f"Armando el manifiesto con el manager '{manager_model}'...", file=sys.stderr)
    run = core.manager_runner(cfg, manager_model)
    m = manifest.generate(cfg, run, manager_model, notes, probed)
    manifest.save(m, reason)
    print("\n" + manifest.render(m))
    print(f"\nGuardado en {manifest.path()}")
    print("Para ajustarlo: cli.py manifest refine \"usá Codex para todo lo de código\"   (o sin texto para modo interactivo)")
    return 0


def cmd_setup(_cfg, args) -> int:
    cfg = core.load_config(apply_manifest=False)
    manager_model = _pick_manager(cfg, args.manager)
    probed = None
    if not args.no_probe:
        print("Probando cada CLI con un prompt mínimo...", file=sys.stderr)
        probed = manifest.probe(cfg)
        for n, p in probed.items():
            print(f"  {n:<8} instalado={p['installed']} auth={p['auth']} {p.get('probe_seconds', '-')}s {p.get('version', '')}")
    notes = args.notes
    if notes is None and _interactive():
        notes = _input("\nContame tus preferencias (ej: 'Codex para código, Claude para escribir'; enter para omitir): ")
    return _generate_and_save(cfg, manager_model, notes or "", probed, "setup")


def cmd_manifest(_cfg, args) -> int:
    cfg = core.load_config(apply_manifest=False)
    current = manifest.load()
    if args.action == "path":
        print(manifest.path())
        return 0
    if args.action == "generate":
        keep = current or {}
        return _generate_and_save(cfg, args.manager or core.manager_name(cfg), args.notes if args.notes is not None else keep.get("user_notes", ""),
                                  keep.get("models"), "regenerado")
    if current is None:
        print("No hay manifiesto todavía. Corré: cli.py setup", file=sys.stderr)
        return 1
    if args.action == "show":
        print(manifest.render(current))
        return 0
    # refine
    run = core.manager_runner(cfg, current.get("manager"))
    feedbacks = [args.feedback] if args.feedback else []
    if not feedbacks and not _interactive():
        print("Pasá el feedback como argumento o usá una terminal interactiva.", file=sys.stderr)
        return 1
    if not feedbacks:
        print(manifest.render(current) + "\n\nDecime qué cambiar, en lenguaje natural. Enter vacío para terminar.")
    while True:
        text = feedbacks.pop(0) if feedbacks else (_input("feedback> ") if _interactive() else "")
        if not text:
            return 0
        new = manifest.refine(cfg, run, current, text)
        if new is None:
            print("El manager no devolvió un manifiesto válido; no se cambió nada. Probá reformular.", file=sys.stderr)
            if args.feedback:
                return 1
            continue
        print("Cambios propuestos:\n" + manifest.diff(current, new))
        if args.feedback or _input("¿Aplicar? [S/n]: ").lower() in ("", "s", "si", "sí", "y", "yes"):
            manifest.save(new, f"feedback: {text}")
            current = new
            print("Aplicado." if not args.feedback else f"Aplicado (versión anterior en {manifest.path().with_name('manifest.prev.json')}).")
        if args.feedback:
            return 0


def cmd_stats(cfg, _args) -> int:
    st = state.stats()
    if not st:
        print("Sin historial todavía (se arma con cada `ask`).")
        return 0
    print(f"{'modelo':<12} {'corridas':>8} {'éxito':>6} {'seg. medio':>10} {'rate limits':>11} {'auth':>5} {'tokens in':>10} {'tokens out':>10}")
    for n, m in st.items():
        print(f"{n:<12} {m['runs']:>8} {m['ok_rate']:>6.0%} {m['avg_seconds']:>10} {m['rate_limits']:>11} {m['auth_errors']:>5} {m['tokens_in']:>10} {m['tokens_out']:>10}")
    return 0


def cmd_calibrate(cfg, args) -> int:
    def confirm(q: str) -> bool:
        if args.yes:
            return True
        if not _interactive():
            print("Sin terminal interactiva: agregá --yes para confirmar el gasto.", file=sys.stderr)
            return False
        return _input(f"{q} [S/n]: ").lower() in ("", "s", "si", "sí", "y", "yes")
    rounds = args.rounds or (3 if args.full else 1)
    wanted = [m.strip() for m in args.models.split(",")] if args.models else None
    res = calibrate.run_with_confirmation(cfg, rounds, wanted, args.seed, confirm, lambda s: print(s, flush=True))
    if res is None:
        return 1
    print("\n" + scoring.render_table(core.load_config(apply_manifest=False)))
    return 0


def cmd_scores(_cfg, args) -> int:
    cfg = core.load_config(apply_manifest=False)
    print(scoring.render_table(cfg, [args.category] if args.category else None))
    if args.category:
        print("\n" + scoring.explain(cfg, args.category))
    if manifest.load():
        print("\nNota: hay un manifiesto y manda sobre estos puntajes. `criteria` puede rearmarlo desde ellos.")
    return 0


def cmd_benchmarks(_cfg, args) -> int:
    cfg = core.load_config(apply_manifest=False)
    if args.action == "refresh":
        try:
            external.refresh(cfg, say=lambda s: print(s, flush=True), force=args.force)
        except (OSError, ValueError) as exc:
            print(f"No pude actualizar las métricas externas: {exc}", file=sys.stderr)
            return 1
    print(external.render(cfg, external.load()))
    return 0


def cmd_criteria(_cfg, _args) -> int:
    return 0 if criteria.run(core.load_config(apply_manifest=False), color=_color()) else 1


def main() -> int:
    p = argparse.ArgumentParser(prog="router", description=__doc__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("chat", help="modo conversacional (es lo que se abre sin argumentos)")
    dp = sub.add_parser("doctor", help="verifica qué CLIs están instalados")
    dp.add_argument("--probe", action="store_true", help="llamada mínima real a cada CLI: login, latencia, versión")
    for name in ("route", "ask"):
        sp = sub.add_parser(name, help="rutear" if name == "route" else "rutear y ejecutar")
        sp.add_argument("task")
        sp.add_argument("--context", "-c", action="append", default=[], help="archivo de contexto (repetible)")
        sp.add_argument("--llm", action="store_true", help="clasificar con el manager (modelo barato) en vez de solo reglas")
        if name == "ask":
            sp.add_argument("--model", "-m", default="auto", help="auto|claude|codex|antigravity")
            sp.add_argument("--dry-run", action="store_true", help="solo mostrar la decisión")
    sp = sub.add_parser("setup", help="primer uso: elegir manager, probar CLIs y armar el manifiesto")
    sp.add_argument("--manager", help="modelo barato que arma el manifiesto y clasifica (claude|codex|antigravity)")
    sp.add_argument("--notes", help="tus preferencias en lenguaje natural")
    sp.add_argument("--no-probe", action="store_true", help="no hacer llamadas de prueba a los CLIs")
    sp = sub.add_parser("manifest", help="ver, regenerar o ajustar el manifiesto de preferencias")
    sp.add_argument("action", choices=["show", "generate", "refine", "path"])
    sp.add_argument("feedback", nargs="?", help="(refine) qué cambiar; sin texto = modo interactivo")
    sp.add_argument("--manager")
    sp.add_argument("--notes")
    sub.add_parser("stats", help="éxito, latencia y rate limits por modelo (del log)")
    sp = sub.add_parser("calibrate", help="mide el acierto de cada modelo con tareas verificables y guarda las métricas")
    sp.add_argument("--full", action="store_true", help="3 rondas con preguntas distintas (más confiable, ~3× el costo)")
    sp.add_argument("--rounds", type=int, help="cantidad de rondas (por defecto 1; --full = 3)")
    sp.add_argument("--models", help="solo estos modelos, separados por coma")
    sp.add_argument("--seed", type=int, help="semilla de las preguntas (por defecto cambia cada día)")
    sp.add_argument("--yes", "-y", action="store_true", help="no pedir confirmación")
    sp = sub.add_parser("scores", help="puntaje objetivo por modelo y categoría (con desglose si indicás una)")
    sp.add_argument("category", nargs="?", help="ej: coding")
    sp = sub.add_parser("benchmarks", help="métricas externas (Arena y, con clave gratuita, Artificial Analysis) usadas como prior de calidad")
    sp.add_argument("action", nargs="?", choices=["show", "refresh"], default="show")
    sp.add_argument("--force", action="store_true", help="(refresh) consultar aunque los datos tengan menos de 12 horas")
    sub.add_parser("criteria", help="cuestionario de opción múltiple para ajustar tus criterios de ruteo")
    sub.add_parser("mcp", help="correr el servidor MCP (stdio)")
    sub.add_parser("reset-cooldowns", help="limpiar cooldowns (rate limit y auth)")
    args = p.parse_args()

    if args.cmd in (None, "chat"):
        from ia_router import chat
        return chat.run()
    if args.cmd == "mcp":
        from ia_router import mcp_server
        mcp_server.main()
        return 0
    if args.cmd == "reset-cooldowns":
        state.reset_cooldowns()
        print("cooldowns limpiados")
        return 0
    cfg = core.load_config()
    return {"doctor": cmd_doctor, "route": cmd_route, "ask": cmd_ask, "setup": cmd_setup,
            "manifest": cmd_manifest, "stats": cmd_stats, "calibrate": cmd_calibrate, "scores": cmd_scores, "criteria": cmd_criteria, "benchmarks": cmd_benchmarks}[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
