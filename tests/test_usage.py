import json, os, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["PATH"] = str(ROOT / "tests" / "fake_bin") + os.pathsep + os.environ["PATH"]

from ia_router import chat, core, state, usage as U  # noqa: E402
import test_scoring  # noqa: E402

NOW = time.mktime(time.strptime("2026-10-08T12:00:00", "%Y-%m-%dT%H:%M:%S"))


def ev(model, hours_ago, tin=0, tout=0, error=None):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - hours_ago * 3600))
    return {"model": model, "ts": ts, "tokens": {"input": tin, "output": tout} if (tin or tout) else None, "error": error, "ok": error is None}


class SummaryTests(unittest.TestCase):
    def test_tokens_are_bucketed_by_window_day_and_week(self):
        s = U.summarize([ev("claude", 1, 100, 10), ev("claude", 4, 200, 20), ev("claude", 10, 300, 30), ev("claude", 30, 400, 40), ev("claude", 24 * 8, 999, 9)], NOW)["claude"]
        self.assertEqual(s["tokens_window"], 110 + 220)
        self.assertEqual(s["tokens_24h"], 110 + 220 + 330)
        self.assertEqual(s["tokens_7d"], 110 + 220 + 330 + 440)           # the 8-day-old run is out
        self.assertEqual(s["runs_7d"], 4)

    def test_without_a_rate_limit_there_is_no_reference_and_no_warning(self):
        s = U.summarize([ev("claude", 1, 1_000_000, 0)], NOW)
        self.assertIsNone(s["claude"]["observed_limit"])
        self.assertIsNone(s["claude"]["used_ratio"])
        self.assertEqual(U.warnings(s), [])

    def test_the_limit_is_what_was_used_in_the_window_before_a_rate_limit(self):
        evs = [ev("codex", 30, 400, 0), ev("codex", 29.5, 400, 0), ev("codex", 29, error="rate_limited"),     # a day ago: 800 tokens, then blocked
               ev("codex", 2, 500, 100)]                                                                          # now: 600 used in the last window
        s = U.summarize(evs, NOW)["codex"]
        self.assertEqual(s["observed_limit"], 800)
        self.assertEqual(s["tokens_window"], 600)
        self.assertEqual(s["used_ratio"], 0.75)
        self.assertEqual(s["rate_limits_7d"], 1)
        self.assertEqual(U.warnings({"codex": s}), [])                      # 75% is below the 80% warning line

    def test_it_warns_when_close_to_the_observed_limit_and_the_largest_ceiling_wins(self):
        evs = [ev("codex", 50, 300, 0), ev("codex", 49.9, error="rate_limited"),        # a smaller ceiling, long ago
               ev("codex", 30, 900, 0), ev("codex", 29.9, error="rate_limited"),        # the largest ceiling: 900
               ev("codex", 1, 800, 0)]
        s = U.summarize(evs, NOW)["codex"]
        self.assertEqual(s["observed_limit"], 900)
        text = U.warnings({"codex": s})
        self.assertEqual(len(text), 1)
        self.assertIn("89%", text[0])
        self.assertEqual(U.warnings({"codex": s}, names=["claude"]), [])        # only the models asked about

    def test_future_or_broken_timestamps_are_ignored(self):
        bad = {"model": "claude", "ts": "not a date", "tokens": {"input": 5}}
        self.assertEqual(U.summarize([ev("claude", -2, 100)], NOW)["claude"]["tokens_7d"], 0)
        self.assertEqual(U.table({}), ["No usage recorded yet (it builds up with every `ask`)."])
        self.assertTrue(bad)

    def test_the_table_lists_models_and_explains_its_assumptions(self):
        text = "\n".join(U.table(U.summarize([ev("claude", 1, 100, 10)], NOW)))
        self.assertIn("claude", text)
        self.assertIn("n/a", text)
        self.assertIn("assumption", text)
        self.assertIn("list-price proxy", text)


class LogTests(test_scoring.Home):
    def test_read_log_skips_damaged_lines_and_events_without_a_time(self):
        p = Path(self.tmp.name) / "log.jsonl"
        p.write_text("not json\n" + json.dumps({"model": "claude", "ts": "2026-10-08T10:00:00"}) + "\n" + json.dumps({"model": "claude"}) + "\n", encoding="utf-8")
        self.assertEqual(len(U.read_log()), 1)

    def test_a_real_run_shows_up_with_its_cost(self):
        self.with_aa()
        cfg = core.load_config()
        core.ask("Fix this bug in my Python function", cfg, model="codex")
        s = U.summarize(U.read_log(), cfg=cfg)
        self.assertGreater(s["codex"]["tokens_window"], 0)
        self.assertGreater(s["codex"]["cost_7d"], 0)

    def test_the_chat_command_and_the_after_answer_warning(self):
        out = []
        c = chat.Chat(read=lambda p="": "", write=out.append, color=False)
        c.handle("/usage")
        self.assertIn("No usage recorded yet", "\n".join(out))
        self.assertIn("/usage", chat.HELP)
        self.assertIn("/usage", [x.name for x in chat.COMMANDS])
        now = time.time()
        for hours_ago, tok, err in ((30, 1000, None), (29.9, 0, "rate_limited"), (1, 900, None)):
            e = ev("codex", hours_ago, tok, 0, err)
            e["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - hours_ago * 3600))
            with open(Path(self.tmp.name) / "log.jsonl", "a") as fh:
                fh.write(json.dumps(e) + "\n")
        out.clear()
        c.pinned = "codex"
        c.handle("Fix this bug in my Python function")
        self.assertIn("codex is at", "\n".join(out))


if __name__ == "__main__":
    unittest.main()
