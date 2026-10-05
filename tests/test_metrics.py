import json, os, sys, tempfile, threading, time, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fixtures as F  # noqa: E402
from ia_router import core, metrics as M, state  # noqa: E402


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        os.environ.pop("ARTIFICIAL_ANALYSIS_API_KEY", None)
        self.snap = Path(self.tmp.name) / "snapshot.json"
        self.snap.write_text(json.dumps(F.snapshot()))
        patcher = mock.patch.object(M, "SNAPSHOT", self.snap)
        patcher.start()
        self.addCleanup(patcher.stop)
        M._memo.clear()
        self.cfg = core.load_config(apply_scoring=False)

    def tearDown(self):
        self.tmp.cleanup()


class NameTests(unittest.TestCase):
    def test_split_effort(self):
        cases = {"gpt-6.1-sol-max": ("gpt-6-1-sol", "max"), "Gemini 3.8 Flash (High)": ("gemini-3-8-flash", "high"), "claude-sonnet-5-5": ("claude-sonnet-5-5", None),
                 "claude-sonnet-5.5-xhigh": ("claude-sonnet-5-5", "xhigh"), "claude-opus-5-max": ("claude-opus-5", "max"),
                 "gemini-3-flash (thinking-minimal)": ("gemini-3-flash", "minimal"), "gpt-6.1-sol": ("gpt-6-1-sol", None)}
        for name, exp in cases.items():
            self.assertEqual(M.split_effort(name), exp, name)

    def test_real_artificial_analysis_names(self):
        # nombres tal como los publica la API real de Artificial Analysis
        cases = {"Claude Sonnet 5.5 (Max, Default Fallback)": ("claude-sonnet-5-5", "max"), "Claude Sonnet 5.5 (Medium, Default Fallback)": ("claude-sonnet-5-5", "medium"),
                 "GPT-6.1 Sol (Medium)": ("gpt-6-1-sol", "medium"), "GPT-6.1 Sol (Xhigh)": ("gpt-6-1-sol", "xhigh"), "Gemini 3.8 Flash (High)": ("gemini-3-8-flash", "high"),
                 "Claude Sonnet 5 (Max)": ("claude-sonnet-5", "max")}
        for name, exp in cases.items():
            self.assertEqual(M.split_effort(name), exp, name)
        self.assertEqual(M.split_effort("claude-sonnet-5-5")[0], M.split_effort("Claude Sonnet 5.5 (Max, Default Fallback)")[0])

    def test_aa_variants_are_chosen_like_arena_ones(self):
        aa = [{"name": f"Claude Sonnet 5.5 ({e}, Default Fallback)", "slug": f"claude-sonnet-5-5-{e.lower()}", "tps": 100.0 + i, "evals": {}} for i, e in enumerate(["Max", "Xhigh", "High", "Medium", "Low"])]
        aa.append({"name": "Claude Sonnet 5 (Max)", "slug": "claude-sonnet-5", "tps": 0, "evals": {}})
        m = M.match_aa(aa, {"claude": "claude-sonnet-5-5"})
        self.assertEqual(m["claude"]["slug"], "claude-sonnet-5-5-medium")        # sin esfuerzo conocido: el habitual
        self.assertFalse(m["claude"]["exact"])                                     # y se marca aproximado
        self.assertEqual(M.match_aa(aa, {"claude": "claude-sonnet-5.5-high"})["claude"]["slug"], "claude-sonnet-5-5-high")

    def test_versions_do_not_collide(self):
        self.assertNotEqual(M.split_effort("claude-sonnet-5-high")[0], M.split_effort("claude-sonnet-5.5-high")[0])

    def test_pick_variant(self):
        v = [("m-low", "low"), ("m-high", "high"), ("m-max", "max")]
        self.assertEqual(M.pick_variant(v, "high"), ("m-high", "high", True))
        self.assertEqual(M.pick_variant(v, "xhigh"), ("m-high", "high", False))    # empate de cercanía: el de menor esfuerzo; marcada aproximada
        self.assertEqual(M.pick_variant(v, "none"), ("m-low", "low", False))
        self.assertEqual(M.pick_variant(v, None)[:2], ("m-high", "high"))          # sin dato: el nivel más habitual
        self.assertEqual(M.pick_variant([("m", None), ("m-high", "high")], None), ("m", None, True))
        self.assertIsNone(M.pick_variant([], "high"))


class EloTests(unittest.TestCase):
    def test_tie_is_ten_and_gap_lowers_score(self):
        s = M.elo_scores({"a": (1500, 2), "b": (1500, 2), "c": (1400, 2)})
        self.assertEqual((s["a"], s["b"]), (10.0, 10.0))
        self.assertTrue(6 < s["c"] < 8)

    def test_gap_inside_margin_of_error_does_not_count(self):
        self.assertEqual(M.elo_scores({"a": (1500, 20), "b": (1490, 20)})["b"], 10.0)

    def test_monotonic_and_100_points_is_about_7_2(self):
        s = M.elo_scores({"a": (1500, 1), "b": (1480, 1), "c": (1440, 1), "d": (1300, 1)})
        self.assertTrue(s["a"] >= s["b"] > s["c"] > s["d"])
        self.assertAlmostEqual(M.elo_scores({"a": (1600, 0.1), "b": (1500, 0.1)})["b"], 7.2, delta=0.1)


class _Handler(BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        _Handler.seen.append((self.path, self.headers.get("x-api-key")))
        status, body = (429, b"slow down") if self.path.startswith("/limited") else (200, b'{"hola": "mundo"}') if self.path.startswith("/ok") else (401, b'{"error":"API key is required"}')
        self.send_response(status)
        self.end_headers()
        self.wfile.write(body)

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
        self.assertEqual(json.loads(M.http_get(self.base + "/ok", {"x-api-key": "SECRETO"})), {"hola": "mundo"})
        self.assertEqual(_Handler.seen[-1][1], "SECRETO")

    def test_api_key_never_in_process_arguments(self):
        calls, real = [], M.subprocess.run
        def spy(argv, **kw):
            calls.append(argv)
            return real(argv, **kw)
        with mock.patch.object(M.subprocess, "run", spy):
            M.http_get(self.base + "/ok", {"x-api-key": "SECRETO"})
        self.assertTrue(calls)
        self.assertFalse(any("SECRETO" in a for argv in calls for a in argv))

    def test_http_errors_carry_the_status(self):
        for path, status in (("/limited", 429), ("/nada", 401)):
            with self.assertRaises(M.HttpError) as cm:
                M.http_get(self.base + path)
            self.assertEqual(cm.exception.status, status)

    def test_falls_back_to_urllib_without_curl(self):
        with mock.patch.object(M.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(json.loads(M.http_get(self.base + "/ok")), {"hola": "mundo"})
            with self.assertRaises(M.HttpError):
                M.http_get(self.base + "/limited")


class RetryTests(unittest.TestCase):
    def test_retries_rate_limits_with_growing_waits_then_succeeds(self):
        calls, waits = [], []
        def get(url, headers=None):
            calls.append(url)
            if len(calls) < 3:
                raise M.HttpError(429, url)
            return "ok"
        self.assertEqual(M.get_with_retry(get, "u", sleep=waits.append), "ok")
        self.assertEqual(waits, [8, 16])

    def test_gives_up_after_all_tries_and_does_not_retry_client_errors(self):
        n = []
        def always(url, headers=None):
            n.append(1)
            raise M.HttpError(503, url)
        with self.assertRaises(M.HttpError):
            M.get_with_retry(always, "u", tries=3, sleep=lambda s: None)
        self.assertEqual(len(n), 3)
        n.clear()
        def bad(url, headers=None):
            n.append(1)
            raise M.HttpError(401, url)
        with self.assertRaises(M.HttpError):
            M.get_with_retry(bad, "u", sleep=lambda s: None)
        self.assertEqual(len(n), 1)

    def test_connection_errors_are_retried(self):
        n = []
        def get(url, headers=None):
            n.append(1)
            if len(n) == 1:
                raise OSError("corte")
            return "ok"
        self.assertEqual(M.get_with_retry(get, "u", sleep=lambda s: None), "ok")


class FetchTests(unittest.TestCase):
    def test_reads_each_category_page_once_with_a_pause(self):
        get, calls = F.fake_arena_get(F.sample_pages())
        waits = []
        out = M.fetch_arena(get=get, sleep=waits.append)
        self.assertEqual(len(calls), len(M.arena_pages()))
        self.assertEqual(waits.count(1.0), len(calls) - 1)
        self.assertIn("text/coding", out)
        row = next(r for r in out["text/coding"] if r[0] == "claude-sonnet-5.5-xhigh")
        self.assertEqual((row[1], row[4], row[5], row[6]), (1520, 1000, 2, 10))   # rating, votos, precios

    def test_a_failing_page_is_reported_and_the_rest_continue(self):
        msgs = []
        out = M.fetch_arena(get=F.fake_arena_get(F.sample_pages(), fail={"/text/coding": 404})[0], say=msgs.append, sleep=lambda s: None)
        self.assertTrue(any("text/coding" in m and "⚠" in m for m in msgs))
        self.assertNotIn("text/coding", out)
        self.assertIn("text/math", out)

    def test_empty_page_is_a_warning_not_a_crash(self):
        msgs = []
        out = M.fetch_arena(get=F.fake_arena_get(F.sample_pages())[0], say=msgs.append, sleep=lambda s: None)   # search está vacío en el fixture
        self.assertTrue(any("search" in m and "⚠" in m for m in msgs))
        self.assertNotIn("search/overall", out)

    def test_all_pages_failing_raises(self):
        with self.assertRaises(OSError):
            M.fetch_arena(get=F.fake_arena_get(F.sample_pages(), fail={"arena.ai": 500})[0], sleep=lambda s: None)

    def test_rate_limit_is_retried_with_a_wait(self):
        waits = []
        self.assertTrue(M.fetch_arena(get=F.fake_arena_get(F.sample_pages(), fail={1: 429})[0], sleep=waits.append))
        self.assertIn(8, waits)

    def test_unexpected_page_format_gives_a_clear_error(self):
        for html in ("<html>nada</html>", "<script>self.__next_f.push([1,\"{}\"])</script>", F.page_html([])):
            with self.assertRaises(ValueError) as cm:
                M.parse_leaderboard(html)
            self.assertIn("formato", str(cm.exception))

    def test_pages_cover_the_whole_category_map(self):
        pages = {(s, c): u for s, c, u in M.arena_pages()}
        for pairs in M.ARENA_MAP.values():
            for pair in pairs:
                self.assertIn(pair, pages)
        self.assertEqual(pages[("text", "overall")], "https://arena.ai/leaderboard/text")
        self.assertEqual(pages[("text", "hard_prompts")], "https://arena.ai/leaderboard/text/hard-prompts")
        self.assertEqual(pages[("webdev", "overall")], "https://arena.ai/leaderboard/code/webdev")

    def test_parses_escapes_and_unicode(self):
        html = F.page_html([['modelo-ñ "x"', 1500.5, 1490, 1510, 10, None, None]])
        self.assertEqual(M.parse_leaderboard(html)[0]["modelDisplayName"], 'modelo-ñ "x"')


class MatchTests(unittest.TestCase):
    def match(self, ids=None, overrides=None, **kw):
        return M.match_arena(M.rows_from_pages(F.sample_pages(**kw)), ids or F.IDS, overrides, "2026-10-02")

    def test_picks_closest_effort_and_flags_it(self):
        m = self.match()
        self.assertEqual(m["claude"]["name"], "claude-sonnet-5.5-xhigh")
        self.assertFalse(m["claude"]["exact"])
        self.assertEqual(m["antigravity"]["name"], "gemini-3.8-flash-high")
        self.assertTrue(m["antigravity"]["exact"])
        self.assertEqual(m["codex"]["variants"]["text"]["name"], "gpt-6.1-sol-high")   # sin esfuerzo conocido: el nivel habitual
        self.assertEqual(m["claude"]["ratings"]["text"]["coding"]["rating"], 1520)
        self.assertEqual(m["claude"]["published"], "2026-10-02")

    def test_each_subset_chooses_its_own_variant_and_prices_are_kept(self):
        m = self.match()
        self.assertEqual(m["claude"]["variants"]["vision"]["name"], "claude-sonnet-5.5-high")
        self.assertEqual(m["claude"]["variants"]["webdev"]["name"], "claude-sonnet-5.5-xhigh")
        self.assertEqual(m["claude"]["price"], {"in": 2, "out": 10})
        self.assertEqual(self.match(no_price=("gpt-6.1-sol-high", "gpt-6.1-sol-max"))["codex"]["price"], {})

    def test_unknown_or_missing_models_are_skipped(self):
        m = self.match({"claude": None, "codex": "modelo-inexistente-9", "antigravity": "gemini-3.8-flash-high"})
        self.assertEqual(list(m), ["antigravity"])

    def test_override_forces_a_specific_entry(self):
        m = self.match(overrides={"codex": "gpt-6.1-sol-max"})
        self.assertEqual(m["codex"]["name"], "gpt-6.1-sol-max")
        self.assertTrue(m["codex"]["variants"]["text"]["exact"])
        self.assertEqual(m["codex"]["ratings"]["text"]["coding"]["rating"], 1515)


class DimensionTests(unittest.TestCase):
    NAMES = ["claude", "codex", "antigravity"]

    def arena(self, **kw):
        return M.match_arena(M.rows_from_pages(F.sample_pages(**kw)), F.IDS)

    def test_precision_only_for_categories_everyone_covers(self):
        pv = M.precision_values(self.NAMES, self.arena(), {})
        self.assertIn("coding", pv)
        self.assertNotIn("math", pv)         # claude no tiene math en Arena: ningún modelo lo usa
        self.assertNotIn("research", pv)     # la página de búsqueda está vacía
        self.assertTrue(pv["coding"]["claude"][1].startswith("Arena text/coding"))
        self.assertIn("webdev", pv["coding"]["claude"][1])
        self.assertLess(pv["coding"]["antigravity"][0], pv["coding"]["claude"][0])

    def test_a_source_must_cover_all_models(self):
        arena = self.arena()
        arena.pop("codex")
        self.assertEqual(M.precision_values(self.NAMES, arena, {}), {})
        self.assertEqual(M.precision_values(["claude"], self.arena(), {}), {})

    def test_artificial_analysis_ratio_and_averaging(self):
        aa = M.match_aa(F.aa_models(), F.IDS)
        self.assertEqual(set(aa), set(self.NAMES))
        self.assertEqual(aa["claude"]["slug"], "claude-sonnet-5-5-max")
        pv = M.precision_values(self.NAMES, {}, aa)
        self.assertEqual(pv["coding"]["codex"][0], 10.0)
        self.assertAlmostEqual(pv["coding"]["claude"][0], 9.09, places=2)
        self.assertIn("math", pv)                                              # AA sí cubre math
        both = M.precision_values(self.NAMES, self.arena(), aa)
        self.assertIn("Arena", both["coding"]["claude"][1])
        self.assertIn("AA coding_index", both["coding"]["claude"][1])

    REAL = [  # forma de los datos reales de la API para estos modelos: faltan los índices de coding y math; hay otros benchmarks
        {"name": "Claude Sonnet 5.5 (Medium, Default Fallback)", "slug": "c", "tps": 89.0, "evals": {"artificial_analysis_intelligence_index": 40.8, "hle": 0.398, "scicode": 0.529, "lcr": 0.763, "terminalbench_v4_0": 0.298}},
        {"name": "GPT-6.1 Sol (Medium)", "slug": "g", "tps": 49.0, "evals": {"artificial_analysis_intelligence_index": 47.8, "hle": 0.499, "scicode": 0.532, "lcr": 0.833, "terminalbench_v4_0": 0.48}},
        {"name": "Gemini 3.8 Flash (High)", "slug": "f", "tps": 238.0, "evals": {"artificial_analysis_intelligence_index": 40.9, "artificial_analysis_coding_index": 76.3, "hle": 0.478, "scicode": 0.566, "lcr": 0.813, "terminalbench_v4_0": 0.197}},
    ]

    def test_uses_the_benchmarks_that_cover_every_model_even_when_the_indexes_are_missing(self):
        pv = M.precision_values(self.NAMES, {}, M.match_aa(self.REAL, F.IDS))
        self.assertIn("long_context", pv)                                     # lcr
        self.assertEqual(pv["long_context"]["codex"][0], 10.0)
        self.assertIn("lcr", pv["long_context"]["claude"][1])
        self.assertIn("hle", pv["analysis"]["claude"][1])
        self.assertIn("scicode", pv["coding"]["claude"][1])
        self.assertIn("terminalbench_v4_0", pv["debugging"]["antigravity"][1])
        self.assertNotIn("coding_index", pv["coding"]["claude"][1])           # solo gemini lo tiene: no cubre a todos
        self.assertNotIn("math", pv)                                          # ningún benchmark de math cubre a los tres
        self.assertLess(pv["debugging"]["antigravity"][0], pv["debugging"]["codex"][0] - 3)   # 0,197 vs 0,48 en terminal-bench

    def test_speed_relative_to_the_fastest_needs_everyone(self):
        aa = M.match_aa(F.aa_models(), F.IDS)
        sv = M.speed_values(self.NAMES, aa)
        self.assertEqual(sv["antigravity"][0], 10.0)                       # 200 tok/s: el más rápido
        self.assertAlmostEqual(sv["claude"][0], 7.36, places=2)             # 80 tok/s: 2,5× más lento = 1,32 duplicaciones
        self.assertAlmostEqual(sv["codex"][0], 5.36, places=2)             # 40 tok/s: 5× más lento
        aa.pop("codex")
        self.assertEqual(M.speed_values(self.NAMES, aa), {})
        self.assertEqual(M.speed_values(self.NAMES, {}), {})

    def test_cost_prefers_aa_then_arena_and_needs_everyone(self):
        aa = M.match_aa(F.aa_models(), F.IDS)
        cv = M.cost_values(self.NAMES, self.arena(), aa)
        self.assertEqual(cv["antigravity"][0], 10.0)
        self.assertEqual(cv["claude"][0], 6.0)                              # 4× más caro = 2 duplicaciones = −4 puntos
        self.assertTrue(cv["claude"][1].startswith("AA $"))
        arena_only = M.cost_values(self.NAMES, self.arena(), {})
        self.assertTrue(arena_only["claude"][1].startswith("Arena $"))
        self.assertEqual(M.cost_values(self.NAMES, self.arena(no_price=("gpt-6.1-sol-high", "gpt-6.1-sol-max")), {}), {})   # Arena no publica el precio de codex


class LogScaleTests(unittest.TestCase):
    def test_each_doubling_costs_two_points_and_it_floors_at_zero(self):
        self.assertEqual(M._log_value(1), 10.0)
        self.assertEqual(M._log_value(2), 8.0)
        self.assertEqual(M._log_value(4), 6.0)
        self.assertEqual(M._log_value(1000), 0.0)
        self.assertEqual(M._log_value(0.5), 10.0)                           # nunca por encima de 10

    def test_a_big_price_gap_cannot_beat_a_real_precision_gap_under_precision_priority(self):
        # el defecto que corrige la escala logarítmica: con la proporcional, 4× de precio valía 7,5 puntos
        self.assertGreater(M._log_value(4), 5)


class ActiveDataTests(Home):
    def test_bundled_snapshot_is_valid_and_complete(self):
        real = json.loads((ROOT / "ia_router" / "data" / "arena.json").read_text(encoding="utf-8"))
        self.assertRegex(real["fetched_at"], r"^\d{4}-\d{2}-\d{2}T")
        for sub, cat, _ in M.arena_pages():
            self.assertTrue(real["pages"].get(f"{sub}/{cat}"), f"falta {sub}/{cat} en la foto incluida")
        self.assertIn("CC BY 4.0", real["attribution"])
        self.assertGreaterEqual(len(real["pages"]["text/overall"][0]), 7)

    def test_without_cache_the_bundled_snapshot_rules(self):
        d = M.active()
        self.assertEqual(d["arena_at"], "2026-10-02T00:00:00")
        self.assertEqual(d["arena_origin"], "incluida en esta versión")
        self.assertIsNone(d["aa"])

    def test_a_newer_cache_wins_and_an_older_one_loses(self):
        M.save_cache({"pages": {"text/overall": [["m", 1, 1, 1, 1, None, None]]}, "arena_at": "2026-10-09T00:00:00"})
        self.assertEqual(M.active()["arena_origin"], "actualizada en tu máquina")
        M.save_cache({"pages": {"text/overall": [["m", 1, 1, 1, 1, None, None]]}, "arena_at": "2026-01-01T00:00:00"})
        self.assertEqual(M.active()["arena_origin"], "incluida en esta versión")

    def test_a_legacy_metrics_file_is_ignored_and_replaced_cleanly(self):
        M.cache_path().write_text(json.dumps({"models": {"claude": {"categories": {}}}, "pages": {"text/overall": [["m", 1, 1, 1, 1, None, None]]}, "arena_at": "2026-10-09T00:00:00"}))
        self.assertEqual(set(M.load_cache()), {"pages", "arena_at"})          # "models" (formato viejo) no pasa
        M.cache_path().write_text(json.dumps({"models": {"claude": {}}}))
        self.assertEqual(M.load_cache(), {})
        self.assertEqual(M.active()["arena_origin"], "incluida en esta versión")
        M.refresh(self.cfg, lambda s: None, F.fake_arena_get(F.sample_pages())[0], sleep=lambda s: None)
        self.assertNotIn("models", json.loads(M.cache_path().read_text()))

    def test_missing_snapshot_does_not_crash(self):
        self.snap.unlink()
        self.assertEqual(M.active()["pages"], {})
        self.assertEqual(M.status_line(), "sin métricas")

    def test_staleness_and_status_line(self):
        recent = {"arena_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "pages": {"a": []}}
        self.assertFalse(M.is_stale(recent))
        self.assertTrue(M.is_stale({"arena_at": "2020-01-01T00:00:00"}))
        self.assertTrue(M.is_stale({}))
        self.assertIn("Arena 2026-10-02 (incluida)", M.status_line())
        with_aa = dict(M.active(), aa=[{}], aa_at="2026-10-03T00:00:00")
        self.assertIn("Artificial Analysis 2026-10-03", M.status_line(with_aa))

    def test_known_ids_come_from_seen_then_the_log(self):
        self.assertEqual(M.known_model_ids(["claude"]), {"claude": None})
        state.remember_model_id("claude", "claude-sonnet-5-5")
        state.remember_model_id("codex", None)                         # no se guarda lo vacío
        (Path(self.tmp.name) / "log.jsonl").write_text(json.dumps({"model": "x", "model_id": "m-9"}) + "\n")
        self.assertEqual(M.known_model_ids(["claude", "codex", "x"]), {"claude": "claude-sonnet-5-5", "codex": None, "x": "m-9"})
        self.assertEqual(state.seen_ids(), {"claude": "claude-sonnet-5-5"})

    def test_matches_are_memoized_per_data_and_ids(self):
        d = M.active()
        a1 = M.matches(["claude", "codex"], F.IDS, {}, d)
        a2 = M.matches(["claude", "codex"], F.IDS, {}, d)
        self.assertIs(a1, a2)


class RefreshTests(Home):
    def setUp(self):
        super().setUp()
        self.get = F.fake_arena_get(F.sample_pages())

    def test_refresh_saves_in_the_user_cache_and_becomes_the_active_data(self):
        out = []
        c = M.refresh(self.cfg, out.append, self.get[0], sleep=lambda s: None)
        self.assertIn("text/coding", c["pages"])
        self.assertTrue(any("arena.ai" in m for m in out))
        self.assertEqual(M.active()["arena_origin"], "actualizada en tu máquina")
        self.assertTrue(M.cache_path().exists())

    def test_fresh_data_is_not_requested_again_unless_forced(self):
        out = []
        M.refresh(self.cfg, out.append, self.get[0], sleep=lambda s: None)
        n = len(self.get[1])
        again = M.refresh(self.cfg, out.append, self.get[0], sleep=lambda s: None)
        self.assertEqual(len(self.get[1]), n)
        self.assertIn("no vuelvo a consultar", out[-1])
        self.assertTrue(again["pages"])
        M.refresh(self.cfg, out.append, self.get[0], force=True, sleep=lambda s: None)
        self.assertGreater(len(self.get[1]), n)

    def test_without_aa_key_it_says_so(self):
        out = []
        M.refresh(self.cfg, out.append, self.get[0], sleep=lambda s: None)
        self.assertTrue(any("sin clave" in m and ".env.example" in m for m in out))
        self.assertIsNone(M.load_cache().get("aa"))

    def test_with_aa_key_it_adds_speed_and_price_data(self):
        seen = {}
        def get(url, headers=None, timeout=None):
            if "artificialanalysis" in url:
                seen["headers"] = headers
                return F.aa_api_payload()
            return self.get[0](url, headers, timeout)
        os.environ["ARTIFICIAL_ANALYSIS_API_KEY"] = "K123"
        out = []
        M.refresh(self.cfg, out.append, get, sleep=lambda s: None)
        self.assertEqual(seen["headers"], {"x-api-key": "K123"})
        aa = M.load_cache()["aa"]
        self.assertEqual(len(aa), 3)
        self.assertEqual(aa[0]["tps"], 80.0)
        self.assertEqual(aa[1]["price_blended"], 4.5)
        self.assertEqual(aa[0]["evals"]["artificial_analysis_coding_index"], 60)
        self.assertIsNotNone(M.active()["aa"])

    def test_adding_the_key_later_fetches_only_aa(self):
        M.refresh(self.cfg, lambda s: None, self.get[0], sleep=lambda s: None)
        n = len(self.get[1])
        def get(url, headers=None, timeout=None):
            assert "artificialanalysis" in url, "no debía volver a pedir Arena"
            return F.aa_api_payload()
        M.refresh(self.cfg, lambda s: None, get, key="K", sleep=lambda s: None)
        self.assertEqual(len(self.get[1]), n)
        self.assertTrue(M.load_cache()["aa"])

    def test_aa_failure_does_not_break_arena(self):
        def get(url, headers=None, timeout=None):
            if "artificialanalysis" in url:
                raise M.HttpError(401, url)
            return self.get[0](url, headers, timeout)
        out = []
        M.refresh(self.cfg, out.append, get, key="MALA", sleep=lambda s: None)
        self.assertTrue(any("no se pudo consultar" in m for m in out))
        self.assertTrue(M.load_cache()["pages"])

    def test_unexpected_aa_payload_is_reported(self):
        with self.assertRaises(ValueError):
            M.fetch_aa("K", lambda url, headers=None, timeout=None: json.dumps({"hola": 1}), sleep=lambda s: None)

    def test_total_failure_raises_and_keeps_the_previous_data(self):
        M.save_cache({"pages": {"text/overall": [["m", 1, 1, 1, 1, None, None]]}, "arena_at": "2020-01-01T00:00:00"})
        with self.assertRaises(OSError):
            M.refresh(self.cfg, lambda s: None, F.fake_arena_get(F.sample_pages(), fail={"arena.ai": 500})[0], sleep=lambda s: None)
        self.assertEqual(M.load_cache()["arena_at"], "2020-01-01T00:00:00")


if __name__ == "__main__":
    unittest.main()
