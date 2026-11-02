"""Shop / booster-pack advice and multi-step shop action planning.

Joker and planet offers are valued with the engine: average best-play score over random
8-card hands from the deck, with vs. without the purchase. Long-term value (scaling jokers,
economy, retriggers) can't be seen in one hand, so a tier rating and run-context adjustments
(strategy.py) are added.
"""
import random
from dataclasses import asdict, dataclass, replace

from .data import PLANETS
from .engine import PASSIVE
from .jokers.part_b import _sell_value
from .models import Joker
from .optimizer import _best_score

TIER_POINTS = {"S": 10, "A": 7, "B": 5, "C": 3, "D": 1}
_TIERS = {
    "S": "Blueprint, Brainstorm, Triboulet, Yorick, Perkeo, Canio, Baron, Cavendish, Hologram, Vampire, "
         "Campfire, The Family, The Order, Photograph, Hanging Chad, Mime, Baseball Card, "
         "Chicot",
    "A": "Bloodstone, Spare Trousers, Constellation, Card Sharp, The Duo, The Trio, The Tribe, Ancient Joker, The Idol, "
         "Sock and Buskin, Hack, Steel Joker, Hiker, Obelisk, Lucky Cat, Throwback, Ride the Bus, Green Joker, "
         "Square Joker, Runner, Castle, Wee Joker, Burnt Joker, Invisible Joker, Cloud 9, Rocket, "
         "Golden Joker, Madness, Ceremonial Dagger, Glass Joker, Driver's License, Smeared Joker, Midas Mask, "
         "Oops! All 6s, Gros Michel",
    "B": "Dusk, Seltzer, Stone Joker, Acrobat, Swashbuckler, Bull, Bootstraps, Abstract Joker, Joker Stencil, "
         "Supernova, Fibonacci, Even Steven, Odd Todd, Smiley Face, Scary Face, Zany Joker, Droll Joker, "
         "Half Joker, Raised Fist, Shoot the Moon, Blackboard, Flower Pot, Seeing Double, Rough Gem, Popcorn, "
         "Ramen, Loyalty Card, Stuntman, Four Fingers, Shortcut, Pareidolia, Vagabond, Showman, Satellite, "
         "Cartomancer, Space Joker, Hit the Road, Ice Cream, Delayed Gratification, To the Moon, Walkie Talkie, "
         "Erosion, Flash Card, Fortune Teller, Scholar, Misprint, Arrowhead, Onyx Agate, Marble Joker, "
         "Mr. Bones, Juggler, Drunkard, Merry Andy, Turtle Bean, Trading Card, Business Card",
    "C": "Joker, Greedy Joker, Lusty Joker, Wrathful Joker, Gluttonous Joker, Jolly Joker, Mad Joker, "
         "Crazy Joker, Sly Joker, Wily Joker, Clever Joker, Devious Joker, Crafty Joker, Banner, Mystic Summit, "
         "8 Ball, Chaos the Clown, Egg, Burglar, Blue Joker, Sixth Sense, Faceless Joker, Superposition, "
         "To Do List, Red Card, Séance, Riff-Raff, Splash, Luchador, Gift Card, Reserved Parking, "
         "Mail-In Rebate, Hallucination, Golden Ticket, Diet Cola, Certificate, Astronomer, Credit Card, "
         "DNA",  # never triggered: the autopilot doesn't open rounds with 1 card (run 201325 Z29-128: dead slot)
    "D": "Troubadour, Matador",
}
TIER = {name.strip(): t for t, names in _TIERS.items() for name in names.split(",")}

VOUCHER_POINTS = {
    "Grabber": 8, "Nacho Tong": 8, "Antimatter": 9, "Observatory": 7, "Telescope": 6, "Wasteful": 6,
    "Recyclomancy": 6, "Clearance Sale": 5, "Liquidation": 5, "Overstock": 6, "Overstock Plus": 6,
    "Seed Money": 5, "Money Tree": 6, "Hieroglyph": 6, "Petroglyph": 5, "Paint Brush": 6, "Palette": 5,
    "Reroll Surplus": 4, "Reroll Glut": 4, "Crystal Ball": 4, "Omen Globe": 3, "Tarot Merchant": 3,
    "Tarot Tycoon": 3, "Planet Merchant": 3, "Planet Tycoon": 3, "Magic Trick": 2, "Illusion": 3,
    "Hone": 4, "Glow Up": 4, "Director's Cut": 3, "Retcon": 3, "Blank": 1,
}


@dataclass
class Offer:
    kind: str
    name: str
    price: int
    value: float
    why: str
    buyable: bool = True
    idx: int = -1  # position in the items list


@dataclass
class ShopAction:
    kind: str    # "buy" | "sell" | "reroll" | "leave"
    index: int   # buy: Index in items; sell: Index in state.jokers; sonst -1
    why: str     # deutsch, kurz


def consumable_slots(state):
    return 2 + ("Crystal Ball" in state.vouchers) - bool((state.deck_type or "").startswith("Nebula"))


def _sim_state(state):
    return replace(state, hand=[], blind=None, blind_chips=0, score=0, deck=None, hands_left=4)


def _avg_best(state, hands):
    return sum(_best_score(replace(state, hand=h)) for h in hands) / len(hands)


def _best_with_joker(base_state, new_j, hands, reorder=True):
    """reorder=False: only where a bought joker lands (rightmost) - the autopilot can't drag jokers."""
    js = base_state.jokers
    best = _avg_best(replace(base_state, jokers=js + [new_j]), hands)
    if reorder and js and (new_j.name == "Blueprint" or any(TIER.get(j.name) == "S" for j in js)):
        best = max(best, _avg_best(replace(base_state, jokers=[new_j] + js), hands))
    return best


def _pack_value(name, free_slots):
    n = name.lower()
    big = 2 if "mega" in n else 1 if "jumbo" in n else 0
    if "buffoon" in n:
        return (6 if free_slots else 1) + big, "joker pack" + ("" if free_slots else " (no free joker slot)")
    for key, v, label in (("celestial", 4, "planet"), ("arcana", 4, "tarot"), ("spectral", 4, "spectral"),
                          ("standard", 1.5, "playing card")):
        if key in n:
            return v + big, f"{label} pack"
    return 2, "pack"


def default_price(item):
    """Base shop price when the tag wasn't readable (vanilla, no discount vouchers); never 0, so an unread
    price can't make an item look free. ponytail: jokers vary by rarity ($4-$10), taken as $6."""
    n = item.get("name", "").lower()
    if item["kind"] == "booster_pack":
        return 8 if "mega" in n else 6 if "jumbo" in n else 4
    return {"voucher": 10, "planet": 3, "tarot": 3, "spectral": 4, "playing_card": 1}.get(item["kind"], 6)


def evaluate(state, items, samples=40, seed=0, auto=False):
    """items: [{"kind","name","price","edition"}] -> Offers sorted by value.
    auto: the autopilot plays it - jokers keep the order they are bought in."""
    rng = random.Random(seed)
    deck = state.get_full_deck()
    hands = [rng.sample(deck, min(state.hand_size, len(deck))) for _ in range(samples)]
    base_state = _sim_state(state)
    base = _avg_best(base_state, hands) or 1
    free = state.joker_slots - len(state.jokers)
    free_cons = consumable_slots(state) - len(state.consumables)
    out = []
    for i, it in enumerate(items):
        kind, name = it["kind"], it["name"]
        price = default_price(it) if it.get("price") is None else it["price"]
        if kind == "joker":
            ed = it.get("edition")
            fits = free > 0 or ed == "negative"
            gain = _best_with_joker(base_state, Joker(name, edition=ed), hands, reorder=not auto or state.telemetry) / base - 1
            tier = TIER.get(name, "C")
            # Blueprint lands rightmost; with no slot left behind it, nothing can ever sit to its right
            dead_bp = auto and not state.telemetry and name == "Blueprint" and free - (ed != "negative") <= 0
            if dead_bp:
                tier = "D"
            # ponytail: value = tier + capped immediate gain; tune weights against real runs
            value = TIER_POINTS[tier] + min(gain * 4, 4) + {"poly": 2, "holo": 1, "foil": 0.5, "negative": 2}.get(ed, 0)
            why = (f"Tier {tier}, now {gain:+.0%} score/hand" + (f", {ed}" if ed else "")
                   + (", useless rightmost" if dead_bp else ""))
            if it.get("rental"):  # $1 to buy, but $3 every round: a trap on a thin budget
                value, why = value - 3, why + ", rental (−$3/round)"
            if it.get("perishable"):
                value, why = value - 1.5, why + ", perishable (5 rounds)"
            out.append(Offer(kind, name, price, value, why + ("" if fits else ", no free slot"), fits, i))
        elif kind == "planet" and name in PLANETS:
            fits_c = free_cons > 0
            hand = PLANETS[name]
            lv = dict(base_state.hand_levels, **{hand: base_state.level(hand) + 1})
            gain = _avg_best(replace(base_state, hand_levels=lv), hands) / base - 1
            why = f"{hand} +1 level, {gain:+.0%} score/hand" + ("" if fits_c else ", no free consumable slot")
            out.append(Offer(kind, name, price, 2 + min(gain * 10, 5), why, fits_c, idx=i))
        elif kind == "voucher":
            out.append(Offer(kind, name, price, VOUCHER_POINTS.get(name, 3), "voucher (permanent)", idx=i))
        elif kind == "booster_pack":
            v, why = _pack_value(name, free > 0)
            out.append(Offer(kind, name, price, v, why, idx=i))
        elif kind in ("tarot", "spectral"):
            fits_c = free_cons > 0
            why = kind.capitalize() + ("" if fits_c else ", no free consumable slot")
            out.append(Offer(kind, name, price, 2.5, why, fits_c, idx=i))
        else:
            out.append(Offer(kind, name, price, 1, "playing card" if kind == "playing_card" else kind, idx=i))
    return sorted(out, key=lambda o: -o.value)


def _owned_joker_value(state, idx, ante, hands):
    from . import strategy
    j = state.jokers[idx]
    base_state = _sim_state(state)
    with_all = _avg_best(base_state, hands) or 1
    without = _avg_best(replace(base_state, jokers=state.jokers[:idx] + state.jokers[idx + 1:]), hands) or 1
    gain = max(0.0, with_all / without - 1)
    tier = TIER.get(j.name, "C")
    other_state = replace(state, jokers=state.jokers[:idx] + state.jokers[idx + 1:])
    syn_d, _ = strategy.joker_synergy(other_state, j.name, ante)
    # a passive joker's whole effect is in the simulation: +0% means a dead slot, not its tier (run 20261101-203240
    # Z60-215: Oops! All 6s without a probability joker kept Tier A, Photograph/Blackboard/Stuntman left in the shop)
    if j.name in PASSIVE and j.name != "Chicot" and gain < 0.01 and syn_d <= 0:
        tier = "C"
    val = TIER_POINTS[tier] + min(gain * 4, 4) + {"poly": 2, "holo": 1, "foil": 0.5, "negative": 3}.get(j.edition, 0)
    role = strategy.joker_role(j.name)
    if role:
        val += strategy.ROLE_BONUS[strategy._phase(ante)][role]
    if j.name in strategy.HAND_JOKERS.get(strategy.focus_hand(state), ()):
        val += 2
    val += syn_d
    val += min(3.0, j.vars.get("mult", 0) / 8 + j.vars.get("chips", 0) / 40
               + max(0.0, j.vars.get("xmult", 1) - 1) * 2 + max(0.0, j.vars.get("x", 1) - 1) * 2)
    if idx in dict(strategy.sell_candidates(state, ante)):
        val -= 2.0
    if j.rental:
        val -= 2.0  # costs $3 every round it stays
    return val


def _helps_score(state, offer):
    from . import strategy
    if offer.kind == "joker":
        has_gain = "now +0%" not in offer.why and "now -" not in offer.why
        fast_scale = {"Ride the Bus", "Green Joker", "Runner", "Square Joker", "Spare Trousers",
                      "Wee Joker", "Hiker", "Supernova", "Castle", "Constellation", "Hologram", "Madness"}
        return has_gain or offer.name in fast_scale or offer.name in {"Four Fingers", "Shortcut", "Smeared Joker"}
    if offer.kind == "planet":
        return PLANETS.get(offer.name) == strategy.focus_hand(state)
    if offer.kind == "booster_pack":
        n = offer.name.lower()
        return ("buffoon" in n and len(state.jokers) < state.joker_slots) or ("celestial" in n and strategy.has_clear_focus(state))
    if offer.kind == "voucher":
        return offer.name in ("Grabber", "Nacho Tong", "Antimatter", "Paint Brush", "Palette")
    return False


def _horizon(ante):
    """Weight of lost interest: it pays only for the rounds left - full up to ante 4, none in endless (9+)."""
    return min(1.0, max(0.0, (9 - ante) / 5))


def _need_this_ante(state, ante):
    """Hardest blind left this ante; in the shop the upcoming boss counts (The Wall 4x: run 20261101-081407
    Z99 sold Blue Joker for Ride the Bus, -54%/hand, checked against 10,000 instead of 20,000, then lost)."""
    from . import strategy
    boss = next((b for b in (state.blind, state.upcoming_boss) if b in strategy.BLIND_MULT), "boss")
    return max(strategy.required_score(ante, "big", state), strategy.required_score(ante, boss, state),
               state.blind_chips)


def _lost_steps(state, money, net_cost, ante):
    from . import strategy
    cap = strategy.interest_cap(state)

    def lost(c):
        return min(max(money, 0) // 5, c) - min(max(money - net_cost, 0) // 5, c)
    base = lost(min(cap, 5))
    # Seed Money / Money Tree steps above $25 count half: run 101337 sat on $43-60 from ante 4 to 6 for a $50
    # cap while its blinds were tight and celestial packs stayed in the shop
    return round((base + 0.5 * (lost(cap) - base)) * _horizon(ante) - 1e-9)


def _min_buy_value(state, offer, money, net_cost, ante, est, need_curr, need_next):
    from . import strategy
    weak = est < need_curr * 1.15
    strong = not weak and est >= need_next * 1.25
    cap = strategy.interest_cap(state)
    lost_steps = _lost_steps(state, money, net_cost, ante)
    under_cap = cap > 0 and (money - net_cost) < min(cap, 5) * 5 and _horizon(ante) >= 0.5

    if offer.kind == "tarot" and offer.name in ("The Hermit", "Temperance") and offer.value >= 6.0:
        return 5.0
    if offer.kind == "voucher" and not weak:
        # rated down by the run context: idle money only (run 20261101-203240 Z85 Wasteful "build avoids
        # discards" next to Green Joker, $13 -> $8; Crystal Ball "enough consumable slots" in 194150/105644)
        if offer.value < VOUCHER_POINTS.get(offer.name, 3) and money - net_cost < cap * 5:
            return 99.0
        # permanent effect: money above the interest cap is idle, and the $10 price is regained in a few rounds
        return 2.0 if lost_steps == 0 else 5.0 + 0.5 * lost_steps
    if weak:
        if _helps_score(state, offer):
            return 3.5 + 0.4 * lost_steps
        return 7.5 + 1.5 * lost_steps
    if strong:
        # 3+ empty joker slots: a scoring joker beats a $1 interest step (runs 215640 Z9, 074053 Z16/Z23/Z32)
        if (state.joker_slots - len(state.jokers) >= 3 and _helps_score(state, offer)
                and (offer.kind == "joker" or "buffoon" in offer.name.lower())):
            return 5.0 + 1.2 * lost_steps + (0.5 if under_cap else 0.0)
        if lost_steps > 0:
            return 9.0 + 1.5 * lost_steps
        if under_cap:
            if offer.kind == "planet" and PLANETS.get(offer.name) == strategy.focus_hand(state) and money >= 10:
                return 6.5
            return 8.5
        return 7.5 if offer.kind == "joker" else 5.5
    return 5.0 + 1.2 * lost_steps + (0.5 if under_cap else 0.0)


def _reroll_goal(state, ante, est, need_curr, free_jokers, free_cons, money, reroll_cost, weakest=None):
    """weakest: _owned_joker_value of the weakest sellable joker when slots are full (None = not computed)."""
    from . import strategy
    if reroll_cost is None or money < reroll_cost + 4:
        return None
    weak = est < need_curr * 1.15
    cap = strategy.interest_cap(state)
    floor = cap * 5
    has_flash = any(j.name == "Flash Card" and not j.debuff for j in state.jokers)
    can_afford_econ = (money - reroll_cost >= floor) or (has_flash and money - reroll_cost >= max(15, floor - 5))
    # keep $10 (2 interest steps): urgent rerolls down to $5 drained ~$30/ante without a useful buy
    can_afford_urgent = weak and ante >= 2 and (money - reroll_cost >= 10)
    if not (can_afford_econ or can_afford_urgent):
        return None

    owned = {j.name for j in state.jokers if not j.debuff}
    # full slots: a swap needs offer value >= weakest + 2, out of reach for non-S jokers once weakest >= 8
    # (run 105755 Z446-451: $56 -> $11 rerolled at ante 10, nothing bought; same in 5 earlier runs)
    swappable = weakest is None or weakest < 8
    can_replace = swappable and any(not j.eternal and j.edition != "negative" for j in state.jokers)
    cands = strategy.sell_candidates(state, ante) if swappable else []
    if state.blind in ("The Plant", "The Wall", "Violet Vessel", "The Eye", "Verdant Leaf") and weak and (free_jokers > 0 or can_replace):
        return f"look for a counter or instant score against {state.blind}"
    if weak and (free_jokers > 0 or can_replace):
        return f"look for a scoring joker for ante {ante} (~{est:,.0f}/{need_curr:,} points)"
    # can_replace, not cands: five flat +mult jokers are never tier C/D, so cands stayed empty and the money went
    # to planet rerolls instead (runs 201325/081407 lost at A4 with $40-48; 112941 A5 $49, zero xMult)
    if ante >= 4 and not (owned & strategy.XMULT) and (free_jokers > 0 or can_replace):
        return "look for an xMult joker for higher antes"
    if ante <= 4 and not (owned & strategy.SCALING) and free_jokers > 0:
        return "look for a scaling joker to build up"
    if free_jokers > 0:
        return f"fill the free joker slot ({len(state.jokers)}/{state.joker_slots})"
    if cands:
        idx, _ = cands[0]
        return f"look for a better joker to replace {state.jokers[idx].name}"
    # planet rerolls without a clear focus returned ~0 (learnings: 7 runs, 105644 ~$110 for one Uranus at +0%)
    if free_cons > 0 and money - reroll_cost >= floor + 5 and strategy.has_clear_focus(state):
        return f"look for a planet for the focus hand ({strategy.focus_hand(state)}) or a tarot"
    return None


def shop_plan(state, items, ante, reroll_cost=None, samples=40, auto=False) -> list[ShopAction]:
    """Ordered list of shop actions (buy/sell/reroll), always ending with 'leave'.
    auto: plan only what the autopilot can carry out (no joker drag, never rerolls the boss)."""
    from . import strategy
    ante = ante or 1
    sim = state.copy()
    money = sim.money
    free = sim.joker_slots - len(sim.jokers)
    free_cons = max(0, consumable_slots(sim) - len(sim.consumables))
    rng = random.Random(0)
    deck = sim.get_full_deck()
    hands = [rng.sample(deck, min(sim.hand_size, len(deck))) for _ in range(max(10, samples))]

    # Map current sim.jokers back to their original index in state.jokers
    # ponytail: greedy single-pass over sorted offers; full knapsack unnecessary for <=7 shop items
    joker_orig_idx = list(range(len(sim.jokers)))
    offers = offers_for(sim, items, ante, samples, auto)
    actions: list[ShopAction] = []
    bought_indices = set()

    for o in offers:
        if o.idx in bought_indices or o.kind == "playing_card":
            continue
        est = strategy.estimate_round(sim, samples=min(samples, 20), boss=sim.upcoming_boss)
        need_curr = _need_this_ante(sim, ante)
        need_next = strategy.next_ante_score(sim, ante, "big")
        item = items[o.idx]
        ed = item.get("edition")
        is_neg = ed == "negative"

        if o.kind in ("planet", "tarot", "spectral"):
            if free_cons <= 0 or o.price > money:
                continue
            min_v = _min_buy_value(sim, o, money, o.price, ante, est, need_curr, need_next)
            if o.value < min_v:
                continue
            actions.append(ShopAction("buy", o.idx, f"{o.name} (${o.price}): {o.why}"))
            money -= o.price
            free_cons -= 1
            sim.consumables = list(sim.consumables) + [o.name]
            if o.kind == "planet" and o.name in PLANETS:
                h = PLANETS[o.name]
                sim.hand_levels = dict(sim.hand_levels, **{h: sim.level(h) + 1})
            bought_indices.add(o.idx)

        elif o.kind == "joker":
            if free > 0 or is_neg:
                if o.price > money:
                    continue
                min_v = _min_buy_value(sim, o, money, o.price, ante, est, need_curr, need_next)
                # $0 Negative (Negative Tag): no money, no slot, still has a sell value - never a loss (run 094727 Z242)
                if o.value < min_v and not (is_neg and o.price <= 0 and not item.get("rental")):
                    continue
                actions.append(ShopAction("buy", o.idx, f"{o.name} (${o.price}): {o.why}"))
                money -= o.price
                if not is_neg:
                    free -= 1
                else:
                    sim.joker_slots += 1
                sim.jokers = list(sim.jokers) + [Joker(o.name, edition=ed)]
                joker_orig_idx.append(-1)
                bought_indices.add(o.idx)
            else:
                # Slots full: consider selling the weakest non-eternal, non-negative owned joker
                sellable = [(k, _owned_joker_value(sim, k, ante, hands))
                            for k, j in enumerate(sim.jokers)
                            if not j.eternal and j.edition != "negative" and joker_orig_idx[k] >= 0]
                if not sellable:
                    continue
                # swap target: the owned joker whose replacement scores best, among those the value rule allows
                # (eff_val <= value + 2). The lowest-valued joker alone was Banner at Z293 of run 20261101-212446:
                # -42% for Smiley Face while Rocket/Half Joker would have given +39%/+21%; ~$60 rerolled instead
                base = _avg_best(_sim_state(sim), hands) or 1
                swaps = [(_avg_best(replace(_sim_state(sim), jokers=sim.jokers[:k] + sim.jokers[k + 1:]
                                            + [Joker(o.name, edition=ed)]), hands) / base - 1, k, v)
                         for k, v in sellable if v <= o.value]
                if not swaps:
                    continue
                rep_gain, k_weak, v_weak = max(swaps)
                j_weak = sim.jokers[k_weak]
                sv = _sell_value(j_weak)
                if money + sv < o.price:
                    continue
                rep_jokers = sim.jokers[:k_weak] + sim.jokers[k_weak + 1:] + [Joker(o.name, edition=ed)]
                eff_val = max(o.value, o.value + min(rep_gain * 2, 2))
                net_cost = max(0, o.price - sv)
                lost = _lost_steps(sim, money, net_cost, ante)
                if eff_val < max(v_weak + 2.0, 5.0 + 1.0 * lost):
                    continue
                # a joker that doesn't scale or earn shows its whole effect in the simulation: scoring less after
                # the swap never pays off (runs 094338 Z82 Half Joker -> Seeing Double -11%, 105644 Z168 Abstract
                # Joker -> Smeared Joker -16% with Two Pair focus, dead slot until sold at Z239)
                if rep_gain < -0.05 and o.name not in strategy.SCALING | strategy.ECON:
                    continue
                # Vampire scales only on enhanced cards: judge it at the xMult the deck can feed (run 20261101-205435
                # Z320: Odd Todd sold for Vampire at -35%, 3 enhanced cards in the deck, stuck at x1.3 to the end)
                if o.name == "Vampire" and rep_gain < -0.05:
                    fed = rep_jokers[:-1] + [Joker(o.name, edition=ed, vars={
                        "xmult": 1 + 0.1 * sum(1 for c in deck if c.enhancement)})]
                    if _avg_best(replace(_sim_state(sim), jokers=fed), hands) / (
                            _avg_best(_sim_state(sim), hands) or 1) < 0.95:
                        continue
                # eff_val ignores a score loss; a swap must not leave the next blind short (run 20261031-091942
                # Z230: Abstract Joker sold for a fresh Obelisk before The Club, est ~21,021/22,000, lost)
                if rep_gain < 0 and strategy.estimate_round(replace(sim, jokers=rep_jokers),
                                                            samples=min(samples, 20), boss=sim.upcoming_boss) < need_curr * 1.15:
                    continue
                # after the sale the replan sees a free slot: the buy must pass that rule too, else
                # the joker is sold and the replacement left in the shop (run 20261031-082933 Z80/81)
                after = replace(sim, jokers=sim.jokers[:k_weak] + sim.jokers[k_weak + 1:])
                if o.value < _min_buy_value(after, o, money + sv, o.price, ante, est, need_curr, need_next):
                    continue
                orig_k = joker_orig_idx[k_weak]
                clean_why = o.why.replace(", no free slot", "")
                # net of what the sold joker contributed: 200×20 → 120×40 is +20 %, not ×2
                actions.append(ShopAction("sell", orig_k, f"sell {j_weak.name} (+${sv}) for {o.name}: "
                                                          f"{rep_gain:+.0%} score/hand after the swap"))
                actions.append(ShopAction("buy", o.idx, f"{o.name} (${o.price}): {clean_why}"))
                money = money + sv - o.price
                sim.jokers = rep_jokers
                joker_orig_idx = joker_orig_idx[:k_weak] + joker_orig_idx[k_weak + 1:] + [-1]
                bought_indices.add(o.idx)

        elif o.kind in ("booster_pack", "voucher"):
            if o.price > money or (auto and o.name in ("Director's Cut", "Retcon")):  # autopilot always Selects
                continue
            min_v = _min_buy_value(sim, o, money, o.price, ante, est, need_curr, need_next)
            if o.value < min_v:
                continue
            actions.append(ShopAction("buy", o.idx, f"{o.name} (${o.price}): {o.why}"))
            money -= o.price
            if o.kind == "voucher":
                sim.vouchers = list(sim.vouchers) + [o.name]
            bought_indices.add(o.idx)

    # Consider a reroll only when no booster pack was just bought and a concrete target + budget exist
    bought_pack = any(a.kind == "buy" and items[a.index]["kind"] == "booster_pack" for a in actions)
    if not bought_pack:
        est = strategy.estimate_round(sim, samples=min(samples, 20), boss=sim.upcoming_boss)
        need_curr = _need_this_ante(sim, ante)
        weakest = None
        if free <= 0 and reroll_cost is not None:
            weakest = min((_owned_joker_value(sim, k, ante, hands) for k, j in enumerate(sim.jokers)
                           if not j.eternal and j.edition != "negative"), default=None)
        goal = _reroll_goal(sim, ante, est, need_curr, free, free_cons, money, reroll_cost, weakest)
        if goal:
            money -= reroll_cost
            actions.append(ShopAction("reroll", -1, f"Reroll (${reroll_cost}): {goal}"))

    zins = strategy.interest_of(money, sim)
    leave_why = (f"leave the shop (${money} left → ${zins} interest)" if actions
                 else f"keep the money for interest (${money} → ${zins} interest)" if zins
                 else f"nothing worthwhile affordable (${money}, no interest below $5)")
    actions.append(ShopAction("leave", -1, leave_why))
    return actions


def choose_buys(state, offers):
    """-> (offers to buy in this order, money left)."""
    money = state.money
    free = state.joker_slots - len(state.jokers)
    free_cons = max(0, consumable_slots(state) - len(state.consumables))
    buys = []
    for o in offers:
        if o.value < 5 or not o.buyable or o.price > money:
            continue
        if o.kind == "joker" and free <= 0 and "negative" not in o.why:
            continue
        if o.kind in ("planet", "tarot", "spectral") and free_cons <= 0:
            continue
        buys.append(o)
        money -= o.price
        if o.kind == "joker" and "negative" not in o.why:
            free -= 1
        elif o.kind in ("planet", "tarot", "spectral"):
            free_cons -= 1
    return buys, money


def offers_for(state, items, ante=None, samples=40, auto=False):
    """evaluate() plus the run-phase / focus-hand weighting when the ante is known."""
    from . import strategy
    offers = evaluate(state, items, samples, auto=auto)
    return strategy.adjust_offers(state, offers, ante) if ante else offers


def advise(state, items, phase="shop", reroll_cost=None, samples=40, ante=None, details=None, auto=False):
    """-> text: what to buy/sell/reroll (shop) or which card to take (opened booster pack).
    In shop phase, recommendations are built directly from shop_plan()."""
    from . import strategy
    eval_state = replace(state, consumables=[]) if phase == "booster_pack" else state
    offers = offers_for(eval_state, items, ante, samples, auto=auto)
    if details is not None:
        details["offers"] = [asdict(o) for o in offers]
    lines = []
    if phase == "booster_pack":
        best = next((o for o in offers if o.buyable), None)
        lines.append(f"RECOMMENDATION: Take {best.name} ({best.why})" if best else "RECOMMENDATION: Skip")
    else:
        plan = shop_plan(state, items, ante or 1, reroll_cost=reroll_cost, samples=samples, auto=auto)
        if details is not None:
            details["shop_actions"] = [asdict(a) for a in plan]
        steps = [a for a in plan if a.kind != "leave"]
        money = state.money
        if steps:
            parts = []
            for a in steps:
                if a.kind == "sell":
                    j = state.jokers[a.index]
                    sv = _sell_value(j)
                    money += sv
                    parts.append(f"Sell {j.name} (+${sv})")
                elif a.kind == "buy":
                    it = items[a.index]
                    p = default_price(it) if it.get("price") is None else it["price"]
                    money -= p
                    parts.append(f"Buy {it['name']} (${p})")
                elif a.kind == "reroll":
                    money -= reroll_cost or 0
                    parts.append(a.why)
            lines.append("RECOMMENDATION: " + ", then ".join(parts))
        else:
            lines.append("RECOMMENDATION: Buy nothing (Next Round)")
        lines.append(plan[-1].why)
        if ante:
            lines.append(strategy.economy_note(money, ante, state))
    lines.append("\nOffers (value / price):")
    for o in offers:
        lines.append(f"  {o.value:5.1f}  ${o.price:<3} {o.name:<22} {o.why}")
    return "\n".join(lines)


if __name__ == "__main__":
    from .data import JOKER_NAMES
    from .models import State
    assert set(TIER) == set(JOKER_NAMES), set(JOKER_NAMES) ^ set(TIER)
    s = State(hand=[], money=10)
    items = [{"kind": "joker", "name": "Joker", "price": 2}, {"kind": "joker", "name": "Spare Trousers", "price": 6},
              {"kind": "booster_pack", "name": "Buffoon Pack", "price": 4}, {"kind": "planet", "name": "Mercury", "price": 3}]
    offers = evaluate(s, items, samples=20)
    assert offers[0].name == "Spare Trousers" and all(o.value > 0 for o in offers)
    full = State(hand=[], money=10, jokers=[Joker("Joker")] * 5)
    assert not evaluate(full, items[:1], samples=5)[0].buyable
    # Full slots: a swap needs a hand to score with (an empty hand can't show the gain), the plan still ends with leave
    plan_full = shop_plan(full, items[1:2], ante=2, samples=10)
    assert plan_full[-1].kind == "leave", plan_full
    print(advise(s, items, ante=1))
    # an unreadable price is never "free": with $2 an unpriced Buffoon Pack (base $4) is not bought
    poor = State(hand=[], money=2)
    pack = evaluate(poor, [{"kind": "booster_pack", "name": "Buffoon Pack", "price": None}], samples=5)
    assert pack[0].price == 4 and not choose_buys(poor, pack)[0]
    assert shop_plan(poor, [{"kind": "booster_pack", "name": "Buffoon Pack", "price": None}], ante=1, samples=5)[-1].kind == "leave"
    # Gold Stake: a $1 rental joker costs $3 every round - never the "cheap" pick over the normal one
    rent = evaluate(s, [{"kind": "joker", "name": "Bull", "price": 1, "rental": True},
                        {"kind": "joker", "name": "Bull", "price": 6}], samples=5)
    assert rent[0].price == 6 and "rental" in rent[1].why
    print("shop ok")
