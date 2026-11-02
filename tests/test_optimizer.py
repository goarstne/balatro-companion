"""Run: .venv/bin/python -m tests.test_optimizer"""
import time

from balatro.models import Card, Joker, State
from balatro.optimizer import best_joker_order, best_plays, discard_options, format_recommendation, recommend


def hand(s):
    return [Card.parse(x) for x in s.split()]


def test_flush_over_pair():
    st = State(hand=hand("2H 5H 9H JH KH KS 3C 4D"))
    idx, r = best_plays(st, top=1)[0]
    assert r.hand_type == "Flush", r.hand_type
    assert sorted(idx) == [0, 1, 2, 3, 4]


def test_forced_card():
    st = State(hand=hand("2H 5H 9H JH KH KS 3C 4D"))
    st.hand[7].forced = True
    plays = best_plays(st, top=50)
    assert plays and all(7 in idx for idx, _ in plays)
    assert plays[0][1].hand_type == "Pair", plays[0][1].hand_type  # forced 4D blocks the flush
    assert len(plays[0][0]) <= 5


def test_flush_draw_beats_junk():
    st = State(hand=hand("AH KH 9H 5H 2S 3C 7D JC"), blind_chips=250, hands_left=1, discards_left=3)
    opts = discard_options(st, samples=200, seed=1)
    by = {o.label: o for o in opts}
    flush = by["flush draw ♥"]
    junk = by["discard the 2 weakest"]
    assert flush.p_win > junk.p_win + 0.1, (flush, junk)
    assert opts[0].label == "flush draw ♥", opts[0].label


def test_recommend_play_when_winning():
    st = State(hand=hand("2H 5H 9H JH KH KS 3C 4D"), blind_chips=300, jokers=[Joker("Joker")])
    rec = recommend(st)
    assert rec.action == "play" and rec.play.hand_type == "Flush", rec
    assert "wins" in rec.reason
    assert rec.play.log, "verbose log missing"
    print(format_recommendation(rec))


def test_keep_discards_when_on_pace():
    # lost run 2026-10-27: discarding a made Two Pair for full-house odds burned all discards on hand one
    st = State(hand=hand("KH KC JH JC 8C 8D 4D 3S"), blind_chips=300, hands_left=5, discards_left=3)
    assert recommend(st).action == "play"
    # below pace with discards left: a free discard beats wasting a hand on High Card
    st = State(hand=hand("KD QC 10H 7H 6D 5C 4S 2H"), blind_chips=300, score=172, hands_left=3, discards_left=2)
    assert recommend(st).action == "discard"


def test_no_discards_reason():
    # run 20261031-074053 #414-#434: "keep the discards for later" with no discard left
    st = State(hand=hand("AH JD 8H 5H KD 9S 7C 3D"), blind_chips=560000, score=92874, hands_left=6, discards_left=0)
    rec = recommend(st, samples=8)
    assert rec.action == "play" and "for later" not in rec.reason, rec.reason


def test_kickers():
    from balatro.optimizer import add_kickers
    # a winnable round (at hopeless odds the expected round score may prefer dumping junk and keeping the pair)
    st = State(hand=hand("9H 9C 2S 4D 6C JS QD 7H"), blind_chips=150, hands_left=4, discards_left=0)
    rec = recommend(st)
    assert len(rec.play_idx) == 5 and len(rec.kickers) == 3, rec.play_idx  # pair + 3 junk cards
    assert rec.play.score == score_of(st, (0, 1))
    # Half Joker (+20 mult at <= 3 cards): only one kicker keeps the score
    st.jokers = [Joker("Half Joker")]
    assert len(add_kickers(st, (0, 1))) == 3
    # pairs and 3+ cards of a suit stay in hand
    st = State(hand=hand("KH KC 8C 8D 2S 5S 9S 3D"), blind_chips=800, hands_left=4)
    kept = {str(st.hand[i]) for i in range(8)} - {str(st.hand[i]) for i in add_kickers(st, (0, 1))}
    assert {"8C", "8D", "2S", "5S", "9S"} <= kept, kept
    # the play that wins the blind gets no kickers
    assert not recommend(State(hand=hand("9H 9C 2S 4D 6C JS QD 7H"), blind_chips=50, hands_left=4)).kickers


def test_no_kicker_that_lowers_score():
    # run 20261030-215640 Z29: K♦ played as kicker beside Two Pair, Baron lost x1.5 (76 instead of 114)
    st = State(hand=hand("AS AC KD 7S 7C 6C 4D 3S"), jokers=[Joker("Gluttonous Joker"), Joker("Baron")],
               blind="The Pillar", blind_chips=600, score=51, hands_left=4, discards_left=0,
               deck=hand("10H 5S 6H 10D 5C QC 3C JS 5D JC 3H 2S 9C 2D 4C QD AD KH 8S 8D 4S 10S QH QS 8C"))
    st.hand[1].debuff = st.hand[4].debuff = True
    assert not any(2 in idx and r.hand_type == "Two Pair" for idx, r in best_plays(st, top=None))
    rec = recommend(st)
    assert 2 not in rec.play_idx and rec.play.score == 114, (rec.play_idx, rec.play.score)


def test_kicker_base_follows_raised_score():
    # run 20261031-101337 Z198/Z203/Z217: High Card Q♠ + "kicker" Q♦ made a Pair, then a 4th card killed
    # Half Joker (16,640 instead of 21,840) – the old base (High Card) still allowed it
    from balatro.optimizer import add_kickers
    st = State(hand=hand("QS QD KH AC 7S 7C 5H 5D"), jokers=[Joker("Half Joker")], blind_chips=30000,
               hands_left=6, discards_left=3, hand_levels={"Pair": 7})
    out = add_kickers(st, (0,))
    assert len(out) <= 3 and score_of(st, out) == score_of(st, (0, 1)), (out, score_of(st, out))


def score_of(st, idx):
    from balatro.engine import score
    return score(st, idx).score


def test_joker_order_noop_for_plain_jokers():
    st = State(hand=hand("KH KS"), jokers=[Joker("Joker"), Joker("Joker")])
    assert best_joker_order(st, (0, 1)) is None


def test_pruned_matches_full():
    import random
    from balatro.models import standard_deck
    from balatro.optimizer import _best_score, _subsets
    from balatro.engine import score
    rng = random.Random(3)
    for _ in range(30):
        st = State(hand=rng.sample(standard_deck(), 8), jokers=[Joker("Joker")])
        full = max(score(st, idx).score for idx in _subsets(st))
        assert _best_score(st) == full


def test_timing():
    st = State(hand=hand("AH KH 9H 5H 2S 3C 7D JC"), blind_chips=5000, hands_left=2, discards_left=3,
               jokers=[Joker("Joker") for _ in range(5)])
    t = time.perf_counter()
    rec = recommend(st)
    dt = time.perf_counter() - t
    print(format_recommendation(rec))
    print(f"recommend(): {dt:.2f} s, {len(rec.discard_options)} discard options")
    assert rec.discard_options and dt < 5, dt


def test_joker_growth_and_resets():
    from balatro.optimizer import joker_utility, _joker_plan
    cases = [
        ("Ride the Bus", "2H 2C KH KC", {"mult": 12}, (0, 1), (2, 3)),
        ("Runner", "2H 3C 4D 5S 6H KH KC", {"chips": 30}, (0, 1, 2, 3, 4), (5, 6)),
        ("Square Joker", "KH KC 2H 5S 7C", {"chips": 12}, (0, 1, 2, 3), (0, 1)),
        ("Spare Trousers", "KH KC 2H 2S 7C", {"mult": 4}, (0, 1, 2, 3), (0, 1)),
        ("Wee Joker", "2H 2C KH KC", {"chips": 16}, (0, 1), (2, 3)),
        ("Obelisk", "2H 2C KH KC", {"xmult": 3}, (0,), (0, 1)),
    ]
    for name, cards, counters, good, bad in cases:
        st = State(hand=hand(cards), jokers=[Joker(name, vars=counters.copy())],
                   played_counts={"Pair": 5})
        before = repr(st)
        good_value, reasons = joker_utility(st, play_idx=good)
        assert good_value > joker_utility(st, play_idx=bad)[0], name
        assert reasons and name in reasons[0], (name, reasons)
        assert repr(st) == before, name
    st = State(hand=hand("2H 2C KH KC"), jokers=[Joker("Ride the Bus", vars={"mult": 12})])
    # A face kicker is harmless; a debuffed face cannot reset the bus either.
    assert joker_utility(st, play_idx=(0, 1, 2))[0] > 0
    st.hand[2].debuff = st.hand[3].debuff = True
    assert joker_utility(st, play_idx=(2, 3))[0] > 0
    st.jokers.append(Joker("Pareidolia"))
    assert joker_utility(st, play_idx=(0, 1))[0] < 0
    st.jokers[0].debuff = True
    assert joker_utility(st, play_idx=(0, 1)) == (0, [])
    st = State(hand=hand("2H 2C"), jokers=[Joker("Wee Joker")])
    st.hand[0].seal = "red"
    after, value, _ = _joker_plan(st, play_idx=(0, 1))
    assert after.jokers[0].vars["chips"] == 24 and value > 0
    assert joker_utility(st) == (0, [])


def test_joker_history_decay_and_cards():
    from balatro.optimizer import joker_utility, _joker_plan
    st = State(hand=hand("2H 2C KH KC"), played_counts={"Pair": 7}, jokers=[Joker("Supernova")])
    assert joker_utility(st, play_idx=(0, 1))[0] > joker_utility(st, play_idx=(0,))[0]
    st.jokers = [Joker("Card Sharp")]
    assert joker_utility(st, play_idx=(0, 1))[0] > 0
    for boss in ("The Eye", "The Needle"):
        st.blind = boss
        assert joker_utility(st, play_idx=(0, 1))[0] == 0
    st.blind = None
    st.hands_left = 1
    assert joker_utility(st, play_idx=(0, 1))[0] == 0
    st.jokers = [Joker("Ice Cream", vars={"chips": 3})]
    after, value, _ = _joker_plan(st, play_idx=(0, 1))
    assert value < 0 and after.jokers[0].vars["chips"] == 0
    st.jokers = [Joker("Popcorn", vars={"mult": 2})]
    st.blind_chips = 10
    assert joker_utility(st, play_idx=(0, 1))[0] < 0
    st.blind_chips = 10000
    assert joker_utility(st, play_idx=(0, 1))[0] == 0  # decay is per round, not per hand
    st.jokers = [Joker("Hiker")]
    after, value, _ = _joker_plan(st, play_idx=(0, 1))
    assert value > 0 and [c.bonus for c in after.hand] == [5, 5, 0, 0]
    st.hand[0].enhancement = "lucky"
    st.jokers = [Joker("Lucky Cat")]
    plain = joker_utility(st, play_idx=(0, 1))[0]
    st.jokers.append(Joker("Oops! All 6s"))
    assert joker_utility(st, play_idx=(0, 1))[0] > plain > 0
    st.jokers = [Joker("Lucky Cat"), Joker("Vampire")]
    after, _, _ = _joker_plan(st, play_idx=(0, 1))
    assert after.jokers[0].vars["x"] == 1  # Vampire strips Lucky before on-scored hooks
    st.jokers = [Joker("Vampire", vars={"xmult": 2})]
    after, value, reasons = _joker_plan(st, play_idx=(0, 1))
    assert value > 0 and after.jokers[0].vars["xmult"] == 2.1
    assert after.hand[0].enhancement is None and st.hand[0].enhancement == "lucky"
    assert any("lost" in r for r in reasons)
    for name in ("Constellation", "Madness", "Hologram"):
        st.jokers = [Joker(name)]
        assert joker_utility(st, play_idx=(0, 1)) == (0, []), name
    st.jokers = [Joker("Hologram", vars={"xmult": 2}), Joker("DNA")]
    assert joker_utility(st, play_idx=(0,))[0] > 0
    st.round_hand_types = ["Pair"]
    assert joker_utility(st, play_idx=(0,))[0] == 0


def test_joker_discard_effects():
    from balatro.optimizer import joker_utility, _joker_plan
    cases = [
        ("Green Joker", {"mult": 4}, "mult", 3, -1),
        ("Ramen", {"x": 2}, "x", 1.97, -1),
        ("Castle", {"chips": 8, "suit": "H"}, "chips", 14, 1),
        ("Hit the Road", {"x": 1}, "x", 1.5, 1),
        ("Yorick", {"x": 1, "discarded": 21}, "x", 2, 1),
    ]
    for name, counters, key, expected, sign in cases:
        st = State(hand=hand("JH QH KC 2S"), jokers=[Joker(name, vars=counters.copy())])
        before = repr(st)
        after, value, reasons = _joker_plan(st, discard_idx=(0, 1, 2))
        assert value * sign > 0 and reasons, name
        assert abs(after.jokers[0].vars[key] - expected) < 1e-9, name
        assert repr(st) == before
    for name, counters, indices, money in [
        ("Mail-In Rebate", {"rank": 11}, (0,), 5),
        ("Faceless Joker", {}, (0, 1, 2), 5),
        ("Trading Card", {"first_discard": True}, (3,), 3),
    ]:
        st = State(hand=hand("JH QH KC 2S"), jokers=[Joker(name, vars=counters)])
        after, value, _ = _joker_plan(st, discard_idx=indices)
        assert value > 0 and after.money == money, name
    st = State(hand=hand("JH QH KC 2S"), jokers=[Joker("Trading Card")])
    after, _, _ = _joker_plan(st, discard_idx=(3,))
    assert len(after.get_full_deck()) == 51 and len(st.get_full_deck()) == 52
    assert all((c.rank, c.suit) != (2, "S") for c in after.get_full_deck())
    st.deck_type = "Red Deck"
    assert joker_utility(st, discard_idx=(3,))[0] == 0  # three discards means the first was spent
    st.discards_left = 4
    assert joker_utility(st, discard_idx=(3,))[0] > 0
    st.jokers = [Joker("Burnt Joker", vars={"first_discard": True})]
    after, value, _ = _joker_plan(st, discard_idx=(0, 1, 2))
    assert value > 0 and after.level("High Card") == 2
    again, value, _ = _joker_plan(after, discard_idx=(0,))
    assert value == 0 and again.level("High Card") == 2
    for name in ("Trading Card", "Burnt Joker"):
        st.jokers = [Joker(name, vars={"first_discard": False})]
        assert joker_utility(st, discard_idx=(3,))[0] == 0
    st.jokers = [Joker("Mail-In Rebate")]  # unknown rank defaults to Ace
    st.hand = hand("AH 2S")
    assert joker_utility(st, discard_idx=(0,))[0] > 0
    assert joker_utility(st, discard_idx=(1,))[0] == 0


def test_targeted_discard_candidates():
    from balatro.optimizer import _discard_candidates
    st = State(hand=hand("JH QH KC 2S 2H 3C 4D 5S"))
    for name, counters, wanted in [
        ("Mail-In Rebate", {"rank": 2}, (3, 4)),
        ("Castle", {"suit": "H"}, (0, 1, 4)),
        ("Faceless Joker", {}, (0, 1, 2)),
        ("Hit the Road", {}, (0,)),
        ("Trading Card", {}, (7,)),
        ("Burnt Joker", {}, (3, 4)),
    ]:
        st.jokers = [Joker(name, vars=counters)]
        plays = best_plays(st, top=None)
        assert wanted in [tuple(d) for _, d in _discard_candidates(st, plays)], name
        # Cerulean Bell: the forced card stays highlighted and is discarded too, 5 cards at most
        # (state_events.lua discard_cards_from_highlighted; run 20261031-101337 hung trying to deselect it)
        st.hand[3].forced = True
        assert all(3 in d and len(d) <= 5 for _, d in _discard_candidates(st, plays)), name
        st.hand[3].forced = False


def test_safe_winners_feed_jokers_and_focus():
    cases = [
        ("Runner", "2H 3C 4D 5S 6H KH KC 2D", "Straight", 5),
        ("Square Joker", "KH KC 2D 5S 7H 9C 10D 3S", "Pair", 4),
        ("Spare Trousers", "KH KC 2D 2S 7H 9C 10D 3S", "Two Pair", 4),
        ("Ride the Bus", "KH KC 2D 2S 7H 9C 10D 3S", "Pair", 2),
    ]
    for name, cards, ht, size in cases:
        st = State(hand=hand(cards), blind_chips=30, discards_left=0,
                   jokers=[Joker(name, vars={"mult": 10} if name == "Ride the Bus" else {})])
        rec = recommend(st, samples=2)
        assert rec.action == "play" and rec.play.hand_type == ht and len(rec.play_idx) == size, name
        assert "Jokers:" in rec.reason and name in rec.reason
        if name == "Ride the Bus":
            assert all(not c.is_face() for c in rec.play.scoring)
    st = State(hand=hand("2H 5H 9H JH KH KS 3C 4D"), blind_chips=100,
               hand_levels={"Pair": 2}, discards_left=0)
    rec = recommend(st, samples=2)
    assert best_plays(st)[0][1].hand_type == "Flush"
    assert rec.play.hand_type == "Pair" and "Focus hand: Pair" in rec.reason


def test_discard_rewards_and_probability_guard():
    from unittest.mock import patch
    from balatro.optimizer import DiscardOption
    st = State(hand=hand("AH AC 2S 4D 6C JS QD 7H"), jokers=[Joker("Mail-In Rebate", vars={"rank": 2})],
               deck=[], blind_chips=20, hands_left=1)
    rec = recommend(st, samples=2)
    assert rec.action == "discard" and rec.discard.discard_idx == [2]
    assert "Mail-In Rebate" in rec.reason
    st.blind_chips = 5000
    st.jokers = [Joker("Green Joker", vars={"mult": 10})]
    option = DiscardOption("Testabwurf", [2], [st.hand[2]], 6000, 0.84, 2)
    with patch("balatro.optimizer.play_now_odds", return_value=(0.8, 5000)), \
            patch("balatro.optimizer.discard_options", return_value=[option]):
        assert recommend(st, samples=2).action == "play"  # within 5%: preserve Green Joker
        option.p_win = 0.86
        assert recommend(st, samples=2).action == "discard"  # survival outranks growth
    # The chosen action must remain within 5% of the globally best action too.
    option.p_win = 0.85
    safer = DiscardOption("safer discard", [3], [st.hand[3]], 7000, 0.90, 2)
    with patch("balatro.optimizer.play_now_odds", return_value=(0.8, 5000)), \
            patch("balatro.optimizer.discard_options", return_value=[option, safer]):
        assert recommend(st, samples=2).action == "discard"
    st.jokers = [Joker("Mail-In Rebate", vars={"rank": 2})]
    option.p_win = 0.74
    with patch("balatro.optimizer.play_now_odds", return_value=(0.8, 5000)), \
            patch("balatro.optimizer.discard_options", return_value=[option]):
        assert recommend(st, samples=2).action == "play"  # money cannot buy a >5% survival loss


def test_boss_rules_and_rollout_history():
    import random
    from balatro.optimizer import _round_total, _after_play, add_kickers, play_now_odds
    st = State(hand=hand("2H 2C 3D 3S"), hand_size=4, deck=[], hands_left=2, discards_left=0,
               jokers=[Joker("Half Joker")])
    assert _round_total(st, st.hand, [], 2, 0, 600) >= 600
    st.blind = "The Eye"
    assert _round_total(st, st.hand, [], 2, 0, 600) < 600
    st.round_hand_types = ["Pair"]
    assert all(r.hand_type != "Pair" for _, r in best_plays(st, top=None))
    st.blind = "The Mouth"
    st.round_hand_types = ["High Card"]
    assert all(r.hand_type == "High Card" for _, r in best_plays(st, top=None))
    assert _round_total(st, st.hand, [], 2, 0, 400) < 400
    st.blind = "The Needle"
    st.round_hand_types = []
    st.blind_chips = 600
    assert _round_total(st, st.hand, [], 4, 0, 600) < 600
    assert play_now_odds(st, (2, 3), 352, samples=2)[0] == 0
    assert recommend(st, samples=2).play.score < 600
    st.boss_disabled = True
    assert _round_total(st, st.hand, [], 2, 0, 600) >= 600
    st.boss_disabled = False
    st.jokers.append(Joker("Chicot"))
    assert _round_total(st, st.hand, [], 2, 0, 600) >= 600
    st = State(hand=hand("KH KC 2S 4D 6C JS QD 7H"), blind="The Psychic", blind_chips=10)
    assert all(len(idx) == 5 for idx, _ in best_plays(st, top=None))
    assert len(add_kickers(st, (0, 1), deck_left=False)) == 5
    assert len(recommend(st, samples=2).play_idx) == 5
    st.blind = "The Tooth"
    st.blind_chips = 50
    assert add_kickers(st, (0, 1)) == (0, 1)
    rec = recommend(st, samples=2)
    assert len(rec.play_idx) == 2 and not rec.kickers
    st.blind = "The Hook"
    before = repr(st)
    _, rest, _ = _after_play(st, (0, 1), random.Random(4))
    _, same, _ = _after_play(st, (0, 1), random.Random(4))
    assert len(rest) == 4 and rest == same and repr(st) == before
    st.boss_disabled = True
    assert len(_after_play(st, (0, 1), random.Random(4))[1]) == 6


def test_hook_scores_after_forced_discard():
    import random
    from balatro.optimizer import _after_play, play_now_odds
    st = State(hand=hand("2H KH KC"), jokers=[Joker("Baron")], blind="The Hook", blind_chips=10,
               hands_left=1, discards_left=3, deck=[])
    assert score_of(st, (0,)) >= 10  # optimistic held-King score cannot be used as a guaranteed win
    after, rest, result = _after_play(st, (0,), random.Random(0))
    assert result.score == 7 and not rest and after.discards_left == 3
    assert play_now_odds(st, (0,), score_of(st, (0,)), samples=2)[0] == 0
    st.jokers = [Joker("Green Joker", vars={"mult": 5}), Joker("Hit the Road"),
                 Joker("Burnt Joker", vars={"first_discard": True})]
    st.hand = hand("2H JH JC")
    after, _, _ = _after_play(st, (0,), random.Random(0))
    assert after.jokers[0].vars["mult"] == 5  # -1 for Hook, +1 for the play
    assert after.jokers[1].vars["x"] == 2 and not after.hand_levels
    assert after.jokers[2].vars["first_discard"] is True
    assert after.discards_left == 3
    st.boss_disabled = True
    assert _after_play(st, (0,), random.Random(0))[0].jokers[0].vars["mult"] == 6


def test_the_ox():
    # most played hand under The Ox: money drops to $0 before jokers score (Bull: +2 chips per $)
    st = State(hand=hand("KH KC 2S"), jokers=[Joker("Bull")], blind="The Ox", money=20,
               played_counts={"Pair": 3, "High Card": 1}, blind_chips=10_000)
    assert score_of(st, (0, 1)) == (10 + 10 + 10) * 2           # Pair: $0, Bull adds nothing
    assert score_of(st, (2,)) == 5 + 2 + 2 * 20                 # High Card keeps the $20
    from balatro.optimizer import joker_utility
    assert joker_utility(st, play_idx=(0, 1))[0] < joker_utility(st, play_idx=(2,))[0]


def test_the_arm():
    # The Arm lowers the played hand's level for good: rollouts see it, utility prefers a level-1 hand
    from balatro.optimizer import _joker_plan
    st = State(hand=hand("KH KC 2S"), blind="The Arm", hand_levels={"Pair": 3}, blind_chips=10_000)
    after, value, _ = _joker_plan(st, play_idx=(0, 1))
    assert after.level("Pair") == 2 and value < 0 and st.level("Pair") == 3
    after, value, _ = _joker_plan(st, play_idx=(2,))
    assert after.level("High Card") == 1 and value == 0
    st.boss_disabled = True
    assert _joker_plan(st, play_idx=(0, 1))[0].level("Pair") == 3


def test_the_serpent():
    from balatro.optimizer import _draw, add_kickers
    st = State(hand=hand("KH KC 2S 3D 5C 7H 9S JD"), blind="The Serpent", blind_chips=10_000, deck=hand("4H 6H 8H 10H"))
    assert add_kickers(st, (0, 1)) == (0, 1)  # kickers would shrink the hand: Serpent draws 3 whatever leaves
    kept, deck = _draw(st.hand[2:], st.deck, st)
    assert len(kept) == 9 and len(deck) == 1  # 6 kept + 3 drawn


def test_discard_keeps_the_focus_pair():
    """Runs 094727/101337: discards whose thrown cards formed the focus hand were preferred, so a Pair build
    threw its pairs away. Run 101337 move 18 discarded A-A (675 expected) instead of keeping them (757)."""
    st = State(hand=hand("AS AC KS KD JD 8S 4D 2C"), blind="Big Blind", blind_chips=450, hands_left=5,
               discards_left=2, played_counts={"Pair": 2, "Three of a Kind": 1}, jokers=[Joker("Scary Face")])
    rec = recommend(st, samples=30, reorder=False)
    gone = rec.discard.discard_idx if rec.action == "discard" else ()
    assert not {0, 1} <= set(gone), (rec.action, gone)


def test_joker_growth_only_between_comparable_hands():
    """Run 082933 move 83: Ride the Bus growth kept High Card 180 over Two Pair 834 at a simulated 100 %;
    the blind was then won with the last hand (2,128 of 2,000)."""
    st = State(hand=hand("KS KD 10C 8S 7C 6D 4C 2H 2C"), blind="Small Blind", blind_chips=2000, hands_left=5,
               discards_left=3, hand_size=9,
               hand_levels={"Full House": 2, "Flush": 2, "Straight": 2, "Two Pair": 2},
               played_counts={"Full House": 2, "Flush": 3, "Pair": 2, "High Card": 1, "Straight": 1, "Two Pair": 1},
               jokers=[Joker("Vampire"), Joker("Ice Cream", vars={"chips": 75}), Joker("Lusty Joker"),
                       Joker("Ride the Bus")])
    rec = recommend(st, samples=30, reorder=False)
    assert rec.action == "discard" or rec.play.hand_type != "High Card", (rec.action, rec.play.hand_type)


def test_expected_score_before_focus_when_not_yet_won():
    """Run 115250 Z292: Pair 6,435 (focus) over Flush 11,547 at equal 100 %; the round ended with 1 hand left."""
    from unittest.mock import patch
    from balatro.strategy import focus_hand
    st = State(hand=hand("2H 5H 9H JH KH KS 3C 4D"), blind_chips=5000, hands_left=3, discards_left=0,
               hand_levels={"Pair": 3})
    assert focus_hand(st) == "Pair"
    with patch("balatro.optimizer.play_now_odds", side_effect=lambda s, idx, sc, n, seed: (1.0, sc)):
        rec = recommend(st, samples=2, reorder=False)
    assert rec.action == "play" and rec.play.hand_type == "Flush", rec.play.hand_type


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
