import json, os, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import calibrate as C, chat, core, external as X, scoring  # noqa: E402


def row(model, category, rating, sigma=5.0, votes=1000, date="2026-10-02"):
    return {"model_name": model, "organization": "x", "license": "Proprietary", "rating": rating, "rating_lower": rating - 1.96 * sigma,
            "rating_upper": rating + 1.96 * sigma, "variance": sigma * sigma, "vote_count": votes, "rank": 1.0, "category": category,
            "leaderboard_publish_date": date}


IDS = {"claude": "claude-sonnet-5-5", "codex": "gpt-6.1-sol", "antigravity": "Gemini 3.8 Flash (High)"}


def sample_rows():
    """Leaderboard de juguete: claude solo con -xhigh, codex con -high y -max, antigravity exacto; math falta para claude."""
    text = []
    for model, base in (("claude-sonnet-5.5-xhigh", 1500), ("gpt-6.1-sol-high", 1480), ("gpt-6.1-sol-max", 1495), ("gemini-3.8-flash-high", 1450), ("claude-opus-5-max", 1520)):
        for cat, delta in (("overall", 0), ("coding", 20), ("hard_prompts", 5), ("expert", 10), ("creative_writing", -10), ("instruction_following", 0), ("longer_query", 3), ("math", 15)):
            if model.startswith("claude-sonnet") and cat == "math":
                continue
            text.append(row(model, cat, base + delta))
    return {"text": text,
            "vision": [row("claude-sonnet-5.5-high", "overall", 1300), row("gpt-6.1-sol-max", "overall", 1280), row("gemini-3.8-flash-high", "overall", 1310)],
            "search": [], "webdev": [row("claude-sonnet-5.5-xhigh", "overall", 1470), row("gpt-6.1-sol-max", "overall", 1460), row("gemini-3.8-flash-high", "overall", 1400)]}


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        os.environ.pop("ARTIFICIAL_ANALYSIS_API_KEY", None)
        for k in list(os.environ):
            if k.startswith(("FAKE_", "ROUTER_CMD_")):
                del os.environ[k]
        self.cfg = core.load_config(apply_manifest=False)

    def tearDown(self):
        self.tmp.cleanup()


class NameTests(unittest.TestCase):
    def test_split_effort(self):
        cases = {"gpt-6.1-sol-max": ("gpt-6-1-sol", "max"), "Gemini 3.8 Flash (High)": ("gemini-3-8-flash", "high"), "claude-sonnet-5-5": ("claude-sonnet-5-5", None),
                 "claude-sonnet-5.5-xhigh": ("claude-sonnet-5-5", "xhigh"), "claude-opus-5-max": ("claude-opus-5", "max"),
                 "gemini-3-flash (thinking-minimal)": ("gemini-3-flash", "minimal"), "gpt-6.1-sol": ("gpt-6-1-sol", None)}
        for name, exp in cases.items():
            self.assertEqual(X.split_effort(name), exp, name)

    def test_versions_do_not_collide(self):
        self.assertNotEqual(X.split_effort("claude-sonnet-5-high")[0], X.split_effort("claude-sonnet-5.5-high")[0])

    def test_pick_variant(self):
        v = [("m-low", "low"), ("m-high", "high"), ("m-max", "max")]
        self.assertEqual(X.pick_variant(v, "high"), ("m-high", "high", True))
        self.assertEqual(X.pick_variant(v, "xhigh"), ("m-high", "high", False))    # empate de cercanía: el de menor esfuerzo; marcada aproximada
        self.assertEqual(X.pick_variant(v, "none"), ("m-low", "low", False))
        self.assertEqual(X.pick_variant(v, None)[:2], ("m-high", "high"))          # sin dato: la más habitual
        self.assertEqual(X.pick_variant([("m", None), ("m-high", "high")], None), ("m", None, True))
        self.assertIsNone(X.pick_variant([], "high"))


class EloTests(unittest.TestCase):
    def test_tie_is_ten_and_gap_lowers_score(self):
        s = X.elo_scores({"a": (1500, 2), "b": (1500, 2), "c": (1400, 2)})
        self.assertEqual(s["a"], 10.0)
        self.assertEqual(s["b"], 10.0)
        self.assertLess(s["c"], 8)
        self.assertGreater(s["c"], 6)

    def test_gap_inside_margin_of_error_does_not_count(self):
        s = X.elo_scores({"a": (1500, 20), "b": (1490, 20)})  # sigma combinada ≈ 28 > 10
        self.assertEqual(s["b"], 10.0)

    def test_monotonic(self):
        s = X.elo_scores({"a": (1500, 1), "b": (1480, 1), "c": (1440, 1), "d": (1300, 1)})
        self.assertTrue(s["a"] >= s["b"] > s["c"] > s["d"])
        self.assertAlmostEqual(X.elo_scores({"a": (1600, 0.1), "b": (1500, 0.1)})["b"], 7.2, delta=0.1)  # 100 Elo ≈ 64% de victoria


class MatchTests(unittest.TestCase):
    def test_match_picks_closest_effort_and_flags_it(self):
        m = X.match_arena(sample_rows(), IDS)
        self.assertEqual(m["claude"]["name"], "claude-sonnet-5.5-xhigh")
        self.assertFalse(m["claude"]["exact"])
        self.assertEqual(m["antigravity"]["name"], "gemini-3.8-flash-high")
        self.assertTrue(m["antigravity"]["exact"] is False or m["antigravity"]["exact"])  # vision/webdev: mismo esfuerzo → exacto
        self.assertEqual(m["codex"]["variants"]["text"]["name"], "gpt-6.1-sol-high")     # sin dato de esfuerzo: se prefiere el nivel habitual (medium > high > …)
        self.assertEqual(m["claude"]["published"], "2026-10-02")
        self.assertEqual(m["claude"]["ratings"]["text"]["coding"]["rating"], 1520)

    def test_each_subset_chooses_its_own_variant(self):
        m = X.match_arena(sample_rows(), IDS)
        self.assertEqual(m["claude"]["variants"]["vision"]["name"], "claude-sonnet-5.5-high")      # el único de visión; text solo tiene xhigh
        self.assertEqual(m["claude"]["variants"]["webdev"]["name"], "claude-sonnet-5.5-xhigh")

    def test_unknown_or_missing_models_are_skipped(self):
        m = X.match_arena(sample_rows(), {"claude": None, "codex": "modelo-inexistente-9", "antigravity": "gemini-3.8-flash-high"})
        self.assertEqual(list(m), ["antigravity"])

    def test_override_forces_a_specific_entry(self):
        m = X.match_arena(sample_rows(), IDS, {"codex": "gpt-6.1-sol-high"})
        self.assertEqual(m["codex"]["name"], "gpt-6.1-sol-high")
        self.assertTrue(m["codex"]["variants"]["text"]["exact"])
        self.assertEqual(m["codex"]["ratings"]["text"]["coding"]["rating"], 1500)


class DeriveTests(unittest.TestCase):
    def data(self):
        return {"arena": X.match_arena(sample_rows(), IDS)}

    def test_priors_for_categories_covered_by_everyone(self):
        pri = X.derive_priors(["claude", "codex", "antigravity"], self.data())
        self.assertIn("coding", pri["claude"])
        self.assertNotIn("math", pri["claude"])           # claude no tiene math en Arena: ninguno lo usa
        self.assertNotIn("research", pri["claude"])       # search vacío
        self.assertLessEqual(max(pri[m]["coding"]["prior"] for m in pri), 10)
        self.assertTrue(pri["claude"]["coding"]["src"].startswith("Arena text/coding"))
        self.assertIn("webdev", pri["claude"]["coding"]["src"])  # coding promedia text/coding y webdev
        self.assertLess(pri["antigravity"]["coding"]["prior"], pri["claude"]["coding"]["prior"])

    def test_a_source_must_cover_all_models(self):
        d = self.data()
        d["arena"].pop("codex")
        self.assertEqual(X.derive_priors(["claude", "codex", "antigravity"], d), {"claude": {}, "codex": {}, "antigravity": {}})

    def test_single_model_has_nothing_to_compare(self):
        self.assertEqual(X.derive_priors(["claude"], self.data()), {"claude": {}})

    AA = [{"name": "Claude Sonnet 5.5 (Reasoning, Max Effort)", "slug": "claude-sonnet-5-5-max", "evaluations": {"artificial_analysis_intelligence_index": 70.0,
                                                                                                              "artificial_analysis_coding_index": 60.0, "artificial_analysis_math_index": 90.0},
           "median_output_tokens_per_second": 80.5, "median_time_to_first_token_seconds": 1.2, "pricing": {"price_1m_input_tokens": 3}},
          {"name": "GPT-6.1 Sol (high)", "slug": "gpt-6-1-sol-high", "evaluations": {"artificial_analysis_intelligence_index": 75.0, "artificial_analysis_coding_index": 66.0,
                                                                                  "artificial_analysis_math_index": 92.0}},
          {"name": "Gemini 3.8 Flash (High)", "slug": "gemini-3-8-flash-high", "evaluations": {"artificial_analysis_intelligence_index": 60.0, "artificial_analysis_coding_index": 50.0,
                                                                                           "artificial_analysis_math_index": 85.0}}]

    def test_artificial_analysis_matching_and_ratio_scores(self):
        aa = X.match_aa(self.AA, IDS)
        self.assertEqual(set(aa), {"claude", "codex", "antigravity"})
        self.assertEqual(aa["claude"]["slug"], "claude-sonnet-5-5-max")
        self.assertEqual(aa["claude"]["tokens_per_second"], 80.5)
        pri = X.derive_priors(["claude", "codex", "antigravity"], {"aa": aa})
        self.assertEqual(pri["codex"]["coding"]["prior"], 10.0)
        self.assertAlmostEqual(pri["claude"]["coding"]["prior"], 9.09, places=2)
        self.assertIn("math", pri["claude"])                                  # AA sí cubre math
        self.assertEqual(pri["codex"]["data"]["prior"], round((10 + 10) / 2, 2))  # data = promedio de coding y math

    def test_both_sources_are_averaged(self):
        d = {"arena": X.match_arena(sample_rows(), IDS), "aa": X.match_aa(self.AA, IDS)}
        pri = X.derive_priors(["claude", "codex", "antigravity"], d)
        src = pri["claude"]["coding"]["src"]
        self.assertIn("Arena", src)
        self.assertIn("AA coding_index", src)


class _Handler(BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        _Handler.seen.append((self.path, self.headers.get("x-api-key")))
        if self.path.startswith("/limited"):
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b"slow down")
        elif self.path.startswith("/ok"):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"hola": "mundo"}')
        else:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b'{"error":"API key is required"}')

    def log_message(self, *a):
        pass


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def test_ok_and_header_sent(self):
        _Handler.seen.clear()
        self.assertEqual(json.loads(X.http_get(self.base + "/ok", {"x-api-key": "SECRETO"})), {"hola": "mundo"})
        self.assertEqual(_Handler.seen[-1][1], "SECRETO")

    def test_api_key_never_in_process_arguments(self):
        calls = []
        real = X.subprocess.run
        def spy(argv, **kw):
            calls.append(argv)
            return real(argv, **kw)
        with mock.patch.object(X.subprocess, "run", spy):
            X.http_get(self.base + "/ok", {"x-api-key": "SECRETO"})
        self.assertTrue(calls)
        self.assertFalse(any("SECRETO" in a for argv in calls for a in argv))

    def test_http_errors_carry_the_status(self):
        with self.assertRaises(X.HttpError) as cm:
            X.http_get(self.base + "/limited")
        self.assertEqual(cm.exception.status, 429)
        with self.assertRaises(X.HttpError) as cm:
            X.http_get(self.base + "/nada")
        self.assertEqual(cm.exception.status, 401)

    def test_falls_back_to_urllib_without_curl(self):
        with mock.patch.object(X.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(json.loads(X.http_get(self.base + "/ok")), {"hola": "mundo"})
            with self.assertRaises(X.HttpError):
                X.http_get(self.base + "/limited")


class RetryTests(unittest.TestCase):
    def test_retries_rate_limits_with_growing_waits_then_succeeds(self):
        calls, waits = [], []
        def get(url, headers=None):
            calls.append(url)
            if len(calls) < 3:
                raise X.HttpError(429, url)
            return "ok"
        self.assertEqual(X.get_with_retry(get, "u", sleep=waits.append), "ok")
        self.assertEqual(waits, [8, 16])

    def test_gives_up_after_all_tries(self):
        def get(url, headers=None):
            raise X.HttpError(503, url)
        with self.assertRaises(X.HttpError):
            X.get_with_retry(get, "u", tries=3, sleep=lambda s: None)

    def test_client_errors_are_not_retried(self):
        calls = []
        def get(url, headers=None):
            calls.append(1)
            raise X.HttpError(401, url)
        with self.assertRaises(X.HttpError):
            X.get_with_retry(get, "u", sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_connection_errors_are_retried(self):
        n = []
        def get(url, headers=None):
            n.append(1)
            if len(n) == 1:
                raise OSError("corte")
            return "ok"
        self.assertEqual(X.get_with_retry(get, "u", sleep=lambda s: None), "ok")


def fake_server(rows_by_cfg, page=100, fail=None):
    """get() falso de datasets-server /rows: pagina de a `page` filas. `fail` = {nro_de_llamada: estado HTTP}."""
    calls = []

    def get(url, headers=None):
        calls.append(url)
        if fail and len(calls) in fail:
            raise X.HttpError(fail[len(calls)], url)
        import urllib.parse as up
        q = dict(up.parse_qsl(up.urlparse(url).query))
        rows = rows_by_cfg.get(q["config"], [])
        off = int(q["offset"])
        return json.dumps({"num_rows_total": len(rows), "rows": [{"row": r} for r in rows[off:off + page]]})
    return get, calls


class FetchTests(unittest.TestCase):
    def test_pages_sequentially_with_a_pause_between_requests(self):
        rows = {"text": [row(f"m-{i}", "overall", 1000 + i) for i in range(250)], "vision": [], "search": [], "webdev": []}
        get, calls = fake_server(rows)
        waits, msgs = [], []
        out = X.fetch_arena(get=get, say=msgs.append, sleep=waits.append)
        self.assertEqual(len(out["text"]), 250)
        self.assertEqual([c.split("offset=")[1].split("&")[0] for c in calls if "config=text" in c], ["0", "100", "200"])
        self.assertEqual(waits.count(0.3), 2)
        self.assertTrue(any("3 páginas" in m for m in msgs))

    def test_survives_a_rate_limit_in_the_middle(self):
        rows = {"text": [row(f"m-{i}", "overall", 1000 + i) for i in range(250)]}
        get, calls = fake_server(rows, fail={2: 429})
        waits = []
        out = X.fetch_arena(subsets=("text",), get=get, sleep=waits.append)
        self.assertEqual(len(out["text"]), 250)
        self.assertIn(8, waits)                                      # esperó tras el 429 y reintentó

    def test_gives_up_with_a_clear_error_when_limited_for_good(self):
        get, _ = fake_server({"text": [row("m", "overall", 1)]}, fail={n: 429 for n in range(1, 20)})
        with self.assertRaises(X.HttpError):
            X.fetch_arena(subsets=("text",), get=get, sleep=lambda s: None)

    def test_progress_for_long_downloads(self):
        rows = {"text": [row(f"m-{i}", "overall", 1) for i in range(3000)]}
        msgs = []
        X.fetch_arena(subsets=("text",), get=fake_server(rows)[0], say=msgs.append, sleep=lambda s: None)
        self.assertTrue(any("25/30" in m for m in msgs))


class RefreshTests(Home):
    def setUp(self):
        super().setUp()
        C.save({"models": {n: {"model_id": mid, "categories": {}, "calls": []} for n, mid in IDS.items()}})

    def test_known_ids_come_from_metrics_then_the_log(self):
        self.assertEqual(X.known_model_ids(["claude", "codex", "x"]), {"claude": "claude-sonnet-5-5", "codex": "gpt-6.1-sol", "x": None})
        (Path(self.tmp.name) / "log.jsonl").write_text(json.dumps({"model": "x", "model_id": "m-9"}) + "\n")
        self.assertEqual(X.known_model_ids(["x"])["x"], "m-9")

    def test_refresh_saves_and_caches(self):
        get, calls = fake_server(sample_rows())
        out = []
        data = X.refresh(self.cfg, out.append, get)
        self.assertEqual(set(data["arena"]), {"claude", "codex", "antigravity"})
        self.assertEqual(X.load()["arena"]["claude"]["name"], "claude-sonnet-5.5-xhigh")
        n = len(calls)
        again = X.refresh(self.cfg, out.append, get)           # menos de 12 h: no vuelve a pedir
        self.assertEqual(len(calls), n)
        self.assertIn("no vuelvo a consultar", out[-1])
        self.assertEqual(again["fetched_at"], data["fetched_at"])
        X.refresh(self.cfg, out.append, get, force=True)
        self.assertGreater(len(calls), n)

    def test_without_aa_key_it_says_so_and_still_works(self):
        out = []
        X.refresh(self.cfg, out.append, fake_server(sample_rows())[0])
        self.assertTrue(any("sin clave" in m for m in out))
        self.assertNotIn("aa", X.load())

    def test_with_aa_key_from_env_and_file(self):
        aa_payload = json.dumps({"status": 200, "data": DeriveTests.AA})
        def get(url, headers=None):
            if "artificialanalysis" in url:
                assert headers == {"x-api-key": "K123"}, headers
                return aa_payload
            return fake_server(sample_rows())[0](url, headers)
        os.environ["ARTIFICIAL_ANALYSIS_API_KEY"] = "K123"
        out = []
        X.refresh(self.cfg, out.append, get)
        self.assertEqual(set(X.load()["aa"]), {"claude", "codex", "antigravity"})
        del os.environ["ARTIFICIAL_ANALYSIS_API_KEY"]
        self.assertIsNone(X.aa_key())
        (Path(self.tmp.name) / "artificialanalysis.key").write_text("K123\n")
        self.assertEqual(X.aa_key(), "K123")

    def test_aa_failure_does_not_break_arena(self):
        def get(url, headers=None):
            if "artificialanalysis" in url:
                raise X.HttpError(401, url)
            return fake_server(sample_rows())[0](url, headers)
        out = []
        X.refresh(self.cfg, out.append, get, key="MALA")
        self.assertTrue(any("no se pudo consultar" in m for m in out))
        self.assertIn("arena", X.load())

    def test_unknown_model_id_is_reported(self):
        C.save({"models": {}})
        out = []
        X.refresh(self.cfg, out.append, fake_server(sample_rows())[0])
        self.assertTrue(any("Sin id de modelo" in m for m in out))

    def test_render_warns_about_approximate_effort_and_cites_sources(self):
        get = fake_server(sample_rows())[0]
        X.refresh(self.cfg, lambda s: None, get)
        text = X.render(self.cfg, X.load())
        self.assertIn("aproximado", text)
        self.assertIn("claude-sonnet-5.5-xhigh", text)
        self.assertIn("CC BY 4.0", text)
        self.assertIn("votos", text)
        self.assertIn("Todavía no bajaste", X.render(self.cfg, {}))


class ScoringIntegrationTests(Home):
    def setUp(self):
        super().setUp()
        X.save({"fetched_at": "2026-10-02T00:00:00", "arena": X.match_arena(sample_rows(), IDS)})

    def test_external_prior_replaces_hand_written_estimates(self):
        cfg = core.load_config(apply_manifest=False)
        sc = cfg["models"]["claude"]["_scoring"]
        self.assertTrue(sc["coding"]["quality_src"].startswith("externo: Arena"))
        self.assertEqual(sc["research"]["quality_src"], "estimado")      # sin cobertura de Arena: sigue la estimación
        self.assertEqual(sc["math"]["quality_src"], "estimado")
        self.assertIn("Arena", scoring.render_table(cfg).splitlines()[-1] + scoring.render_table(cfg))

    def test_measured_results_correct_the_external_prior(self):
        C.save({"models": {"claude": {"categories": {"coding": {"passed": 0, "total": 5}}, "calls": []},
                           "codex": {"categories": {"coding": {"passed": 5, "total": 5}}, "calls": []},
                           "antigravity": {"categories": {"coding": {"passed": 5, "total": 5}}, "calls": []}}})
        cfg = core.load_config(apply_manifest=False)
        sc = {n: cfg["models"][n]["_scoring"]["coding"] for n in IDS}
        self.assertIn("medido 0/5 + prior externo", sc["claude"]["quality_src"])
        self.assertLess(sc["claude"]["values"]["quality"], 4)             # lo medido manda sobre el prior de Arena
        self.assertGreater(sc["codex"]["values"]["quality"], 8)

    def test_partial_coverage_is_ignored(self):
        d = X.load()
        d["arena"].pop("codex")
        X.save(d)
        cfg = core.load_config(apply_manifest=False)
        self.assertTrue(all(v["quality_src"] == "estimado" for v in cfg["models"]["claude"]["_scoring"].values()))

    def test_table_tag_and_attribution(self):
        cfg = core.load_config(apply_manifest=False)
        table = scoring.render_table(cfg)
        self.assertRegex(table, r"coding\s+\d+\.\d\*?x")
        self.assertIn("Fuentes externas: Arena", table)

    def test_chat_benchmarks_command(self):
        out = []
        chat.Chat(read=lambda p: "", write=out.append).handle("/benchmarks")
        self.assertIn("Arena", "\n".join(out))
        with mock.patch.object(X, "refresh") as ref:
            chat.Chat(read=lambda p: "", write=out.append).handle("/benchmarks refresh")
            ref.assert_called_once()
            self.assertFalse(ref.call_args.kwargs["force"])
            chat.Chat(read=lambda p: "", write=out.append).handle("/benchmarks force")
            self.assertTrue(ref.call_args.kwargs["force"])
        self.assertIn("/benchmarks", {c.name for c in chat.COMMANDS})
        self.assertIn("/benchmarks", chat.HELP)


if __name__ == "__main__":
    unittest.main()
