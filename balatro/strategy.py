"""Run-level strategy: blind requirements, hand focus, skip/play, shop weighting, economy, selling.

Heuristics summarise community guides and docs/BALATRO_STRATEGIEN_RECHERCHE.md. Texts for the user are German.
"""
import math
import random
from dataclasses import replace

from .data import HAND_TYPES, PLANETS
from .engine import most_played
from .jokers import get as get_joker
from .models import State
from .optimizer import _best_score
from .shop import TIER, TIER_POINTS, consumable_slots

# https://balatrowiki.org/w/Blinds_and_Antes & Stakes (section 12.2)
ANTE_BASE = [300, 800, 2000, 5000, 11000, 20000, 35000, 50000]
GREEN_BASE = [300, 900, 2600, 8000, 20000, 36000, 60000, 100000]
PURPLE_BASE = [300, 1000, 3200, 9000, 25000, 60000, 110000, 200000]
STAKE_BASE = {"white": ANTE_BASE, "red": ANTE_BASE, "green": GREEN_BASE, "black": GREEN_BASE,
              "blue": GREEN_BASE, "purple": PURPLE_BASE, "orange": PURPLE_BASE, "gold": PURPLE_BASE}
BLIND_MULT = {"small": 1, "Small Blind": 1, "big": 1.5, "Big Blind": 1.5,
              "boss": 2, "The Wall": 4, "Violet Vessel": 6, "The Needle": 1}


def _stake_table(state):
    stake = state.stake
    if isinstance(stake, int):
        stake = ("white", "red", "green", "black", "blue", "purple", "orange", "gold")[max(0, min(7, stake - 1))]
    return STAKE_BASE.get(str(stake).lower().removesuffix(" stake"), ANTE_BASE)


def _table_base(ante, table=ANTE_BASE):
    if ante < 1:
        return 100
    if ante <= 8:
        return table[ante - 1]
    c = ante - 8
    base = math.floor(table[-1] * (1.6 + (0.75 * c) ** (1 + 0.2 * c)) ** c)
    return base - base % 10 ** math.floor(math.log10(base) - 1)


def required_score(ante, blind="small", base=None, *, stake=None, blind_chips=0):
    """Chips needed. `base`/`stake`/`blind_chips` accepts a stake name ('gold'), a known target/base int, or a State."""
    arg = base if base is not None else stake if stake is not None else (blind_chips or None)
    mult = BLIND_MULT.get(blind, 2)
    if isinstance(arg, State):
        plasma = 2 if (arg.deck_type or "").startswith("Plasma") else 1
        if arg.stake is not None:
            return int(_table_base(ante, _stake_table(arg)) * plasma * mult)
        if arg.blind_chips > 0:
            return int(round(arg.blind_chips / (BLIND_MULT.get(arg.blind, 1) if arg.blind else 1) * mult))
        return int(_table_base(ante, _stake_table(arg)) * plasma * mult)
    if isinstance(arg, str):
        return int(_table_base(ante, STAKE_BASE.get(arg.lower().removesuffix(" stake"), ANTE_BASE)) * mult)
    if isinstance(arg, (int, float)) and arg > 0:
        return int(round(arg * mult))
    return int(_table_base(ante) * mult)


def next_ante_score(state, ante, blind="big"):
    """Required score in `ante + 1`, inferred from `state.blind_chips` (stake/Plasma scaling) when known."""
    plasma = 2 if (state.deck_type or "").startswith("Plasma") else 1
    mult = BLIND_MULT.get(blind, 2)
    if state.stake is not None:
        return int(_table_base(ante + 1, _stake_table(state)) * plasma * mult)
    if state.blind_chips > 0 and ante >= 1:
        obs_base = state.blind_chips / (BLIND_MULT.get(state.blind, 1) if state.blind else 1)
        if 1 <= ante <= 8:
            for table in (PURPLE_BASE, GREEN_BASE, ANTE_BASE):
                if round(obs_base) == table[ante - 1] * plasma:
                    return int(_table_base(ante + 1, table) * plasma * mult)
        return int(round(_table_base(ante + 1) * (obs_base / _table_base(ante)) * mult))
    return int(_table_base(ante + 1, _stake_table(state)) * plasma * mult)


HAND_JOKERS = {
    "Pair": {"Jolly Joker", "Sly Joker", "The Duo"},
    "Two Pair": {"Mad Joker", "Clever Joker", "Spare Trousers"},
    "Three of a Kind": {"Zany Joker", "Wily Joker", "The Trio"},
    "Four of a Kind": {"The Family"},
    "Straight": {"Crazy Joker", "Devious Joker", "The Order", "Runner", "Shortcut", "Four Fingers"},
    "Flush": {"Droll Joker", "Crafty Joker", "The Tribe", "Smeared Joker", "Four Fingers",
              "Greedy Joker", "Lusty Joker", "Wrathful Joker", "Gluttonous Joker",
              "Bloodstone", "Arrowhead", "Onyx Agate", "Rough Gem"},
    "Full House": {"The Duo", "The Trio", "Jolly Joker", "Zany Joker"},
}
HAND_XMULT = {"The Duo", "The Trio", "The Family", "The Order", "The Tribe"}  # xMult only if the hand contains X


def _focus_weight(state, ht):
    return 3 * (state.level(ht) - 1) + 4 * sum(j.name in HAND_JOKERS.get(ht, ()) for j in state.jokers) + state.played_counts.get(ht, 0)


def focus_hand(state):
    """Hand type to build around: planet levels weigh most, then joker synergy, then play history."""
    best = max(HAND_TYPES, key=lambda ht: _focus_weight(state, ht))
    return best if _focus_weight(state, best) > 0 else "Pair"


def has_clear_focus(state):
    return _focus_weight(state, focus_hand(state)) >= 3 or "Telescope" in state.vouchers


TAG_POINTS = {
    "Negative Tag": 9, "Polychrome Tag": 8, "Rare Tag": 8, "Investment Tag": 8, "Coupon Tag": 7,
    "Holographic Tag": 6, "Charm Tag": 6, "Double Tag": 5, "Uncommon Tag": 5, "Foil Tag": 5,
    "Ethereal Tag": 5, "Buffoon Tag": 6, "Meteor Tag": 5, "Orbital Tag": 6, "Voucher Tag": 5,
    "Speed Tag": 4, "Economy Tag": 4, "Handy Tag": 3, "Garbage Tag": 3, "Top-up Tag": 3,
    "D6 Tag": 3, "Juggle Tag": 1, "Standard Tag": 2, "Boss Tag": 2,
}
SKIP_TAG_MIN = 7
SKIP_SAFETY = 1.2


def leaf_sale(state, ante=8):
    from .shop import _owned_joker_value
    rng = random.Random(0)
    deck = state.get_full_deck()
    hands = [rng.sample(deck, min(state.hand_size, len(deck))) for _ in range(8)]
    return min((i for i, j in enumerate(state.jokers) if not j.eternal),
               key=lambda i: _owned_joker_value(state, i, ante, hands), default=None)


def boss_state(state, boss):
    """Fresh-round resources for a known boss; the engine applies its scoring restrictions."""
    disabled = state.boss_disabled and state.blind == boss or any(j.name == "Chicot" and not j.debuff for j in state.jokers)
    hands = state.round_hands if state.round_hands is not None else max(1, state.hands_left or 4)
    discards = state.round_discards if state.round_discards is not None else state.discards_left
    size = state.hand_size
    jokers = state.jokers
    if boss == "Verdant Leaf" and not disabled:
        sale = leaf_sale(state)
        if sale is not None:
            jokers = state.jokers[:sale] + state.jokers[sale + 1:]
            disabled = True
    if not disabled:
        hands = 1 if boss == "The Needle" else hands
        discards = 0 if boss == "The Water" else discards
        if boss == "The Manacle" and state.blind != boss:
            size = max(1, size - 1)
    deck = [replace(c, debuff=False, forced=False, face_down=False) for c in state.get_full_deck()]
    return replace(state, hand=[], full_deck=deck, deck=None, blind=boss, boss_disabled=disabled,
                   hands_left=hands, discards_left=discards, hand_size=size, score=0, round_hand_types=[],
                   discards_used=0, jokers=jokers)


def estimate_round(state, samples=20, seed=0, boss=None):
    """Rough round score: avg best single hand from random draws x hands per round (no discards)."""
    rng = random.Random(seed)
    sim = boss_state(state, boss) if boss else replace(state, blind=None, blind_chips=0, score=0, deck=None)
    deck = sim.get_full_deck()
    hands = [rng.sample(deck, min(sim.hand_size, len(deck))) for _ in range(samples)]
    return (sum(_best_score(replace(sim, hand=h)) for h in hands) / samples) * max(1, sim.hands_left or 4)  # shortcut: cheap estimate ignores redraws; use boss_forecast for full rounds.


def boss_forecast(state, ante=1, samples=12, seed=0):
    """Simulate fresh rounds against the known boss, with actual restrictions and redraws."""
    from .data import BOSSES
    from .optimizer import _draw, _round_total
    boss = state.upcoming_boss or state.blind
    if boss not in BOSSES:
        return {"boss": None, "notes": ["No upcoming boss known."]}
    prepared = boss_state(state, boss)
    target = required_score(ante, boss, state)
    totals = []
    for n in range(samples):
        rng = random.Random(seed + n)
        deck = rng.sample(prepared.get_full_deck(), len(prepared.get_full_deck()))
        sim = replace(prepared, blind_chips=target)
        totals.append(_round_total(sim, *_draw([], deck, sim), sim.hands_left, sim.discards_left, target, rng=rng))
    notes = [BOSSES[boss]]
    if boss == "Verdant Leaf" and len(prepared.jokers) < len(state.jokers):
        sale = leaf_sale(state, ante)
        notes.append(f"Plan assumes selling {state.jokers[sale].name} during the blind to remove the debuff.")
    counters = {"The Plant": "Build a scoring line without face cards, or disable the boss with Luchador/Chicot.",
                "The Needle": "Save Glass cards and scoring consumables for the single hand.",
                "Verdant Leaf": "Keep a sellable joker; selling it during this blind removes the debuff.",
                "The Water": "Prepare a reliable first draw; there are no discards.",
                "The Eye": "Keep multiple viable hand types; repeated types score zero.",
                "The Mouth": "Choose the first hand type carefully; later hands must match it.",
                "The Manacle": "Prioritise hand-size vouchers and hands that need fewer cards."}
    if boss in counters:
        notes.append(counters[boss])
    uncertain = boss in {"Crimson Heart", "Amber Acorn", "The House", "The Wheel", "The Fish", "The Mark", "Cerulean Bell"}
    if uncertain:
        notes.append("Estimate uses visible-state scoring; future hidden cards, shuffles or random joker disables are not modelled.")
    return {"boss": boss, "target": target, "p_win": sum(t >= target for t in totals) / len(totals),
            "expected": sum(totals) / len(totals), "samples": samples, "approximate": uncertain,
            "notes": notes, "baseline": estimate_round(state, samples=samples),
            "boss_estimate": estimate_round(state, samples=samples, boss=boss)}


def _tag_points(tag, state_or_money, ante=1):
    money = state_or_money.money if isinstance(state_or_money, State) else state_or_money
    state = state_or_money if isinstance(state_or_money, State) else None
    if tag == "Economy Tag":
        return min(10, 2 + min(money, 40) // 5)
    if tag == "Investment Tag":
        return 9 if ante <= 2 else 7 if ante <= 4 else 5
    if tag == "Boss Tag" and state and state.blind in ("The Plant", "The Wall", "Violet Vessel", "The Eye", "Verdant Leaf"):
        return 9
    pts = TAG_POINTS.get(tag, 3)
    if state and len(state.jokers) >= state.joker_slots and tag in ("Polychrome Tag", "Rare Tag", "Holographic Tag", "Foil Tag", "Uncommon Tag", "Buffoon Tag"):
        pts -= 3
    return pts


def _skip_cost(state, ante, blind):
    """Opportunity cost of skipping (section 11): lost shop, blind payout, interest, and scaling."""
    # ponytail: linear round-value heuristic; full EV would simulate next shop & blind payout
    owned = {j.name for j in state.jokers if not j.debuff}
    scaling = len(owned & SCALING)
    econ = len(owned & {"Golden Joker", "Rocket", "Cloud 9", "Delayed Gratification", "Satellite", "Egg", "Gift Card"})
    tb = -2.5 if "Throwback" in owned else 0
    if ante == 1 and blind in ("small", "Small Blind") and state.money < 10 and not scaling:
        return 6.0 + tb
    base = 5.0 if blind in ("small", "Small Blind") else 6.0
    return base + 0.4 * interest_of(state.money, state) + 0.8 * min(scaling + econ, 3) + tb


def blind_choice(state, ante, blind, tag=None):
    """-> ("play" | "skip", reason) weighing tag benefit against lost shop, interest, and scaling."""
    if blind not in ("small", "Small Blind", "big", "Big Blind"):
        return "play", "A Boss Blind can't be skipped."
    if not tag:
        return "play", "No tag known – play (money, interest and a shop)."
    pts, cost = _tag_points(tag, state, ante), _skip_cost(state, ante, blind)
    if pts < max(SKIP_TAG_MIN - 1, cost):
        return "play", f"{tag} (value {pts}/10) doesn't outweigh the lost shop/interest/scaling (cost {cost:.1f})."
    # skipping the Big Blind leads straight to the known boss (run 20261101-105644 Z387: The Wall 440,000, not 220,000)
    boss = state.upcoming_boss if state.upcoming_boss in BLIND_MULT else "boss"
    nxt, label = ("big", "Big Blind") if blind in ("small", "Small Blind") else \
        (boss, "Boss Blind" if boss == "boss" else boss)
    need, est = required_score(ante, nxt, state), estimate_round(state)
    if est < need * SKIP_SAFETY and tag != "Boss Tag":
        return "play", (f"{tag} would be nice, but the deck only makes ~{est:,.0f} of "
                        f"{need:,} for the {label} – better play and get stronger.")
    return "skip", (f"Skip for {tag} (value {pts}/10 ≥ cost {cost:.1f}): the deck makes ~{est:,.0f}, "
                    f"{need:,} needed for the {label}.")


def blind_decision(d, state):
    """Telemetry state d -> (blind name, skip tag, "play" | "skip", reason); shared by advice and autopilot."""
    blind = {0: "Small Blind", 1: "Big Blind"}.get(d.get("blind_col"), "Boss")
    tag = d.get("small_tag") if blind == "Small Blind" else d.get("big_tag") if blind == "Big Blind" else None
    if tag is None and blind != "Boss":
        return blind, None, "play", "Skip tag not recognised (telemetry only)."
    return (blind, tag) + blind_choice(state, d.get("ante") or 1, blind, tag)


FLAT = set("""Joker,Greedy Joker,Lusty Joker,Wrathful Joker,Gluttonous Joker,Jolly Joker,Zany Joker,Mad Joker,
Crazy Joker,Droll Joker,Sly Joker,Wily Joker,Clever Joker,Devious Joker,Crafty Joker,Half Joker,Banner,
Mystic Summit,Misprint,Raised Fist,Fibonacci,Scary Face,Abstract Joker,Even Steven,Odd Todd,Scholar,
Gros Michel,Popcorn,Ice Cream,Walkie Talkie,Smiley Face,Stuntman,Bull,Blue Joker,Swashbuckler,Arrowhead,
Onyx Agate,Stone Joker,Seltzer,Sock and Buskin,Shoot the Moon,Erosion,Bootstraps""".replace("\n", "").split(","))
SCALING = {"Ride the Bus", "Green Joker", "Runner", "Square Joker", "Spare Trousers", "Wee Joker", "Hiker",
           "Castle", "Red Card", "Flash Card", "Ceremonial Dagger", "Supernova", "Fortune Teller",
           "Constellation", "Hologram", "Vampire", "Lucky Cat", "Madness", "Obelisk", "Campfire",
           "Glass Joker", "Throwback", "Canio", "Yorick", "Hit the Road", "Ramen"}
XMULT = {"Cavendish", "The Duo", "The Trio", "The Family", "The Order", "The Tribe", "Photograph", "Baron",
         "Card Sharp", "Ancient Joker", "The Idol", "Bloodstone", "Blackboard", "Steel Joker", "Acrobat",
         "Loyalty Card", "Driver's License", "Triboulet", "Seeing Double", "Flower Pot", "Joker Stencil",
         "Baseball Card", "Constellation", "Hologram", "Vampire", "Lucky Cat", "Madness", "Obelisk", "Campfire",
         "Glass Joker", "Throwback", "Canio", "Yorick", "Hit the Road", "Ramen", "Blueprint", "Brainstorm"}
ECON = {"Golden Joker", "Rocket", "To the Moon", "Delayed Gratification", "Business Card", "Cloud 9", "Egg",
        "Faceless Joker", "Gift Card", "Reserved Parking", "Mail-In Rebate", "Satellite", "Trading Card",
        "Golden Ticket", "Rough Gem", "Matador", "Credit Card"}
FACE_JOKERS = {"Photograph", "Baron", "Shoot the Moon", "Smiley Face", "Scary Face", "Sock and Buskin",
               "Business Card", "Reserved Parking", "Hit the Road", "Triboulet", "Midas Mask", "Faceless Joker", "Canio"}
SYNERGY_GROUPS = [
    ({"Photograph"}, {"Hanging Chad", "Sock and Buskin", "Pareidolia", "Smiley Face", "Scary Face"}, 2.5, "Photochad/face-card synergy"),
    ({"Baron"}, {"Mime", "Steel Joker"}, 2.5, "held-in-hand synergy"),
    ({"Mime"}, {"Baron", "Shoot the Moon", "Raised Fist", "Steel Joker", "Reserved Parking"}, 2.0, "retriggers held cards"),
    ({"Hanging Chad", "Dusk", "Seltzer"}, {"Photograph", "Bloodstone", "Triboulet", "Ancient Joker", "Fibonacci",
                                           "Wee Joker", "Hiker", "Lucky Cat", "Scholar", "Walkie Talkie"}, 1.5, "retrigger synergy"),
    ({"Hack"}, {"Wee Joker", "Fibonacci", "Odd Todd", "Even Steven", "Walkie Talkie"}, 2.0, "Retrigger 2–5"),
    ({"Oops! All 6s"}, {"Bloodstone", "Space Joker", "Lucky Cat", "Business Card", "Reserved Parking", "8 Ball", "Hallucination"}, 2.0, "probability synergy"),
    ({"Midas Mask"}, {"Golden Ticket", "Vampire", "Driver's License"}, 2.5, "gold/enhancement synergy"),
    ({"Swashbuckler"}, {"Egg", "Gift Card"}, 2.0, "sell-value synergy"),
    ({"Constellation"}, {"Astronomer", "Satellite"}, 2.0, "planet synergy"),
    ({"Fortune Teller"}, {"Cartomancer", "Vagabond", "Hallucination", "8 Ball"}, 1.5, "tarot synergy"),
    ({"Hologram"}, {"Marble Joker", "Certificate", "DNA"}, 2.0, "card supply for Hologram"),
    ({"Burglar"}, {"Green Joker", "Ride the Bus", "Supernova"}, 2.0, "+3 hands for scaling"),
    ({"Smeared Joker"}, {"Bloodstone", "Arrowhead", "Onyx Agate", "Rough Gem", "Ancient Joker", "The Tribe"}, 2.0, "suit synergy"),
    ({"Four Fingers", "Shortcut"}, {"Runner", "The Order", "The Tribe", "Crazy Joker", "Devious Joker", "Superposition", "Séance"}, 1.5, "Straight/Flush helper"),
]


def _phase(ante):
    return "early" if ante <= 2 else "mid" if ante <= 4 else "late"


ROLE_BONUS = {
    "early": {"flat": 2, "econ": 2, "scaling": 1, "xmult": 0},
    "mid": {"flat": 0, "econ": 0.5, "scaling": 2, "xmult": 1},
    "late": {"flat": -2, "econ": -2, "scaling": 0, "xmult": 3},
}
ROLE_DE = {"flat": "flat mult/chips joker", "econ": "economy joker", "scaling": "scaling joker", "xmult": "xMult joker"}


def joker_role(name):
    return ("xmult" if name in XMULT else "scaling" if name in SCALING else "econ" if name in ECON
            else "flat" if name in FLAT else None)


def joker_synergy(state, name, ante=1):
    """(delta, [notes]) for synergies/conflicts between candidate joker `name` and `state`."""
    owned = {j.name for j in state.jokers if not j.debuff}
    delta, notes = 0.0, []
    for a, b, bonus, label in SYNERGY_GROUPS:
        if (name in a and owned & b) or (name in b and owned & a):
            delta, notes = bonus, [label]
            break
    if name == "Baseball Card":
        unc = sum(1 for j in state.jokers if get_joker(j.name) and get_joker(j.name).rarity == "Uncommon")
        if unc:
            delta, notes = delta + 1.5 * unc, notes + [f"{unc} uncommon jokers (x1.5)"]
    elif "Baseball Card" in owned and get_joker(name) and get_joker(name).rarity == "Uncommon":
        delta, notes = delta + 2.0, notes + ["uncommon for Baseball Card"]
    if name in ("Blueprint", "Brainstorm") and owned & (XMULT | SCALING):
        delta, notes = delta + 2.0, notes + ["copies an owned core joker"]
    if ("Ride the Bus" in owned and name in FACE_JOKERS) or (name == "Ride the Bus" and owned & FACE_JOKERS):
        delta, notes = delta - 3.0, notes + ["conflicts with Ride the Bus"]
    if (state.deck_type or "").startswith("Abandoned") and "Pareidolia" not in owned and name in FACE_JOKERS:
        delta, notes = delta - 4.0, notes + ["no face cards in the Abandoned Deck"]
    if name == "Madness":
        # destroys a random other non-eternal joker at every Small/Big Blind (card.lua); run 20261101-094338
        # Z91-405 lost 11 jokers to it, incl. Vampire, Baseball Card, Hologram, Hanging Chad
        # ponytail: flat -2 per S/A joker at risk, at most 2 (one ante = 2 kills); not the exact expected loss
        risk = sum(1 for j in state.jokers if not j.eternal and TIER.get(j.name) in ("S", "A"))
        if risk:
            delta, notes = delta - 2.0 * min(risk, 2), notes + [f"destroys jokers ({risk} strong at risk)"]
    if name in owned and name in {"Four Fingers", "Shortcut", "Smeared Joker", "Pareidolia", "Splash"}:
        delta, notes = delta - 5.0, notes + ["passive effect already active"]
    if state.blind in ("The Plant", "The Wall", "Violet Vessel", "The Eye", "Verdant Leaf") and name in ("Luchador", "Chicot", "Mr. Bones"):
        delta, notes = delta + 3.0, notes + [f"counter to {state.blind}"]
    return delta, notes


def _adjust_pack(state, name, ante, focus):
    n, owned = name.lower(), {j.name for j in state.jokers if not j.debuff}
    if "celestial" in n:
        if has_clear_focus(state) and "Constellation" not in owned:
            # the focus bonus needs a focus level that scores (runs 082933 Z92/105, 091942 Z71: picks +0-9%)
            up = replace(state, hand_levels={**state.hand_levels, focus: state.level(focus) + 1})
            if estimate_round(up, samples=10) < 1.02 * estimate_round(state, samples=10):
                return 0.5, [f"{focus} +1 level adds hardly any score"]
        if has_clear_focus(state) or "Constellation" in owned:
            return 3.0 + ("Telescope" in state.vouchers or "Constellation" in owned), [f"level focus hand {focus}"]
        return (-1.0, ["focus hand still unclear"]) if ante <= 1 else (0.5, [f"planets for {focus}"])
    if "arcana" in n:
        ok = owned & {"Fortune Teller", "Vampire"} or ante >= 2 or focus in ("Flush", "Straight", "Four of a Kind", "Full House")
        return (2.0, ["deck manipulation/money"]) if ok else (0.5, ["tarot options"])
    if "buffoon" in n:
        if len(state.jokers) >= state.joker_slots:
            return -2.0, ["no free joker slot"]
        need = len(state.jokers) < 2 or estimate_round(state, samples=10) < required_score(ante, "boss", state)
        return (1.5, ["needs jokers"]) if need else (0.0, [])
    if "standard" in n:
        return (4.0, ["feeds Hologram"]) if "Hologram" in owned else (-1.0, ["usually dilutes the deck"])
    if "spectral" in n:  # each card is optional (Skip): no risk, Black Hole/The Soul/seals/Cryptid are strong
        # rated 3.0 against Arcana's 6.0, none of 24 offers in 9 runs was bought; on a par with Arcana now,
        # more while Ankh/Hex can still copy/upgrade a single carrying joker
        return (3.0, ["early spectral potential (Ankh/Hex)"]) if ante <= 2 and len(state.jokers) <= 1 else \
            (1.5, ["seals, Black Hole, Cryptid; every card optional"])
    return 0.0, []


def _adjust_voucher(state, name, ante, focus):
    owned = {j.name for j in state.jokers if not j.debuff}
    big_hand = focus in ("Straight", "Flush", "Full House", "Four of a Kind", "Straight Flush")
    if name in ("Grabber", "Nacho Tong"):
        return (1.5, ["+1 hand & leftover-hand money"]) if ante <= 6 or state.hands_left <= 3 else (0.0, [])
    if name in ("Paint Brush", "Palette"):
        return (2.0, [f"+1 hand size for {focus}"]) if big_hand or owned & {"Baron", "Mime", "Shoot the Moon", "Blackboard"} else (0.0, [])
    if name in ("Wasteful", "Recyclomancy"):
        if owned & {"Green Joker", "Burglar", "Ramen", "Delayed Gratification"}:
            return -3.0, ["build avoids discards"]
        return (1.5, ["+1 discard for consistency"]) if state.discards_left <= 2 or big_hand else (0.0, [])
    if name == "Telescope":
        # vanilla: Celestial Packs hold the planet of the MOST PLAYED hand, not of the focus hand (run 094727 Z58)
        mp = most_played(state) if state.played_counts else None
        if mp != focus:
            return -1.5, [f"planet only for the most played hand ({mp or 'none'}), not focus {focus}"]
        return (2.5, [f"guarantees a planet for {focus}"]) if has_clear_focus(state) and ante <= 6 else (-1.5, ["only useful with a focus hand"])
    if name == "Observatory":
        return (2.0, ["planets give x1.5"]) if owned & {"Perkeo", "Constellation"} or state.consumables else (-2.5, ["weak without a planet source"])
    if name == "Crystal Ball":
        need = owned & {"Perkeo", "Cartomancer", "Vagabond", "Constellation", "8 Ball", "Hallucination"} or \
            (state.deck_type or "").startswith("Nebula") or len(state.consumables) >= consumable_slots(state)
        return (2.0, ["+1 consumable slot"]) if need else (-1.5, ["enough consumable slots"])
    if name in ("Seed Money", "Money Tree"):
        if (state.deck_type or "").startswith("Green") or state.money < 25 or ante >= 7:
            return -3.5, ["too little capital/rounds for an interest voucher"]
        return 2.0, ["enough capital for a higher interest cap"]
    if name in ("Overstock", "Overstock Plus", "Clearance Sale", "Liquidation", "Reroll Surplus", "Reroll Glut"):
        return (1.5, ["pays off over shop visits"]) if state.money >= 25 and ante <= 6 else (-2.5, ["reserve/rounds left too short"])
    if name in ("Director's Cut", "Retcon"):
        return (2.5, ["boss reroll as insurance"]) if state.money >= 25 else (-1.0, ["no reserve for a boss reroll"])
    if name in ("Magic Trick", "Illusion"):
        return (3.5, ["playing cards for Hologram"]) if "Hologram" in owned else (-2.0, ["dilutes shop offers"])
    if name in ("Hieroglyph", "Petroglyph"):  # docs/STRATEGY.md §3: the extra ante must carry the lost resource
        lost = "hand" if name == "Hieroglyph" else "discard"
        # -5: with idle money vouchers buy from value 2 (run 105755 still bought Hieroglyph at -3)
        return -5.0, [f"-1 ante costs 1 {lost} every round for good (091942: bosses tight afterwards)"]
    if name == "Blank":
        return (1.0, ["option on Antimatter"]) if state.money >= 35 and ante <= 5 else (-1.5, ["no immediate effect"])
    if name in ("Planet Merchant", "Planet Tycoon", "Tarot Merchant", "Tarot Tycoon", "Hone", "Glow Up", "Omen Globe"):
        return (1.0, ["shop weighting"]) if state.money >= 30 and ante <= 6 else (-1.5, ["crowds jokers out of the shop"])
    return 0.0, []


def _adjust_tarot(state, name, focus):
    owned = {j.name for j in state.jokers if not j.debuff}
    b = 1.5 if owned & {"Fortune Teller", "Vampire"} else 0.0
    if name == "The Hermit":
        return b + (3.5 if state.money >= 6 else 1.0), ["double money"]
    if name == "Temperance":
        return b + (3.5 if len(state.jokers) >= 2 else 0.5), ["joker sell value as money"]
    if name in ("Death", "The Hanged Man", "Strength"):
        return b + 2.5, ["thin the deck on purpose"]
    if name == "The Fool":
        return b + 2.0, ["copy the last consumable"]
    if name in ("The Star", "The Moon", "The Sun", "The World"):
        return (b + 2.5, ["suit tarot for Flush"]) if focus == "Flush" else (b - 0.5, ["suit tarot"])
    if name == "The Wheel of Fortune":
        return (b + 1.5, ["edition chance"]) if owned & {"Oops! All 6s", "Fortune Teller"} else (-1.0, ["only a 25% hit chance"])
    return b + 1.0, ["card enhancement"]


def adjust_offers(state, offers, ante):
    """Re-weight shop.Offer values by focus hand, run phase, and synergies; returns a new list sorted by value."""
    focus, phase = focus_hand(state), _phase(ante)
    owned = {j.name for j in state.jokers if not j.debuff}
    out = []
    for o in offers:
        delta, notes = 0.0, []
        if o.kind == "planet" and o.name in PLANETS:
            delta, notes = (3, [f"focus hand {focus}"]) if PLANETS[o.name] == focus else (-2, [f"not the focus hand ({focus})"])
            if "Constellation" in owned:
                delta, notes = delta + 2, notes + ["Constellation +X0.1"]
        elif o.kind == "joker":
            role = joker_role(o.name)
            # Duo/Trio/Family/Order/Tribe only fire on their hand: off-focus and +0% now -> rate as Tier C
            if (o.name in HAND_XMULT and o.name not in HAND_JOKERS.get(focus, ())
                    and ("now +0%" in o.why or "now -" in o.why)):
                role = None
                delta = TIER_POINTS["C"] - TIER_POINTS[TIER.get(o.name, "C")]
                notes.append(f"condition doesn't fit focus hand {focus}")
            if role:
                delta += ROLE_BONUS[phase][role]
                notes.append(f"{ROLE_DE[role]}, {phase} phase")
            if o.name in HAND_JOKERS.get(focus, ()):
                delta, notes = delta + 2, notes + [f"fits focus hand {focus}"]
            syn_d, syn_notes = joker_synergy(state, o.name, ante)
            delta, notes = delta + syn_d, notes + syn_notes
        elif o.kind == "booster_pack":
            delta, notes = _adjust_pack(state, o.name, ante, focus)
        elif o.kind == "voucher":
            delta, notes = _adjust_voucher(state, o.name, ante, focus)
        elif o.kind == "tarot":
            delta, notes = _adjust_tarot(state, o.name, focus)
        if delta or notes:
            val = max(0.5, o.value + delta)
            if int(val) == val and isinstance(o.value, int) and int(delta) == delta:
                val = int(val)
            o = replace(o, value=val, why=o.why + ("; " + ", ".join(notes) if notes else ""))
        out.append(o)
    return sorted(out, key=lambda o: -o.value)


def interest_cap(state=None):
    if state is None:
        return 5
    if (state.deck_type or "").startswith("Green"):
        return 0
    return 20 if "Money Tree" in state.vouchers else 10 if "Seed Money" in state.vouchers else 5


def interest_of(money, state=None):
    rate = 2 if state and any(j.name == "To the Moon" and not j.debuff for j in state.jokers) else 1
    return min(max(money, 0) // 5, interest_cap(state)) * rate


def economy_note(money, ante, state=None):
    """One-liner on interest ($1 per $5, max $5 at $25: balatrowiki.org/w/Interest) and rerolls."""
    cap = interest_cap(state)
    if cap == 0:
        return "Green Deck: no interest – money is free for strength and deck building."
    target, interest = cap * 5, interest_of(money, state)
    if money >= target:
        return (f"Interest ${interest} (maximum). Everything above ${target} (${money - target}) earns nothing – "
                f"free for buys or rerolls.")
    text = f"Interest ${interest}/round; ${5 - money % 5} more to the next $5 step (target ${target})."
    if ante <= 2:
        return text + " Early on strength counts: buy jokers that help now, but don't reroll."
    return text + f" Save up to ${target} before rerolling; only drop below ${target} if the buy secures the next blind."


def sell_candidates(state, ante):
    """-> [(joker index, reason)] of weak jokers, weakest first. Phase/role weighting lives in ROLE_BONUS only:
    a second flat-late/econ-late penalty here counted twice in _owned_joker_value (learnings: tier/role beat the
    real score gain in 4 runs) - whether a swap pays is shop_plan's score check, not an ante rule."""
    focus, out = focus_hand(state), []
    for i, j in enumerate(state.jokers):
        if j.eternal:
            continue
        tier = TIER.get(j.name, "C")
        if tier in "CD" and ante >= 3 and j.name not in HAND_JOKERS.get(focus, ()):
            reason = f"tier {tier} unrelated to focus hand {focus}"
        elif j.name in ("Luchador", "Diet Cola") and len(state.jokers) >= state.joker_slots:
            reason = f"{j.name} takes a slot without adding score"
        else:
            continue
        out.append((tier, i, reason))
    out.sort(key=lambda t: "SABCD".index(t[0]), reverse=True)
    return [(i, r) for _, i, r in out]
