"""Presentation, capture isolation and observer integration. No real game clicks."""
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image

from balatro import live
from balatro.hud import Hud, model
from balatro.models import State
from balatro.optimizer import recommend

ROOT = Path(__file__).resolve().parent.parent


class Companion(unittest.TestCase):
    def test_hand_model_uses_current_order_and_exposes_reorder_separately(self):
        d = {"phase": "playing", "hand": ["AS"], "jokers": ["Cavendish", "Joker"],
             "hands_left": 1, "discards_left": 0, "blind_chips": 150, "score": 0, "money": 0}
        text, rec = live.plan(d)
        out = model(d, text, rec)
        self.assertEqual(out["recommendation"]["score"], rec.play.score)
        self.assertEqual(out["recommendation"]["projected"], rec.play.score)
        self.assertGreater(out["recommendation"]["reordered_score"], rec.play.score)
        self.assertIsNone(out["recommendation"]["play_win"])
        self.assertTrue(out["recommendation"]["joker_order"])
        self.assertEqual(out["stats"]["money"], 0)
        self.assertEqual(out["stats"]["interest"], 0)
        json.dumps(out, allow_nan=False)

    def test_unknown_values_are_not_engine_defaults(self):
        out = model({"phase": "other"}, "No hand cards recognised")
        for field in ("money", "score", "joker_count", "hands_left", "focus_level", "deck_count"):
            self.assertIsNone(out["stats"][field], field)
        self.assertTrue(any("standard deck" in w for w in out["warnings"]))
        self.assertEqual(out["strategy"], [])

    def test_telemetry_deck_and_strategy(self):
        d = json.loads((ROOT / "examples/flush_blueprint.json").read_text())
        d.update(telemetry=True, deck=["2C"], full_deck=d["hand"] + ["2C"], ante=4, round=10)
        out = model(d, "Note")
        self.assertEqual(out["stats"]["deck_count"], 1)
        self.assertEqual(out["stats"]["full_deck_count"], 9)
        self.assertEqual(out["stats"]["focus"], "Flush")
        self.assertEqual(out["stats"]["focus_level"], 3)
        self.assertTrue(any("Jupiter" in s["text"] for s in out["strategy"]))
        self.assertFalse(any("unknown" in w for w in out["warnings"]))

    def test_simulated_odds_are_structured(self):
        state = State.from_dict({"hand": ["2H", "4S", "7C", "9D", "JS"], "hands_left": 1,
                                 "discards_left": 1, "blind_chips": 5000})
        rec = recommend(state, samples=2, reorder=False)
        self.assertEqual(rec.samples, 2)
        self.assertIsNotNone(rec.play_expected)
        self.assertGreaterEqual(rec.play_win, 0)
        self.assertLessEqual(rec.play_win, 1)

    def test_shop_plan_and_display_share_results(self):
        from balatro.shop import Offer, ShopAction
        details = {}
        actions = [ShopAction("buy", 0, "buy Joker"), ShopAction("leave", -1, "next")]
        offer = Offer("joker", "Joker", 0, 6, "helps now")
        with patch("balatro.shop.offers_for", return_value=[offer]), patch("balatro.shop.shop_plan", return_value=actions) as plan:
            text, rec = live.plan({"phase": "shop", "money": 8,
                                   "shop": [{"kind": "joker", "name": "Joker", "price": 0}]}, details=details)
        plan.assert_called_once()
        out = model({"phase": "shop", "money": 8}, text, rec, details)
        self.assertEqual(out["shop_actions"][0]["kind"], "buy")
        self.assertEqual(out["offers"][0]["price"], 0)
        self.assertIn("Buy Joker ($0)", out["recommendation"]["title"])

    def test_pack_choice_and_phase_changes(self):
        d = {"phase": "booster_pack", "hand_levels": {"Flush": 3},
             "booster": {"pack": "Celestial Pack", "choose": 1,
                         "cards": [{"kind": "planet", "name": "Jupiter"}]}}
        details = {}
        text, rec = live.plan(d, details=details)
        out = model(d, text, rec, details)
        self.assertEqual(out["pack_items"], [{"name": "Jupiter", "chosen": True}])
        out = model({"phase": "cash_out"}, *live.plan({"phase": "cash_out"}))
        self.assertEqual(out["recommendation"]["title"], "Cash Out")
        self.assertEqual(out["pack_items"], [])
        self.assertEqual(out["offers"], [])

    def test_short_headline_keeps_the_strategy_explanation(self):
        out = model({"phase": "blind_select"}, "RECOMMENDATION: Play the blind (Select) – tag not worth it.\nBoss: The Wall")
        self.assertEqual(out["recommendation"]["title"], "Play the blind (Select)")
        self.assertEqual(out["recommendation"]["reason"], "tag not worth it.\nBoss: The Wall")

    def test_widget_capture_never_falls_back_to_desktop(self):
        with tempfile.TemporaryDirectory() as tmp:
            hud = Hud(Path(tmp))
            with self.assertRaises(RuntimeError):
                hud.select({"address": "x"})
            with self.assertRaises(RuntimeError):
                hud.grab("0,0 200x100", Path(tmp) / "x.png")
            hud.select({"stableId": "1800016a"})
            with patch("balatro.live.subprocess.run") as run:
                hud.grab("0,0 200x100", Path(tmp) / "x.png", scale=0.15)
            cmd = run.call_args.args[0]
            self.assertIn("-T", cmd)
            self.assertNotIn("-g", cmd)
            self.assertIn("1800016a", cmd)

    def test_snapshot_status_history_and_single_instance(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"XDG_RUNTIME_DIR": tmp}), \
                patch("balatro.hud.subprocess.Popen") as popen, patch("balatro.hud.shutil.which", return_value="quickshell"):
            popen.return_value.poll.return_value = None
            with Hud(Path(tmp)) as hud:
                with self.assertRaisesRegex(RuntimeError, "already open"):
                    Hud(Path(tmp)).__enter__()
                d = {"phase": "playing", "score": 100, "round": 1, "ante": 1}
                hud.update(d, "Test", None, {}, [], 0.2)
                before = hud.data["observed_at"]
                hud.status("hidden", "not visible")
                self.assertEqual(json.loads(hud.path.read_text())["status"], "hidden")
                hud.observed(True)
                self.assertEqual(hud.data["status"], "ready")
                self.assertGreaterEqual(hud.data["observed_at"], before)
                hud.observed(False)
                self.assertEqual(hud.data["status"], "waiting")
                hud.update(d, "Test", None, {}, [], 0.2)
                hud.update({**d, "score": 300}, "Test", None, {}, [], 0.2)
                self.assertEqual(hud.data["history"], [100, 300])
                hud.update({**d, "round": 2, "score": 0}, "Test", None, {}, [], 0.2)
                self.assertEqual(hud.data["history"], [0])
                hud.update({"phase": "other"}, "No game", None, {}, [], 0)
                hud.observed(True)
                self.assertEqual(hud.data["status"], "waiting")
                self.assertFalse(hud.path.with_suffix(".tmp").exists())
            popen.return_value.terminate.assert_called_once()

    def test_live_publishes_once_for_stable_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "frame.png"
            Image.new("RGB", (96, 64), "black").save(path)
            hud = Mock()
            hud.running.side_effect = [True] * 8 + [False]
            hud.grab.side_effect = lambda geom, p, **kw: Image.new("RGB", (96, 64), "black").save(p)
            state = {"phase": "cash_out", "telemetry": True, "score": 500}
            a = Namespace(window="Balatro", interval=0.1, still=1, auto=False, debug=False,
                          model=None, vision="local", no_notify=True)
            with patch.object(live, "find_window", return_value=("0,0 96x64", {"stableId": "test"})), \
                    patch.object(live, "best_read", return_value=(path, state, {}, [])), \
                    patch.object(live.time, "sleep"), patch.object(live, "LOG", directory / "log"), \
                    patch.object(live, "analyze", return_value=([], state, "Cash Out", None)) as analyze:
                live._loop(a, directory, [], hud)
            analyze.assert_called_once()
            hud.update.assert_called_once()
            self.assertTrue(hud.observed.called)
            self.assertTrue(hud.heartbeat.called)

    def test_hud_rejects_autopilot_before_start(self):
        with self.assertRaisesRegex(RuntimeError, "advisor"):
            live.run(Namespace(hud=True, auto=True))


if __name__ == "__main__":
    unittest.main()
