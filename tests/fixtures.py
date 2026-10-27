"""Shared test data: a toy leaderboard in metrics.py's compact format and a fake Hugging Face datasets server."""
import json

IDS = {"claude": "claude-sonnet-5-5", "codex": "gpt-6.1-sol", "antigravity": "Gemini 3.8 Flash (High)"}

# Arena model -> (base rating, input price, output price)
MODELS = {"claude-sonnet-5.5-xhigh": (1500, 2, 10), "gpt-6.1-sol-high": (1480, 3, 12), "gpt-6.1-sol-max": (1495, 3, 12),
          "gemini-3.8-flash-high": (1450, 0.75, 3.75), "claude-opus-5-max": (1520, 5, 25)}
CAT_DELTA = {"overall": 0, "coding": 20, "hard_prompts": 5, "expert": 10, "creative_writing": -10, "instruction_following": 0, "longer_query": 3, "math": 15}


def row(model, rating, sigma=5.0, votes=1000, pin=None, pout=None):
    return [model, rating, rating - 1.96 * sigma, rating + 1.96 * sigma, votes, pin, pout]


def sample_pages(no_price=(), no_math=("claude-sonnet-5.5-xhigh",)):
    """{"text/coding": [[model, rating, lower, upper, votes, price_in, price_out], …], …}; vision and webdev with other variants; empty search."""
    pages = {}
    for cat, delta in CAT_DELTA.items():
        rows = []
        for model, (base, pin, pout) in MODELS.items():
            if cat == "math" and model in no_math:
                continue
            rows.append(row(model, base + delta, pin=None if model in no_price else pin, pout=None if model in no_price else pout))
        pages[f"text/{cat}"] = rows
    pages["vision/overall"] = [row("claude-sonnet-5.5-high", 1300), row("gpt-6.1-sol-max", 1280), row("gemini-3.8-flash-high", 1310)]
    pages["webdev/overall"] = [row("claude-sonnet-5.5-xhigh", 1470), row("gpt-6.1-sol-max", 1460), row("gemini-3.8-flash-high", 1400)]
    return pages


def snapshot(date="2026-10-02T00:00:00", **kw):
    return {"fetched_at": date, "source": "test", "pages": sample_pages(**kw)}


def aa_models(codex_tps=40.0):
    """Compact Artificial Analysis list (metrics.fetch_aa format)."""
    ev = lambda i, c, m: {"artificial_analysis_intelligence_index": i, "artificial_analysis_coding_index": c, "artificial_analysis_math_index": m}
    return [{"name": "Claude Sonnet 5.5 (Reasoning, Max Effort)", "slug": "claude-sonnet-5-5-max", "tps": 80.0, "ttft": 1.2, "price_in": 3, "price_out": 15, "price_blended": 6.0, "evals": ev(70, 60, 90)},
            {"name": "GPT-6.1 Sol (high)", "slug": "gpt-6-1-sol-high", "tps": codex_tps, "ttft": 3.0, "price_in": 3, "price_out": 12, "price_blended": 4.5, "evals": ev(75, 66, 92)},
            {"name": "Gemini 3.8 Flash (High)", "slug": "gemini-3-8-flash-high", "tps": 200.0, "ttft": 0.5, "price_in": 0.75, "price_out": 3.75, "price_blended": 1.5, "evals": ev(60, 50, 85)}]


def aa_api_payload():
    """Artificial Analysis API response in the format of its documentation."""
    out = []
    for m in aa_models():
        out.append({"id": "x", "name": m["name"], "slug": m["slug"], "model_creator": {"name": "x"}, "evaluations": m["evals"],
                    "pricing": {"price_1m_blended_3_to_1": m["price_blended"], "price_1m_input_tokens": m["price_in"], "price_1m_output_tokens": m["price_out"]},
                    "median_output_tokens_per_second": m["tps"], "median_time_to_first_token_seconds": m["ttft"]})
    return json.dumps({"status": 200, "data": out})


def fake_arena_get(pages, fail=None):
    """Fake datasets-server get(): serves the sample pages as the rows of the Arena dataset, in pages of `length` like the real API.
    `fail` = {url_fragment | call_number: HTTP status}. Returns (get, calls)."""
    from urllib.parse import parse_qs, urlparse
    from ia_router import metrics as M
    calls = []

    def get(url, headers=None, timeout=None):
        calls.append(url)
        for key, status in (fail or {}).items():
            if (isinstance(key, int) and len(calls) == key) or (isinstance(key, str) and key in url):
                raise M.HttpError(status, url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        rows = []
        for key, items in pages.items():
            sub, _, cat = key.partition("/")
            if sub != q["config"]:
                continue
            for r in items:
                rows.append({"row": {"model_name": r[0], "rating": r[1], "rating_lower": r[2], "rating_upper": r[3], "vote_count": r[4],
                                     "category": cat, "organization": "x", "license": "x"}})
        off, n = int(q["offset"]), int(q["length"])
        return json.dumps({"num_rows_total": len(rows), "rows": rows[off:off + n], "features": []})
    return get, calls
