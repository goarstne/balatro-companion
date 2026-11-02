"""Audit regressions and the new control/planning paths; no real game or pointer required."""
import json
import os
import struct
import subprocess
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from balatro import autopilot, learn
from balatro.autopilot import Autopilot, cash_out_button
from balatro.control import Control, request
from balatro.deckplan import advice, compare
from balatro.engine import score, stochastic
from balatro.localvision.layout import normalize
from balatro.localvision.telemetry import parse_tsv
from balatro.models import Card, Joker, State
from balatro.optimizer import best_card_order, play_now_odds, recommend
from balatro.strategy import boss_forecast, boss_state, estimate_round, required_score

ROOT = Path(__file__).resolve().parent.parent


class Improvements(unittest.TestCase):
    def test_telemetry_keeps_zero_and_one(self):
        row = lambda name, mult, chips, x: f"J\t{name}\t\t0\t0\t0\t0\t0.5\t0.2\t{mult}\t{chips}\t{x}\t-1\t\t\t\t0"
        d, _, _ = parse_tsv("\n".join([row("Ramen", 0, 0, 1), row("Popcorn", 0, 0, 1), row("Ice Cream", 0, 0, 1)]))
        self.assertEqual(d["jokers"][0]["vars"]["x"], 1)
        self.assertEqual(d["jokers"][1]["vars"]["mult"], 0)
        self.assertEqual(d["jokers"][2]["vars"]["chips"], 0)
        d["hand"] = ["AS"]
        self.assertEqual(score(State.from_dict(d), [0]).score, 16)

    def test_lucky_is_not_a_guaranteed_win(self):
        state = State(hand=[Card(14, "S", enhancement="lucky")], hands_left=1, discards_left=0, blind_chips=60)
        self.assertEqual(score(state, [0]).score, 80)
        chance, _ = play_now_odds(state, [0], 80, samples=1000, seed=5)
        self.assertTrue(0.16 < chance < 0.24, chance)
        self.assertNotIn("wins the blind", recommend(state, samples=40, reorder=False).reason)
        cat = replace(state, jokers=[Joker("Lucky Cat")])
        miss = SimpleNamespace(random=Mock(side_effect=[0.9, 0.9]))
        hit = SimpleNamespace(random=Mock(side_effect=[0.0, 0.9]))
        self.assertEqual(score(cat, [0], rng=miss).joker_vars[0]["x"], 1)
        self.assertEqual(score(cat, [0], rng=hit).joker_vars[0]["x"], 1.25)
        self.assertTrue(stochastic(State(hand=[], jokers=[Joker("Bull"), Joker("Business Card")])))

    def test_score_rejects_duplicate_and_invalid_cards(self):
        state = State.from_dict({"hand": ["AS"]})
        for indices in ([0, 0], [-1], [1], [True]):
            with self.subTest(indices=indices), self.assertRaises(ValueError):
                score(state, indices)

    def test_shop_advice_uses_the_execution_mode(self):
        from balatro.live import plan
        from balatro.autopilot import _shop_plan
        d = {"phase": "shop", "money": 49, "ante": 2, "hand": [], "hand_levels": {"Flush": 4},
             "jokers": ["Cavendish", "Gros Michel", "The Tribe"],
             "shop": [{"kind": "voucher", "name": "Director's Cut", "price": 10}]}
        self.assertIn("Buy nothing", plan(d, auto=True)[0])
        self.assertEqual(_shop_plan(State.from_dict(d), d).kind, "leave")
        self.assertIn("Buy Director's Cut", plan(d)[0])

    def test_card_order_improves_the_same_play(self):
        state = State(hand=[Card(14, "S", enhancement="glass"), Card(13, "H", enhancement="mult")],
                      jokers=[Joker("Splash")])
        order, result = best_card_order(state, [0, 1])
        self.assertEqual(order, (1, 0))
        self.assertGreater(result.score, score(state, [0, 1]).score)
        self.assertEqual(state.hand[0].enhancement, "glass")
        rec = recommend(replace(state, blind_chips=200, hands_left=1, discards_left=1))
        self.assertEqual(rec.action, "play")
        self.assertIsNotNone(rec.card_order)

    def test_shop_executes_the_displayed_plan_once(self):
        ap = Autopilot.__new__(Autopilot)
        ap._screen, ap._name_packs = Mock(return_value=(None, None)), Mock()
        ap.shop_fails = {}
        ap._shop_action = Mock(return_value=(True, "left"))
        d = {"phase": "shop", "telemetry": True, "shop": [], "hand": []}
        with patch.object(autopilot, "_shop_plan", side_effect=AssertionError("planned twice")):
            self.assertTrue(ap.shop(d, [{"kind": "leave", "index": -1, "why": "displayed plan"}])[0])
        self.assertEqual(ap._shop_action.call_args.args[2].why, "displayed plan")

    def test_drag_confirms_every_intermediate_order(self):
        ap = Autopilot.__new__(Autopilot)
        names = ["Joker", "Cavendish", "Mime"]
        def observed():
            return {"phase": "playing", "jokers": [{"name": name, "pos": [i, 0]} for i, name in enumerate(names)]}
        scr = SimpleNamespace(point=lambda x, y: (x, y))
        ap._screen = lambda: (scr, None)
        ap.focus, ap.park = (lambda *a: False), Mock()
        def drag(start, end):
            names.insert(int(end[0]), names.pop(int(start[0])))
        ap.ptr = SimpleNamespace(drag=Mock(side_effect=drag))
        ap._confirm = lambda done, **kw: done(observed())
        d = observed()
        desired = list(reversed(d["jokers"]))
        with patch.object(autopilot, "decode_strip", side_effect=lambda canvas: (observed(), {}, [])):
            self.assertTrue(ap.reorder(d, "jokers", desired)[0])
        self.assertEqual(names, ["Mime", "Cavendish", "Joker"])
        self.assertEqual(ap.ptr.drag.call_count, 2)
        ap._confirm = lambda *a, **kw: False
        d = observed()
        with patch.object(autopilot, "decode_strip", side_effect=lambda canvas: (observed(), {}, [])):
            self.assertFalse(ap.reorder(d, "jokers", list(reversed(d["jokers"])))[0])

    def test_drag_releases_on_motion_error(self):
        from balatro.pointer import BTN_LEFT, Pointer
        pointer = Pointer.__new__(Pointer)
        pointer._send, pointer.nudge, pointer._ms = Mock(), Mock(), lambda: 1
        with patch("balatro.pointer.move_cursor", side_effect=[None, RuntimeError("lost")]), patch("balatro.pointer.time.sleep"):
            with self.assertRaises(RuntimeError):
                pointer.drag((0, 0), (10, 0))
        payload = pointer._send.call_args_list[-2].args[2]
        self.assertEqual(struct.unpack("III", payload), (1, BTN_LEFT, 0))

    def test_death_moves_the_source_right_before_use(self):
        from balatro.packs import use_consumable
        d = {"hand": [{"rank": "K", "suit": "S", "seal": "red"}, {"rank": "2", "suit": "H"}], "telemetry": True}
        choice = use_consumable(State.from_dict(d), "Death", "tarot")
        ap = Autopilot.__new__(Autopilot)
        ap.reorder = Mock(return_value=(True, "reordered"))
        self.assertTrue(ap._death_order(d, choice, "Death")[0])
        desired = ap.reorder.call_args.args[2]
        self.assertEqual([c["rank"] for c in desired], ["2", "K"])
        self.assertFalse(ap._death_order({**d, "telemetry": False}, choice, "Death")[0])

    def test_cashout_waits_for_the_button(self):
        fixture = ROOT / "tests/fixtures"
        animation, ready = normalize(fixture / "cashout_animating.jpg"), normalize(fixture / "cashout_ready.jpg")
        self.assertIsNone(cash_out_button(animation))
        self.assertAlmostEqual(cash_out_button(ready)[0], 0.546, delta=0.01)
        ap = Autopilot.__new__(Autopilot)
        ap._screen = Mock(side_effect=[(None, animation), (None, ready)])
        ap.click, ap.park, ap._confirm = Mock(), Mock(), Mock(return_value=True)
        with patch.object(autopilot.time, "sleep"):
            self.assertTrue(ap.act({"phase": "cash_out", "telemetry": True}, None)[0])
        self.assertEqual(ap._screen.call_count, 2)
        ap.click.assert_called_once()

    def test_pause_step_resume_and_single_instance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            with Control(paused=True, path=path) as control:
                with self.assertRaises(RuntimeError):
                    Control(path=path).__enter__()
                self.assertFalse(control.poll())
                request("step", path)
                self.assertTrue(control.poll())
                control.consume()
                self.assertFalse(control.poll())
                request("resume", path)
                self.assertTrue(control.poll())
                request("pause", path)
                self.assertFalse(control.poll())
            with self.assertRaises(RuntimeError):
                request("step", path)

    def test_pause_stops_the_next_click(self):
        ap = Autopilot.__new__(Autopilot)
        ap.allowed = lambda: False
        ap.ptr = Mock()
        with self.assertRaisesRegex(RuntimeError, "paused"):
            ap.click(None, 0.5, 0.5)
        ap.ptr.click.assert_not_called()

    def test_boss_constraints_and_stake_scaling(self):
        state = State.from_dict({"hand": [], "full_deck": ["KS", "KH", "QD", "JC", "AS"],
                                 "hand_size": 5, "hands_left": 4, "discards_left": 3, "round_hands": 6})
        self.assertLess(estimate_round(state, samples=2, boss="The Plant"), estimate_round(state, samples=2))
        self.assertEqual(boss_state(state, "The Needle").hands_left, 1)
        self.assertEqual(boss_state(state, "The Water").discards_left, 0)
        self.assertEqual(boss_state(state, "The Manacle").hand_size, 4)
        self.assertEqual(required_score(3, "boss", replace(state, stake=8)), 6400)
        self.assertEqual(required_score(3, "boss", replace(state, stake=8, blind_chips=300, blind="Small Blind")), 6400)
        self.assertFalse(boss_state(replace(state, blind="The Wall", boss_disabled=True), "The Plant").boss_disabled)
        result = boss_forecast(replace(state, upcoming_boss="The Plant"), samples=2)
        self.assertEqual(result["boss"], "The Plant")
        self.assertTrue(result["notes"])
        self.assertEqual(result["samples"], 2)

    def test_deck_forecast_uses_full_deck_and_is_repeatable(self):
        state = State.from_dict({"hand": [], "hand_size": 2, "full_deck": ["AS", "AH", "2C"], "hand_levels": {"Pair": 4}})
        result = compare(state, remove=[Card.parse("2C")])
        self.assertGreater(result["gain"], 0)
        self.assertGreater(result["focus_delta"], 0)
        self.assertEqual(result, compare(state, remove=[Card.parse("2C")]))
        self.assertEqual(advice(state)["changes"][0]["action"], "remove")
        self.assertEqual(len(state.full_deck), 3)
        self.assertFalse(advice(State(hand=[]))["known"])
        # Old telemetry knew the card, but did not export its permanent Hiker bonus in full_deck.
        self.assertEqual(compare(state, remove=[Card(2, "C", bonus=5)])["deck_size"], 2)

    def test_verdant_leaf_accounts_for_and_executes_the_sale(self):
        from balatro.strategy import leaf_sale
        state = State.from_dict({"hand": ["AS"], "full_deck": ["AS", "AH", "2C"],
                                 "jokers": ["Cavendish", "Joker"], "blind": "Verdant Leaf", "telemetry": True})
        self.assertEqual(leaf_sale(state), 1)
        prepared = boss_state(state, "Verdant Leaf")
        self.assertTrue(prepared.boss_disabled)
        self.assertEqual([j.name for j in prepared.jokers], ["Cavendish"])
        ap = Autopilot.__new__(Autopilot)
        ap._shop_action = Mock(return_value=(True, "sold"))
        d = {"phase": "playing", "blind": "Verdant Leaf", "telemetry": True,
             "hand": ["AS"], "jokers": ["Cavendish", "Joker"], "full_deck": ["AS", "AH", "2C"]}
        self.assertTrue(ap.act(d, None)[0])
        self.assertEqual(ap._shop_action.call_args.args[2].index, 1)

    def test_learner_timeout_commit_failure_and_dirty_file(self):
        real = subprocess.run
        with tempfile.TemporaryDirectory() as tmp:
            root, rd = Path(tmp) / "repo", Path(tmp) / "run"
            root.mkdir(); rd.mkdir()
            def git(*args):
                return real(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                            check=True, capture_output=True, text=True).stdout
            (root / "a.py").write_text("x = 1\n")
            git("init", "-q"); git("add", "a.py"); git("commit", "-qm", "base")
            (rd / "opus.patch").write_text("--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n")
            def run(args, **kw):
                if "-m" in args:
                    raise subprocess.TimeoutExpired(args, 180)
                return real(args, **kw)
            with patch.object(learn, "TESTS", ["fake"]), patch.object(learn.subprocess, "run", side_effect=run):
                self.assertIn("rolled back", learn.apply(rd, root))
            self.assertEqual((root / "a.py").read_text(), "x = 1\n")
            def commit_failure(args, **kw):
                if "-m" in args and args[0] != "git":
                    return subprocess.CompletedProcess(args, 0)
                if args[:2] == ["git", "commit"]:
                    raise subprocess.CalledProcessError(1, args)
                return real(args, **kw)
            with patch.object(learn, "TESTS", ["fake"]), patch.object(learn.subprocess, "run", side_effect=commit_failure):
                self.assertIn("rolled back", learn.apply(rd, root))
            self.assertEqual(git("status", "--porcelain"), "")
            self.assertFalse((root / "docs/CHANGELOG.md").exists())
            (root / "a.py").write_text("x = 3\n")
            self.assertIn("local changes", learn.apply(rd, root))
            self.assertEqual((root / "a.py").read_text(), "x = 3\n")

    def test_statistics_pairs_and_retention_keep_evidence(self):
        from balatro.runs import paired, prune, record_metadata, statistics
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for version, ante in (("a", 4), ("b", 9)):
                path = base / version
                path.mkdir()
                rows = [{"n": i, "state": {"phase": "game_over" if i == 7 else "shop", "ante": ante,
                                            "seed": "TEST", "stake": 1, "deck_type": "Red Deck"},
                         "msg": "failed click" if i == 4 else "ok", "ok": i != 4} for i in range(8)]
                (path / "moves.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
                record_metadata(path, rows[0]["state"], version)
                for row in rows:
                    (path / f"{row['n']:03d}.jpg").write_bytes(b"fixture")
                old = time.time() - 40 * 86400
                for p in path.iterdir():
                    os.utime(p, (old, old))
            stats = statistics(base)
            result = paired(stats["runs"], "a", "b")
            self.assertEqual(result["paired_seeds"], 1)
            self.assertEqual(result["win_delta"], 1)
            preview = prune(base, days=30)
            self.assertTrue(preview["paths"])
            self.assertTrue((base / "a/002.jpg").exists())
            prune(base, days=30, apply=True)
            self.assertFalse((base / "a/002.jpg").exists())
            self.assertTrue((base / "a/004.jpg").exists())
            self.assertTrue((base / "a/moves.jsonl").exists())
            self.assertTrue((base / "a/run.json").exists())

    def test_retention_preserves_past_bug_evidence_and_unfinished_work(self):
        from balatro.runs import prune
        with tempfile.TemporaryDirectory() as tmp:
            base, old = Path(tmp), time.time() - 40 * 86400
            path = base / "historical"
            path.mkdir()
            rows = [{"n": i, "state": {"phase": "shop"}, "msg": "ok", "ok": True} for i in range(10)]
            (path / "moves.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
            (path / "report.txt").write_text("Engine mismatches: 1\n  #2 High Card: predicted 20, actual 16\n")
            (path / "work").mkdir()
            (path / "work/unexported.py").write_text("# unfinished proposal\n")
            for row in rows:
                (path / f"{row['n']:03d}.jpg").write_bytes(b"fixture")
            for item in path.iterdir():
                os.utime(item, (old, old))
            prune(base, apply=True)
            self.assertTrue((path / "002.jpg").exists())
            self.assertTrue((path / "003.jpg").exists())
            self.assertFalse((path / "006.jpg").exists())
            self.assertTrue((path / "work/unexported.py").exists())


if __name__ == "__main__":
    unittest.main()
