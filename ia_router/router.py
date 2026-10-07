"""Task classification (rule-based) and model ranking.

Idea: each task is broken down into weighted categories (coding, writing, long_context...).
Each model has a score per category in models.json. A model's score is the weighted
average. Models that are not installed or are in cooldown go last.
"""
from __future__ import annotations

import json
import re
from typing import Callable, Dict, List, Optional

# Each category has several patterns, in Spanish and English on purpose: the router classifies tasks written in either
# language. How many distinct patterns match (max 3) sets the category weight.
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
        r"redact\w+|\bdraft\b|borrador",  # no "escrib*"/"write": generic verbs that also show up in coding tasks
        r"\b(mail|correo|email|art[ií]culo|post|copy|gui[oó]n|syllabus|newsletter)\b",
        r"resum\w+|traduc\w+|\btono\b|reescrib\w+|summar\w+|translat\w+",
    ],
    "analysis": [
        r"analiz\w+|analy[sz]e|compar\w+|evalu\w+|trade-?offs?|pros y contras",
        r"estrategia|decisi[oó]n|diagn[oó]stic\w+|auditor\w+|arquitectura|dise[ñn]o|strateg\w+|decision|diagnos\w+|audit\w*|architecture|design",
    ],
    "data": [
        r"\b(csv|excel|xlsx|dataset|pandas|dataframe|dashboard|kpi|m[eé]tricas?)\b",
        r"estad[ií]stic\w+|regresi[oó]n|correlaci[oó]n|series? de tiempo|statistic\w+|regression|correlation|time series",
    ],
    "research": [
        r"investig\w+|research|fuentes|estado del arte|[uú]ltimas novedades|mercado|sources|state of the art|latest news|market",
        r"\b(benchmark|competencia|competitiv\w+|tendencias?)\b",
    ],
    "math": [
        r"calcul\w+|demostr\w+|ecuaci[oó]n|probabilidad|matem[aá]tic\w+|\bprove\b|integral|derivad\w+",
    ],
    "multimodal": [
        r"\b(imagen|im[aá]genes|image|screenshot|captura|foto|pdf|video|audio|diagrama)\b",
    ],
    "long_context": [
        r"documento largo|todo el repo|codebase completa|libro|transcripci[oó]n|contexto largo|long document|whole repo|entire codebase|book|transcript|long context",
    ],
}
QUICK_PATTERN = r"r[aá]pido|breve|en una l[ií]nea|tl;?dr|quick|brief|one-?line\w*"


def detect(task: str, context_len: int = 0) -> Dict[str, float]:
    """Returns {category: weight}. Empty means 'general'."""
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


def rank(
    weights: Dict[str, float],
    models: Dict[str, dict],
    is_available: Callable[[str], bool],
    cooldown: Callable[[str], float],
    prefer: Optional[str] = None,
) -> List[dict]:
    """Sorts the models for this task. The 'usable' ones go first."""
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
            why += " (+1 preferred)"
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
