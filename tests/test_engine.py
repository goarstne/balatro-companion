"""Hand detection and core scoring checks. Run: .venv/bin/python -m tests.test_engine"""
from balatro.engine import detect, score
from balatro.models import Card, State


def cards(*xs):
    return [Card.parse(x) for x in xs]


def ht(*xs, flags=()):
    return detect(cards(*xs), frozenset(flags))[0]


def main():
    assert ht("AS", "2H", "3D", "4C", "5S") == "Straight"
    assert ht("QS", "KH", "AD", "2C", "3S") == "High Card"          # no wraparound
    assert ht("2S", "4H", "6D", "8C", "TS", flags=["Shortcut"]) == "Straight"
    assert ht("2S", "3H", "4D", "5C", "KS", flags=["Four Fingers"]) == "Straight"
    assert ht("2H", "5H", "9H", "KH", "AS", flags=["Four Fingers"]) == "Flush"
    assert ht("2H", "5D", "9H", "KD", "AH", flags=["Smeared Joker"]) == "Flush"
    assert ht("AS", "AH", "AD", "KC", "KS") == "Full House"
    assert ht("AS", "AH", "AD", "AC", "KS") == "Four of a Kind"
    assert ht("AS", "AS", "AS", "AS", "AS") == "Flush Five"
    assert ht("AH", "AH", "AH", "KH", "KH") == "Flush House"

    # contains: Four of a Kind contains Pair and Three, not Two Pair
    _, _, c = detect(cards("AS", "AH", "AD", "AC", "KS"))
    assert {"Pair", "Three of a Kind"} <= c and "Two Pair" not in c

    # Wild card completes a flush; Stone always scores, has no rank
    wild = {"rank": "2", "suit": "S", "enhancement": "wild"}
    stone = {"rank": "9", "suit": "S", "enhancement": "stone"}
    t, scoring, _ = detect([Card.parse(x) for x in ("3H", "7H", "9H", "KH", wild)])
    assert t == "Flush" and len(scoring) == 5
    t, scoring, _ = detect([Card.parse(x) for x in ("AS", "AH", stone)])
    assert t == "Pair" and len(scoring) == 3

    # Pair of aces + stone: (10 + 11 + 11 + 50) * 2
    s = State.from_dict({"hand": ["AS", "AH", stone]})
    assert score(s, [0, 1, 2]).score == 164
    # Splash: kicker scores too
    s = State.from_dict({"hand": ["AS", "AH", "KD"], "jokers": ["Splash"]})
    assert score(s, [0, 1, 2]).score == (10 + 32) * 2
    # The Flint halves base: pair 10x2 -> 5x1, + 22 chips
    s = State.from_dict({"hand": ["AS", "AH"], "blind": "The Flint"})
    assert score(s, [0, 1]).score == 27
    # The Club debuffs clubs (and wild cards)
    s = State.from_dict({"hand": ["AC", "AH"], "blind": "The Club"})
    assert score(s, [0, 1]).score == (10 + 11) * 2
    # The Psychic needs 5 cards; Chicot disables the boss
    s = State.from_dict({"hand": ["AS", "AH"], "blind": "The Psychic"})
    assert score(s, [0, 1]).score == 0
    s.jokers = State.from_dict({"hand": [], "jokers": ["Chicot"]}).jokers
    assert score(s, [0, 1]).score == 64
    # Plasma deck balances chips and mult: (32 + 2) / 2 = 17 -> 289
    s = State.from_dict({"hand": ["AS", "AH"], "deck_type": "Plasma Deck"})
    assert score(s, [0, 1]).score == 289
    # levels: Pair lvl 3 = 40 chips x 4 mult
    s = State.from_dict({"hand": ["AS", "AH"], "hand_levels": {"Pair": 3}})
    assert score(s, [0, 1]).score == (40 + 22) * 4
    # Red seal glass poly: 11 chips, per trigger x2 then x1.5 -> mult 1*3*3 = 9, chips 5+22
    s = State.from_dict({"hand": [{"rank": "A", "suit": "S", "enhancement": "glass", "edition": "poly", "seal": "red"}]})
    assert score(s, [0]).score == 27 * 9
    # Steel held: x1.5
    s = State.from_dict({"hand": ["AS", "AH", {"rank": "K", "suit": "S", "enhancement": "steel"}]})
    assert score(s, [0, 1]).score == 32 * 3
    # Observatory: Mercury in consumables x1.5 on Pair
    s = State.from_dict({"hand": ["AS", "AH"], "vouchers": ["Observatory"], "consumables": ["Mercury"]})
    assert score(s, [0, 1]).score == 32 * 3
    # regressions from the vanilla-source review
    def sc(d, idx):
        return score(State.from_dict(d), idx).score
    assert sc({"hand": ["7H", {"rank": "7", "suit": "D", "enhancement": "wild"}], "jokers": ["Seeing Double"]}, [0, 1]) == 96
    assert sc({"hand": ["KS"], "jokers": ["Seeing Double", "Smeared Joker"]}, [0]) == 30
    assert sc({"hand": ["2H", "3D", "4S", "5C", "6C"], "jokers": ["Flower Pot"], "blind": "The Head"}, range(5)) == 576
    assert sc({"hand": [{"rank": "K", "suit": "S", "enhancement": "stone"}], "jokers": ["Pareidolia", "Scary Face"]}, [0]) == 85
    assert sc({"hand": ["KS"], "jokers": [{"name": "Mime", "debuff": True}, "Baseball Card"]}, [0]) == 22
    from balatro.optimizer import _discard_candidates, best_plays
    s = State.from_dict({"hand": ["2S", "2H", "3C", "5D", "7S", "9H", "JC", "KD"]})
    pair_keep = [d for label, d in _discard_candidates(s, best_plays(s)) if label == "keep Pair"]
    assert pair_keep and not {0, 1} & set(pair_keep[0])
    print("engine ok")


if __name__ == "__main__":
    main()
