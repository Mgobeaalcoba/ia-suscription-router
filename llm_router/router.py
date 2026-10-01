"""Clasificación de tareas (reglas + clasificador LLM opcional) y ranking de modelos.

Idea: cada tarea se descompone en categorías con peso (coding, writing, long_context...).
Cada modelo tiene una puntuación por categoría en models.json. El score de un modelo es
el promedio ponderado. Los modelos no instalados o en cooldown quedan al final.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional

# Cada categoría tiene varios patrones (ES/EN). Cuántos patrones distintos matchean
# (máx. 3) define el peso de la categoría.
PATTERNS: Dict[str, List[str]] = {
    "coding": [
        r"c[oó]digo|\bcode\b|funci[oó]n|\bfunction\b|\bclase\b|\bclass\b",
        r"\b(python|typescript|javascript|react|node|sql|rust|golang|java|bash|docker|git)\b",
        r"```",
        r"\.(py|ts|tsx|js|go|rs|java|sql|sh)\b",
        r"\b(api|endpoint|script|repo|pull request|refactor\w*|tests?|unit ?tests?|regex)\b",
    ],
    "debugging": [
        r"\b(bug|error|exception|traceback|stack ?trace|debug\w*)\b",
        r"no funciona|falla\b|fallando|arregl\w+|\bfix\b|rompi\w+",
    ],
    "writing": [
        r"redact\w+|escrib\w+|\bwrite\b|\bdraft\b|borrador",
        r"\b(mail|correo|email|art[ií]culo|post|copy|gui[oó]n|syllabus|newsletter)\b",
        r"resum\w+|traduc\w+|\btono\b|reescrib\w+|summar\w+|translat\w+",
    ],
    "analysis": [
        r"analiz\w+|analy[sz]e|compar\w+|evalu\w+|trade-?offs?|pros y contras",
        r"estrategia|decisi[oó]n|diagn[oó]stic\w+|auditor\w+|arquitectura|dise[ñn]o",
    ],
    "data": [
        r"\b(csv|excel|xlsx|dataset|pandas|dataframe|dashboard|kpi|m[eé]tricas?)\b",
        r"estad[ií]stic\w+|regresi[oó]n|correlaci[oó]n|series? de tiempo",
    ],
    "research": [
        r"investig\w+|research|fuentes|estado del arte|[uú]ltimas novedades|mercado",
        r"\b(benchmark|competencia|competitiv\w+|tendencias?)\b",
    ],
    "math": [
        r"calcul\w+|demostr\w+|ecuaci[oó]n|probabilidad|matem[aá]tic\w+|\bprove\b|integral|derivad\w+",
    ],
    "multimodal": [
        r"\b(imagen|im[aá]genes|image|screenshot|captura|foto|pdf|video|audio|diagrama)\b",
    ],
    "long_context": [
        r"documento largo|todo el repo|codebase completa|libro|transcripci[oó]n|contexto largo",
    ],
}
QUICK_PATTERN = r"r[aá]pido|breve|en una l[ií]nea|tl;?dr|quick|one-liner"


def detect(task: str, context_len: int = 0) -> Dict[str, float]:
    """Devuelve {categoría: peso}. Vacío significa 'general'."""
    weights: Dict[str, float] = {}
    for cat, pats in PATTERNS.items():
        hits = sum(1 for p in pats if re.search(p, task, re.I))
        if hits:
            weights[cat] = float(min(hits, 3))
    total = len(task) + context_len
    if total > 100_000:
        weights["long_context"] = 5.0
    elif total > 30_000:
        weights["long_context"] = max(weights.get("long_context", 0), 3.0)
    if re.search(QUICK_PATTERN, task, re.I) or (total < 160 and not weights):
        weights["quick"] = max(weights.get("quick", 0), 2.0)
    return weights


def classify_with_llm(task: str, run: Callable[[str], Optional[str]]) -> Optional[Dict[str, float]]:
    """Clasificador opcional con un modelo barato. `run(prompt)` devuelve texto o None.

    Si algo falla, devuelve None y el llamador usa las reglas.
    """
    cats = ", ".join(list(PATTERNS) + ["quick"])
    prompt = (
        "Clasificá la siguiente tarea. Respondé SOLO un JSON con la forma "
        '{"categories": {"<categoria>": <peso 1-3>}} usando únicamente estas categorías: '
        f"{cats}. Incluí entre 1 y 3 categorías.\n\nTAREA:\n{task[:4000]}"
    )
    try:
        text = run(prompt)
        if not text:
            return None
        match = re.search(r"\{.*\}", text, re.S)
        data = json.loads(match.group(0)) if match else None
        cats_out = data.get("categories") if isinstance(data, dict) else None
        if not isinstance(cats_out, dict):
            return None
        valid = {k: float(v) for k, v in cats_out.items() if k in PATTERNS or k == "quick"}
        return valid or None
    except (ValueError, TypeError, AttributeError):
        return None


def rank(
    weights: Dict[str, float],
    models: Dict[str, dict],
    is_available: Callable[[str], bool],
    cooldown: Callable[[str], float],
    prefer: Optional[str] = None,
) -> List[dict]:
    """Ordena los modelos para esta tarea. Los 'usable' van primero."""
    out: List[dict] = []
    for name, spec in models.items():
        strengths = spec.get("strengths", {})
        default = strengths.get("general", 5)
        if weights:
            total_w = sum(weights.values())
            parts = [(c, w, strengths.get(c, default)) for c, w in weights.items()]
            score = sum(w * s for _, w, s in parts) / total_w
            parts.sort(key=lambda p: p[1] * p[2], reverse=True)
            why = ", ".join(f"{c}×{w:g}→{s}" for c, w, s in parts[:3])
        else:
            score, why = float(default), f"general→{default}"
        if prefer and prefer == name:
            score += 1.0
            why += " (+1 preferido)"
        available = is_available(name)
        cd = cooldown(name)
        out.append(
            {
                "name": name,
                "score": round(score, 2),
                "available": available,
                "cooldown_s": round(cd),
                "usable": available and cd <= 0,
                "why": why,
            }
        )
    out.sort(key=lambda r: (not r["usable"], -r["score"]))
    return out
