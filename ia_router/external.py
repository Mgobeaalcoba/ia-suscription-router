"""Fuentes externas de métricas: Arena (arena.ai) y Artificial Analysis (artificialanalysis.ai).

Se usan como PRIOR de calidad (reemplazan las estimaciones a mano de models.json) y después las mediciones locales
(`calibrate`) las corrigen. Nunca se consultan solas: `benchmarks refresh` es una acción explícita del usuario.

  Arena                dataset público (CC BY 4.0) en Hugging Face: preferencia humana en comparaciones a ciegas (Elo con
                       intervalo de confianza) por categoría: coding, hard_prompts, math, creative_writing, longer_query…
                       No es una prueba con respuesta correcta: mide qué prefieren las personas.
  Artificial Analysis  API con clave GRATUITA (variable ARTIFICIAL_ANALYSIS_API_KEY o ~/.ia-router/artificialanalysis.key):
                       benchmarks con respuesta correcta (índices de inteligencia/coding/math, GPQA, HLE, LiveCodeBench,
                       SciCode, AIME), velocidad y precio. Pide atribución. Sin clave se omite.

Reglas para no engañarse:
  - una fuente solo se usa para una categoría si cubre a TODOS tus modelos (no se mezclan escalas);
  - Arena publica variantes por nivel de esfuerzo (-high, -max…): se elige la que coincide con tu CLI y, si no hay, la más
    cercana, avisando que el dato es aproximado;
  - diferencias de Elo que caen dentro del margen de error no premian a nadie.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

from . import calibrate, state

ARENA_DATASET = "lmarena-ai/leaderboard-dataset"
ARENA_ROWS = "https://datasets-server.huggingface.co/rows"
ARENA_PAGE = "https://arena.ai/leaderboard"
AA_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
AA_SITE = "https://artificialanalysis.ai/"
EFFORTS = ("minimal", "none", "low", "medium", "high", "xhigh", "max")
EFFORT_PREFERENCE = ("medium", "high", "xhigh", "max", "low", "minimal", "none")  # si no sabemos el esfuerzo del CLI
NOISE = {"thinking", "reasoning", "non", "adaptive", "effort", "preview", "default"}

# categoría del router -> [(subconjunto de Arena, categoría de Arena)]; se promedian las que cubren a todos los modelos
ARENA_MAP: Dict[str, List[Tuple[str, str]]] = {
    "general": [("text", "overall")],
    "coding": [("text", "coding"), ("webdev", "overall")],
    "debugging": [("text", "coding"), ("text", "hard_prompts")],
    "writing": [("text", "creative_writing"), ("text", "instruction_following")],
    "analysis": [("text", "hard_prompts"), ("text", "expert")],
    "data": [("text", "math"), ("text", "coding")],
    "math": [("text", "math")],
    "research": [("search", "overall")],
    "long_context": [("text", "longer_query")],
    "multimodal": [("vision", "overall")],
}
ARENA_SUBSETS = ("text", "vision", "search", "webdev")
# categoría del router -> campo de evaluación de Artificial Analysis
AA_MAP: Dict[str, List[str]] = {
    "general": ["artificial_analysis_intelligence_index"],
    "analysis": ["artificial_analysis_intelligence_index"],
    "research": ["artificial_analysis_intelligence_index"],
    "coding": ["artificial_analysis_coding_index"],
    "debugging": ["artificial_analysis_coding_index"],
    "data": ["artificial_analysis_coding_index", "artificial_analysis_math_index"],
    "math": ["artificial_analysis_math_index"],
}
ATTRIBUTION = {"arena": "Arena (arena.ai) · dataset leaderboard-dataset, CC BY 4.0", "aa": "Artificial Analysis (artificialanalysis.ai)"}


# ---------- red ----------

class HttpError(OSError):
    def __init__(self, status: int, url: str, body: str = "") -> None:
        super().__init__(f"HTTP {status} al pedir {url.split('?')[0]}")
        self.status, self.body = status, body


def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 40) -> str:
    """GET con curl (usa los certificados del sistema; el Python de python.org en macOS a veces no los tiene)
    y, si no hay curl, urllib. Las cabeceras (p. ej. la API key) viajan por stdin de curl, no por argv.
    Devuelve el cuerpo si el estado es 2xx; si no, lanza HttpError con el estado (429 = límite de pedidos)."""
    cfg = "".join(f'header = "{k}: {v}"\n' for k, v in (headers or {}).items())
    try:
        p = subprocess.run(["curl", "-sL", "-m", str(int(timeout)), "-w", "\n%{http_code}", "--config", "-", url],
                           input=cfg, capture_output=True, text=True, timeout=timeout + 10)
    except FileNotFoundError:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as r:
                return r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            e.close()
            raise HttpError(e.code, url) from None
    if p.returncode != 0:
        raise OSError(f"curl falló con código {p.returncode} al pedir {url.split('?')[0]}")
    body, _, code = p.stdout.rpartition("\n")
    status = int(code) if code.isdigit() else 0
    if not 200 <= status < 300:
        raise HttpError(status, url, body[:200])
    return body


def get_with_retry(get: Callable[..., str], url: str, headers: Optional[Dict[str, str]] = None, tries: int = 4, sleep: Callable[[float], None] = time.sleep) -> str:
    """Reintenta con espera creciente ante 429 (límite de pedidos), 5xx y cortes de conexión; los demás errores 4xx no se reintentan."""
    for attempt in range(tries):
        try:
            return get(url, headers) if headers else get(url)
        except HttpError as e:
            if e.status not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
            sleep(8 * (attempt + 1) if e.status == 429 else 2 * (attempt + 1))
        except OSError:
            if attempt == tries - 1:
                raise
            sleep(2 * (attempt + 1))
    raise OSError("sin respuesta")


# ---------- nombres ----------

def norm(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (name or "").lower())).strip("-")


def split_effort(name: str) -> Tuple[str, Optional[str]]:
    """'gpt-6.1-sol-max' -> ('gpt-6-1-sol', 'max'); 'Gemini 3.8 Flash (High)' -> ('gemini-3-8-flash', 'high')."""
    toks = [t for t in norm(name).split("-") if t and t not in NOISE]
    idx = next((i for i in range(len(toks) - 1, -1, -1) if toks[i] in EFFORTS), None)
    if idx is None:
        return "-".join(toks), None
    return "-".join(toks[:idx] + toks[idx + 1:]), toks[idx]


def pick_variant(entries: List[Tuple[str, Optional[str]]], wanted: Optional[str]) -> Optional[Tuple[str, Optional[str], bool]]:
    """Entre las variantes (nombre, esfuerzo) de una misma familia: (nombre, esfuerzo, coincide_exacto)."""
    if not entries:
        return None
    if wanted:
        for n, e in entries:
            if e == wanted:
                return n, e, True
        order = {e: i for i, e in enumerate(EFFORTS)}
        near = min(entries, key=lambda x: (abs(order.get(x[1], 3) - order.get(wanted, 3)), order.get(x[1], 3)))  # empate: menor esfuerzo
        return near[0], near[1], False
    for pref in (None, *EFFORT_PREFERENCE):
        for n, e in entries:
            if e == pref:
                return n, e, pref is None
    return entries[0][0], entries[0][1], False


def known_model_ids(names: List[str]) -> Dict[str, Optional[str]]:
    """Id real del modelo que usa cada CLI, de la calibración o del log de uso (no se adivina)."""
    ids: Dict[str, Optional[str]] = {n: None for n in names}
    for n, e in (calibrate.load().get("models") or {}).items():
        if n in ids and e.get("model_id"):
            ids[n] = e["model_id"]
    try:
        for line in (state.home() / "log.jsonl").read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("model") in ids and ev.get("model_id") and not ids[ev["model"]]:
                ids[ev["model"]] = ev["model_id"]
    except (OSError, ValueError):
        pass
    return ids


# ---------- Arena ----------

def _arena_url(cfg: str, off: int) -> str:
    return f"{ARENA_ROWS}?dataset={ARENA_DATASET}&config={cfg}&split=latest&offset={off}&length=100"


def fetch_arena(subsets=ARENA_SUBSETS, get: Callable[..., str] = http_get, say: Callable[[str], None] = lambda s: None,
                sleep: Callable[[float], None] = time.sleep) -> Dict[str, List[Dict]]:
    """Filas del último leaderboard de cada subconjunto, {subconjunto: [fila, …]}.
    Es un servicio público y gratuito: se pide de a una página (100 filas) por vez, con una pausa entre pedidos y
    reintentos con espera si responde 429. El subconjunto de texto son ~110 páginas; por eso el resultado se cachea."""
    out: Dict[str, List[Dict]] = {}
    for cfg in subsets:
        first = json.loads(get_with_retry(get, _arena_url(cfg, 0), sleep=sleep))
        rows, total = [r["row"] for r in first["rows"]], first.get("num_rows_total", 0)
        pages = max(1, -(-total // 100))
        say(f"Arena · {cfg}: {total} filas en {pages} páginas")
        for k, off in enumerate(range(100, total, 100), 2):
            sleep(0.3)
            rows += [r["row"] for r in json.loads(get_with_retry(get, _arena_url(cfg, off), sleep=sleep))["rows"]]
            if pages > 20 and k % 25 == 0:
                say(f"  … {k}/{pages}")
        out[cfg] = rows
    return out


def match_arena(rows_by_subset: Dict[str, List[Dict]], model_ids: Dict[str, Optional[str]], overrides: Optional[Dict[str, str]] = None) -> Dict[str, Dict]:
    """{modelo: {"name", "effort", "exact", "published", "variants", "ratings": {subconjunto: {categoría: {...}}}}} para los
    modelos con id conocido. Cada subconjunto elige su propia variante de esfuerzo (la que coincide, o la más cercana)."""
    overrides = overrides or {}
    index: Dict[str, Dict[str, List[Tuple[str, Optional[str]]]]] = {}
    for sub, rows in rows_by_subset.items():
        fams = index.setdefault(sub, {})
        for r in rows:
            fam, eff = split_effort(r["model_name"])
            if (r["model_name"], eff) not in fams.setdefault(fam, []):
                fams[fam].append((r["model_name"], eff))
    out: Dict[str, Dict] = {}
    for model, mid in model_ids.items():
        src = overrides.get(model) or mid
        if not src:
            continue
        fam, eff = split_effort(src)
        variants: Dict[str, Dict] = {}
        ratings: Dict[str, Dict] = {}
        published = None
        for sub, rows in rows_by_subset.items():
            cands = [(src, eff)] if overrides.get(model) and sub == "text" else index[sub].get(fam, [])
            chosen = pick_variant(cands, eff)
            if not chosen:
                continue
            name, effort, exact = chosen
            if overrides.get(model) and sub == "text":
                exact = True
            variants[sub] = {"name": name, "effort": effort, "exact": exact}
            for r in rows:
                if r["model_name"] == name:
                    ratings.setdefault(sub, {})[r["category"]] = {"rating": r["rating"], "lower": r.get("rating_lower"), "upper": r.get("rating_upper"),
                                                                  "votes": r.get("vote_count"), "rank": r.get("rank")}
                    published = published or r.get("leaderboard_publish_date")
        if "text" not in variants:
            continue
        out[model] = {"name": variants["text"]["name"], "effort": variants["text"]["effort"], "exact": all(v["exact"] for v in variants.values()),
                      "published": published, "variants": variants, "ratings": ratings}
    return out


# ---------- Artificial Analysis ----------

def aa_key() -> Optional[str]:
    k = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "").strip()
    if k:
        return k
    try:
        return (state.home() / "artificialanalysis.key").read_text(encoding="utf-8").splitlines()[0].strip() or None
    except (OSError, IndexError):
        return None


def fetch_aa(key: str, get: Callable[..., str] = http_get) -> List[Dict]:
    d = json.loads(get_with_retry(get, AA_URL, {"x-api-key": key}, tries=2))
    if not isinstance(d, dict) or not isinstance(d.get("data"), list):
        raise ValueError("respuesta inesperada de Artificial Analysis")
    return d["data"]


def match_aa(models: List[Dict], model_ids: Dict[str, Optional[str]], overrides: Optional[Dict[str, str]] = None) -> Dict[str, Dict]:
    overrides = overrides or {}
    fams: Dict[str, List[Tuple[str, Optional[str]]]] = {}
    by_name: Dict[str, Dict] = {}
    for m in models:
        label = m.get("slug") or m.get("name") or ""
        fam, eff = split_effort(m.get("name") or label)
        fams.setdefault(fam, []).append((label, eff))
        by_name[label] = m
    out: Dict[str, Dict] = {}
    for model, mid in model_ids.items():
        if overrides.get(model) and overrides[model] in by_name:
            chosen = (overrides[model], split_effort(overrides[model])[1], True)
        elif mid:
            fam, eff = split_effort(mid)
            chosen = pick_variant(fams.get(fam, []), eff)
        else:
            chosen = None
        if not chosen:
            continue
        m = by_name[chosen[0]]
        out[model] = {"name": m.get("name"), "slug": chosen[0], "effort": chosen[1], "exact": chosen[2],
                      "evaluations": m.get("evaluations") or {}, "tokens_per_second": m.get("median_output_tokens_per_second"),
                      "ttft_s": m.get("median_time_to_first_token_seconds"), "pricing": m.get("pricing") or {}}
    return out


# ---------- persistencia ----------

def path():
    return state.home() / "external.json"


def load() -> Dict:
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: Dict) -> None:
    state.home().mkdir(parents=True, exist_ok=True)
    tmp = path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path())


def age_hours(data: Dict) -> Optional[float]:
    try:
        return (time.time() - time.mktime(time.strptime(data["fetched_at"], "%Y-%m-%dT%H:%M:%S"))) / 3600
    except (KeyError, ValueError):
        return None


def refresh(cfg: Dict, say: Callable[[str], None] = print, get: Callable[..., str] = http_get, key: Optional[str] = None, force: bool = False) -> Dict:
    """Descarga y guarda las fuentes. Solo para los modelos cuyo id real se conoce. No repite el pedido si los datos
    tienen menos de 12 horas (los leaderboards se publican de a días), salvo force."""
    cached = load()
    age = age_hours(cached)
    if cached.get("arena") and age is not None and age < 12 and not force:
        say(f"Los datos tienen {age:.1f} h: no vuelvo a consultar (usá --force para insistir).")
        return cached
    names = [n for n, s in cfg["models"].items() if s.get("enabled", True)]
    ids = known_model_ids(names)
    over = {n: cfg["models"][n].get("external", {}) for n in names}
    unknown = [n for n in names if not ids[n] and not over[n].get("arena")]
    if unknown:
        say(f"Sin id de modelo para: {', '.join(unknown)} (corré /calibrate o una tarea con ese modelo, o definí \"external\" en models.json).")
    data = load()
    data["fetched_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rows = fetch_arena(get=get, say=say)
    data["arena"] = match_arena(rows, ids, {n: over[n].get("arena") for n in names if over[n].get("arena")})
    say(f"Arena: {len(data['arena'])}/{len(names)} modelos encontrados")
    key = key or aa_key()
    if key:
        try:
            data["aa"] = match_aa(fetch_aa(key, get), ids, {n: over[n].get("aa") for n in names if over[n].get("aa")})
            say(f"Artificial Analysis: {len(data['aa'])}/{len(names)} modelos encontrados")
        except Exception as exc:  # clave inválida, cuota diaria, red caída: no rompe lo demás
            say(f"Artificial Analysis: no se pudo consultar ({str(exc)[:120]})")
    else:
        say("Artificial Analysis: sin clave (opcional). Sacá una gratis en artificialanalysis.ai y exportá ARTIFICIAL_ANALYSIS_API_KEY.")
    save(data)
    return data


# ---------- de datos externos a prior 0-10 ----------

def elo_scores(entries: Dict[str, Tuple[float, float]]) -> Dict[str, float]:
    """{modelo: (rating, sigma)} -> 0..10. 10 = empata o gana al mejor de tus modelos; una diferencia que cae dentro del
    margen de error no cuenta; el resto se traduce a probabilidad de victoria de Elo (100 puntos ≈ 64%) y se escala ×2."""
    best = max(entries, key=lambda m: entries[m][0])
    out = {}
    for m, (r, s) in entries.items():
        gap = entries[best][0] - r
        eff = max(0.0, gap - math.sqrt(s * s + entries[best][1] ** 2))
        out[m] = round(10 * min(1.0, 2 / (1 + 10 ** (eff / 400))), 2)
    return out


def _sigma(c: Dict) -> float:
    lo, up = c.get("lower"), c.get("upper")
    return (up - lo) / 3.92 if lo is not None and up is not None else 10.0


def derive_priors(models: List[str], data: Dict) -> Dict[str, Dict[str, Dict]]:
    """{modelo: {categoría: {"prior": 0-10, "src": texto}}}. Una fuente solo cuenta si cubre a TODOS los modelos."""
    out: Dict[str, Dict[str, Dict]] = {m: {} for m in models}
    arena, aa = data.get("arena") or {}, data.get("aa") or {}
    arena_ok = all(m in arena for m in models)
    aa_ok = all(m in aa for m in models)
    cats = set(ARENA_MAP) | set(AA_MAP)
    for cat in cats:
        scores: Dict[str, List[Tuple[float, str]]] = {m: [] for m in models}
        if arena_ok and cat in ARENA_MAP:
            for sub, ac in ARENA_MAP[cat]:
                have = {m: arena[m]["ratings"].get(sub, {}).get(ac) for m in models}
                if len(models) >= 2 and all(have.values()):
                    sc = elo_scores({m: (have[m]["rating"], _sigma(have[m])) for m in models})
                    for m in models:
                        scores[m].append((sc[m], f"Arena {sub}/{ac} {have[m]['rating']:.0f}"))
        if aa_ok and cat in AA_MAP:
            for field in AA_MAP[cat]:
                have = {m: (aa[m]["evaluations"] or {}).get(field) for m in models}
                if len(models) >= 2 and all(isinstance(v, (int, float)) for v in have.values()) and max(have.values()) > 0:
                    top = max(have.values())
                    for m in models:
                        scores[m].append((round(10 * have[m] / top, 2), f"AA {field.replace('artificial_analysis_', '')} {have[m]:g}"))
        for m in models:
            if scores[m]:
                out[m][cat] = {"prior": round(sum(s for s, _ in scores[m]) / len(scores[m]), 2), "src": "; ".join(t for _, t in scores[m])}
    return out


# ---------- presentación ----------

def render(cfg: Dict, data: Dict) -> str:
    if not data.get("arena") and not data.get("aa"):
        return "Todavía no bajaste métricas externas. Corré `benchmarks refresh` (/benchmarks refresh): consulta Arena (sin clave) y, si hay clave, Artificial Analysis."
    names = [n for n, s in cfg["models"].items() if s.get("enabled", True)]
    lines = [f"Métricas externas · actualizadas {data.get('fetched_at', '?')}", ""]
    for n in names:
        a, x = (data.get("arena") or {}).get(n), (data.get("aa") or {}).get(n)
        if a:
            warn = "" if a["exact"] else "  ⚠ Arena no publica el mismo nivel de esfuerzo que usa tu CLI: el dato es aproximado (" + ", ".join(f"{sub}: {v['effort'] or 'sin nivel'}" for sub, v in a["variants"].items() if not v["exact"]) + ")"
            lines.append(f"{n:<12} Arena: {a['name']} (leaderboard {a.get('published', '?')}){warn}")
            for sub, cats in a["ratings"].items():
                for c, v in sorted(cats.items()):
                    if (sub, c) in {p for ps in ARENA_MAP.values() for p in ps}:
                        lines.append(f"{'':<12}   {sub}/{c:<22} {v['rating']:>7.0f}  [{v['lower']:.0f}, {v['upper']:.0f}]  {int(v['votes'] or 0):>7} votos")
        else:
            lines.append(f"{n:<12} Arena: sin coincidencia (no se encontró su modelo en el leaderboard)")
        if x:
            ev = x["evaluations"]
            shown = ", ".join(f"{k.replace('artificial_analysis_', '')} {v:g}" for k, v in ev.items() if isinstance(v, (int, float)) and k.startswith("artificial_analysis"))
            lines.append(f"{'':<12} Artificial Analysis: {x['name']} · {shown}" + (f" · {x['tokens_per_second']:.0f} tok/s" if x.get("tokens_per_second") else ""))
    lines += ["", "Fuentes: " + " · ".join(ATTRIBUTION[k] for k in ("arena", "aa") if data.get(k))]
    return "\n".join(lines)
