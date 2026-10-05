"""Cuestionario de criterios de ruteo: preguntas de opción múltiple (selector ↑/↓ + Enter) que ajustan los pesos con los
que se combinan las métricas objetivas. No inventa números: cada respuesta se traduce a pesos explícitos que se muestran.

El perfil se guarda en <home>/profile.json. Si hay manifiesto, al guardar se puede rearmar su orden de preferencias desde
los puntajes (el manifiesto anterior queda en manifest.prev.json).
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from . import manifest, scoring
from .select import Option, choose as tty_choose

PRIORITIES = [  # (etiqueta, descripción, pesos)
    ("Calidad primero", "lo que más acierta, aunque tarde un poco", {"quality": 0.70, "speed": 0.15, "quota": 0.05, "reliability": 0.10}),
    ("Equilibrado", "calidad y velocidad parejas, cuidando algo la cuota", {"quality": 0.45, "speed": 0.25, "quota": 0.15, "reliability": 0.15}),
    ("Velocidad primero", "el que responde más rápido entre los que aciertan", {"quality": 0.30, "speed": 0.45, "quota": 0.10, "reliability": 0.15}),
    ("Ahorro de cuota", "el que gasta menos tokens por tarea", {"quality": 0.35, "speed": 0.15, "quota": 0.40, "reliability": 0.10}),
]
CODING = [
    ("Máxima calidad", "el que más acierta en código, sin importar el tiempo", {"quality": 0.85, "speed": 0.05, "quota": 0.02, "reliability": 0.08}),
    ("Igual que el resto", "los mismos pesos generales", None),
    ("Rapidez razonable", "buen acierto pero sin esperar", {"quality": 0.50, "speed": 0.35, "quota": 0.05, "reliability": 0.10}),
]
QUICK = [
    ("El más rápido", "tareas cortas al que contesta antes", {"quality": 0.40, "speed": 0.40, "quota": 0.10, "reliability": 0.10}),
    ("El de mejor calidad", "aunque sea una pregunta corta", {"quality": 0.70, "speed": 0.15, "quota": 0.05, "reliability": 0.10}),
]
TIEBREAKS = [("Mejor calidad", "quality"), ("Más rápido", "speed"), ("Gasta menos cuota", "quota")]


def build_profile(answers: Dict[str, int], models: List[str]) -> Dict:
    """Respuestas (índices) -> perfil con pesos explícitos."""
    base = PRIORITIES[answers["priority"]]
    profile: Dict = {"weights": dict(base[2]), "category_weights": {}, "answers": dict(answers)}
    cw = CODING[answers["coding"]][2]
    if cw:
        profile["category_weights"]["coding"] = profile["category_weights"]["debugging"] = dict(cw)
    profile["category_weights"]["quick"] = dict(QUICK[answers["quick"]][2])
    spare = answers["spare"]  # 0 = ninguna; i>0 = models[i-1]
    if spare > 0:
        profile["spare"] = {models[spare - 1]: 1.0}
    profile["tiebreak"] = TIEBREAKS[answers["tiebreak"]][1]
    return profile


def describe(profile: Dict) -> str:
    w = profile["weights"]
    lines = ["Pesos generales: " + ", ".join(f"{k} {v:.0%}" for k, v in w.items())]
    for cat, cw in (profile.get("category_weights") or {}).items():
        lines.append(f"  {cat}: " + ", ".join(f"{k} {v:.0%}" for k, v in cw.items()))
    if profile.get("spare"):
        lines.append("Cuota a cuidar: " + ", ".join(profile["spare"]))
    lines.append(f"Desempate (diferencia < {scoring.TIEBREAK_GAP} pts): {profile.get('tiebreak')}")
    return "\n".join(lines)


def run(cfg: Dict, choose: Callable = tty_choose, say: Callable[[str], None] = print, color: bool = True) -> Optional[Dict]:
    """Hace las preguntas. Devuelve el perfil guardado o None si se canceló. `choose` es inyectable para tests."""
    models = list(cfg["models"])
    prev = (scoring.load_profile().get("answers") or {})
    total = 6

    def ask(step: int, title: str, options: List[Option], key: str, subtitle: str = "") -> Optional[int]:
        return choose(title, options, default=min(prev.get(key, 0), len(options) - 1), subtitle=subtitle, step=f"{step}/{total}", color=color)

    steps = [
        ("priority", "¿Qué priorizás al elegir un modelo?", [Option(l, d) for l, d, _ in PRIORITIES], "Define los pesos generales con que se combinan las métricas medidas."),
        ("coding", "Para código y debugging…", [Option(l, d) for l, d, _ in CODING], ""),
        ("quick", "Para tareas cortas…", [Option(l, d) for l, d, _ in QUICK], ""),
        ("spare", "¿Querés cuidar la cuota de alguna suscripción?", [Option("Ninguna", "se usan todas por igual")] + [Option(m, "resta 1 punto a su puntaje") for m in models],
         "Útil si una de tus suscripciones se agota más rápido."),
        ("tiebreak", "Si dos modelos quedan parejos…", [Option(l, "") for l, _ in TIEBREAKS], f"Parejos = menos de {scoring.TIEBREAK_GAP} puntos de diferencia."),
    ]
    answers: Dict[str, int] = {}
    for i, (key, title, opts, sub) in enumerate(steps, 1):
        a = ask(i, title, opts, key, sub)
        if a is None:
            say("Cancelado: no cambié nada.")
            return None
        answers[key] = a
    profile = build_profile(answers, models)
    say("\nCriterios elegidos:\n" + describe(profile))

    has_manifest = manifest.load() is not None
    opts = [Option("Guardar", "los criterios pasan a regir el puntaje objetivo")]
    if has_manifest:
        opts.append(Option("Guardar y rearmar el manifiesto", "su orden de preferencias se recalcula desde los puntajes (la versión anterior queda en manifest.prev.json)"))
    opts.append(Option("Descartar", "no cambiar nada"))
    final = choose("¿Aplico estos criterios?", opts, default=0, step=f"{total}/{total}", color=color)
    if final is None or opts[final].label == "Descartar":
        say("Descartado: no cambié nada.")
        return None
    scoring.save_profile(profile)
    if has_manifest and opts[final].label.startswith("Guardar y rearmar"):
        rebuild_manifest(cfg_with_scores(), say)
    say("Guardado." + ("" if has_manifest else " Se aplican sobre el puntaje objetivo (/scores)."))
    return profile


def cfg_with_scores() -> Dict:
    from . import core
    return core.load_config(apply_manifest=False)


def rebuild_manifest(cfg: Dict, say: Callable[[str], None] = print) -> None:
    """Rearma `tasks` del manifiesto desde los puntajes; conserva manager, notas y modelos desactivados."""
    cur = manifest.load()
    if not cur:
        return
    ordered = {}
    for cat in manifest.CATEGORIES:
        names = sorted(cfg["models"], key=lambda n: -cfg["models"][n].get("strengths", {}).get(cat, 0))
        top = names[0]
        d = (cfg["models"][top].get("_scoring") or {}).get(cat)
        why = f"criterios: {top} {d['score']:.1f} (calidad {d['quality_src']})" if d else "criterios del usuario"
        ordered[cat] = {"prefer": names, "why": why}
    cur["tasks"] = ordered
    cur["source"] = "criterios (puntaje objetivo)"
    manifest.save(cur, "rearmado desde los criterios de ruteo")
    say("Manifiesto rearmado desde los puntajes.")
