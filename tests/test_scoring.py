import json, os, pty, re, select, struct, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import adapters, calibrate as C, chat, core, criteria, manifest, scoring, select as S  # noqa: E402


def res(ok=True, output="", seconds=1.0, error=None, tokens=None, model_id="m-fake"):
    return {"ok": ok, "output": output, "seconds": seconds, "error": error, "tokens": tokens or {"input": 100, "output": 10, "cached": 0, "reasoning": 0},
            "model_id": model_id, "rate_limited": error == "rate_limited", "auth_required": False}


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith(("FAKE_", "ROUTER_CMD_")):
                del os.environ[k]
        self.cfg = core.load_config(apply_manifest=False)

    def tearDown(self):
        self.tmp.cleanup()


class ParsingTests(unittest.TestCase):
    def test_sections_variants(self):
        t = "### 1\nuno\n## 2.\ndos\n### 3)\n```python\nx=1\n```"
        self.assertEqual(C.sections(t), {1: "uno", 2: "dos", 3: "```python\nx=1\n```"})
        self.assertEqual(C.sections("sin secciones"), {})
        self.assertEqual(C.sections(""), {})

    def test_first_number(self):
        for text, exp in (("42", 42), ("El resultado es 1.234", 1234), ("1.234,5", 1234.5), ("3.5", 3.5), ("-7", -7), ("3,25", 3.25)):
            self.assertEqual(C.first_number(text), exp, text)
        self.assertIsNone(C.first_number("nada"))

    def test_num_check_accepts_first_or_last_number(self):
        chk = C.num_check(1184)
        self.assertTrue(chk("1184"))
        self.assertTrue(chk("25 × 34 + 334 = 1184"))
        self.assertTrue(chk("1184 (porque 25×34 = 850)"))
        self.assertFalse(chk("25 × 34 + 334 = 1185"))
        self.assertFalse(chk("entre 7 y 1184 hay 16 pasos y 9 más"))  # el esperado queda en el medio: no vale
        self.assertFalse(chk(""))

    def test_word_check_ignores_accents_and_rejects_extra_names(self):
        self.assertTrue(C.word_check("miércoles")("Será miercoles."))
        self.assertFalse(C.word_check("Ana", ["Beto"])("Ana o Beto"))
        self.assertFalse(C.word_check("Ana")("Analía"))

    def test_extract_code(self):
        self.assertEqual(C.extract_code("```python\nx = 1\n```"), "x = 1")
        self.assertEqual(C.extract_code("x = 1"), "x = 1")


class CodeGraderTests(unittest.TestCase):
    CASES = [[[1, 2], 3], [[5, 5], 10]]

    def test_correct_wrong_and_broken_code(self):
        self.assertTrue(C.run_tests("def f(l):\n    return sum(l)", "f", [[[[1, 2]], 3], [[[5, 5]], 10]]))
        self.assertFalse(C.run_tests("def f(l):\n    return 0", "f", [[[[1, 2]], 3]]))
        self.assertFalse(C.run_tests("def f(l) return", "f", [[[[1]], 1]]))       # error de sintaxis
        self.assertFalse(C.run_tests("def f(l):\n    raise ValueError", "f", [[[[1]], 1]]))
        self.assertFalse(C.run_tests("", "f", []))
        self.assertFalse(C.run_tests("def g(l):\n    return 1", "f", [[[[1]], 1]]))  # nombre equivocado

    def test_infinite_loop_times_out(self):
        with mock.patch.object(C, "CODE_TIMEOUT", 1):
            t0 = time.time()
            self.assertFalse(C.run_tests("def f(l):\n    while True: pass", "f", [[[[1]], 1]]))
            self.assertLess(time.time() - t0, 4)

    def test_tuples_and_lists_compare_equal_through_json(self):
        self.assertTrue(C.run_tests("def f(n):\n    return (n, n + 1)", "f", [[[1], [1, 2]]]))

    def test_every_reference_passes_and_every_bug_fails(self):
        for name, sig, spec, cases, ref in C.CODING_EASY + C.CODING_HARD:
            self.assertTrue(C.run_tests(ref, name, C._expected(ref, name, cases)), name)
        for name, sig, spec, buggy, cases, ref in C.DEBUG_EASY + C.DEBUG_HARD:
            tests = C._expected(ref, name, cases)
            self.assertTrue(C.run_tests(ref, name, tests), name)
            self.assertFalse(C.run_tests(buggy, name, tests), f"el bug de {name} no se detecta")


class BatchTests(unittest.TestCase):
    def batches(self, seed, rnd):
        with tempfile.TemporaryDirectory() as d:
            return C.build_batches(seed, rnd, d), d

    def test_all_graders_accept_reference_and_reject_empty(self):
        with tempfile.TemporaryDirectory() as d:
            for seed in (1, 2):
                for rnd in range(3):
                    for b in C.build_batches(seed, rnd, d):
                        secs = C.sections(C.perfect_answer(b))
                        self.assertEqual(len(secs), len(b.items))
                        for i, it in enumerate(b.items, 1):
                            self.assertTrue(it.check(secs[i]), (seed, rnd, b.key, i, it.ref[:60]))
                            self.assertFalse(it.check(""), (seed, rnd, b.key, i))

    def test_categories_covered(self):
        with tempfile.TemporaryDirectory() as d:
            cats = {it.category for b in C.build_batches(1, 0, d) for it in b.items}
        self.assertEqual(cats, {"coding", "debugging", "math", "data", "long_context", "writing", "analysis", "multimodal"})

    def test_deterministic_by_seed_and_varied_across_seeds_and_rounds(self):
        with tempfile.TemporaryDirectory() as d:
            p = lambda s, r: [b.prompt for b in C.build_batches(s, r, d)]
            self.assertEqual(p(5, 0), p(5, 0))
            self.assertNotEqual(p(5, 0), p(6, 0))
            self.assertNotEqual(p(5, 0), p(5, 1))

    def test_context_grows_with_rounds(self):
        with tempfile.TemporaryDirectory() as d:
            sizes = [len(next(b for b in C.build_batches(1, r, d) if b.key == "context").prompt) for r in range(3)]
        self.assertTrue(sizes[0] < sizes[1] < sizes[2])

    @staticmethod
    def decode_png(path):
        import zlib
        raw = Path(path).read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = struct.unpack(">II", raw[16:24])
        i, idat = 8, b""
        while i < len(raw):
            n, t = struct.unpack(">I4s", raw[i:i + 8])
            if t == b"IDAT":
                idat += raw[i + 8:i + 8 + n]
            i += 12 + n
        data = zlib.decompress(idat)
        rows = [data[y * (1 + 3 * w) + 1:(y + 1) * (1 + 3 * w)] for y in range(h)]
        return w, h, [[tuple(r[x * 3:x * 3 + 3]) for x in range(w)] for r in rows]

    def test_vision_image_matches_the_expected_answers(self):
        with tempfile.TemporaryDirectory() as d:
            for seed in range(1, 8):
                b = C._vision_batch(__import__("random").Random(seed), 0, d)
                w, h, px = self.decode_png(Path(d) / "cuadrados_0.png")
                self.assertEqual((w, h), (144, 144))
                count = lambda color: sum(row.count(C._COLORS[color]) for row in px)
                self.assertEqual(count("rojo"), int(b.items[0].ref) * 144, seed)
                self.assertEqual(count("azul"), int(b.items[1].ref) * 144, seed)
                self.assertEqual(count("verde"), 144)
                self.assertEqual(count(b.items[2].ref), 400)              # el grande: 20×20
                ys = [y for y, row in enumerate(px) if C._COLORS["verde"] in row]
                self.assertEqual("arriba" if ys[0] < 72 else "abajo", b.items[3].ref)
                self.assertTrue(b.needs_files)

    def test_math_references_are_computed_not_guessed(self):
        for seed in range(30):
            items = C._math_items(__import__("random").Random(seed))
            self.assertEqual(len(items), 3)
            for q, exp, tol in items:
                self.assertIsInstance(exp, (int, float))
                self.assertTrue(C.num_check(exp, tol)(str(exp)))
        self.assertTrue(C.num_check(0.5, 0.011)("0.50"))
        self.assertFalse(C.num_check(7)("8"))

    def test_hard_reference_solutions_match_known_values(self):
        hard = {n: r for n, _, _, _, r in C.CODING_HARD}
        def call(name, *args):
            ns = {}
            exec(hard[name], ns)
            return ns[name](*args)
        self.assertEqual([call("n_reinas", n) for n in (1, 4, 5, 6, 8)], [1, 2, 10, 4, 92])
        self.assertEqual([call("particiones", n) for n in (0, 1, 4, 7, 20)], [1, 1, 5, 15, 627])
        self.assertEqual(call("distancia_edicion", "kitten", "sitting"), 3)
        self.assertEqual(call("subcadena_unica", "abba"), 2)
        self.assertEqual(call("calcular", "-(2+3)*4"), -20)
        self.assertEqual(call("calcular", "8/2/2"), 2)
        self.assertEqual(call("lru", 2, [["put", "a", 1], ["put", "b", 2], ["get", "a"], ["put", "c", 3], ["get", "b"], ["get", "c"], ["get", "a"]]), [1, -1, 3, 1])

    def test_every_batch_mixes_easy_and_hard(self):
        with tempfile.TemporaryDirectory() as d:
            code = C.build_batches(1, 0, d)[0]
        self.assertEqual([it.category for it in code.items], ["coding"] * 3 + ["debugging"] * 2)
        easy = {n for n, *_ in C.CODING_EASY}
        self.assertTrue(any(f"`{n}(" in code.prompt for n in easy))
        self.assertTrue(any(f"`{n}(" in code.prompt for n, *_ in C.CODING_HARD))

    def test_permutation_puzzle_has_a_unique_answer(self):
        import itertools
        for seed in range(10):
            chosen, q, who, people = C._perm_puzzle(__import__("random").Random(seed))
            valid = [pm for pm in itertools.permutations(people) if all(f({p: i for i, p in enumerate(pm)}) for _, f in chosen)]
            self.assertTrue(valid)
            self.assertEqual({pm[q] for pm in valid}, {who})

    def test_context_has_distractors_and_an_update_after_the_original(self):
        with tempfile.TemporaryDirectory() as d:
            for seed in range(8):
                b = next(x for x in C.build_batches(seed, 0, d) if x.key == "context")
                self.assertIn("código de respaldo", b.prompt)
                self.assertIn("Actualización:", b.prompt)
                project = re.search(r"Actualización: el código de acceso del proyecto (\w+) cambió a (\d{4})", b.prompt)
                old = re.search(rf"\. El código de acceso del proyecto {project.group(1)} es (\d{{4}})\.", b.prompt) or re.search(rf"El código de acceso del proyecto {project.group(1)} es (\d{{4}})\.", b.prompt)
                self.assertLess(b.prompt.index(f"del proyecto {project.group(1)} es {old.group(1)}"), b.prompt.index("Actualización:"))
                self.assertIn(project.group(2), [it.ref for it in b.items])
                self.assertNotIn(old.group(1), [it.ref for it in b.items])


class RunTests(Home):
    def patch_run(self, behaviour):
        lookup = {}
        orig = C.build_batches

        def wrap(seed, rnd, work):
            bs = orig(seed, rnd, work)
            lookup.update({b.prompt: b for b in bs})
            return bs

        def fake(name, spec, prompt, **kw):
            if prompt == C.PING:
                return behaviour(name, None, kw)
            return behaviour(name, lookup[prompt], kw)
        return mock.patch.object(C, "build_batches", wrap), mock.patch.object(adapters, "run_cli", fake)

    def run_model(self, name, behaviour, rounds=1):
        a, b = self.patch_run(behaviour)
        with a, b:
            return C._run_model(name, self.cfg["models"][name], rounds, 1, lambda s: None)

    def test_perfect_model_gets_everything(self):
        e = self.run_model("claude", lambda n, b, kw: res(output="OK" if b is None else C.perfect_answer(b)))
        self.assertTrue(all(v["passed"] == v["total"] for v in e["categories"].values()), e["categories"])
        self.assertEqual(sum(v["total"] for v in e["categories"].values()), 5 + 6 + 3 + 4 + 3 + 4)
        self.assertEqual(e["model_id"], "m-fake")
        self.assertEqual(len(e["calls"]), 7)

    def test_wrong_answers_count_as_failures(self):
        e = self.run_model("codex", lambda n, b, kw: res(output="OK" if b is None else "### 1\nnada"))
        self.assertTrue(all(v["passed"] == 0 for v in e["categories"].values()))
        self.assertEqual(e["categories"]["coding"]["total"], 3)

    def test_rate_limited_batch_is_skipped_not_counted(self):
        def beh(n, b, kw):
            if b is None:
                return res(output="OK")
            return res(ok=False, error="rate_limited") if b.key == "math" else res(output=C.perfect_answer(b))
        e = self.run_model("claude", beh)
        self.assertNotIn("math", e["categories"])
        self.assertNotIn("data", e["categories"])
        self.assertTrue(any("math" in s for s in e["skipped"]))
        self.assertEqual(e["categories"]["coding"]["passed"], e["categories"]["coding"]["total"])

    def test_permission_blocked_batch_is_counted_as_failure_and_flagged(self):
        err = 'jetski: no output produced — a tool required the "command" permission that headless mode cannot prompt for, so it was auto-denied.'
        def beh(n, b, kw):
            if b is None:
                return res(output="OK")
            return res(ok=False, error=err) if b.key == "math" else res(output=C.perfect_answer(b))
        e = self.run_model("antigravity", beh)
        self.assertEqual(e["categories"]["math"], {"passed": 0, "total": 3, "blocked": 3})
        self.assertEqual(e["categories"]["data"]["blocked"], 3)
        self.assertEqual(e["categories"]["coding"]["blocked"], 0)
        out = []
        with mock.patch.object(C, "calibrate", return_value={"antigravity": e}), mock.patch.object(C, "estimate", return_value={"antigravity": {"calls": 1, "tokens_in": 1}}):
            C.run_with_confirmation(self.cfg, 1, ["antigravity"], 1, lambda q: True, out.append)
        self.assertIn("sin respuesta por permisos", "\n".join(out))

    def test_failed_ping_aborts_that_model(self):
        e = self.run_model("codex", lambda n, b, kw: res(ok=False, error="auth_required") if b is None else self.fail("no debería seguir"))
        self.assertEqual(e["categories"], {})
        self.assertIn("ping", e["skipped"][0])

    def test_antigravity_skips_vision_and_files_are_only_requested_for_it(self):
        seen = []
        def beh(n, b, kw):
            seen.append((b.key if b else "ping", kw.get("lean"), kw.get("extra_dirs")))
            return res(output="OK" if b is None else C.perfect_answer(b))
        e = self.run_model("claude", beh)
        self.assertIn(("vision", False, mock.ANY), seen)   # con archivos no va en modo liviano
        self.assertTrue(all(lean for k, lean, _ in seen if k not in ("vision",)))
        e2 = self.run_model("antigravity", lambda n, b, kw: res(output="OK" if b is None else C.perfect_answer(b)))
        self.assertIn("vision", e2["skipped"])
        self.assertNotIn("multimodal", e2["categories"])

    def test_rounds_multiply_questions(self):
        e = self.run_model("claude", lambda n, b, kw: res(output="OK" if b is None else C.perfect_answer(b)), rounds=3)
        self.assertEqual(e["categories"]["coding"]["total"], 9)
        self.assertEqual(len(e["calls"]), 1 + 18)

    def test_calibrate_saves_and_merges_metrics(self):
        a, b = self.patch_run(lambda n, b_, kw: res(output="OK" if b_ is None else C.perfect_answer(b_)))
        with a, b:
            C.calibrate(self.cfg, ["claude"], 1, 1, lambda s: None)
            C.calibrate(self.cfg, ["codex"], 1, 1, lambda s: None)
        data = C.load()
        self.assertEqual(set(data["models"]), {"claude", "codex"})

    def test_estimate(self):
        est = C.estimate(self.cfg, ["claude", "codex", "antigravity"], 1)
        self.assertEqual([est[n]["calls"] for n in est], [7, 7, 6])
        self.assertLess(est["claude"]["tokens_in"], est["codex"]["tokens_in"])
        self.assertEqual(C.estimate(self.cfg, ["codex"], 3)["codex"]["calls"], 19)

    def test_confirmation_declined_runs_nothing(self):
        out = []
        with mock.patch.object(adapters, "run_cli", side_effect=AssertionError("no debía llamar")):
            self.assertIsNone(C.run_with_confirmation(self.cfg, 1, ["claude"], 1, lambda q: False, out.append))
        self.assertIn("claude", "\n".join(out))
        self.assertIn("Cancelado", out[-1])

    def test_no_models_available(self):
        out = []
        self.assertIsNone(C.run_with_confirmation(self.cfg, 1, ["nada"], 1, lambda q: True, out.append))
        self.assertIn("No hay modelos", out[0])

    def test_lean_flags_are_inserted_only_in_lean_mode(self):
        spec = self.cfg["models"]["claude"]
        argv = adapters._with_lean("claude", spec, ["claude", "-p", "{prompt}"])
        self.assertEqual(argv[:3], ["claude", "--tools", ""])
        self.assertEqual(adapters._with_lean("codex", self.cfg["models"]["codex"], ["codex", "exec", "x"]), ["codex", "exec", "x"])  # codex: sin recorte (no ahorra y oculta el modelo)
        self.assertEqual(adapters._with_lean("antigravity", self.cfg["models"]["antigravity"], ["agy", "-p", "x"]), ["agy", "-p", "x"])


def metrics(models):
    """{modelo: (aciertos por categoría {cat: (ok, total)}, segundos medios)}"""
    out = {}
    for n, (cats, secs) in models.items():
        out[n] = {"model_id": f"{n}-v1", "categories": {c: {"passed": p, "total": t} for c, (p, t) in cats.items()},
                  "calls": [{"key": "ping", "ok": True, "seconds": 1}] + [{"key": f"b{i}", "ok": True, "seconds": secs} for i in range(6)]}
    return {"models": out}


class ScoringTests(Home):
    def prior(self):
        return {n: dict(s["strengths"]) for n, s in self.cfg["models"].items()}

    def test_no_evidence_leaves_strengths_untouched(self):
        before = json.dumps(self.cfg["models"], sort_keys=True)
        self.assertEqual(json.dumps(scoring.apply_to_config(self.cfg)["models"], sort_keys=True), before)
        self.assertFalse(self.cfg.get("_scored"))

    def test_profile_without_metrics_does_not_move_scores(self):
        t = scoring.compute(self.cfg, {"models": {}}, {}, {"weights": {"quality": 0.1, "speed": 0.8, "quota": 0.05, "reliability": 0.05}}, self.prior())
        for n in self.cfg["models"]:
            for cat, p in self.cfg["models"][n]["strengths"].items():
                self.assertAlmostEqual(t[n][cat]["score"], p, places=1, msg=(n, cat))

    def test_blocked_answers_show_in_the_quality_source(self):
        m = metrics({"antigravity": ({"math": (0, 3)}, 3)})
        m["models"]["antigravity"]["categories"]["math"]["blocked"] = 3
        t = scoring.compute(self.cfg, m, {}, {}, self.prior())
        self.assertIn("3 sin respuesta por permisos", t["antigravity"]["math"]["quality_src"])

    def test_perfect_ties_stay_close_whatever_the_prior(self):
        m = metrics({n: ({"coding": (5, 5)}, 3) for n in self.cfg["models"]})
        t = scoring.compute(self.cfg, m, {}, {"weights": {"quality": 1, "speed": 0, "quota": 0, "reliability": 0}}, self.prior())
        scores = [t[n]["coding"]["score"] for n in self.cfg["models"]]
        self.assertLess(max(scores) - min(scores), 0.8)

    def test_measured_quality_moves_score_and_shrinks_toward_prior(self):
        m = metrics({"claude": ({"coding": (5, 5)}, 3), "codex": ({"coding": (0, 5)}, 3), "antigravity": ({"coding": (5, 5)}, 3)})
        t = scoring.compute(self.cfg, m, {}, {"weights": {"quality": 1, "speed": 0, "quota": 0, "reliability": 0}}, self.prior())
        self.assertGreater(t["claude"]["coding"]["score"], t["codex"]["coding"]["score"] + 3)
        # con una sola pregunta no se llega a los extremos: manda el estimado
        m1 = metrics({"codex": ({"coding": (0, 1)}, 3)})
        t1 = scoring.compute(self.cfg, m1, {}, {"weights": {"quality": 1, "speed": 0, "quota": 0, "reliability": 0}}, self.prior())
        self.assertGreater(t1["codex"]["coding"]["score"], 5)
        self.assertTrue(t["claude"]["coding"]["quality_src"].startswith("medido 5/5"))
        self.assertEqual(t["claude"]["research"]["quality_src"], "estimado")

    def test_profile_weights_flip_the_winner(self):
        m = metrics({"claude": ({"writing": (4, 4)}, 20), "codex": ({"writing": (3, 4)}, 4), "antigravity": ({"writing": (2, 4)}, 10)})
        quality = {"weights": {"quality": 0.9, "speed": 0.05, "quota": 0.0, "reliability": 0.05}}
        speed = {"weights": {"quality": 0.2, "speed": 0.75, "quota": 0.0, "reliability": 0.05}}
        best = lambda prof: max(self.cfg["models"], key=lambda n: scoring.compute(self.cfg, m, {}, prof, self.prior())[n]["writing"]["score"])
        self.assertEqual(best(quality), "claude")
        self.assertEqual(best(speed), "codex")

    def test_speed_is_relative_to_the_fastest(self):
        m = metrics({"claude": ({}, 10), "codex": ({}, 5)})
        t = scoring.compute(self.cfg, m, {}, {}, self.prior())
        self.assertEqual(t["codex"]["coding"]["values"]["speed"], 10.0)
        self.assertEqual(t["claude"]["coding"]["values"]["speed"], 5.0)
        self.assertTrue(t["codex"]["coding"]["known"]["speed"])
        self.assertFalse(t["antigravity"]["coding"]["known"]["speed"])

    def test_telemetry_feeds_quota_and_reliability(self):
        stats = {"claude": {"runs": 5, "ok_rate": 0.4, "tokens_in": 5000, "tokens_out": 0}, "codex": {"runs": 5, "ok_rate": 1.0, "tokens_in": 500, "tokens_out": 0}}
        t = scoring.compute(self.cfg, {"models": {}}, stats, {}, self.prior())
        self.assertEqual(t["codex"]["coding"]["values"]["quota"], 10.0)
        self.assertEqual(t["claude"]["coding"]["values"]["quota"], 1.0)
        self.assertEqual(t["claude"]["coding"]["values"]["reliability"], 4.0)

    def test_few_runs_are_ignored(self):
        stats = {"claude": {"runs": 2, "ok_rate": 0.0, "tokens_in": 9, "tokens_out": 9}}
        t = scoring.compute(self.cfg, {"models": {}}, stats, {}, self.prior())
        self.assertFalse(t["claude"]["coding"]["known"]["reliability"])

    def test_spare_quota_subtracts(self):
        base = scoring.compute(self.cfg, {"models": {}}, {}, {}, self.prior())
        spared = scoring.compute(self.cfg, {"models": {}}, {}, {"spare": {"claude": 1.0}}, self.prior())
        self.assertAlmostEqual(base["claude"]["coding"]["score"] - spared["claude"]["coding"]["score"], 1.0, places=2)
        self.assertEqual(base["codex"]["coding"]["score"], spared["codex"]["coding"]["score"])

    def test_quick_defaults_to_speed_weighted(self):
        self.assertGreater(scoring.weights_for({}, "quick")["speed"], scoring.weights_for({}, "coding")["speed"])
        self.assertAlmostEqual(sum(scoring.weights_for({"weights": {"quality": 2, "speed": 2}}, "x").values()), 1.0)

    def test_general_category_averages_measured(self):
        m = metrics({"claude": ({"coding": (5, 5), "math": (5, 5), "writing": (5, 5)}, 3)})
        t = scoring.compute(self.cfg, m, {}, {}, self.prior())
        self.assertTrue(t["claude"]["general"]["quality_src"].startswith("promedio"))

    def test_tiebreak_reorders_only_close_candidates(self):
        cfg = {"models": {"a": {"_scoring": {"general": {"values": {"speed": 2, "quality": 9, "quota": 5}}}},
                          "b": {"_scoring": {"general": {"values": {"speed": 9, "quality": 8, "quota": 5}}}},
                          "c": {"_scoring": {"general": {"values": {"speed": 10, "quality": 1, "quota": 5}}}}}}
        rank = [{"name": "a", "score": 8.0, "usable": True}, {"name": "b", "score": 7.8, "usable": True}, {"name": "c", "score": 5.0, "usable": True}]
        out = scoring.tiebreak(list(rank), cfg, {}, "speed")
        self.assertEqual([r["name"] for r in out], ["b", "a", "c"])      # c queda fuera del empate
        self.assertEqual([r["name"] for r in scoring.tiebreak(list(rank), cfg, {}, None)], ["a", "b", "c"])
        self.assertEqual([r["name"] for r in scoring.tiebreak(list(rank), cfg, {}, "quality")], ["a", "b", "c"])

    def test_apply_to_config_writes_scores_and_keeps_priors(self):
        C.save(metrics({"claude": ({"coding": (5, 5)}, 3), "codex": ({"coding": (0, 5)}, 3)}))
        cfg = scoring.apply_to_config(json.loads((ROOT / "models.json").read_text()))
        self.assertTrue(cfg["_scored"])
        self.assertIn("_prior_strengths", cfg["models"]["claude"])
        self.assertGreater(cfg["models"]["claude"]["strengths"]["coding"], cfg["models"]["codex"]["strengths"]["coding"])

    def test_route_uses_measured_scores(self):
        C.save(metrics({"claude": ({"coding": (0, 5), "debugging": (0, 5)}, 3), "codex": ({"coding": (5, 5), "debugging": (5, 5)}, 3)}))
        d = core.route("Arreglá este bug en mi función Python", core.load_config())
        self.assertEqual(d["chosen"], "codex")

    def test_manifest_takes_precedence_over_scores(self):
        C.save(metrics({"claude": ({"coding": (0, 5)}, 3), "codex": ({"coding": (5, 5)}, 3)}))
        m = {"version": 1, "manager": "claude", "tasks": {c: {"prefer": ["claude", "codex", "antigravity"]} for c in manifest.CATEGORIES}, "disabled": []}
        manifest.save(m, "t")
        cfg = core.load_config()
        self.assertEqual(cfg["models"]["claude"]["strengths"]["coding"], 10)
        scored = core.load_config(apply_manifest=False)["models"]
        self.assertGreater(scored["codex"]["strengths"]["coding"], scored["claude"]["strengths"]["coding"])

    def test_render_table_and_explain(self):
        self.assertIn("Todavía no hay métricas", scoring.render_table(self.cfg))
        C.save(metrics({"claude": ({"coding": (5, 5)}, 3), "codex": ({"coding": (2, 5)}, 4), "antigravity": ({"coding": (4, 5)}, 5)}))
        cfg = core.load_config(apply_manifest=False)
        table = scoring.render_table(cfg)
        self.assertIn("coding", table)
        self.assertIn("claude", table.splitlines()[0])
        self.assertIn("m", table)
        text = scoring.explain(cfg, "coding")
        self.assertIn("medido 5/5", text)
        self.assertIn("calidad", text)
        self.assertIn("Sin desglose", scoring.explain(cfg, "inexistente"))

    def test_table_warns_when_everyone_aces_a_category(self):
        C.save(metrics({"claude": ({"coding": (5, 5), "math": (5, 5)}, 3), "codex": ({"coding": (5, 5), "math": (3, 5)}, 3)}))
        cfg = core.load_config(apply_manifest=False)
        self.assertEqual(scoring.ties(cfg), ["coding"])
        self.assertIn("Sin diferencias medidas", scoring.render_table(cfg))
        self.assertIn("coding", scoring.render_table(cfg).splitlines()[-1])

    def test_profile_roundtrip(self):
        scoring.save_profile({"weights": {"quality": 1}})
        self.assertEqual(scoring.load_profile()["weights"], {"quality": 1})
        self.assertIn("updated", scoring.load_profile())


class CriteriaTests(Home):
    def scripted(self, answers):
        it = iter(answers)
        calls = []
        def choose(title, options, default=0, subtitle="", step="", color=True):
            calls.append((title, [o.label for o in options], default))
            return next(it)
        return choose, calls

    def test_build_profile_maps_answers_to_explicit_weights(self):
        p = criteria.build_profile({"priority": 2, "coding": 0, "quick": 0, "spare": 1, "tiebreak": 1}, ["claude", "codex"])
        self.assertEqual(p["weights"], criteria.PRIORITIES[2][2])
        self.assertEqual(p["category_weights"]["coding"], criteria.CODING[0][2])
        self.assertEqual(p["category_weights"]["debugging"], criteria.CODING[0][2])
        self.assertEqual(p["spare"], {"claude": 1.0})
        self.assertEqual(p["tiebreak"], "speed")
        p2 = criteria.build_profile({"priority": 0, "coding": 1, "quick": 1, "spare": 0, "tiebreak": 0}, ["claude"])
        self.assertNotIn("coding", p2["category_weights"])
        self.assertNotIn("spare", p2)
        self.assertEqual(p2["tiebreak"], "quality")

    def test_all_weight_sets_sum_to_one(self):
        for _, _, w in criteria.PRIORITIES + [x for x in criteria.CODING if x[2]] + criteria.QUICK:
            self.assertAlmostEqual(sum(w.values()), 1.0, places=2)

    def test_full_flow_saves_profile(self):
        choose, calls = self.scripted([2, 0, 0, 2, 1, 0])  # velocidad, código máx. calidad, cortas rápido, cuidar codex, desempate velocidad, guardar
        out = []
        p = criteria.run(self.cfg, choose, out.append, color=False)
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[3][1][0], "Ninguna")
        self.assertEqual(calls[3][1][1:], list(self.cfg["models"]))
        saved = scoring.load_profile()
        self.assertEqual(saved["spare"], {"codex": 1.0})
        self.assertEqual(saved["tiebreak"], "speed")
        self.assertEqual(saved["answers"]["priority"], 2)
        self.assertEqual(p["weights"], criteria.PRIORITIES[2][2])
        self.assertIn("Pesos generales", "\n".join(out))

    def test_cancel_at_any_step_changes_nothing(self):
        choose, _ = self.scripted([0, None])
        self.assertIsNone(criteria.run(self.cfg, choose, lambda s: None, color=False))
        self.assertEqual(scoring.load_profile(), {})

    def test_discard_at_the_end_changes_nothing(self):
        choose, calls = self.scripted([0, 0, 0, 0, 0, 1])
        self.assertIsNone(criteria.run(self.cfg, choose, lambda s: None, color=False))
        self.assertEqual(calls[-1][1], ["Guardar", "Descartar"])
        self.assertFalse(scoring.profile_path().exists())

    def test_previous_answers_are_preselected(self):
        scoring.save_profile({"answers": {"priority": 3, "coding": 2, "quick": 1, "spare": 1, "tiebreak": 2}})
        choose, calls = self.scripted([None])
        criteria.run(self.cfg, choose, lambda s: None, color=False)
        self.assertEqual(calls[0][2], 3)

    def test_save_and_rebuild_manifest_keeps_previous_version(self):
        m = {"version": 1, "manager": "claude", "source": "manager LLM", "user_notes": "usá codex", "disabled": ["antigravity"],
             "tasks": {c: {"prefer": ["claude", "codex", "antigravity"], "why": "x"} for c in manifest.CATEGORIES}}
        manifest.save(m, "inicial")
        C.save(metrics({"claude": ({"coding": (0, 5), "debugging": (0, 5)}, 3), "codex": ({"coding": (5, 5), "debugging": (5, 5)}, 3)}))
        choose, calls = self.scripted([0, 0, 0, 0, 0, 1])  # guardar y rearmar
        criteria.run(core.load_config(apply_manifest=False), choose, lambda s: None, color=False)
        self.assertEqual(calls[-1][1], ["Guardar", "Guardar y rearmar el manifiesto", "Descartar"])
        new = manifest.load()
        self.assertEqual(new["tasks"]["coding"]["prefer"][0], "codex")
        self.assertEqual(new["source"], "criterios (puntaje objetivo)")
        self.assertEqual(new["user_notes"], "usá codex")
        self.assertEqual(new["disabled"], ["antigravity"])
        self.assertTrue(manifest.path().with_name("manifest.prev.json").exists())


class ChatCommandTests(Home):
    def say(self, line, answers=()):
        out, it = [], iter(answers)
        c = chat.Chat(read=lambda p: next(it), write=out.append)
        c.handle(line)
        return "\n".join(out)

    def test_scores_without_metrics_points_to_calibrate(self):
        self.assertIn("/calibrate", self.say("/scores"))

    def test_scores_with_metrics_and_breakdown(self):
        C.save(metrics({"claude": ({"coding": (5, 5)}, 3), "codex": ({"coding": (2, 5)}, 4)}))
        out = self.say("/scores coding")
        self.assertIn("claude", out)
        self.assertIn("Σ peso × valor", out)
        self.assertIn("medido 5/5", out)

    def test_scores_mentions_that_the_manifest_wins(self):
        C.save(metrics({"claude": ({"coding": (5, 5)}, 3)}))
        manifest.save({"version": 1, "manager": "claude", "tasks": {c: {"prefer": ["claude"]} for c in manifest.CATEGORIES}, "disabled": []}, "t")
        self.assertIn("manifiesto", self.say("/scores"))

    def test_calibrate_declined_spends_nothing(self):
        with mock.patch.object(adapters, "run_cli", side_effect=AssertionError("no debía llamar")):
            out = self.say("/calibrate", answers=["n"])
        self.assertIn("llamadas", out)
        self.assertIn("Cancelado", out)

    def test_calibrate_full_asks_for_three_rounds(self):
        out = self.say("/calibrate full", answers=["n"])
        self.assertIn("3 rondas", out)

    def test_criteria_command_runs_the_questionnaire(self):
        with mock.patch.object(criteria, "run", return_value=None) as run:
            self.say("/criteria")
        run.assert_called_once()

    def test_new_commands_are_in_the_palette_and_help(self):
        names = {c.name for c in chat.COMMANDS}
        self.assertTrue({"/calibrate", "/scores", "/criteria"} <= names)
        self.assertIn("/criteria", chat.HELP)


class SelectTests(unittest.TestCase):
    def test_state_navigation_wraps_and_confirms(self):
        st = S.SelectState(3, 0)
        st.apply(("key", "up"))
        self.assertEqual(st.sel, 2)
        st.apply(("key", "down"))
        self.assertEqual(st.sel, 0)
        st.apply(("text", "2"))
        self.assertEqual(st.sel, 1)
        st.apply(("text", "j"))
        self.assertEqual(st.sel, 2)
        st.apply(("text", "k"))
        self.assertEqual(st.sel, 1)
        self.assertEqual(st.apply(("key", "enter")), ("done", "1"))
        self.assertEqual(st.apply(("key", "esc")), ("cancel",))
        self.assertEqual(st.apply(("key", "interrupt")), ("cancel",))
        st.apply(("text", "9"))
        self.assertEqual(st.sel, 1)  # fuera de rango: se ignora

    def test_default_is_clamped(self):
        self.assertEqual(S.SelectState(2, 9).sel, 1)
        self.assertEqual(S.SelectState(2, -3).sel, 0)

    def test_render_marks_the_selected_option(self):
        lines = S.render_lines("¿Qué?", [S.Option("Uno", "a"), S.Option("Dos", "b")], 1, "ayuda", color=False, step="2/5")
        text = "\n".join(lines)
        self.assertIn("2/5", text)
        self.assertIn("> ◉ Dos", text)
        self.assertIn("  ○ Uno", text.replace("    ", "  "))
        self.assertIn("confirmar", text)

    def test_summary_line(self):
        self.assertEqual(S.summary_line("¿Qué?", "Dos", "2/5", color=False), "  ✔ 2/5  ¿Qué? › Dos")
        self.assertIn("Dos", S.summary_line("¿Qué?", "Dos", "2/5", color=True))

    def test_choose_without_a_tty_returns_default(self):
        with mock.patch.object(S, "interactive", return_value=False):
            self.assertEqual(S.choose("x", [S.Option("a"), S.Option("b")], default=1), 1)

    def run_pty(self, keys):
        code = ("import sys; sys.path.insert(0, %r)\nfrom ia_router import select as S\n"
                "r = S.choose('Pregunta', [S.Option('Uno','a'), S.Option('Dos','b'), S.Option('Tres','c')], color=False)\nprint('RESULT=%%r' %% (r,))\n") % str(ROOT)
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update(TERM="xterm-256color", COLUMNS="90", LINES="30")
            os.execvp(sys.executable, [sys.executable, "-c", code])
        buf = b""

        def pump(t):
            nonlocal buf
            end = time.time() + t
            while time.time() < end:
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        d = os.read(fd, 65536)
                    except OSError:
                        return
                    if not d:
                        return
                    buf += d
        pump(1.0)
        for k in keys:
            os.write(fd, k.encode())
            pump(0.3)
        pump(0.8)
        try:
            os.close(fd)
            os.waitpid(pid, 0)
        except OSError:
            pass
        return buf.decode(errors="replace")

    def test_pty_arrows_and_enter(self):
        out = self.run_pty(["\x1b[B", "\x1b[B", "\x1b[A", "\r"])
        self.assertIn("RESULT=1", out)
        self.assertIn("Pregunta", out)
        self.assertIn("Pregunta › Dos", out)  # la pregunta contestada queda resumida en una línea

    def test_pty_escape_cancels(self):
        self.assertIn("RESULT=None", self.run_pty(["\x1b"]))

    def test_pty_number_jumps(self):
        self.assertIn("RESULT=2", self.run_pty(["3", "\r"]))


if __name__ == "__main__":
    unittest.main()
