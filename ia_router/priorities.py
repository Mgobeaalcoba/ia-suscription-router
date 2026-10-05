"""Preguntas de opción múltiple (selector ↑/↓ + Enter) sobre qué priorizás en cada tipo de tarea: precisión, velocidad o costo.

Con tus respuestas se rearma el ruteo: cada tipo de tarea usa los pesos de la opción elegida sobre las métricas objetivas
(ver scoring.PRESETS). No se inventan números: el resultado se muestra antes de guardar. Sin responder, rigen los pesos por defecto.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from . import scoring
from .select import Option, choose as tty_choose

OPTION_TEXT = {
    "precision": "la mejor respuesta, aunque tarde o cueste más",
    "balanced": "un poco de todo: precisión, velocidad y costo",
    "speed": "el que responde más rápido entre los buenos (tokens/s publicados)",
    "cost": "el que menos cuota consume (precio por millón de tokens)",
}


def options_for(available: Dict[str, bool]) -> List[str]:
    """Opciones ofrecidas: velocidad y costo solo si hay dato objetivo para todos tus modelos."""
    return ["precision", "balanced"] + (["speed"] if available.get("speed") else []) + (["cost"] if available.get("cost") else [])


def preview(table: Dict[str, Dict[str, Dict]]) -> List[str]:
    """Qué modelo elegiría el router por categoría con ese cálculo."""
    names = list(table)
    out = []
    for key, title, cats in scoring.GROUPS:
        picks = []
        for c in cats:
            best = max(names, key=lambda n: table[n][c]["score"])
            picks.append(f"{c} → {best}")
        out.append(f"  {title}: " + ", ".join(picks))
    return out


def run(cfg: Dict, choose: Callable = tty_choose, say: Callable[[str], None] = print, color: bool = True) -> Optional[Dict]:
    """Hace las preguntas. Devuelve el perfil guardado o None si se canceló o descartó. `choose` es inyectable para tests."""
    built = scoring.build(cfg)
    avail = built["available"]
    keys = options_for(avail)
    if not avail.get("speed") and not avail.get("cost"):
        say("Con las métricas actuales solo hay datos de precisión para tus modelos: no hay nada que priorizar entre precisión, velocidad y costo.\n"
            "Sumá tu clave gratuita de Artificial Analysis (ver .env.example) y actualizá con `metrics refresh`: aporta velocidad y precio.")
        return None
    prev = scoring.load_profile().get("priorities") or {}
    notes = [f"{scoring.LABELS[d]} no está disponible: " + ("falta tu clave de Artificial Analysis (ver .env.example)" if d == "speed" else "ningún portal publica el precio de todos tus modelos")
             for d in ("speed", "cost") if not avail.get(d)]
    answers: Dict[str, str] = {}
    total = len(scoring.GROUPS) + 1
    for i, (key, title, _) in enumerate(scoring.GROUPS, 1):
        a = choose(f"Para {title.lower()}, ¿qué priorizás?", [Option(scoring.PRESET_LABELS[k], OPTION_TEXT[k]) for k in keys],
                   default=keys.index(prev[key]) if prev.get(key) in keys else 0, subtitle="  ·  ".join(notes) if i == 1 else "",
                   step=f"{i}/{total}", color=color)
        if a is None:
            say("Cancelado: no cambié nada.")
            return None
        answers[key] = keys[a]
    profile = {"priorities": answers}
    say("\nAsí queda el ruteo con tus prioridades:\n" + "\n".join(preview(scoring.build(cfg, profile=profile)["table"])))
    final = choose("¿Aplico estas prioridades?", [Option("Guardar", "el router las usa desde ya"), Option("Descartar", "no cambiar nada")], default=0, step=f"{total}/{total}", color=color)
    if final != 0:
        say("Descartado: no cambié nada.")
        return None
    scoring.save_profile(profile)
    say("Guardado. Se aplican ya (`scores` muestra el detalle).")
    return profile
