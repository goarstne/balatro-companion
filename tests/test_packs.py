"""Run: .venv/bin/python -m tests.test_packs"""
from dataclasses import replace

from balatro.models import Card, Joker, State
from balatro.packs import PackChoice, choose_from_pack, use_consumable


def test_planet_focus():
    s = State(hand=[], hand_levels={"Flush": 3})
    cards = [{"kind": "planet", "name": "Mercury"}, {"kind": "planet", "name": "Jupiter"},
             {"kind": "planet", "name": "Pluto"}]
    ch = choose_from_pack(s, "Celestial Pack", cards)
    assert ch.index == 1 and "Flush" in ch.why and not ch.targets


def test_planet_constellation():
    s = State(hand=[], hand_levels={"Flush": 3}, jokers=[Joker("Constellation")])
    cards = [{"kind": "planet", "name": "Pluto"}]  # non-focus planet still taken
    ch = choose_from_pack(s, "Celestial Pack", cards)
    assert ch.index == 0 and "Constellation" in ch.why


def test_black_hole_always():
    s = State(hand=[])
    cards = [{"kind": "spectral", "name": "Talisman"}, {"kind": "spectral", "name": "Black Hole"}]
    ch = choose_from_pack(s, "Spectral Pack", cards)
    assert ch.index == 1


def test_hermit():
    ch = choose_from_pack(State(hand=[], money=30), "Arcana Pack",
                          [{"kind": "tarot", "name": "The Hermit"}])
    assert ch.index == 0 and "$20" in ch.why


def test_temperance_no_jokers():
    ch = choose_from_pack(State(hand=[]), "Arcana Pack", [{"kind": "tarot", "name": "Temperance"}])
    assert ch.index is None  # $0 from no jokers is not worth the pick


def test_death_targets():
    s = State(hand=[Card(2, "H"), Card(13, "S", seal="red")])
    ch = choose_from_pack(s, "Arcana Pack", [{"kind": "tarot", "name": "Death"}])
    assert ch.index == 0 and ch.targets == [0, 1]  # weak -> red-seal King


def test_hanged_man():
    s = State(hand=[Card(2, "C"), Card(14, "S"), Card(3, "D")], hand_levels={"Three of a Kind": 2})
    ch = choose_from_pack(s, "Arcana Pack", [{"kind": "tarot", "name": "The Hanged Man"}])
    assert ch.index == 0 and ch.targets == [0, 2]


def test_suit_tarot_flush():
    s = State(hand=[Card(10, "H"), Card(9, "H"), Card(8, "H"), Card(7, "H"), Card(5, "C"), Card(4, "D")],
              hand_levels={"Flush": 3})
    ch = choose_from_pack(s, "Arcana Pack", [{"kind": "tarot", "name": "The Sun"}])
    assert ch.index == 0 and set(ch.targets) == {4, 5}


def test_suit_tarot_wrong_suit():
    s = State(hand=[Card(10, "H"), Card(9, "H"), Card(8, "H")], hand_levels={"Flush": 3})
    ch = choose_from_pack(s, "Arcana Pack", [{"kind": "tarot", "name": "The Star"}])  # Diamonds
    assert ch.index is None


def test_strength():
    s = State(hand=[Card(13, "H"), Card(13, "S"), Card(12, "D")])  # KKQ -> Q becomes K
    ch = choose_from_pack(s, "Arcana Pack", [{"kind": "tarot", "name": "Strength"}])
    assert ch.index == 0 and ch.targets == [2]


def test_chariot_steel_on_king():
    s = State(hand=[Card(13, "H"), Card(2, "C")], jokers=[Joker("Baron")])
    ch = choose_from_pack(s, "Arcana Pack", [{"kind": "tarot", "name": "The Chariot"}])
    assert ch.index == 0 and ch.targets == [0]


def test_wheel_needs_editionable_joker():
    assert choose_from_pack(State(hand=[], jokers=[Joker("Joker")]), "Arcana Pack",
                            [{"kind": "tarot", "name": "The Wheel of Fortune"}]).index == 0
    s = State(hand=[], jokers=[Joker("Joker", edition="foil")])
    assert choose_from_pack(s, "Arcana Pack", [{"kind": "tarot", "name": "The Wheel of Fortune"}]).index is None


def test_judgement_needs_slot():
    assert choose_from_pack(State(hand=[]), "Arcana Pack", [{"kind": "tarot", "name": "Judgement"}]).index == 0
    s = State(hand=[], jokers=[Joker("Joker")] * 5)
    assert choose_from_pack(s, "Arcana Pack", [{"kind": "tarot", "name": "Judgement"}]).index is None


def test_buffoon_best_tier():
    s = State(hand=[])
    cards = [{"kind": "joker", "name": "Joker"}, {"kind": "joker", "name": "Spare Trousers"}]
    ch = choose_from_pack(s, "Buffoon Pack", cards)
    assert ch.index == 1  # Spare Trousers (Tier A) beats Joker (Tier C)


def test_buffoon_no_slot():
    s = State(hand=[], jokers=[Joker("Joker")] * 5)
    assert choose_from_pack(s, "Buffoon Pack", [{"kind": "joker", "name": "Cavendish"}]).index is None


def test_buffoon_negative():
    s = State(hand=[], jokers=[Joker("Joker")] * 5)
    ch = choose_from_pack(s, "Buffoon Pack", [{"kind": "joker", "name": "Matador", "edition": "negative"}])
    assert ch.index == 0


def test_standard_seal_fits():
    s = State(hand=[Card(10, "H"), Card(9, "H"), Card(8, "H")], hand_levels={"Flush": 2})
    cards = [{"kind": "playing_card", "name": "Ace of Spades", "rank": 14, "suit": "S",
              "enhancement": None, "seal": None, "edition": None},
             {"kind": "playing_card", "name": "King of Hearts", "rank": 13, "suit": "H",
              "enhancement": None, "seal": "red", "edition": None}]
    assert choose_from_pack(s, "Standard Pack", cards).index == 1


def test_standard_skip_plain():
    s = State(hand=[Card(10, "H"), Card(9, "H"), Card(8, "H")], hand_levels={"Flush": 2})
    cards = [{"kind": "playing_card", "name": "Ace of Spades", "rank": 14, "suit": "S",
              "enhancement": None, "seal": None, "edition": None}]
    assert choose_from_pack(s, "Standard Pack", cards).index is None


def test_standard_plain_feeds_hologram():
    # run 20261101-105644 Z385: the pack was bought for Hologram, then skipped as "dilutes the deck"
    s = State(hand=[Card(10, "H"), Card(9, "H"), Card(8, "H")], hand_levels={"Flush": 2}, jokers=[Joker("Hologram")])
    cards = [{"kind": "playing_card", "name": "Ace of Spades", "rank": 14, "suit": "S",
              "enhancement": None, "seal": None, "edition": None}]
    assert choose_from_pack(s, "Standard Pack", cards).index == 0


def test_standard_steel_baron():
    s = State(hand=[Card(13, "H")], jokers=[Joker("Baron")])
    cards = [{"kind": "playing_card", "name": "King of Hearts", "rank": 13, "suit": "H",
              "enhancement": "steel", "seal": None, "edition": None}]
    assert choose_from_pack(s, "Standard Pack", cards).index == 0


def test_spectral_cryptid():
    s = State(hand=[Card(13, "S", enhancement="steel")])
    ch = choose_from_pack(s, "Spectral Pack", [{"kind": "spectral", "name": "Cryptid"}])
    assert ch.index == 0 and ch.targets == [0]


def test_spectral_ankh_single_strong():
    assert choose_from_pack(State(hand=[], jokers=[Joker("Cavendish")]), "Spectral Pack",
                            [{"kind": "spectral", "name": "Ankh"}]).index == 0
    s = State(hand=[], jokers=[Joker("Joker"), Joker("Cavendish")])
    assert choose_from_pack(s, "Spectral Pack", [{"kind": "spectral", "name": "Ankh"}]).index is None


def test_spectral_immolate_early():
    s = State(hand=[Card(2, "H"), Card(3, "C"), Card(4, "D"), Card(5, "S"), Card(6, "H")], money=5)
    assert choose_from_pack(s, "Spectral Pack", [{"kind": "spectral", "name": "Immolate"}]).index == 0
    s2 = State(hand=[Card(2, "H")] * 5, money=50)
    assert choose_from_pack(s2, "Spectral Pack", [{"kind": "spectral", "name": "Immolate"}]).index is None


def test_spectral_conservative_skip():
    assert choose_from_pack(State(hand=[]), "Spectral Pack",
                            [{"kind": "spectral", "name": "Ectoplasm"}]).index is None
    s = State(hand=[], money=50)  # Wraith: losing $50 is not worth it
    assert choose_from_pack(s, "Spectral Pack", [{"kind": "spectral", "name": "Wraith"}]).index is None


def test_spectral_wraith_early():
    s = State(hand=[], money=0)  # Wraith with $0 and a free slot is worth it
    assert choose_from_pack(s, "Spectral Pack", [{"kind": "spectral", "name": "Wraith"}]).index == 0


def test_use_consumable_planet():
    assert use_consumable(State(hand=[]), "Jupiter", "planet").index == 0


def test_use_consumable_hermit():
    assert use_consumable(State(hand=[], money=10), "The Hermit", "tarot").index == 0


def test_use_consumable_death_hold_or_use():
    assert use_consumable(State(hand=[Card(2, "H"), Card(3, "C")]), "Death", "tarot").index is None
    s = State(hand=[Card(2, "H"), Card(13, "S", seal="red")])
    ch = use_consumable(s, "Death", "tarot")
    assert ch.index == 0 and ch.targets == [0, 1]


def test_use_consumable_wheel_hold():
    s = State(hand=[], jokers=[Joker("Joker", edition="foil")])
    assert use_consumable(s, "The Wheel of Fortune", "tarot").index is None


def test_empty_pack():
    ch = choose_from_pack(State(hand=[]), "Arcana Pack", [])
    assert ch.index is None and isinstance(ch, PackChoice)


def test_death_copies_right_onto_left():
    # live run 20261030-194631 move 33: 4S[glass] left of 2H. Death copies the RIGHT card onto the left
    # one. Targets now carry weak/source roles; the autopilot must reorder when weak sits on the right.
    s = State(hand=[Card(9, "C"), Card(4, "S", enhancement="glass"), Card(2, "H")])
    ch = use_consumable(s, "Death", "tarot")
    assert ch.targets == [2, 1], ch
    s = State(hand=[Card(4, "S", enhancement="glass"), Card(2, "H")])  # nothing weaker left of it
    assert use_consumable(s, "Death", "tarot").targets == [1, 0]


def test_fool_needs_last_card():
    assert use_consumable(State(hand=[]), "The Fool", "tarot").index is None
    assert use_consumable(State(hand=[], last_tarot_planet="The Fool"), "The Fool", "tarot").index is None
    ch = use_consumable(State(hand=[], last_tarot_planet="Jupiter"), "The Fool", "tarot")
    assert ch.index == 0 and "Jupiter" in ch.why


def test_held_generator_with_full_tray():
    # 2/2 slots: using the held High Priestess frees its own slot for the planets
    s = State(hand=[], consumables=["The High Priestess", "Pluto"])
    assert use_consumable(s, "The High Priestess", "tarot").index == 0


def test_nebula_deck_slots():
    s = State(hand=[], consumables=["Pluto"], deck_type="Nebula Deck")  # Nebula: 1 slot
    ch = choose_from_pack(s, "Arcana Pack", [{"kind": "tarot", "name": "The Emperor"}])
    assert ch.index is None


def test_soul_in_arcana_pack():
    cards = [{"kind": "tarot", "name": "The Hermit"}, {"kind": "spectral", "name": "The Soul"}]
    assert choose_from_pack(State(hand=[], money=30), "Arcana Pack", cards).index == 1
    cards = [{"kind": "spectral", "name": "Black Hole"}, {"kind": "planet", "name": "Pluto"}]
    assert choose_from_pack(State(hand=[]), "Celestial Pack", cards).index == 0
    full = State(hand=[], money=30, jokers=[Joker("Joker")] * 5)  # no slot for The Soul: Hermit
    cards = [{"kind": "spectral", "name": "The Soul"}, {"kind": "tarot", "name": "The Hermit"}]
    assert choose_from_pack(full, "Arcana Pack", cards).index == 1


def test_dead_suit_tarot_frees_full_tray():
    # live run 20261030-194631 move 79: both slots held The World + The Star (focus Two Pair) for the rest of
    # the run - no planet could be bought any more
    s = State(hand=[Card(13, "S"), Card(13, "D"), Card(3, "H"), Card(9, "C")], consumables=["The World", "The Star"])
    ch = use_consumable(s, "The World", "tarot")
    assert ch.index == 0 and ch.targets == [2], ch  # the 3H, the weakest non-Spade
    s.consumables = ["The World"]  # free slot: keep it (a flush build may still come)
    assert use_consumable(s, "The World", "tarot").index is None
    from balatro.packs import _junk_recolour
    s.jokers = [Joker("Lusty Joker")]  # hearts matter: the next junk card instead
    assert _junk_recolour(s, "S") == 3


def test_tarot_rules_and_money_scale():
    # vanilla game.lua: c_magician max_highlighted = 2 (was 1)
    from balatro.packs import ENHANCE
    assert ENHANCE["The Magician"][1] == 2 and ENHANCE["The Empress"][1] == 2 and ENHANCE["Justice"][1] == 1
    # live run 20261030-211755 moves 87/89: The Hermit (+$20 = 20 "points") beat every enhancement tarot;
    # both runs never took Magician/Empress/Hierophant/Chariot. Money now sits on the same 1-10 scale.
    hand = [Card(r, s) for r, s in [(13, "S"), (13, "H"), (9, "D"), (7, "C"), (4, "H"), (3, "S"), (2, "D"), (10, "C")]]
    rich = State(hand=hand, money=25)
    cards = [{"kind": "tarot", "name": "The Hermit"}, {"kind": "tarot", "name": "The Chariot"}]
    assert choose_from_pack(rich, "Arcana Pack", cards).index == 1  # Steel King (4) > +$20 (3.3)
    cards = [{"kind": "tarot", "name": "Temperance"}, {"kind": "tarot", "name": "The Empress"}]
    assert choose_from_pack(replace(rich, jokers=[Joker("Joker")] * 4), "Arcana Pack", cards).index == 1
    poor = State(hand=hand, money=20)
    assert choose_from_pack(poor, "Arcana Pack", [{"kind": "tarot", "name": "The Hermit"},
                                                   {"kind": "tarot", "name": "The Tower"}]).index == 0


def test_spectral_pack_and_crystal_ball():
    from balatro.strategy import _adjust_pack, _adjust_voucher
    s = State(hand=[], jokers=[Joker("Joker")] * 3)
    assert _adjust_pack(s, "Spectral Pack", 5, "Pair")[0] > 0  # every card is skippable: no "risk" malus
    full = State(hand=[], consumables=["The World", "The Star"])
    assert _adjust_voucher(full, "Crystal Ball", 3, "Pair")[0] > 0
    assert _adjust_voucher(State(hand=[]), "Crystal Ball", 3, "Pair")[0] < 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("packs ok")
