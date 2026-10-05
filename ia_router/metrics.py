"""Métricas objetivas que alimentan el ruteo. Es la única fuente de verdad: no hay estimaciones a mano salvo como último recurso.

  Arena (arena.ai)              PRECISIÓN y precio de lista. Elo por categoría (preferencia humana en comparaciones a ciegas, con
                                control de estilo e intervalo de confianza). Páginas públicas de arena.ai/leaderboard (su robots.txt
                                las permite); mismo dato que el dataset CC BY 4.0 `lmarena-ai/leaderboard-dataset`.
  Artificial Analysis (opcional) VELOCIDAD (tokens/s), precio y benchmarks con respuesta correcta (índices de coding y math, GPQA,
                                HLE…). API con clave gratuita en ARTIFICIAL_ANALYSIS_API_KEY (ver `.env.example`); pide atribución.

Esta versión del software trae EMBEBIDA la última foto de Arena (`data/arena.json`, regenerada con `tools/update_snapshot.py`),
así que rutea con métricas desde el primer uso, sin red. `metrics refresh` descarga una más nueva a ~/.ia-router/metrics.json;
siempre manda la más reciente. Nada se consulta solo: actualizar es una acción del usuario.

Reglas para no engañarse:
  - se empareja por el id REAL del modelo que usa cada CLI y su nivel de esfuerzo (-high, -max…); si Arena no publica ese nivel se
    usa el más cercano y se marca como aproximado;
  - una dimensión solo cuenta si cubre a TODOS los modelos (no se mezclan escalas); si no, esa dimensión no pesa;
  - las diferencias de Elo dentro del margen de error no premian a nadie.
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
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import state

ARENA_PAGE = "https://arena.ai/leaderboard"  # las páginas por categoría son /leaderboard/text/<categoría>
AA_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
SNAPSHOT = Path(__file__).resolve().parent / "data" / "arena.json"
STALE_DAYS = 7
EFFORTS = ("minimal", "none", "low", "medium", "high", "xhigh", "max")
EFFORT_PREFERENCE = ("medium", "high", "xhigh", "max", "low", "minimal", "none")  # si no sabemos el esfuerzo del CLI
NOISE = {"thinking", "reasoning", "non", "adaptive", "effort", "preview", "default"}
ATTRIBUTION = {"arena": "Arena (arena.ai), dataset leaderboard-dataset, CC BY 4.0", "aa": "Artificial Analysis (artificialanalysis.ai)"}

# categoría del router -> [(subconjunto de Arena, categoría de Arena)]; se promedian las que cubren a todos los modelos
ARENA_MAP: Dict[str, List[Tuple[str, str]]] = {
    "general": [("text", "overall")],
    "quick": [("text", "overall")],
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
# categoría del router -> campo de evaluación de Artificial Analysis (benchmarks con respuesta correcta)
AA_MAP: Dict[str, List[str]] = {
    "general": ["artificial_analysis_intelligence_index"],
    "analysis": ["artificial_analysis_intelligence_index"],
    "research": ["artificial_analysis_intelligence_index"],
    "coding": ["artificial_analysis_coding_index"],
    "debugging": ["artificial_analysis_coding_index"],
    "data": ["artificial_analysis_coding_index", "artificial_analysis_math_index"],
    "math": ["artificial_analysis_math_index"],
}


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


def get_with_retry(get: Callable[..., str], url: str, headers: Optional[Dict[str, str]] = None, tries: int = 4, sleep: Callable[[float], None] = time.sleep,
                   timeout: Optional[float] = None) -> str:
    """Reintenta con espera creciente ante 429 (límite de pedidos), 5xx y cortes de conexión; los demás errores 4xx no se reintentan."""
    for attempt in range(tries):
        try:
            if timeout:
                return get(url, headers, timeout) if headers else get(url, None, timeout)
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


# ---------- Arena: páginas públicas ----------

def arena_pages() -> List[Tuple[str, str, str]]:
    """(subconjunto, categoría, URL) de las páginas de arena.ai que alimentan ARENA_MAP, sin repetir."""
    pages, seen = [], set()
    for pairs in ARENA_MAP.values():
        for sub, cat in pairs:
            if (sub, cat) in seen:
                continue
            seen.add((sub, cat))
            if sub == "text":
                url = f"{ARENA_PAGE}/text" + ("" if cat == "overall" else "/" + cat.replace("_", "-"))
            else:
                url = f"{ARENA_PAGE}/" + {"webdev": "code/webdev"}.get(sub, sub)
            pages.append((sub, cat, url))
    return sorted(pages)


def parse_leaderboard(html: str) -> List[Dict]:
    """Entradas del leaderboard que arena.ai embebe en el HTML (datos de Next.js): [{modelDisplayName, rating, ratingUpper, …}]."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', html, re.S)
    try:
        txt = "".join(json.loads('"' + c + '"') for c in chunks)
        m = re.search(r'"leaderboard":\{"arenaSlug":"[^"]+","leaderboardSlug":"[^"]+","params":\{[^}]*\},', txt)
        i = txt.index('"entries":[', m.start()) + len('"entries":')
        entries, _ = json.JSONDecoder().raw_decode(txt[i:])
    except (ValueError, AttributeError) as exc:
        raise ValueError("arena.ai cambió el formato de la página: no encuentro la tabla del leaderboard") from exc
    if not isinstance(entries, list) or not entries or "rating" not in entries[0]:
        raise ValueError("la tabla del leaderboard de arena.ai no tiene el formato esperado")
    return entries


def fetch_arena(pages: Optional[List[Tuple[str, str, str]]] = None, get: Callable[..., str] = http_get, say: Callable[[str], None] = lambda s: None,
                sleep: Callable[[float], None] = time.sleep) -> Dict[str, List[list]]:
    """{"texto/coding": [[modelo, rating, inferior, superior, votos, precio_in, precio_out], …], …} leyendo las páginas de arena.ai:
    una por categoría (~11 pedidos de 2-3 MB), de a uno y con pausa. Si una falla se avisa y se sigue con las demás."""
    out: Dict[str, List[list]] = {}
    failed = 0
    pages = pages if pages is not None else arena_pages()
    for k, (sub, cat, url) in enumerate(pages):
        if k:
            sleep(1.0)
        try:
            entries = parse_leaderboard(get_with_retry(get, url, sleep=sleep, timeout=90))
        except (OSError, ValueError) as exc:
            failed += 1
            say(f"  ⚠ Arena {sub}/{cat}: {str(exc)[:110]}")
            continue
        out[f"{sub}/{cat}"] = [[e["modelDisplayName"], e["rating"], e.get("ratingLower"), e.get("ratingUpper"), e.get("votes"),
                                e.get("inputPricePerMillion"), e.get("outputPricePerMillion")] for e in entries if "modelDisplayName" in e]
        say(f"Arena · {sub}/{cat}: {len(entries)} modelos")
    if pages and failed == len(pages):
        raise OSError("no pude leer ninguna página de arena.ai")
    return out


def rows_from_pages(pages: Dict[str, List[list]]) -> Dict[str, List[Dict]]:
    """Formato compacto -> filas {subconjunto: [{model_name, rating, rating_lower, rating_upper, vote_count, category, price_in, price_out}]}."""
    out: Dict[str, List[Dict]] = {}
    for key, rows in pages.items():
        sub, _, cat = key.partition("/")
        for r in rows:
            out.setdefault(sub, []).append({"model_name": r[0], "rating": r[1], "rating_lower": r[2], "rating_upper": r[3], "vote_count": r[4],
                                            "price_in": r[5] if len(r) > 5 else None, "price_out": r[6] if len(r) > 6 else None, "category": cat})
    return out


def match_arena(rows_by_subset: Dict[str, List[Dict]], model_ids: Dict[str, Optional[str]], overrides: Optional[Dict[str, str]] = None,
                published: Optional[str] = None) -> Dict[str, Dict]:
    """{modelo: {"name", "effort", "exact", "published", "variants", "price": {"in","out"}, "ratings": {subconjunto: {categoría: {...}}}}}
    para los modelos con id conocido. Cada subconjunto elige su propia variante de esfuerzo (la que coincide, o la más cercana)."""
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
        price: Dict[str, Optional[float]] = {}
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
                                                                  "votes": r.get("vote_count")}
                    if sub == "text" and r.get("price_in") is not None and not price:
                        price = {"in": r["price_in"], "out": r.get("price_out")}
        if "text" not in variants:
            continue
        out[model] = {"name": variants["text"]["name"], "effort": variants["text"]["effort"], "exact": all(v["exact"] for v in variants.values()),
                      "published": published, "variants": variants, "price": price, "ratings": ratings}
    return out


# ---------- Artificial Analysis: API con clave gratuita ----------

def aa_key() -> Optional[str]:
    return os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "").strip() or None


def fetch_aa(key: str, get: Callable[..., str] = http_get, sleep: Callable[[float], None] = time.sleep) -> List[Dict]:
    """Lista compacta de modelos de Artificial Analysis: nombre, velocidad (tokens/s), latencia, precios y evaluaciones."""
    d = json.loads(get_with_retry(get, AA_URL, {"x-api-key": key}, tries=2, sleep=sleep))
    if not isinstance(d, dict) or not isinstance(d.get("data"), list):
        raise ValueError("respuesta inesperada de Artificial Analysis")
    out = []
    for m in d["data"]:
        p = m.get("pricing") or {}
        out.append({"name": m.get("name"), "slug": m.get("slug"), "tps": m.get("median_output_tokens_per_second"), "ttft": m.get("median_time_to_first_token_seconds"),
                    "price_in": p.get("price_1m_input_tokens"), "price_out": p.get("price_1m_output_tokens"), "price_blended": p.get("price_1m_blended_3_to_1"),
                    "evals": {k: v for k, v in (m.get("evaluations") or {}).items() if isinstance(v, (int, float))}})
    return out


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
        out[model] = {"name": m.get("name"), "slug": chosen[0], "effort": chosen[1], "exact": chosen[2], "evals": m.get("evals") or {},
                      "tps": m.get("tps"), "ttft": m.get("ttft"), "price_in": m.get("price_in"), "price_out": m.get("price_out"), "price_blended": m.get("price_blended")}
    return out


# ---------- datos activos: foto incluida + caché del usuario ----------

def cache_path() -> Path:
    return state.home() / "metrics.json"


CACHE_KEYS = ("pages", "arena_at", "aa", "aa_at")


def load_cache() -> Dict:
    """La caché del usuario. Ignora claves de otros formatos (un metrics.json de versiones anteriores tenía otras)."""
    try:
        d = json.loads(cache_path().read_text(encoding="utf-8"))
        return {k: d[k] for k in CACHE_KEYS if k in d} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cache(data: Dict) -> None:
    state.home().mkdir(parents=True, exist_ok=True)
    tmp = cache_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cache_path())


def bundled() -> Dict:
    try:
        d = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def active() -> Dict:
    """Los datos que rigen: la foto de Arena más nueva entre la incluida en el software y la que bajó el usuario, y la de
    Artificial Analysis si la bajó. {"pages", "arena_at", "arena_origin", "aa", "aa_at"}."""
    b, c = bundled(), load_cache()
    use_cache = bool(c.get("pages")) and (c.get("arena_at") or "") >= (b.get("fetched_at") or "")
    src = c if use_cache else b
    return {"pages": src.get("pages") or {}, "arena_at": (c.get("arena_at") if use_cache else b.get("fetched_at")),
            "arena_origin": "actualizada en tu máquina" if use_cache else "incluida en esta versión",
            "aa": c.get("aa"), "aa_at": c.get("aa_at")}


def age_days(stamp: Optional[str]) -> Optional[float]:
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return (time.time() - time.mktime(time.strptime(stamp or "", fmt))) / 86400
        except ValueError:
            continue
    return None


def is_stale(data: Dict, days: float = STALE_DAYS) -> bool:
    a = age_days(data.get("arena_at"))
    return a is None or a > days


def status_line(data: Optional[Dict] = None) -> str:
    """'Arena 2026-10-05 (incluida) · Artificial Analysis 2026-10-05' para el encabezado."""
    d = data or active()
    arena = f"Arena {(d.get('arena_at') or '?')[:10]} ({'actualizada' if 'actualizada' in d.get('arena_origin', '') else 'incluida'})" if d.get("pages") else "sin métricas"
    return arena + (f" · Artificial Analysis {d['aa_at'][:10]}" if d.get("aa") else "")


# ---------- ids reales de los modelos ----------

def known_model_ids(names: List[str]) -> Dict[str, Optional[str]]:
    """Id real del modelo que usa cada CLI: el último que vimos responder (registrado al usarlo o en la sonda). No se adivina."""
    seen = state.seen_ids()
    ids: Dict[str, Optional[str]] = {n: seen.get(n) for n in names}
    try:
        for line in (state.home() / "log.jsonl").read_text(encoding="utf-8").splitlines():
            ev = json.loads(line)
            if ev.get("model") in ids and ev.get("model_id") and not ids[ev["model"]]:
                ids[ev["model"]] = ev["model_id"]
    except (OSError, ValueError):
        pass
    return ids


# ---------- de datos a valores 0-10 ----------

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


def precision_values(models: List[str], arena: Dict[str, Dict], aa: Dict[str, Dict]) -> Dict[str, Dict[str, Tuple[float, str]]]:
    """{categoría: {modelo: (0-10, fuente)}}. Una fuente solo cuenta para una categoría si cubre a TODOS los modelos."""
    out: Dict[str, Dict[str, Tuple[float, str]]] = {}
    arena_ok = len(models) >= 2 and all(m in arena for m in models)
    aa_ok = len(models) >= 2 and all(m in aa for m in models)
    for cat in set(ARENA_MAP) | set(AA_MAP):
        scores: Dict[str, List[Tuple[float, str]]] = {m: [] for m in models}
        if arena_ok and cat in ARENA_MAP:
            for sub, ac in ARENA_MAP[cat]:
                have = {m: arena[m]["ratings"].get(sub, {}).get(ac) for m in models}
                if all(have.values()):
                    sc = elo_scores({m: (have[m]["rating"], _sigma(have[m])) for m in models})
                    for m in models:
                        scores[m].append((sc[m], f"Arena {sub}/{ac} {have[m]['rating']:.0f}"))
        if aa_ok and cat in AA_MAP:
            for field in AA_MAP[cat]:
                have = {m: (aa[m]["evals"] or {}).get(field) for m in models}
                if all(isinstance(v, (int, float)) for v in have.values()) and max(have.values()) > 0:
                    top = max(have.values())
                    for m in models:
                        scores[m].append((round(10 * have[m] / top, 2), f"AA {field.replace('artificial_analysis_', '')} {have[m]:g}"))
        if all(scores[m] for m in models):
            out[cat] = {m: (round(sum(s for s, _ in scores[m]) / len(scores[m]), 2), "; ".join(t for _, t in scores[m])) for m in models}
    return out


def _log_value(ratio: float) -> float:
    """10 para el mejor y 2 puntos menos cada vez que otro es el doble de lento o de caro (escala logarítmica). Una escala proporcional
    tiene mucho más rango que el Elo (4× de precio = 2,5 puntos): el modelo barato ganaría hasta con 'precisión' como prioridad."""
    return round(max(0.0, 10 - 2 * math.log2(max(ratio, 1.0))), 2)


def speed_values(models: List[str], aa: Dict[str, Dict]) -> Dict[str, Tuple[float, str]]:
    """Velocidad de generación publicada por Artificial Analysis (tokens/s), relativa al más rápido de tus modelos."""
    tps = {m: (aa.get(m) or {}).get("tps") for m in models}
    if len(models) < 2 or not all(isinstance(v, (int, float)) and v > 0 for v in tps.values()):
        return {}
    top = max(tps.values())
    return {m: (_log_value(top / tps[m]), f"AA {tps[m]:.0f} tok/s") for m in models}


def _blended(pin: Optional[float], pout: Optional[float]) -> Optional[float]:
    return (3 * pin + pout) / 4 if isinstance(pin, (int, float)) and isinstance(pout, (int, float)) else None


def cost_values(models: List[str], arena: Dict[str, Dict], aa: Dict[str, Dict]) -> Dict[str, Tuple[float, str]]:
    """Precio de lista por millón de tokens (3 de entrada : 1 de salida), relativo al más barato de tus modelos (escala logarítmica). Es un proxy del
    consumo de cuota: con una suscripción no pagás por token, pero cuanto más caro el modelo, más rápido se agota."""
    for source, tag in (({m: (aa.get(m) or {}).get("price_blended") or _blended((aa.get(m) or {}).get("price_in"), (aa.get(m) or {}).get("price_out")) for m in models}, "AA"),
                        ({m: _blended(((arena.get(m) or {}).get("price") or {}).get("in"), ((arena.get(m) or {}).get("price") or {}).get("out")) for m in models}, "Arena")):
        if len(models) >= 2 and all(isinstance(v, (int, float)) and v > 0 for v in source.values()):
            low = min(source.values())
            return {m: (_log_value(source[m] / low), f"{tag} ${source[m]:.2f}/M tokens") for m in models}
    return {}


# ---------- emparejamiento con tus modelos (memoizado) ----------

_memo: Dict[tuple, Tuple[Dict, Dict]] = {}


def matches(models: List[str], ids: Dict[str, Optional[str]], overrides: Dict[str, Dict[str, str]], data: Dict) -> Tuple[Dict[str, Dict], Dict[str, Dict]]:
    """(arena, aa): entradas encontradas para tus modelos con los datos activos."""
    key = (data.get("arena_at"), data.get("aa_at"), tuple(sorted((m, ids.get(m)) for m in models)), json.dumps(overrides, sort_keys=True))
    if key not in _memo:
        use = {m: ids.get(m) for m in models}
        arena = match_arena(rows_from_pages(data.get("pages") or {}), use, {m: o["arena"] for m, o in overrides.items() if o.get("arena")}, (data.get("arena_at") or "")[:10]) if data.get("pages") else {}
        aa = match_aa(data["aa"], use, {m: o["aa"] for m, o in overrides.items() if o.get("aa")}) if data.get("aa") else {}
        _memo.clear()
        _memo[key] = (arena, aa)
    return _memo[key]


# ---------- actualizar ----------

def refresh(cfg: Dict, say: Callable[[str], None] = print, get: Callable[..., str] = http_get, key: Optional[str] = None, force: bool = False,
            sleep: Callable[[float], None] = time.sleep) -> Dict:
    """Descarga Arena (y Artificial Analysis si hay clave) y lo guarda en ~/.ia-router/metrics.json. No repite el pedido si los datos
    del usuario tienen menos de 12 horas (los leaderboards se publican de a días), salvo force."""
    cache = load_cache()
    key = key or aa_key()
    age = age_days(cache.get("arena_at"))
    fresh = cache.get("pages") and age is not None and age < 0.5
    if fresh and not force and (cache.get("aa") or not key):
        say(f"Tus métricas tienen {age * 24:.1f} h: no vuelvo a consultar (usá --force para insistir).")
        return cache
    if not fresh or force:
        say("Leyendo los leaderboards de arena.ai (una página por categoría, ~11 pedidos de 2-3 MB, alrededor de un minuto)…")
        cache["pages"] = fetch_arena(get=get, say=say, sleep=sleep)
        cache["arena_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if key:
        try:
            say("Consultando Artificial Analysis (velocidad, precio y benchmarks)…")
            cache["aa"], cache["aa_at"] = fetch_aa(key, get, sleep), time.strftime("%Y-%m-%dT%H:%M:%S")
            say(f"Artificial Analysis: {len(cache['aa'])} modelos")
        except Exception as exc:  # clave inválida, cuota diaria, red caída: no rompe lo demás
            say(f"Artificial Analysis: no se pudo consultar ({str(exc)[:120]})")
    else:
        say("Artificial Analysis: sin clave (opcional, aporta velocidad). Ver .env.example.")
    save_cache(cache)
    _memo.clear()
    return cache
