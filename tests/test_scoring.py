import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

import fixtures as F  # noqa: E402
from ia_router import core, metrics as M, priorities, scoring as S, state  # noqa: E402
from ia_router.select import Option  # noqa: E402

NAMES = ["claude", "codex", "antigravity"]


class Home(unittest.TestCase):
    """Isolated state, the toy Arena snapshot instead of the real one, and (optionally) the ids of the already learned models."""
    learn_ids = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["ROUTER_HOME"] = self.tmp.name
        for k in list(os.environ):
            if k.startswith(("FAKE_", "ROUTER_CMD_", "ARTIFICIAL_")):
                del os.environ[k]
        self.snap = Path(self.tmp.name) / "snapshot.json"
        self.snap.write_text(json.dumps(F.snapshot()))
        p = mock.patch.object(M, "SNAPSHOT", self.snap)
        p.start()
        self.addCleanup(p.stop)
        M._memo.clear()
        if self.learn_ids:
            for n, mid in F.IDS.items():
                state.remember_model_id(n, mid)
        self.cfg = core.load_config(apply_scoring=False)

    def tearDown(self):
        self.tmp.cleanup()

    def with_aa(self, **kw):
        M.save_cache({"aa": F.aa_models(**kw), "aa_at": "2026-10-03T00:00:00"})
        M._memo.clear()

    def best(self, cat, cfg=None):
        cfg = cfg or core.load_config()
        return max(NAMES, key=lambda n: cfg["models"][n]["_scoring"][cat]["score"])


class WeightTests(unittest.TestCase):
    def test_defaults_presets_and_groups(self):
        self.assertEqual(S.weights_for({}, "coding"), S.DEFAULT_WEIGHTS)
        self.assertEqual(S.weights_for({}, "quick"), S.QUICK_WEIGHTS)
        self.assertGreater(S.QUICK_WEIGHTS["speed"], S.DEFAULT_WEIGHTS["speed"])
        p = {"priorities": {"coding": "speed", "quick": "precision"}}
        self.assertEqual(S.weights_for(p, "coding"), S.PRESETS["speed"])
        self.assertEqual(S.weights_for(p, "debugging"), S.PRESETS["speed"])          # same group
        self.assertEqual(S.weights_for(p, "writing"), S.DEFAULT_WEIGHTS)             # no answer: default
        self.assertEqual(S.weights_for(p, "quick"), S.PRESETS["precision"])
        self.assertEqual(S.group_of("research"), "analysis")
        self.assertIsNone(S.group_of("multimodal"))

    def test_every_weight_set_sums_to_one(self):
        for w in list(S.PRESETS.values()) + [S.DEFAULT_WEIGHTS, S.QUICK_WEIGHTS]:
            self.assertAlmostEqual(sum(w.values()), 1.0, places=2)

    def test_unknown_choice_falls_back_to_default(self):
        self.assertEqual(S.weights_for({"priorities": {"coding": "inventado"}}, "coding"), S.DEFAULT_WEIGHTS)

    def test_profile_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["ROUTER_HOME"] = d
            S.save_profile({"priorities": {"coding": "cost"}})
            self.assertEqual(S.load_profile()["priorities"], {"coding": "cost"})
            self.assertIn("updated", S.load_profile())


class BuildTests(Home):
    def test_without_ids_nothing_changes(self):
        for n in NAMES:
            (Path(self.tmp.name) / "models_seen.json").unlink(missing_ok=True) if False else None
        os.remove(Path(self.tmp.name) / "models_seen.json")
        M._memo.clear()
        cfg = core.load_config()
        self.assertFalse(cfg.get("_scored"))
        self.assertEqual(cfg["models"], json.loads((ROOT / "ia_router" / "data" / "models.json").read_text())["models"])
        self.assertEqual(S.build(cfg)["missing_ids"], NAMES)
        self.assertIn("There are no metrics covering your models yet", S.render_table(cfg))

    def test_with_ids_the_scores_come_from_the_metrics(self):
        cfg = core.load_config()
        self.assertTrue(cfg["_scored"])
        d = cfg["models"]["claude"]["_scoring"]["coding"]
        self.assertEqual(d["basis"], "metrics")
        self.assertTrue(d["srcs"]["precision"].startswith("Arena text/coding"))
        self.assertEqual(cfg["models"]["claude"]["strengths"]["coding"], d["score"])
        self.assertEqual(cfg["models"]["claude"]["_prior_strengths"]["coding"], 9)    # the hand estimate is kept
        self.assertEqual(cfg["models"]["claude"]["_scoring"]["math"]["basis"], "estimated")   # Arena does not cover math for claude

    def test_dimensions_without_data_do_not_weigh(self):
        b = S.build(self.cfg)
        self.assertEqual(b["available"], {"speed": False, "cost": True})          # Arena's prices cover everyone; speed does not
        d = b["table"]["claude"]["coding"]
        self.assertIsNone(d["values"]["speed"])
        self.assertAlmostEqual(sum(d["weights"].values()), 1.0, places=2)
        self.assertEqual(set(d["weights"]), {"precision", "cost"})

    def test_cost_needs_every_model_priced(self):
        self.snap.write_text(json.dumps(F.snapshot(no_price=("gpt-6.1-sol-high", "gpt-6.1-sol-max"))))
        M._memo.clear()
        b = S.build(self.cfg)
        self.assertFalse(b["available"]["cost"])
        self.assertEqual(set(b["table"]["claude"]["coding"]["weights"]), {"precision"})

    def test_artificial_analysis_adds_speed(self):
        self.with_aa()
        b = S.build(self.cfg)
        self.assertTrue(b["available"]["speed"])
        d = b["table"]["antigravity"]["coding"]
        self.assertEqual(d["values"]["speed"], 10.0)
        self.assertEqual(set(d["weights"]), {"precision", "speed", "cost"})
        self.assertIn("tok/s", d["srcs"]["speed"])

    def test_priorities_change_the_winner_of_that_task_only(self):
        self.with_aa()
        S.save_profile({"priorities": {"coding": "precision"}})
        cfg = core.load_config()
        self.assertEqual(self.best("coding", cfg), "claude")                   # the most accurate (even if it is not the fastest or the cheapest)
        for choice in ("speed", "cost"):
            S.save_profile({"priorities": {"coding": choice}})
            self.assertEqual(self.best("coding", core.load_config()), "antigravity", choice)   # the fastest and the cheapest
        S.save_profile({"priorities": {"coding": "precision"}})
        self.assertEqual(self.best("writing", core.load_config()), self.best("writing", core.load_config(apply_scoring=True)))
        S.save_profile({})
        default_writing = self.best("writing", core.load_config())
        S.save_profile({"priorities": {"coding": "speed"}})
        self.assertEqual(self.best("writing", core.load_config()), default_writing)   # another category: untouched

    def test_precision_priority_picks_the_most_precise_even_when_another_is_cheaper_and_faster(self):
        self.with_aa()
        S.save_profile({"priorities": {"coding": "precision", "writing": "precision"}})
        cfg = core.load_config()
        for cat in ("coding", "writing"):
            d = {n: cfg["models"][n]["_scoring"][cat] for n in NAMES}
            top = max(d[n]["values"]["precision"] for n in NAMES)
            winner = self.best(cat, cfg)
            self.assertNotEqual(winner, "antigravity", cat)                       # it is the fastest and the cheapest, but clearly less accurate…
            self.assertGreater(d["antigravity"]["values"]["speed"], d[winner]["values"]["speed"])
            self.assertGreater(d["antigravity"]["values"]["cost"], d[winner]["values"]["cost"])
            self.assertGreater(top - d["antigravity"]["values"]["precision"], 1.0)  # …(more than 1 point of difference)
            self.assertGreaterEqual(d[winner]["values"]["precision"], top - 0.5, cat)   # and the winner is among the most accurate

    def test_router_uses_the_scores(self):
        self.with_aa()
        S.save_profile({"priorities": {"coding": "speed"}})
        d = core.route("Fix this bug in my Python function", core.load_config())
        self.assertEqual(d["chosen"], "antigravity")
        self.assertTrue(d["metrics"])

    def test_fewer_than_two_enabled_models_is_left_alone(self):
        cfg = core.load_config(apply_scoring=False)
        cfg["models"]["codex"]["enabled"] = False
        cfg["models"]["antigravity"]["enabled"] = False
        self.assertFalse(S.apply_to_config(cfg).get("_scored"))

    def test_disabled_models_are_not_part_of_the_comparison(self):
        cfg = core.load_config(apply_scoring=False)
        cfg["models"]["antigravity"]["enabled"] = False
        S.apply_to_config(cfg)
        self.assertEqual(set(S.build(cfg)["table"]), {"claude", "codex"})

    def test_override_in_models_json_forces_the_leaderboard_entry(self):
        cfg = core.load_config(apply_scoring=False)
        cfg["models"]["codex"]["external"] = {"arena": "gpt-6.1-sol-max"}
        b = S.build(cfg)
        self.assertEqual(b["arena"]["codex"]["name"], "gpt-6.1-sol-max")


class PresentationTests(Home):
    def test_table_marks_the_winner_and_estimates(self):
        table = S.render_table(core.load_config())
        self.assertIn("coding", table.splitlines()[2] + table)
        self.assertRegex(table, r"coding\s+[\d.]+\*?\s")
        self.assertRegex(table, r"math\s+.*e")
        self.assertIn("speed: your Artificial Analysis key is missing (.env)", table)
        self.assertIn("Attribution: Arena", table)
        self.assertIn("Dimensions with data: accuracy, cost", table)

    def test_table_with_aa_mentions_speed_and_attribution(self):
        self.with_aa()
        table = S.render_table(core.load_config())
        self.assertIn("accuracy, speed, cost", table)
        self.assertNotIn("key is missing", table)
        self.assertIn("Artificial Analysis (artificialanalysis.ai)", table)

    def test_explain_shows_weights_and_sources(self):
        self.with_aa()
        text = S.explain(core.load_config(), "coding")
        self.assertIn("Σ weight × value", text)
        self.assertIn("speed", text)
        self.assertIn("AA", text)
        self.assertIn("No breakdown", S.explain(core.load_config(), "inexistente"))

    def test_describe_sources(self):
        text = S.describe_sources(core.load_config())
        self.assertIn("claude-sonnet-5-5", text)
        self.assertIn("claude-sonnet-5.5-xhigh", text)
        self.assertIn("⚠", text)                                   # approximate effort variant
        self.assertIn("gemini-3.8-flash-high", text)
        self.assertIn("Artificial Analysis is not active", text)
        self.assertNotIn("⚠ approximate: your CLI", text)
        self.assertIn("CC BY 4.0", text)
        self.with_aa()
        active = S.describe_sources(core.load_config())
        self.assertNotIn("is not active", active)
        self.assertIn("Artificial Analysis → Claude Sonnet 5.5", active)
        self.assertIn("⚠ approximate: your CLI does not report its effort level", active)    # claude and codex do not report effort; gemini does

    def test_describe_sources_reports_unknown_models(self):
        os.remove(Path(self.tmp.name) / "models_seen.json")
        M._memo.clear()
        self.assertIn("unknown", S.describe_sources(core.load_config(apply_scoring=False)))


class DiffTests(unittest.TestCase):
    def table(self, **scores):
        return {n: {c: {"score": v} for c, v in cats.items()} for n, cats in scores.items()}

    def test_reports_a_new_winner_and_moves(self):
        old = self.table(claude={"coding": 9.0, "writing": 9.0}, codex={"coding": 9.5, "writing": 8.0})
        new = self.table(claude={"coding": 9.9, "writing": 9.1}, codex={"coding": 9.4, "writing": 8.0})
        out = S.diff_tables(old, new)
        self.assertEqual(len(out), 1)
        self.assertIn("coding: now picks claude (was codex)", out[0])
        self.assertIn("claude 9.0→9.9", out[0])

    def test_reports_big_moves_without_a_new_winner(self):
        old = self.table(claude={"writing": 9.0}, codex={"writing": 8.0})
        self.assertEqual(S.diff_tables(old, self.table(claude={"writing": 9.0}, codex={"writing": 7.5})), ["writing: codex 8.0→7.5"])

    def test_no_changes_and_no_baseline(self):
        t = self.table(claude={"coding": 9.0}, codex={"coding": 8.0})
        self.assertEqual(S.diff_tables(t, t), [])
        self.assertEqual(S.diff_tables(None, t), [])
        self.assertEqual(S.diff_tables({}, t), [])


class RefreshReportTests(Home):
    def new_data(self):
        pages = F.sample_pages()
        for key in ("text/coding", "text/overall"):   # the code ranking flips
            pages[key] = [[r[0], 1000 + (2000 - r[1]), r[2], r[3], r[4], r[5], r[6]] if r[0].startswith("gpt") else r for r in pages[key]]
        return pages

    def fake_refresh(self, cfg, say=print, get=None, key=None, force=False, sleep=None):
        M.save_cache({"pages": self.new_data(), "arena_at": "2026-10-09T00:00:00"})
        say("Reading the arena.ai leaderboards…")

    def test_report_shows_each_step_and_what_changed(self):
        out = []
        with mock.patch.object(M, "refresh", self.fake_refresh):
            self.assertTrue(S.refresh_and_report(self.cfg, out.append))
        text = "\n".join(out)
        self.assertIn("Reading the arena.ai leaderboards", text)
        self.assertIn("Metrics up to date: Arena 2026-10-09 (updated)", text)
        self.assertIn("What changed in the routing", text)
        self.assertIn("now picks", text)

    def test_report_says_when_nothing_changes(self):
        out = []
        with mock.patch.object(M, "refresh", lambda *a, **k: None):
            S.refresh_and_report(self.cfg, out.append)
        self.assertIn("The routing does not change with this data.", "\n".join(out))

    def test_failure_keeps_the_previous_metrics_and_says_so(self):
        out = []
        with mock.patch.object(M, "refresh", side_effect=OSError("sin red")):
            self.assertFalse(S.refresh_and_report(self.cfg, out.append))
        self.assertIn("Continuing with the ones you had", out[-1])
        self.assertIn("sin red", out[-1])

    def test_no_diff_without_known_model_ids(self):
        os.remove(Path(self.tmp.name) / "models_seen.json")
        out = []
        with mock.patch.object(M, "refresh", self.fake_refresh):
            S.refresh_and_report(self.cfg, out.append)
        self.assertNotIn("Qué cambió", "\n".join(out))

    def test_end_to_end_with_a_fake_network(self):
        out = []
        get = F.fake_arena_get(self.new_data())[0]
        self.assertTrue(S.refresh_and_report(self.cfg, out.append, get=get, sleep=lambda s: None))
        self.assertEqual(M.active()["arena_origin"], "updated on your machine")


class PrioritiesTests(Home):
    def scripted(self, answers):
        it, calls = iter(answers), []
        def choose(title, options, default=0, subtitle="", step="", color=True):
            calls.append({"title": title, "labels": [o.label for o in options], "default": default, "subtitle": subtitle, "step": step})
            return next(it)
        return choose, calls

    def test_options_depend_on_the_available_data(self):
        self.assertEqual(priorities.options_for({"speed": False, "cost": False}), ["precision", "balanced"])
        self.assertEqual(priorities.options_for({"speed": True, "cost": True}), ["precision", "balanced", "speed", "cost"])
        self.assertEqual(priorities.options_for({"speed": False, "cost": True}), ["precision", "balanced", "cost"])

    def test_full_flow_saves_the_answers(self):
        self.with_aa()
        choose, calls = self.scripted([2, 0, 3, 1, 0, 0])      # code→speed, writing→accuracy, analysis→cost, math→balanced, quick→accuracy, save
        out = []
        p = priorities.run(core.load_config(apply_scoring=False), choose, out.append, color=False)
        self.assertEqual(p["priorities"], {"coding": "speed", "writing": "precision", "analysis": "cost", "math": "balanced", "quick": "precision"})
        self.assertEqual(S.load_profile()["priorities"], p["priorities"])
        self.assertEqual([c["step"] for c in calls], ["1/6", "2/6", "3/6", "4/6", "5/6", "6/6"])
        self.assertEqual(calls[0]["labels"], ["Accuracy", "Balanced", "Speed", "Cost"])
        self.assertIn("This is the routing", "\n".join(out))
        self.assertIn("Saved", out[-1])
        self.assertEqual(self.best("coding"), "antigravity")   # the change takes effect immediately

    def test_speed_is_not_offered_without_data_and_the_reason_is_shown(self):
        choose, calls = self.scripted([None])
        priorities.run(self.cfg, choose, lambda s: None, color=False)
        self.assertNotIn("Speed", calls[0]["labels"])
        self.assertIn("Cost", calls[0]["labels"])            # Arena's prices do cover everyone
        self.assertIn("your Artificial Analysis key is missing", calls[0]["subtitle"])

    def test_both_missing_reasons_are_shown(self):
        self.with_aa()
        self.snap.write_text(json.dumps(F.snapshot(no_price=("gpt-6.1-sol-high", "gpt-6.1-sol-max"))))
        M.save_cache({"aa": [dict(m, price_in=None, price_out=None, price_blended=None) for m in F.aa_models()], "aa_at": "2026-10-03T00:00:00"})
        M._memo.clear()
        choose, calls = self.scripted([None])
        priorities.run(self.cfg, choose, lambda s: None, color=False)
        self.assertIn("no portal publishes the price", calls[0]["subtitle"])
        self.assertIn("Speed", calls[0]["labels"])
        self.assertNotIn("Cost", calls[0]["labels"])

    def test_with_only_precision_there_is_nothing_to_ask_and_it_says_why(self):
        self.snap.write_text(json.dumps(F.snapshot(no_price=("gpt-6.1-sol-high", "gpt-6.1-sol-max"))))
        M._memo.clear()
        out = []
        choose = mock.Mock()
        self.assertIsNone(priorities.run(self.cfg, choose, out.append, color=False))
        choose.assert_not_called()
        self.assertIn("nothing to prioritize", "\n".join(out))
        self.assertIn(".env.example", "\n".join(out))
        self.assertFalse(S.profile_path().exists())

    def test_cancel_or_discard_changes_nothing(self):
        choose, _ = self.scripted([0, None])
        self.assertIsNone(priorities.run(self.cfg, choose, lambda s: None, color=False))
        self.assertEqual(S.load_profile(), {})
        choose, calls = self.scripted([0, 0, 0, 0, 0, 1])
        self.assertIsNone(priorities.run(self.cfg, choose, lambda s: None, color=False))
        self.assertEqual(calls[-1]["labels"], ["Save", "Discard"])
        self.assertFalse(S.profile_path().exists())

    def test_previous_answers_are_preselected(self):
        S.save_profile({"priorities": {"coding": "cost", "writing": "balanced"}})
        choose, calls = self.scripted([None])
        priorities.run(self.cfg, choose, lambda s: None, color=False)
        self.assertEqual(calls[0]["default"], calls[0]["labels"].index("Cost"))
        choose, calls = self.scripted([0, None])
        priorities.run(self.cfg, choose, lambda s: None, color=False)
        self.assertEqual(calls[1]["default"], 1)

    def test_preview_lists_the_pick_per_category(self):
        lines = priorities.preview(S.build(self.cfg)["table"])
        self.assertEqual(len(lines), len(S.GROUPS))
        self.assertTrue(any("coding →" in l and "debugging →" in l for l in lines))


if __name__ == "__main__":
    unittest.main()
