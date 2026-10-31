"""Run: .venv/bin/python -m tests.test_strategy"""
from dataclasses import replace

from balatro.data import HAND_TYPES, JOKER_NAMES
from balatro.jokers.part_b import _sell_value
from balatro.models import Joker, State
from balatro.shop import Offer, ShopAction, _reroll_goal, advise, default_price, evaluate, shop_plan
from balatro.strategy import (ECON, FLAT, HAND_JOKERS, SCALING, TAG_POINTS, XMULT, _adjust_pack, adjust_offers,
                              blind_choice, economy_note, focus_hand, next_ante_score, required_score, sell_candidates)


def test_names_valid():
    names = FLAT | SCALING | XMULT | ECON | set().union(*HAND_JOKERS.values())
    assert names <= set(JOKER_NAMES), names - set(JOKER_NAMES)
    assert set(HAND_JOKERS) <= set(HAND_TYPES)
    assert all(t.endswith(" Tag") for t in TAG_POINTS)


def test_required_score():
    assert required_score(1, "small") == 300
    assert required_score(1, "big") == 450
    assert required_score(8, "boss") == 100000
    assert required_score(3, "The Wall") == 8000
    assert required_score(9, "small") == 110000  # endless ante 9 per balatrowiki
    # Stake tables & blind_chips scaling (section 12.2, 19.1)
    assert required_score(8, "boss", stake="green") == 200000
    assert required_score(8, "boss", "gold") == 400000
    assert required_score(3, "boss", 3200) == 6400
    s_gold = State(hand=[], blind="Big Blind", blind_chips=1500)  # ante 2 purple/gold base = 1000
    assert required_score(2, "boss", s_gold) == 2000
    assert next_ante_score(s_gold, 2, "small") == 3200
    s_plasma = State(hand=[], deck_type="Plasma Deck")
    assert required_score(2, "boss", s_plasma) == 3200


def test_focus_hand():
    assert focus_hand(State(hand=[])) == "Pair"
    assert focus_hand(State(hand=[], hand_levels={"Flush": 3})) == "Flush"
    assert focus_hand(State(hand=[], jokers=[Joker("Crazy Joker")], played_counts={"Pair": 2})) == "Straight"


def test_blind_choice():
    s = State(hand=[], money=4)
    assert blind_choice(s, 2, "boss", "Negative Tag")[0] == "play"
    assert blind_choice(s, 1, "small", "Juggle Tag")[0] == "play"
    assert blind_choice(s, 1, "small", "Investment Tag")[0] == "skip"
    assert blind_choice(s, 6, "small", "Investment Tag")[0] == "play"  # no jokers can't beat ante-6 big
    # Cost calculation (section 11): full interest + scaling jokers raises the bar against skipping
    rich_scaling = State(hand=[], money=25, jokers=[Joker("Green Joker", vars={"mult": 15}),
                                                    Joker("Runner", vars={"chips": 90}), Joker("Cavendish")])
    assert blind_choice(rich_scaling, 4, "big", "Coupon Tag")[0] == "play"
    # Throwback rewards skipping
    with_throwback = State(hand=[], money=10, jokers=[Joker("Throwback", vars={"x": 2}), Joker("Cavendish")])
    assert blind_choice(with_throwback, 2, "small", "Coupon Tag")[0] == "skip"


def test_adjust_offers():
    s = State(hand=[], hand_levels={"Flush": 3})
    offers = [Offer("planet", "Jupiter", 3, 4, "x"), Offer("planet", "Mercury", 3, 4, "x"),
              Offer("joker", "Droll Joker", 6, 3, "x"), Offer("joker", "Cavendish", 4, 10, "x")]
    early = {o.name: o for o in adjust_offers(s, offers, 1)}
    assert early["Jupiter"].value > early["Mercury"].value
    assert early["Droll Joker"].value == 3 + 2 + 2 and "focus hand Flush" in early["Droll Joker"].why
    late = {o.name: o for o in adjust_offers(s, offers, 6)}
    assert late["Cavendish"].value > early["Cavendish"].value and late["Droll Joker"].value < early["Droll Joker"].value
    assert offers[0].value == 4  # input untouched

    # Joker synergies & situational vouchers
    chad = State(hand=[], money=12, jokers=[Joker("Hanging Chad")])
    syn = {o.name: o for o in adjust_offers(chad, [Offer("joker", "Photograph", 5, 8, "x"),
                                                   Offer("voucher", "Seed Money", 10, 5, "x")], 2)}
    assert syn["Photograph"].value > 8 and "synergy" in syn["Photograph"].why
    assert syn["Seed Money"].value < 5  # too poor for Seed Money


def test_economy_note():
    assert "maximum" in economy_note(30, 4)
    assert "$2 more" in economy_note(13, 3) and "Interest $2" in economy_note(13, 3)


def test_sell_candidates():
    s = State(hand=[], jokers=[Joker("Joker"), Joker("Cavendish"), Joker("Golden Joker"),
                               Joker("Greedy Joker", eternal=True)])
    assert sell_candidates(s, 1) == []
    late = dict(sell_candidates(s, 7))
    assert set(late) == {0, 2}, late


def test_weak_run_buys_under_25():
    """A run that is too weak buys strength now, even below $25 / across interest steps."""
    weak = State(hand=[], money=11)  # $11 -> $2 interest; buying a $5-$6 joker drops below $10 step
    items = [{"kind": "joker", "name": "Jolly Joker", "price": 4},
             {"kind": "planet", "name": "Pluto", "price": 3}]
    plan = shop_plan(weak, items, ante=2, samples=15)
    assert any(a.kind == "buy" and a.index == 0 for a in plan), plan
    assert plan[-1].kind == "leave"


def test_strong_run_saves_for_interest():
    """A strong run protects interest steps ($5 steps up to $25), spends a surplus > $25 on permanent things."""
    strong = State(hand=[], money=15, hand_levels={"Flush": 4},
                   jokers=[Joker("Cavendish"), Joker("Gros Michel"), Joker("The Tribe")])
    mediocre = [{"kind": "joker", "name": "Banner", "price": 5},
                {"kind": "booster_pack", "name": "Standard Pack", "price": 4}]
    plan = shop_plan(strong, mediocre, ante=2, samples=15)
    assert [a.kind for a in plan] == ["leave"], plan

    # With > $25 surplus, the same strong run buys its focus-hand planet without losing interest
    rich_strong = State(hand=[], money=30, hand_levels={"Flush": 4},
                        jokers=[Joker("Cavendish"), Joker("Gros Michel"), Joker("The Tribe")])
    planet_shop = [{"kind": "planet", "name": "Jupiter", "price": 3},
                   {"kind": "planet", "name": "Mercury", "price": 3}]
    plan_rich = shop_plan(rich_strong, planet_shop, ante=2, samples=15)
    assert [a.kind for a in plan_rich] == ["buy", "leave"] and plan_rich[0].index == 0, plan_rich


def test_full_slots_sell_then_buy():
    """Full slots: sell the weakest non-eternal joker before buying; never sell an eternal one."""
    s = State(hand=[], money=5, joker_slots=5, jokers=[
        Joker("Greedy Joker", eternal=True),
        Joker("Joker"),
        Joker("Jolly Joker"),
        Joker("Sly Joker"),
        Joker("Zany Joker"),
    ])
    items = [{"kind": "joker", "name": "Cavendish", "price": 6}]  # needs $5 + $1 sell value of Joker
    plan = shop_plan(s, items, ante=5, samples=15)
    kinds = [a.kind for a in plan]
    assert kinds == ["sell", "buy", "leave"], plan
    assert plan[0].index != 0 and not s.jokers[plan[0].index].eternal  # eternal index 0 untouched
    assert plan[1].index == 0

    # All eternal: cannot sell any joker for a normal shop joker
    all_eternal = State(hand=[], money=15, joker_slots=5,
                        jokers=[Joker("Joker", eternal=True) for _ in range(5)])
    assert [a.kind for a in shop_plan(all_eternal, items, ante=5, samples=10)] == ["leave"]

    # Negative joker fits even when all 5 slots are full of eternal jokers
    neg_items = [{"kind": "joker", "name": "Cavendish", "price": 9, "edition": "negative"}]
    plan_neg = shop_plan(all_eternal, neg_items, ante=5, samples=10)
    assert [a.kind for a in plan_neg] == ["buy", "leave"], plan_neg


def test_reroll_only_with_goal_and_money():
    """Reroll only with a concrete goal and enough money (incl. money left for buys); the plan then ends with leave."""
    junk_shop = [{"kind": "planet", "name": "Pluto", "price": 3},
                 {"kind": "playing_card", "name": "2 of Clubs", "price": 1}]
    # Rich enough + open slots / need xMult -> reroll with concrete reason, then leave
    need_jokers = State(hand=[], money=32, jokers=[Joker("Joker")])
    plan = shop_plan(need_jokers, junk_shop, ante=4, reroll_cost=5, samples=15)
    assert [a.kind for a in plan] == ["reroll", "leave"], plan
    assert plan[0].index == -1 and len(plan[0].why) > 10

    # Too little money to reroll and still buy a card -> no reroll
    broke = State(hand=[], money=7, jokers=[Joker("Joker")])
    assert "reroll" not in [a.kind for a in shop_plan(broke, junk_shop, ante=4, reroll_cost=5, samples=10)]

    # No goal: 5 strong S-tier jokers + full consumables -> no reroll even with $40
    maxed = State(hand=[], money=40, consumables=["Jupiter", "Jupiter"],
                  jokers=[Joker("Cavendish"), Joker("Blueprint"), Joker("Brainstorm"),
                          Joker("Triboulet"), Joker("Hologram", vars={"xmult": 3})])
    assert "reroll" not in [a.kind for a in shop_plan(maxed, junk_shop, ante=5, reroll_cost=5, samples=10)]


def test_celestial_pack_on_clear_focus():
    """A Celestial pack is bought with a clear focus hand."""
    focused = State(hand=[], money=28, hand_levels={"Flush": 4}, jokers=[Joker("Droll Joker")])
    items = [{"kind": "booster_pack", "name": "Celestial Pack", "price": 4},
             {"kind": "booster_pack", "name": "Standard Pack", "price": 4}]
    plan = shop_plan(focused, items, ante=3, samples=15)
    buys = [a.index for a in plan if a.kind == "buy"]
    assert 0 in buys and 1 not in buys, plan
    assert plan[-1].kind == "leave"


def test_celestial_focus_bonus_needs_level_gain():
    """Run 20261031-091942 moves 70/71 (like 082933 moves 92/105): Jumbo Celestial to "level focus hand Pair", Mercury
    added +0% – the Pair always loses to Two Pair/Straight here."""
    s = State(hand=[], money=40, hand_size=10,
              jokers=[Joker("Jolly Joker"), Joker("Juggler"), Joker("Abstract Joker"), Joker("Clever Joker")],
              played_counts={"Pair": 6, "Two Pair": 4, "Straight": 2, "Flush": 1})
    assert focus_hand(s) == "Pair"
    assert _adjust_pack(s, "Jumbo Celestial Pack", 3, "Pair")[0] < 3


def test_budget_and_consumable_slots_and_leave():
    """Never spend more than there is; respect consumable slots; the plan always ends with leave."""
    s = State(hand=[], money=7, consumables=["Mercury"])  # 1 of 2 consumable slots free
    items = [
        {"kind": "joker", "name": "Gros Michel", "price": 5},
        {"kind": "joker", "name": "Spare Trousers", "price": 6},
        {"kind": "planet", "name": "Mercury", "price": 3},
        {"kind": "tarot", "name": "The Hermit", "price": 3},
    ]
    plan = shop_plan(s, items, ante=2, reroll_cost=5, samples=15)
    assert plan and plan[-1].kind == "leave" and plan[-1].index == -1
    spent = 0
    cons_bought = 0
    for a in plan:
        assert isinstance(a, ShopAction)
        if a.kind == "sell":
            spent -= _sell_value(s.jokers[a.index])
        elif a.kind == "buy":
            it = items[a.index]
            spent += it.get("price") or default_price(it)
            if it["kind"] in ("planet", "tarot", "spectral"):
                cons_bought += 1
        elif a.kind == "reroll":
            spent += 5
    assert spent <= s.money, (spent, s.money, plan)
    assert cons_bought <= 1, cons_bought  # only 1 free consumable slot

    # Full consumable slots (2/2): cannot buy planets/tarots directly from the shop
    full_cons = State(hand=[], money=30, hand_levels={"Flush": 4}, consumables=["Jupiter", "Jupiter"])
    p_plan = shop_plan(full_cons, [{"kind": "planet", "name": "Jupiter", "price": 3}], ante=3, samples=10)
    assert [a.kind for a in p_plan] == ["leave"], p_plan
    assert "RECOMMENDATION:" in advise(s, items, ante=2, reroll_cost=5, samples=10)


def test_autopilot_plans_only_executable():
    """Autopilot: a joker lands rightmost (no reordering), never a boss-reroll voucher (always Select)."""
    full = State(hand=[], money=17, jokers=[Joker("Mime"), Joker("Baseball Card"), Joker("Hanging Chad"),
                                            Joker("Flash Card", vars={"mult": 16}), Joker("Photograph")])
    bp = [{"kind": "joker", "name": "Blueprint", "price": 10}]
    manual = evaluate(full, bp, samples=10)[0]
    auto = evaluate(full, bp, samples=10, auto=True)[0]
    assert manual.why.startswith("Tier S") and auto.why.startswith("Tier D") and auto.value < manual.value, auto
    assert "buy" not in [a.kind for a in shop_plan(full, bp, ante=6, samples=10, auto=True)]
    # with a slot left behind it, a later joker lands to its right: Blueprint keeps its tier
    open_slots = State(hand=[], money=17, jokers=[Joker("Photograph")])
    assert evaluate(open_slots, bp, samples=10, auto=True)[0].why.startswith("Tier S")

    rich = State(hand=[], money=49, hand_levels={"Flush": 4},
                 jokers=[Joker("Cavendish"), Joker("Gros Michel"), Joker("The Tribe")])
    dc = [{"kind": "voucher", "name": "Director's Cut", "price": 10}]
    assert "buy" in [a.kind for a in shop_plan(rich, dc, ante=2, samples=10)]
    assert "buy" not in [a.kind for a in shop_plan(rich, dc, ante=2, samples=10, auto=True)]


def test_hand_xmult_off_focus():
    """Run 20261030-211755 move 232: Cloud 9 sold for The Family (Tier S, now +0%) – focus High Card lv 7."""
    s = State(hand=[], money=40, hand_levels={"High Card": 7},
              jokers=[Joker("Hanging Chad"), Joker("Walkie Talkie", vars={"chips": 10}),
                      Joker("Green Joker", vars={"mult": 23}), Joker("Midas Mask"), Joker("Cloud 9")])
    fam = {o.name: o for o in adjust_offers(s, [Offer("joker", "The Family", 8, 10, "Tier S, now +0% score/hand")], 6)}
    assert fam["The Family"].value == 3 and "focus hand High Card" in fam["The Family"].why, fam
    plan = shop_plan(s, [{"kind": "joker", "name": "The Family", "price": 8}], ante=6, samples=10, auto=True)
    assert [a.kind for a in plan if a.kind in ("sell", "buy")] == [], plan
    # on-focus (Flush) The Tribe keeps Tier S/A + xMult bonus
    fl = State(hand=[], hand_levels={"Flush": 4})
    tribe = adjust_offers(fl, [Offer("joker", "The Tribe", 8, 7, "Tier A, now +0% score/hand")], 6)[0]
    assert tribe.value > 7, tribe


def test_vouchers_bought():
    """Run 20261030-220724: no voucher bought – Grabber (9.5) at $11 move 11, Crystal Ball at $63 move 91 (threshold 12/5)."""
    j = [Joker("Ice Cream", vars={"chips": 100}), Joker("Gros Michel")]
    grab = [{"kind": "voucher", "name": "Grabber", "price": 10}]
    assert "buy" in [a.kind for a in shop_plan(State(hand=[], money=11, jokers=j), grab, ante=1, samples=10, auto=True)]
    ball = [{"kind": "voucher", "name": "Crystal Ball", "price": 10}]
    assert "buy" in [a.kind for a in shop_plan(State(hand=[], money=63, jokers=j), ball, ante=2, samples=10, auto=True)]
    assert "buy" not in [a.kind for a in shop_plan(State(hand=[], money=15, jokers=j), ball, ante=2, samples=10, auto=True)]


def test_interest_weighs_less_late():
    """Run 20261031-074053: from ante 5 hardly any rounds left for interest, none in Endless Mode (ante 9+)."""
    from balatro.shop import _lost_steps
    s = State(hand=[], money=25)
    assert [_lost_steps(s, 25, 10, a) for a in (1, 4, 6, 7, 8, 9, 12)] == [2, 2, 1, 1, 0, 0, 0]
    # run 101337: with Seed Money ($50 cap) it sat on $43-60 while Celestial packs stayed in the shop
    seed = State(hand=[], money=51, vouchers=["Seed Money"])
    assert _lost_steps(seed, 51, 4, 3) == 0 and _lost_steps(seed, 37, 6, 3) == 0
    assert _lost_steps(seed, 25, 10, 3) == 2  # below $25 every step still counts


def test_empty_slots_buy_scoring_joker():
    """Run 20261031-074053 move 32: 2/5 jokers, $20, Raised Fist (8.9, +47%) left in the shop for $1 interest."""
    s = State(hand=[], money=20, hand_levels={"Two Pair": 3}, jokers=[Joker("Gros Michel"), Joker("Hanging Chad")])
    fist = [{"kind": "joker", "name": "Raised Fist", "price": 5}]
    assert [a.kind for a in shop_plan(s, fist, ante=2, samples=15, auto=True)] == ["buy", "leave"]
    # an economy joker with no score gain still has to clear the interest bar
    card = [{"kind": "joker", "name": "Business Card", "price": 4}]
    assert [a.kind for a in shop_plan(s, card, ante=2, samples=15, auto=True)] == ["leave"]


def test_spectral_pack_bought_like_arcana():
    """9 runs, 24 Spectral pack offers, none bought (value 3.0 vs. Arcana 6.0). Run 101337 move 85: $53, ante 4."""
    from balatro.shop import _min_buy_value, offers_for
    s = State(hand=[], money=53, jokers=[Joker("Scary Face"), Joker("Hack"), Joker("Photograph")])
    spec = offers_for(s, [{"kind": "booster_pack", "name": "Spectral Pack", "price": 4}], 4)[0]
    assert spec.value >= _min_buy_value(s, spec, 53, 4, 4, est=20000, need_curr=10000, need_next=16500), spec
    vals = {o.name: o.value for o in adjust_offers(s, evaluate(s, [{"kind": "booster_pack", "name": "Spectral Pack", "price": 4},
                                                                  {"kind": "booster_pack", "name": "Arcana Pack", "price": 4}],
                                                                 samples=10), 4)}
    assert vals["Spectral Pack"] >= vals["Arcana Pack"] - 1, vals


def test_glyph_vouchers_cost_resources():
    """Run 20261031-091942 moves 30/125: Hieroglyph and Petroglyph bought, boss rounds tight afterwards (docs/STRATEGY.md §3)."""
    from balatro.strategy import blind_decision
    s = State(hand=[], money=40)
    vals = {o.name: o.value for o in adjust_offers(s, [Offer("voucher", "Hieroglyph", 10, 6, "x"),
                                                       Offer("voucher", "Petroglyph", 10, 5, "x")], 3)}
    assert vals["Hieroglyph"] < 2 and vals["Petroglyph"] < 2, vals  # below the idle-money voucher threshold
    glyph = [{"kind": "voucher", "name": "Hieroglyph", "price": 10}]
    assert "buy" not in [a.kind for a in shop_plan(State(hand=[], money=40), glyph, ante=4, samples=10, auto=True)]
    assert blind_decision({"blind_col": 2, "ante": 3}, s)[2] == "play"  # boss: never skip
    assert blind_decision({"blind_col": 0, "ante": 3}, s)[1:3] == (None, "play")  # tag unknown


def test_urgent_reroll_keeps_floor():
    """Run 20261030-211755 moves 241/257: emergency rerolls down to $5/$6, nothing useful found."""
    s = State(hand=[], money=14, jokers=[Joker("Joker")] * 5)
    assert _reroll_goal(s, 6, 24000, 40000, 0, 2, 14, 8) is None
    assert "scoring joker" in _reroll_goal(s, 6, 24000, 40000, 0, 2, 20, 5)


def test_sell_only_if_replan_buys():
    """Run 20261031-082933 moves 80/81: Blue Joker sold for Zany Joker, then Zany left in the shop for interest."""
    js = [Joker("Blue Joker"), Joker("Vampire"), Joker("Ice Cream", vars={"chips": 75}), Joker("Lusty Joker"),
          Joker("Ride the Bus")]
    lv = {"Full House": 2, "Flush": 2, "Straight": 2, "Two Pair": 2}
    zany = [{"kind": "joker", "name": "Zany Joker", "price": 4}]
    for money in (4, 8, 12, 30):
        s = State(hand=[], money=money, jokers=js, hand_levels=lv, vouchers=["Paint Brush"])
        plan = shop_plan(s, zany, ante=3, samples=15, auto=True)
        if plan[0].kind != "sell":
            continue
        k = plan[0].index
        after = State(hand=[], money=money + _sell_value(js[k]), jokers=js[:k] + js[k + 1:], hand_levels=lv,
                      vouchers=["Paint Brush"])
        assert "buy" in [a.kind for a in shop_plan(after, zany, ante=3, samples=15, auto=True)], (money, plan)


def test_no_swap_that_leaves_boss_short():
    """Run 20261031-091942 move 230: Abstract Joker (+15 mult) sold before The Club for a fresh Obelisk (×1),
    the estimate fell to ~21,021/22,000, boss lost with 18,364."""
    js = [Joker("Abstract Joker"), Joker("Runner", vars={"chips": 75}), Joker("Hologram", vars={"xmult": 1.25}),
          Joker("Ramen", vars={"x": 1.92}), Joker("Card Sharp")]
    s = State(hand=[], money=28, jokers=js, hand_size=9, discards_left=2,
              hand_levels={"Full House": 4, "Pair": 3, "Straight Flush": 2, "Straight": 2, "Two Pair": 2,
                           "Three of a Kind": 2, "Four of a Kind": 2},
              played_counts={"Full House": 1, "Flush": 2, "Pair": 17, "Straight": 7, "Two Pair": 6})
    plan = shop_plan(s, [{"kind": "joker", "name": "Obelisk", "price": 8}], ante=5, samples=20, auto=True)
    assert "sell" not in [a.kind for a in plan], plan


def test_free_negative_joker_always_taken():
    """Run 20261031-094727 move 242: Diet Cola ($0, negative from the Negative Tag) left in the shop – value 5 < threshold 7.5."""
    js = [Joker("Delayed Gratification"), Joker("Runner", vars={"chips": 105}), Joker("Half Joker"),
          Joker("Photograph", edition="negative"), Joker("The Trio", vars={"xmult": 3.0})]
    s = State(hand=[], money=18, jokers=js, joker_slots=6, hand_size=9,
              hand_levels={"Full House": 3, "Pair": 4, "Straight": 3})
    cola = [{"kind": "joker", "name": "Diet Cola", "price": 0, "edition": "negative"}]
    assert [a.kind for a in shop_plan(s, cola, ante=7, samples=15, auto=True)] == ["buy", "leave"]
    # a paid negative still has to clear the bar; a free rental is not free
    paid = [{"kind": "joker", "name": "Diet Cola", "price": 9, "edition": "negative"}]
    assert "buy" not in [a.kind for a in shop_plan(s, paid, ante=7, samples=15, auto=True)]
    rental = [dict(cola[0], rental=True)]
    assert "buy" not in [a.kind for a in shop_plan(s, rental, ante=7, samples=15, auto=True)]


def test_telescope_needs_most_played_focus():
    """Run 20261031-094727 move 58: Telescope ($10, $15 → $5) bought as "guarantees a planet for Straight"; Telescope
    brings the planet of the most played hand though (Pair 5×) – later Mercury instead of Saturn (move 155)."""
    s = State(hand=[], money=15, hand_levels={"Full House": 2, "Straight": 3},
              played_counts={"Full House": 1, "Flush": 1, "Pair": 5, "Straight": 1, "Two Pair": 2})
    assert focus_hand(s) == "Straight"
    tele = adjust_offers(s, [Offer("voucher", "Telescope", 10, 6, "x")], 2)[0]
    assert tele.value < 6 and "most played hand (Pair)" in tele.why, tele
    # most played = focus: keeps the bonus
    s2 = replace(s, played_counts={"Pair": 1, "Straight": 6})
    assert adjust_offers(s2, [Offer("voucher", "Telescope", 10, 6, "x")], 2)[0].value > 6


def test_no_joker_reroll_when_nothing_replaceable():
    """Run 20261031-105755 moves 446–451: full slots, weakest joker Runner (Tier A, 75 chips) – 6 rerolls
    "look for a scoring joker for ante 10" ($56 → $11), no offer could replace it."""
    js = [Joker("Runner", vars={"chips": 75}), Joker("Vampire", vars={"xmult": 2.0}), Joker("Hanging Chad"),
          Joker("Photograph"), Joker("Card Sharp")]
    s = State(hand=[], money=56, jokers=js, hand_size=8, hands_left=6, consumables=["The Sun"],
              hand_levels={"High Card": 11, "Straight Flush": 3, "Two Pair": 8},
              played_counts={"Flush": 1, "Pair": 4, "High Card": 46, "Straight": 5, "Two Pair": 22})
    plan = shop_plan(s, [], ante=10, reroll_cost=5, samples=15, auto=True)
    assert not any("Joker" in a.why for a in plan if a.kind == "reroll"), plan
    # a weak flat joker in the set can still be replaced: keep searching
    weak = replace(s, jokers=[Joker("Joker")] + js[1:])
    plan = shop_plan(weak, [], ante=10, reroll_cost=5, samples=15, auto=True)
    assert plan[0].kind == "reroll" and "joker" in plan[0].why, plan


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("strategy ok")
