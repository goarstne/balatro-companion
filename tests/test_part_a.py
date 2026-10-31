"""Jokers 1-75 scoring checks (hand-computed from wiki values). Run: .venv/bin/python -m tests.test_part_a"""
from balatro.data import JOKER_NAMES
from balatro.engine import score
from balatro.jokers import REGISTRY
from balatro.models import State


def sc(hand, idx, jokers, **kw):
    return score(State.from_dict({"hand": hand, "jokers": jokers, **kw}), idx).score


def main():
    assert all(n in REGISTRY for n in JOKER_NAMES[:75])
    S = {"rank": "2", "suit": "C"}

    # suit joker: only KD scores +3.  (10+10+10) x (2+3)
    assert sc(["KD", "KS", "4C"], [0, 1], ["Greedy Joker"]) == 150
    # contains + Half Joker: pair of 5s, 3 cards. (10+5+5+50) x (2+8+20)
    assert sc(["5H", "5S", "2C"], [0, 1, 2], ["Jolly Joker", "Sly Joker", "Half Joker"]) == 2100
    assert sc(["5H", "5S", "2C", "3D"], [0, 1, 2, 3], ["Half Joker"]) == 20 * 2
    # Raised Fist: lowest held = rightmost 3 -> +6.  32 x 8
    hand = ["AH", "AD", "3D", "7C", "3S", "KH"]
    assert sc(hand, [0, 1], ["Raised Fist"]) == 256
    assert sc(hand, [0, 1], ["Raised Fist"], blind="The Goad") == 64      # that 3S is debuffed
    # Mime + Steel: x1.5 twice. 14 x 4.5 ; + Raised Fist on 4C (+8 twice): 14 x 20.5
    hand = ["2H", "2D", {"rank": "K", "suit": "S", "enhancement": "steel"}, "4C"]
    assert sc(hand, [0, 1], ["Mime"]) == 63
    assert sc(hand, [0, 1], ["Mime", "Raised Fist"]) == 287
    # Hack + red seal: 3H x3, 3S x2 -> (10+15) x 2
    assert sc([{"rank": "3", "suit": "H", "seal": "red"}, "3S", "KD"], [0, 1], ["Hack"]) == 50
    # Fibonacci: 8s +8 each -> 26 x 18 ; with Hack on 5s -> (10+20) x (2+32)
    assert sc(["8H", "8S"], [0, 1], ["Fibonacci"]) == 468
    assert sc(["5S", "5D"], [0, 1], ["Hack", "Fibonacci"]) == 1020
    # Ride the Bus: 3 -> 4 on a face-free hand, reset by face cards
    assert sc(["2H", "2S"], [0, 1], [{"name": "Ride the Bus", "vars": {"mult": 3}}]) == 14 * 6
    assert sc(["KH", "KS"], [0, 1], [{"name": "Ride the Bus", "vars": {"mult": 3}}]) == 30 * 2
    # Blueprint copies the updated value but does not scale it again: 14 x (2+4+4)
    assert sc(["2H", "2S"], [0, 1], ["Blueprint", {"name": "Ride the Bus", "vars": {"mult": 3}}]) == 140
    # Scholar: (10+11+11+40) x (2+8); Blueprint copy doubles it: 112 x 18
    assert sc(["AH", "AS"], [0, 1], ["Scholar"]) == 720
    assert sc(["AH", "AS"], [0, 1], ["Blueprint", "Scholar"]) == 2016
    # Abstract: 3 jokers -> +9, then +4 +4: 14 x 19
    assert sc(["2H", "2S"], [0, 1], ["Abstract Joker", "Joker", "Joker"]) == 266
    # Joker Stencil: 5 slots, 2 jokers, itself counts -> X4 then Joker +4: 14 x 12
    assert sc(["2H", "2S"], [0, 1], ["Joker Stencil", "Joker"]) == 168
    # Supernova: Pair played 2x before -> +3
    assert sc(["2H", "2S"], [0, 1], ["Supernova"], played_counts={"Pair": 2}) == 70
    # Obelisk: Flush still most played -> 1.4 -> 1.6
    assert sc(["2H", "2S"], [0, 1], [{"name": "Obelisk", "vars": {"xmult": 1.4}}],
              played_counts={"Pair": 3, "Flush": 5}) == 44
    # Misprint expected value, Loyalty active, Card Sharp
    assert sc(["2H", "2S"], [0, 1], ["Misprint"]) == 189
    assert sc(["2H", "2S"], [0, 1], [{"name": "Loyalty Card", "vars": {"remaining": 0}}]) == 112
    assert sc(["2H", "2S"], [0, 1], ["Loyalty Card"]) == 28
    assert sc(["2H", "2S"], [0, 1], ["Card Sharp"], round_hand_types=["Pair"]) == 84
    # Blackboard: held all black (Wild counts) vs a held Heart
    assert sc(["2H", "2D", "3S", {"rank": "4", "suit": "H", "enhancement": "wild"}], [0, 1], ["Blackboard"]) == 84
    assert sc(["2H", "2D", "3S", "4H"], [0, 1], ["Blackboard"]) == 28
    # Vampire strips Glass/Mult before scoring: 30 x (2 x 1.2)
    st = State.from_dict({"hand": [{"rank": "K", "suit": "H", "enhancement": "glass"},
                                   {"rank": "K", "suit": "S", "enhancement": "mult"}], "jokers": ["Vampire"]})
    assert score(st, [0, 1]).score == 72
    assert st.hand[0].enhancement == "glass"                      # state not mutated
    # Hiker: red-sealed 2H scores 2 then 7. (10+2+7+2) x 2
    st = State.from_dict({"hand": [{"rank": "2", "suit": "H", "seal": "red"}, "2S"], "jokers": ["Hiker"]})
    assert score(st, [0, 1]).score == 42 and st.hand[0].bonus == 0
    # Blue Joker: 52 - 2 in hand = 50 cards -> +100 chips
    assert sc(["2H", "2S"], [0, 1], ["Blue Joker"]) == 114 * 2
    # Steel Joker: 2 steel in full deck -> X1.4
    deck = [S, S, {"rank": "3", "suit": "C", "enhancement": "steel"}, {"rank": "4", "suit": "C", "enhancement": "steel"}]
    assert sc(["2H", "2S"], [0, 1], ["Steel Joker"], full_deck=deck) == 39
    # Banner / Mystic Summit
    assert sc(["2H", "2S"], [0, 1], ["Banner", "Mystic Summit"], discards_left=2) == 74 * 2
    assert sc(["2H", "2S"], [0, 1], ["Banner", "Mystic Summit"], discards_left=0) == 14 * 17
    # Dusk on final hand: pair of 2s retriggered -> (10+8) x 2
    assert sc(["2H", "2S"], [0, 1], ["Dusk"], hands_left=1) == 36
    assert sc(["2H", "2S"], [0, 1], ["Dusk"], hands_left=2) == 28
    print("test_part_a: all passed")


if __name__ == "__main__":
    main()
