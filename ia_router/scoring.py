"""Puntaje de cada modelo por tipo de tarea, calculado con métricas objetivas y tus prioridades.

    puntaje(modelo, categoría) = Σ peso × valor      (valores de 0 a 10, relativos a TUS modelos)

  precisión   Elo de Arena en la categoría (preferencia humana, con margen de error) y, si hay clave, los benchmarks con respuesta
              correcta de Artificial Analysis. Si no hay dato, la estimación de `models.json` (marcada como estimada).
  velocidad   tokens/s publicados por Artificial Analysis. Solo existe con su clave.
  costo       precio de lista por millón de tokens (Arena o Artificial Analysis). Proxy del consumo de cuota.
              Velocidad y costo van en escala logarítmica: 2 puntos menos por cada duplicación frente al mejor de tus modelos.

Una dimensión sin datos para TODOS tus modelos no pesa (los pesos se reparten entre las que sí tienen). Los pesos por tipo de
tarea salen de tus respuestas en `priorities`; sin respuestas hay unos por defecto. Este puntaje alimenta al router (`strengths`).
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Dict, List, Optional, Tuple

from . import metrics, router, state

DIMS = ("precision", "speed", "cost")
LABELS = {"precision": "precisión", "speed": "velocidad", "cost": "costo"}
DEFAULT_WEIGHTS = {"precision": 0.70, "speed": 0.15, "cost": 0.15}
QUICK_WEIGHTS = {"precision": 0.40, "speed": 0.50, "cost": 0.10}
PRESETS: Dict[str, Dict[str, float]] = {
    "precision": {"precision": 0.80, "speed": 0.10, "cost": 0.10},
    "balanced": {"precision": 0.50, "speed": 0.25, "cost": 0.25},
    "speed": {"precision": 0.40, "speed": 0.50, "cost": 0.10},
    "cost": {"precision": 0.40, "speed": 0.10, "cost": 0.50},
}
PRESET_LABELS = {"precision": "Precisión", "balanced": "Equilibrado", "speed": "Velocidad", "cost": "Costo"}
# grupos de tareas sobre los que se pregunta: (clave, título, categorías del router)
GROUPS: List[Tuple[str, str, List[str]]] = [
    ("coding", "Código y debugging", ["coding", "debugging"]),
    ("writing", "Escritura y redacción", ["writing"]),
    ("analysis", "Análisis, datos e investigación", ["analysis", "data", "research"]),
    ("math", "Matemática", ["math"]),
    ("quick", "Tareas rápidas", ["quick"]),
]
CATEGORIES: List[str] = list(router.PATTERNS) + ["quick", "general"]


# ---------- perfil: tus prioridades ----------

def profile_path():
    return state.home() / "profile.json"


def load_profile() -> Dict:
    try:
        d = json.loads(profile_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_profile(profile: Dict) -> None:
    state.home().mkdir(parents=True, exist_ok=True)
    profile["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = profile_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, profile_path())


def group_of(cat: str) -> Optional[str]:
    return next((k for k, _, cats in GROUPS if cat in cats), None)


def weights_for(profile: Dict, cat: str) -> Dict[str, float]:
    choice = (profile.get("priorities") or {}).get(group_of(cat) or "")
    if choice in PRESETS:
        return PRESETS[choice]
    return QUICK_WEIGHTS if cat == "quick" else DEFAULT_WEIGHTS


# ---------- cálculo ----------

def enabled_models(cfg: Dict) -> List[str]:
    return [n for n, s in cfg["models"].items() if s.get("enabled", True)]


def build(cfg: Dict, data: Optional[Dict] = None, profile: Optional[Dict] = None, ids: Optional[Dict[str, Optional[str]]] = None) -> Dict:
    """{"table": {modelo: {categoría: desglose}}, "available": {dim: bool}, "arena": …, "aa": …, "missing_ids": [modelos]}.
    El desglose: score, values {dim: 0-10 | None}, srcs {dim: texto}, weights {dim: peso usado}, basis ('métricas' | 'estimado')."""
    data = data if data is not None else metrics.active()
    profile = profile if profile is not None else load_profile()
    names = enabled_models(cfg)
    ids = ids if ids is not None else metrics.known_model_ids(names)
    overrides = {n: cfg["models"][n].get("external") or {} for n in names}
    arena, aa = metrics.matches(names, ids, overrides, data)
    prec = metrics.precision_values(names, arena, aa)
    speed, cost = metrics.speed_values(names, aa), metrics.cost_values(names, arena, aa)
    table: Dict[str, Dict[str, Dict]] = {n: {} for n in names}
    for n in names:
        est = cfg["models"][n].get("_prior_strengths") or cfg["models"][n].get("strengths", {})
        for cat in CATEGORIES:
            p = prec.get(cat, {}).get(n)
            vals: Dict[str, Optional[float]] = {"precision": p[0] if p else float(est.get(cat, est.get("general", 5))),
                                                "speed": speed[n][0] if n in speed else None, "cost": cost[n][0] if n in cost else None}
            srcs = {"precision": p[1] if p else "estimada a mano (models.json)"}
            if n in speed:
                srcs["speed"] = speed[n][1]
            if n in cost:
                srcs["cost"] = cost[n][1]
            w = {d: x for d, x in weights_for(profile, cat).items() if vals[d] is not None}
            total = sum(w.values()) or 1.0
            w = {d: x / total for d, x in w.items()}
            table[n][cat] = {"score": round(sum(w[d] * vals[d] for d in w), 2), "values": {d: (round(v, 2) if v is not None else None) for d, v in vals.items()},
                             "srcs": srcs, "weights": {d: round(x, 2) for d, x in w.items()}, "basis": "métricas" if p else "estimado"}
    return {"table": table, "available": {"speed": bool(speed), "cost": bool(cost)}, "arena": arena, "aa": aa,
            "missing_ids": [n for n in names if not ids.get(n)]}


def apply_to_config(cfg: Dict) -> Dict:
    """Reemplaza `strengths` por el puntaje (si hay métricas que cubran a tus modelos). Guarda el desglose en spec['_scoring']."""
    if len(enabled_models(cfg)) < 2:
        return cfg
    b = build(cfg)
    if not any(d["basis"] == "métricas" for n in b["table"] for d in b["table"][n].values()):
        return cfg  # sin ids de modelos o sin cobertura: rige la estimación de models.json
    for n, spec in cfg["models"].items():
        if n not in b["table"]:
            continue
        spec["_prior_strengths"] = dict(spec.get("strengths", {}))
        spec["_scoring"] = b["table"][n]
        spec["strengths"] = {cat: d["score"] for cat, d in b["table"][n].items()}
    cfg["_scored"], cfg["_available"] = True, b["available"]
    return cfg


# ---------- presentación ----------

def render_table(cfg: Dict, cats: Optional[List[str]] = None) -> str:
    if not cfg.get("_scored"):
        return ("Todavía no hay métricas que cubran a tus modelos: rige la estimación de models.json.\n"
                "Necesito saber qué modelo usa cada CLI: corré `doctor --probe` (o dejá que lo detecte al iniciar el chat).")
    names = enabled_models(cfg)
    table = {n: cfg["models"][n]["_scoring"] for n in names}
    cats = cats or [c for c in CATEGORIES if c != "general"]
    head = f"{'categoría':<14}" + "".join(f"{n:>14}" for n in names)
    lines = [head, "─" * len(head)]
    for cat in cats:
        best = max(table[n][cat]["score"] for n in names)
        row = f"{cat:<14}"
        for n in names:
            d = table[n][cat]
            row += f"{d['score']:>12.1f}{'*' if d['score'] == best else ' '}{'e' if d['basis'] == 'estimado' else ' '} "
        lines.append(row)
    lines += ["", "* = el que elige el router · e = precisión estimada a mano (Arena no cubre esa categoría para todos tus modelos)"]
    a = cfg.get("_available", {})
    lines.append("Dimensiones con datos: precisión" + (", velocidad" if a.get("speed") else "") + (", costo" if a.get("cost") else "")
                 + ("" if a.get("speed") else "  ·  velocidad: falta tu clave de Artificial Analysis (.env)"))
    d = metrics.active()
    lines.append("Datos: " + metrics.status_line(d) + "  ·  Atribución: " + " · ".join(metrics.ATTRIBUTION[k] for k in (("arena", "aa") if d.get("aa") else ("arena",))))
    return "\n".join(lines)


def explain(cfg: Dict, cat: str) -> str:
    names = enabled_models(cfg)
    if not cfg.get("_scored") or cat not in cfg["models"][names[0]].get("_scoring", {}):
        return f"Sin desglose para '{cat}'."
    lines = [f"Puntaje de «{cat}» = Σ peso × valor (0-10)"]
    for n in names:
        d = cfg["models"][n]["_scoring"][cat]
        parts = [f"{LABELS[k]} {d['values'][k]:.1f}×{w:.2f}" for k, w in d["weights"].items()]
        lines.append(f"{n:<13}= {d['score']:>5.2f}   " + "  ".join(parts))
        lines.append(f"{'':<13}  " + " · ".join(f"{LABELS[k]}: {v}" for k, v in d["srcs"].items()))
    return "\n".join(lines)


def describe_sources(cfg: Dict) -> str:
    """Qué entrada de cada portal se emparejó con cada uno de tus modelos, y de dónde salen los datos."""
    names = enabled_models(cfg)
    b = build(cfg)
    d = metrics.active()
    lines = [f"Métricas: {metrics.status_line(d)}  ·  Arena {d.get('arena_origin', '')}", ""]
    for n in names:
        mid = metrics.known_model_ids([n])[n]
        a, x = b["arena"].get(n), b["aa"].get(n)
        lines.append(f"{n:<12} modelo del CLI: {mid or 'desconocido (corré /models probe)'}")
        if a:
            warn = "" if a["exact"] else "  ⚠ Arena no publica el mismo nivel de esfuerzo que usa tu CLI: dato aproximado (" + ", ".join(
                f"{sub}: {v['effort'] or 'sin nivel'}" for sub, v in a["variants"].items() if not v["exact"]) + ")"
            lines.append(f"{'':<12} Arena → {a['name']}{warn}")
        elif mid:
            lines.append(f"{'':<12} Arena → sin coincidencia para ese modelo")
        if x:
            warn = "" if x["exact"] else "  ⚠ aproximado: tu CLI no informa su nivel de esfuerzo"
            lines.append(f"{'':<12} Artificial Analysis → {x['name']}" + (f" · {x['tps']:.0f} tok/s" if x.get("tps") else "") + warn)
    if not d.get("aa"):
        lines += ["", "Artificial Analysis no está activo: sin su clave no hay velocidad ni, en general, costo. Ver .env.example."]
    lines.append("Fuentes: " + " · ".join(metrics.ATTRIBUTION[k] for k in (("arena", "aa") if d.get("aa") else ("arena",))))
    return "\n".join(lines)


def diff_tables(old: Optional[Dict], new: Dict, threshold: float = 0.3) -> List[str]:
    """Qué cambió entre dos cálculos: el modelo elegido por categoría y los puntajes que se movieron al menos `threshold`."""
    if not old:
        return []
    out = []
    for cat in CATEGORIES:
        if cat == "general":
            continue
        o = {n: old[n][cat]["score"] for n in old if cat in old[n]}
        w = {n: new[n][cat]["score"] for n in new if cat in new[n]}
        if not o or not w:
            continue
        bo, bw = max(o, key=o.get), max(w, key=w.get)
        moved = [f"{n} {o[n]:.1f}→{w[n]:.1f}" for n in w if n in o and abs(w[n] - o[n]) >= threshold]
        if bo != bw:
            out.append(f"{cat}: ahora elige {bw} (antes {bo})" + (f"  [{', '.join(moved)}]" if moved else ""))
        elif moved:
            out.append(f"{cat}: {', '.join(moved)}")
    return out


def refresh_and_report(cfg: Dict, say: Callable[[str], None] = print, force: bool = False, get=metrics.http_get, sleep=time.sleep) -> bool:
    """Actualiza las métricas mostrando cada paso y, al final, qué cambió en el ruteo. True si se actualizó."""
    first = build(cfg)
    before, ids_known = first["table"], not first["missing_ids"]
    try:
        metrics.refresh(cfg, say=say, get=get, force=force, sleep=sleep)
    except (OSError, ValueError) as exc:
        say(f"No pude actualizar las métricas: {exc}. Sigo con las que tenías ({metrics.status_line()}).")
        return False
    after = build(cfg)["table"]
    changes = diff_tables(before, after) if ids_known else []
    say("Métricas al día: " + metrics.status_line())
    if changes:
        say("Qué cambió en el ruteo:\n" + "\n".join("  · " + c for c in changes))
    elif ids_known:
        say("El ruteo no cambia con estos datos.")
    return True
