"""Puntaje objetivo de cada modelo por categoría, con el perfil de criterios del usuario.

    puntaje(modelo, categoría) = Σ peso_d × valor_d     con d en {calidad, velocidad, cuota, confiabilidad}

  calidad       acierto medido por `calibrate` en esa categoría (0-10), mezclado con la estimación inicial de models.json:
                (aciertos + K × estimado) / (preguntas + K), con K = 2: la estimación vale como 2 preguntas más, y con pocas muestras pesa más
  velocidad     10 × (más rápido / este modelo), con los segundos medios de las llamadas de calibración
  cuota         10 × (el que menos tokens gasta / este modelo), con tokens por corrida reales (log)
  confiabilidad 10 × tasa de éxito real (log) o, si no hay, la de las llamadas de calibración

Si falta un dato objetivo (p. ej. nunca calibraste) esa dimensión vale lo mismo que la calidad de la categoría, de modo que
sin datos el puntaje es exactamente la estimación inicial: nada se mueve sin evidencia.
Los pesos salen del perfil (`criteria`) y la precedencia final es: manifiesto (preferencias explícitas) > perfil > medido > estimado.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

from . import calibrate, state

PRIOR_K = 2
DIMS = ("quality", "speed", "quota", "reliability")
DEFAULT_WEIGHTS = {"quality": 0.70, "speed": 0.15, "quota": 0.05, "reliability": 0.10}
QUICK_WEIGHTS = {"quality": 0.40, "speed": 0.40, "quota": 0.10, "reliability": 0.10}
MIN_RUNS = 3  # corridas reales mínimas para confiar en el log
TIEBREAK_GAP = 0.5


# ---------- perfil ----------

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


def weights_for(profile: Dict, cat: str) -> Dict[str, float]:
    w = (profile.get("category_weights") or {}).get(cat) or (QUICK_WEIGHTS if cat == "quick" and "weights" not in profile else None) \
        or profile.get("weights") or DEFAULT_WEIGHTS
    total = sum(max(0.0, w.get(d, 0.0)) for d in DIMS) or 1.0
    return {d: max(0.0, w.get(d, 0.0)) / total for d in DIMS}


# ---------- métricas por modelo ----------

def _calibration_speed(entry: Dict) -> Optional[float]:
    secs = [c["seconds"] for c in entry.get("calls", []) if c.get("ok") and c["key"] != "ping" and c.get("seconds")]
    return sum(secs) / len(secs) if secs else None


def _calibration_reliability(entry: Dict) -> Optional[float]:
    calls = entry.get("calls", [])
    return sum(1 for c in calls if c.get("ok")) / len(calls) if len(calls) >= MIN_RUNS else None


def raw_metrics(names: List[str], metrics: Dict, stats: Dict) -> Dict[str, Dict]:
    """Valores objetivos sin normalizar por modelo: segundos, tokens por corrida, tasa de éxito."""
    out = {}
    for n in names:
        e = (metrics.get("models") or {}).get(n, {})
        s = stats.get(n, {})
        runs = s.get("runs", 0)
        out[n] = {
            "seconds": _calibration_speed(e),
            "tokens_per_run": (s.get("tokens_in", 0) + s.get("tokens_out", 0)) / runs if runs >= MIN_RUNS and (s.get("tokens_in", 0) + s.get("tokens_out", 0)) else None,
            "ok_rate": s.get("ok_rate") if runs >= MIN_RUNS else _calibration_reliability(e),
            "categories": e.get("categories", {}),
            "model_id": e.get("model_id"), "calibrated_at": e.get("calibrated_at"),
        }
    return out


def _relative_best(values: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """10 × (mejor / este); el menor valor (más rápido, menos tokens) obtiene 10."""
    known = [v for v in values.values() if v]
    best = min(known) if known else None
    return {n: (10 * best / v if v and best else None) for n, v in values.items()}


def compute(cfg: Dict, metrics: Dict, stats: Dict, profile: Dict, priors: Optional[Dict[str, Dict]] = None) -> Dict[str, Dict[str, Dict]]:
    """{modelo: {categoría: desglose}}. `priors` = estimaciones iniciales (models.json) por modelo."""
    names = list(cfg["models"])
    raw = raw_metrics(names, metrics, stats)
    speed = _relative_best({n: raw[n]["seconds"] for n in names})
    quota = _relative_best({n: raw[n]["tokens_per_run"] for n in names})
    cats = sorted({c for n in names for c in (priors or {}).get(n, cfg["models"][n].get("strengths", {}))} | {"quick"})
    spare = profile.get("spare") or {}
    out: Dict[str, Dict[str, Dict]] = {n: {} for n in names}
    for n in names:
        prior_s = (priors or {}).get(n) or cfg["models"][n].get("strengths", {})
        meas = raw[n]["categories"]
        measured_vals = [10 * c["passed"] / c["total"] for c in meas.values() if c.get("total")]
        for cat in cats:
            prior = prior_s.get(cat, prior_s.get("general", 5))
            m = meas.get(cat)
            if m and m.get("total"):
                n_q = m["total"]
                quality = (10 * m["passed"] + PRIOR_K * prior) / (n_q + PRIOR_K)
                src = f"medido {m['passed']}/{n_q}" + (f" ({m['blocked']} sin respuesta por permisos)" if m.get("blocked") else "")
            elif cat == "general" and measured_vals:
                quality = (sum(measured_vals) + PRIOR_K * prior) / (len(measured_vals) + PRIOR_K)
                src = f"promedio de {len(measured_vals)} medidas"
            else:
                quality, src = float(prior), "estimado"
            vals = {"quality": quality,
                    "speed": speed[n] if speed[n] is not None else quality,
                    "quota": quota[n] if quota[n] is not None else quality,
                    "reliability": 10 * raw[n]["ok_rate"] if raw[n]["ok_rate"] is not None else quality}
            w = weights_for(profile, cat)
            score = sum(w[d] * vals[d] for d in DIMS) - float(spare.get(n, 0))
            out[n][cat] = {"score": round(max(0.0, min(10.0, score)), 2), "values": {d: round(v, 2) for d, v in vals.items()},
                           "weights": {d: round(x, 2) for d, x in w.items()}, "quality_src": src,
                           "known": {"speed": speed[n] is not None, "quota": quota[n] is not None, "reliability": raw[n]["ok_rate"] is not None},
                           "spare": float(spare.get(n, 0))}
    return out


def has_evidence(metrics: Dict, profile: Dict) -> bool:
    return bool((metrics.get("models") or {}) or profile)


def apply_to_config(cfg: Dict) -> Dict:
    """Reemplaza `strengths` por el puntaje objetivo (si hay métricas o perfil). Guarda el desglose en spec['_scoring']."""
    metrics, profile = calibrate.load(), load_profile()
    if not has_evidence(metrics, profile):
        return cfg
    priors = {n: dict(spec.get("strengths", {})) for n, spec in cfg["models"].items()}
    table = compute(cfg, metrics, state.stats(), profile, priors)
    for n, spec in cfg["models"].items():
        spec["_prior_strengths"] = priors[n]
        spec["_scoring"] = table[n]
        spec["strengths"] = {cat: d["score"] for cat, d in table[n].items()}
    cfg["_scored"] = True
    cfg["_tiebreak"] = profile.get("tiebreak")
    return cfg


def tiebreak(ranking: List[Dict], cfg: Dict, weights: Dict[str, float], mode: Optional[str]) -> List[Dict]:
    """Entre los usables a menos de TIEBREAK_GAP del primero, desempata por calidad, velocidad o cuota (según el perfil)."""
    dim = {"quality": "quality", "speed": "speed", "quota": "quota"}.get(mode or "")
    usable = [r for r in ranking if r["usable"]]
    if not dim or len(usable) < 2:
        return ranking
    top = usable[0]["score"]
    tied = [r for r in usable if top - r["score"] <= TIEBREAK_GAP]
    if len(tied) < 2:
        return ranking

    def key(r):
        sc = cfg["models"][r["name"]].get("_scoring") or {}
        cats = weights or {"general": 1.0}
        tot = sum(cats.values()) or 1.0
        return -sum(w * (sc.get(c) or sc.get("general") or {}).get("values", {}).get(dim, 0) for c, w in cats.items()) / tot

    tied.sort(key=key)
    rest = [r for r in ranking if r not in tied]
    return tied + rest


# ---------- presentación ----------

def render_table(cfg: Dict, cats: Optional[List[str]] = None) -> str:
    names = list(cfg["models"])
    if not cfg.get("_scored"):
        return "Todavía no hay métricas ni criterios: se usan las estimaciones iniciales de models.json. Corré /calibrate."
    table = {n: cfg["models"][n]["_scoring"] for n in names}
    cats = cats or [c for c in table[names[0]] if c != "general"]
    head = f"{'categoría':<14}" + "".join(f"{n:>14}" for n in names)
    lines = [head, "─" * len(head)]
    for cat in cats:
        row = f"{cat:<14}"
        best = max(table[n][cat]["score"] for n in names)
        for n in names:
            d = table[n][cat]
            mark = "*" if d["score"] == best else " "
            tag = "m" if d["quality_src"].startswith("medido") else "e"
            row += f"{d['score']:>11.1f}{mark}{tag} "
        lines.append(row)
    lines += ["", "* = mejor de la fila · m = calidad medida · e = estimada (sin calibrar para esa categoría)"]
    flat = ties(cfg)
    if flat:
        lines.append(f"Sin diferencias medidas (todos acertaron todo): {', '.join(flat)}. Ahí decide la velocidad y la estimación; `calibrate --full` suma preguntas distintas.")
    return "\n".join(lines)


def ties(cfg: Dict) -> List[str]:
    """Categorías medidas en las que todos los modelos medidos sacaron el máximo: la medición no los distingue."""
    out = []
    names = [n for n in cfg["models"] if cfg["models"][n].get("_scoring")]
    for cat in (cfg["models"][names[0]]["_scoring"] if names else {}):
        got = []
        for n in names:
            m = re.match(r"medido (\d+)/(\d+)", cfg["models"][n]["_scoring"][cat]["quality_src"])
            if m:
                got.append(m.group(1) == m.group(2))
        if len(got) >= 2 and all(got):
            out.append(cat)
    return out


def explain(cfg: Dict, cat: str) -> str:
    names = list(cfg["models"])
    if not cfg.get("_scored") or cat not in cfg["models"][names[0]].get("_scoring", {}):
        return f"Sin desglose para '{cat}'."
    lines = [f"Puntaje de «{cat}» = Σ peso × valor (0-10)", f"{'':<13}{'calidad':>9}{'velocidad':>11}{'cuota':>8}{'confiab.':>10}{'':>3}{'total':>7}"]
    for n in names:
        d = cfg["models"][n]["_scoring"][cat]
        v, w, k = d["values"], d["weights"], d["known"]
        cell = lambda dim, known=True: f"{v[dim]:>5.1f}×{w[dim]:.2f}" + ("" if known else "~")
        lines.append(f"{n:<13}{cell('quality'):>9}{cell('speed', k['speed']):>12}{cell('quota', k['quota']):>9}{cell('reliability', k['reliability']):>11}  = {d['score']:>5.2f}"
                     + (f"  (−{d['spare']:g} cuota cuidada)" if d["spare"] else "") + f"   calidad: {d['quality_src']}")
    lines.append("~ sin dato objetivo todavía: vale igual que la calidad, no mueve el puntaje.")
    return "\n".join(lines)
