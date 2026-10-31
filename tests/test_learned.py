"""Regression cases harvested by the autolearner (python -m balatro learn): the game's real score must match."""
import json
from pathlib import Path
import unittest

from balatro.engine import score
from balatro.learn import CASES, game_over, plays
from balatro.models import State


class Learned(unittest.TestCase):
    def test_cases(self):
        for p in sorted(CASES.glob("*.json")):
            c = json.loads(p.read_text())
            with self.subTest(p.name):
                self.assertEqual(score(State.from_dict(c["state"]), c["idx"]).score, c["actual"])

    def test_pairing(self):
        def m(n, phase, score, hands, msg="", rnd=18):
            return {"n": n, "state": {"phase": phase, "score": score, "hands_left": hands, "round": rnd,
                                      "blind_chips": 40000}, "msg": msg, "ok": True}
        moves = [m(0, "playing", 0, 2, "Used: Justice"),  # consumable, not a play
                 m(1, "playing", 0, 2, "Played"), m(2, "playing", 28250, 1, "Played"),
                 m(3, "other", 28250, 0),  # scoring animation: old score, no hands - not game over
                 m(4, "cash_out", 61955, 0)]
        self.assertEqual([(a["n"], d) for a, d in plays(moves)], [(1, 28250), (2, 33705)])
        self.assertFalse(game_over(moves[-1]["state"]))
        self.assertTrue(game_over(moves[3]["state"]))  # looks lost: evaluate() only trusts it as the last frame

    def test_misclick_is_no_case(self):
        """Run 20261031-074053 #424: A A 10 10 2 planned, the click hit 4♥ instead of 10♣ - actually just a Pair (6906)."""
        def m(n, score, hands, hand, idx=None):
            return {"n": n, "state": {"phase": "playing", "score": score, "hands_left": hands, "round": 28,
                                      "blind_chips": 560000, "hand": [{"rank": r, "suit": s} for r, s in hand]},
                    "msg": "Played", "ok": True, "idx": idx}
        before = [("A", "S"), ("A", "D"), ("K", "D"), ("10", "S"), ("4", "H"), ("10", "C"), ("2", "D")]
        lost_4h = [("K", "D"), ("10", "C"), ("Q", "S"), ("10", "D"), ("8", "S"), ("8", "D"), ("2", "S")]
        self.assertEqual(plays([m(0, 186068, 3, before, [0, 1, 3, 5, 6]), m(1, 192974, 2, lost_4h)]), [])
        kept_4h = [("K", "D"), ("4", "H"), ("Q", "S"), ("10", "D"), ("8", "S"), ("8", "D"), ("2", "S")]
        self.assertEqual(len(plays([m(0, 186068, 3, before, [0, 1, 3, 5, 6]), m(1, 211000, 2, kept_4h)])), 1)

    def test_resume_run(self):
        import json, os, tempfile, time
        from balatro import live
        with tempfile.TemporaryDirectory() as d:
            live.RUNS, old = Path(d), live.RUNS
            try:
                run = Path(d) / "20261030-220724"
                run.mkdir()
                f = run / "moves.jsonl"
                f.write_text(json.dumps({"n": 0, "state": {"phase": "shop"}, "msg": ""}) + "\n")
                self.assertEqual(live.resume_run(), run)  # mid-run restart: keep logging there
                os.utime(f, (time.time() - 3600,) * 2)
                self.assertIsNone(live.resume_run())  # stale: a new run
                f.write_text(json.dumps({"n": 0, "state": {"phase": "game_over"}, "msg": ""}) + "\n")
                self.assertIsNone(live.resume_run())  # finished run: a new one
            finally:
                live.RUNS = old

    def test_auto_apply(self):
        """Tested Opus patches are taken without asking (user, 2026-10-31): applied, re-tested, committed with a
        CHANGELOG entry - or rolled back when the suite is red in the real tree."""
        import subprocess, tempfile
        from unittest import mock
        from balatro import learn
        with tempfile.TemporaryDirectory() as tmp:
            root, rd = Path(tmp) / "repo", Path(tmp) / "20261031-120000"
            root.mkdir(), rd.mkdir()
            git = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=root,
                                            check=True, capture_output=True, text=True).stdout
            (root / "a.py").write_text("x = 1\n")
            git("init", "-q"), git("add", "a.py"), git("commit", "-qm", "base")
            (rd / "opus.patch").write_text("--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")
            (rd / "opus.md").write_text("## Run\nx\n\n## Changes\n- a.py: x = 2\n\n## Tests\ngreen\n")
            env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@t"}
            with mock.patch.object(learn, "TESTS", ["nonexistent_suite"]):  # red: rolled back, nothing committed
                self.assertIn("rolled back", learn.apply(rd, root))
            self.assertEqual((root / "a.py").read_text(), "x = 1\n")
            with mock.patch.object(learn, "TESTS", []), mock.patch.dict("os.environ", env):
                self.assertIn("committed", learn.apply(rd, root))
            self.assertEqual((root / "a.py").read_text(), "x = 2\n")
            log = (root / "docs" / "CHANGELOG.md").read_text()
            self.assertIn("run 20261031-120000", log)
            self.assertIn("- a.py: x = 2", log)
            self.assertIn("auto-applied", git("log", "-1", "--format=%s"))


if __name__ == "__main__":
    unittest.main()
