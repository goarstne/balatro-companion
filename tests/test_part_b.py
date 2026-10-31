"""Jokers 76-150 checks, expected scores computed by hand from wiki values.
Run: .venv/bin/python -m tests.test_part_b"""
import balatro.jokers as J
from balatro.data import JOKER_NAMES
from balatro.engine import score
from balatro.models import State


def s(hand, jokers, idx, **kw):
    return score(State.from_dict({"hand": hand, "jokers": jokers, **kw}), idx)


def main():
    assert all(n in J.REGISTRY for n in JOKER_NAMES[75:])

    # Hanging Chad: first KS scores 3x, Photograph X2 on each of its triggers.
    # Pair 10/2; chips 10 + 3*10 + 10 = 50; mult 2 * 2^3 = 16
    assert s(["KS", "KH"], ["Photograph", "Hanging Chad"], [0, 1]).score == 800
    # Sock and Buskin + Smiley: both Ks twice -> chips 10+40=50, mult 2+4*5=22
    assert s(["KS", "KH"], ["Smiley Face", "Sock and Buskin"], [0, 1]).score == 1100
    # Baseball Card: X1.5 after the Uncommon Sock and Buskin, not after common Smiley -> 50*33
    assert s(["KS", "KH"], ["Smiley Face", "Sock and Buskin", "Baseball Card"], [0, 1]).score == 1650
    # Flush containing a Pair: 35+43=78 chips, 4 x2 (Duo) x2 (Tribe)
    assert s(["AH", "AH", "5H", "7H", "9H"], ["The Duo", "The Tribe"], range(5)).score == 1248
    # Flower Pot: Four of a Kind with all 4 suits: (60+44) * 7*3
    assert s(["AS", "AH", "AD", "AC", "KS"], ["Flower Pot"], range(4)).score == 2184
    # Full House S,H,D,S + wild K fills Clubs: (40+33+20) * 4*3 ; without wild only x4
    wild = {"rank": "K", "suit": "S", "enhancement": "wild"}
    assert s(["AS", "AH", "AD", "KS", wild], ["Flower Pot"], range(5)).score == 1116
    assert s(["AS", "AH", "AD", "KS", "KS"], ["Flower Pot"], range(5)).score == 372
    # Seeing Double: Club + Spade scoring -> (10+22) * 2*2
    assert s(["AS", "AC"], ["Seeing Double"], [0, 1]).score == 128
    assert s(["AS", "AH"], ["Seeing Double"], [0, 1]).score == 64
    # Bloodstone EV X1.25 per Heart (X1.5 with Oops!): 32 * 2*1.25^2 ; 32 * 2*1.5^2
    assert s(["AH", "AH"], ["Bloodstone"], [0, 1]).score == 100
    assert s(["AH", "AH"], ["Bloodstone", "Oops! All 6s"], [0, 1]).score == 144
    # Triboulet: pair of Queens (10+20) * 2*2*2
    assert s(["QS", "QH"], ["Triboulet"], [0, 1]).score == 240
    # Shoot the Moon: high card A (5+11), held QH QD +26 Mult
    assert s(["AS", "QH", "QD"], ["Shoot the Moon"], [0]).score == 16 * 27
    # Acrobat only on final hand
    assert s(["AS"], ["Acrobat"], [0], hands_left=1).score == 48
    assert s(["AS"], ["Acrobat"], [0], hands_left=3).score == 16
    # Spare Trousers scales before scoring: two pair (20+42) * (2+6)
    assert s(["AS", "AH", "KS", "KH"], [{"name": "Spare Trousers", "vars": {"mult": 4}}], range(4)).score == 496
    assert s(["AS", "AH"], [{"name": "Spare Trousers", "vars": {"mult": 4}}], [0, 1]).score == 32 * 6
    # Blueprint copies Trousers' new value without scaling twice: 62 * (2+6+6)
    assert s(["AS", "AH", "KS", "KH"], ["Blueprint", {"name": "Spare Trousers", "vars": {"mult": 4}}],
             range(4)).score == 868
    # Brainstorm copies leftmost The Duo: 32 * 2*2*2
    assert s(["AS", "AH"], ["The Duo", "Brainstorm"], [0, 1]).score == 256
    # Midas Mask: bonus K becomes Gold before scoring (loses +30); Golden Ticket pays $4 x2
    bonus = {"rank": "K", "suit": "S", "enhancement": "bonus"}
    assert s([bonus, "KH"], [], [0, 1]).score == 120
    r = s([bonus, "KH"], ["Midas Mask", "Golden Ticket"], [0, 1])
    assert (r.score, r.money) == (60, 8)
    # Wee Joker scales on each scored 2: (10+4+16) * 2
    assert s(["2S", "2H"], ["Wee Joker"], [0, 1]).score == 60
    # Swashbuckler: Baseball Card sells for 4 -> 16 * (1+4)
    assert s(["AS"], ["Swashbuckler", "Baseball Card"], [0]).score == 80
    # Stuntman +250, Walkie Talkie on a 10
    assert s(["TS"], ["Stuntman", "Walkie Talkie"], [0]).score == (5 + 10 + 250 + 10) * 5
    print("test_part_b: all passed")


if __name__ == "__main__":
    main()
