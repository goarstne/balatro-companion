"""Jokers 76-150 of data.JOKER_NAMES (Midas Mask .. Perkeo).

Numbers / rarities / costs checked against balatrowiki.org (Jokers list, Blueprint page)
and the vanilla 1.0.1 card logic (Card:calculate_joker).
"""

from . import JokerSpec, get, register

EDITION_COST = {"foil": 2, "holo": 3, "poly": 5, "negative": 5}


def R(name, rarity, cost, desc, **kw):
    register(JokerSpec(name, rarity, cost, desc, **kw))


def _rank(card, *ranks):
    return not card.stone and card.rank in ranks


def _suit_slots(ctx, wild_order, bypass_debuff=False, one_suit_each=True):
    """Vanilla Flower Pot / Seeing Double suit tally: non-wild cards first, then each wild card
    fills the first still-empty suit in `wild_order`."""
    filled = set()
    for c in ctx.scoring:
        if c.enhancement == "wild":
            continue
        suits = [s for s in "HDSC" if c.is_suit(s, ctx.flags, bypass_debuff=bypass_debuff)]
        if one_suit_each:  # Flower Pot: elseif chain, first empty suit only
            suits = [s for s in suits if s not in filled][:1]
        filled.update(suits)
    for c in ctx.scoring:
        if c.enhancement == "wild":
            s = next((s for s in wild_order if s not in filled and ctx.is_suit(c, s)), None)
            if s:
                filled.add(s)
    return filled


def _sell_value(joker):
    if joker.sell_value is not None:
        return joker.sell_value
    spec = get(joker.name)
    return max(1, ((spec.cost if spec else 0) + EDITION_COST.get(joker.edition, 0)) // 2)


# ----------------------------------------------------------------- hooks
def midas_before(j, ctx):
    # Turns scoring face cards Gold before they score (replaces any other enhancement).
    if ctx.copying:
        return
    for c in ctx.scoring:
        if not c.debuff and ctx.is_face(c):
            c.enhancement = "gold"


def photograph_scored(j, ctx, card):
    first = next((c for c in ctx.scoring if not c.debuff and ctx.is_face(c)), None)
    if card is first:
        ctx.x_mult(2, j)


def lucky_cat_scored(j, ctx, card):
    if card.enhancement == "lucky" and not ctx.copying:
        if ctx.rng is not None:
            j.vars["x"] += 0.25 * ctx.extra.get("lucky_triggered", False)
            return
        p_mult, p_money = ctx.chance(1, 5), ctx.chance(1, 15)
        j.vars["x"] += 0.25 * (1 - (1 - p_mult) * (1 - p_money))  # EV of "lucky triggered"


def baseball_other(j, ctx, other):
    spec = get(other.name)
    if spec and spec.rarity == "Uncommon":
        ctx.x_mult(1.5, j)


def trousers_before(j, ctx):
    if not ctx.copying and "Two Pair" in ctx.contains:
        j.vars["mult"] += 2


def wee_scored(j, ctx, card):
    if _rank(card, 2) and not ctx.copying:
        j.vars["chips"] += 8


def erosion_main(j, ctx):
    start = 40 if (ctx.state.deck_type or "").startswith("Abandoned") else 52
    ctx.add_mult(4 * max(0, start - len(ctx.state.get_full_deck())), j)


def swash_main(j, ctx):
    ctx.add_mult(sum(_sell_value(o) for o in ctx.jokers if o is not j), j)


def dollars(ctx):
    return ctx.state.money + ctx.money  # vanilla adds dollar_buffer (money earned this hand)


def flower_main(j, ctx):
    if len(_suit_slots(ctx, "HDSC", bypass_debuff=True)) == 4:
        ctx.x_mult(3, j)


def seeing_double_main(j, ctx):
    s = _suit_slots(ctx, "CDSH", one_suit_each=False)  # non-wild cards count for every suit they match
    if "C" in s and len(s) >= 2:
        ctx.x_mult(2, j)


def walkie_scored(j, ctx, card):
    if _rank(card, 10, 4):
        ctx.add_chips(10, j)
        ctx.add_mult(4, j)


def xm(x_or_key):
    """main: X mult from a constant or from j.vars[key]."""
    if isinstance(x_or_key, str):
        return lambda j, ctx: ctx.x_mult(j.vars[x_or_key], j)
    return lambda j, ctx: ctx.x_mult(x_or_key, j)


def contains_x(hand, x):
    return lambda j, ctx: ctx.x_mult(x, j) if hand in ctx.contains else None


# ----------------------------------------------------------------- registry
R("Midas Mask", "Uncommon", 7, "All played face cards become Gold cards when scored",
  before=midas_before, blueprint_compat=False,
  notes="Applied in `before`: scoring face cards lose their old enhancement (bonus/mult/glass/lucky) "
        "before scoring; feeds Golden Ticket. State is not mutated.")
R("Luchador", "Uncommon", 5, "Sell this card to disable the current Boss Blind",
  notes="No scoring effect; selling it = boss_disabled=True.")
R("Photograph", "Common", 5, "First played face card gives X2 Mult when scored",
  on_scored=photograph_scored, notes="Every retrigger of that first face card gives X2 again.")
R("Gift Card", "Uncommon", 6, "Add $1 of sell value to every Joker and Consumable card at end of round",
  blueprint_compat=False, notes="Economy only; raises Swashbuckler (set sell_value).")
R("Turtle Bean", "Uncommon", 6, "+5 hand size, reduces by 1 each round", blueprint_compat=False,
  notes="Hand size only; reflect in State.hand_size.")
R("Erosion", "Uncommon", 6, "+4 Mult for each card below the deck's starting size in your full deck",
  main=erosion_main, notes="Starting size 52 (40 for Abandoned Deck); uses state.get_full_deck().")
R("Reserved Parking", "Common", 6, "Each face card held in hand has a 1 in 2 chance to give $1",
  on_held=lambda j, ctx, c: ctx.add_money(ctx.chance(1, 2)) if ctx.is_face(c) else None,
  notes="Money only (expected value).")
R("Mail-In Rebate", "Common", 4, "Earn $5 for each discarded [rank], rank changes every round",
  notes="Discard economy only.")
R("To the Moon", "Uncommon", 5, "Earn an extra $1 of interest for every $5 you have at end of round",
  blueprint_compat=False, notes="Economy only.")
R("Hallucination", "Common", 4, "1 in 2 chance to create a Tarot card when any Booster Pack is opened",
  notes="No scoring effect.")
R("Fortune Teller", "Common", 6, "+1 Mult per Tarot card used this run", defaults={"tarots": 0},
  main=lambda j, ctx: ctx.add_mult(j.vars["tarots"], j), notes="vars.tarots = Tarots used this run.")
R("Juggler", "Common", 4, "+1 hand size", blueprint_compat=False, notes="Hand size only.")
R("Drunkard", "Common", 4, "+1 discard each round", blueprint_compat=False, notes="Discards only.")
R("Stone Joker", "Uncommon", 6, "+25 Chips for each Stone Card in your full deck",
  main=lambda j, ctx: ctx.add_chips(25 * sum(c.stone for c in ctx.state.get_full_deck()), j),
  notes="Counts state.get_full_deck(); set full_deck for accurate results.")
R("Golden Joker", "Common", 6, "Earn $4 at end of round", blueprint_compat=False, notes="End-of-round money only.")
R("Lucky Cat", "Uncommon", 6, "Gains X0.25 Mult every time a Lucky card successfully triggers",
  defaults={"x": 1}, on_scored=lucky_cat_scored, main=xm("x"),
  notes="Scales during scoring by the expected number of lucky triggers (mult or money roll).")
R("Baseball Card", "Rare", 8, "Uncommon Jokers each give X1.5 Mult", on_other_joker=baseball_other,
  notes="Applied right after each non-debuffed Uncommon joker (with or without a main effect).")
R("Bull", "Uncommon", 6, "+2 Chips for each $1 you have",
  main=lambda j, ctx: ctx.add_chips(2 * max(0, dollars(ctx)), j),
  notes="Uses state.money + money earned earlier this hand.")
R("Diet Cola", "Uncommon", 6, "Sell this card to create a free Double Tag", notes="No scoring effect.")
R("Trading Card", "Uncommon", 6, "If first discard of round has only 1 card, destroy it and earn $3",
  blueprint_compat=False, notes="Discard economy only.")
R("Flash Card", "Uncommon", 5, "Gains +2 Mult per reroll in the shop", defaults={"mult": 0},
  main=lambda j, ctx: ctx.add_mult(j.vars["mult"], j), notes="vars.mult = current Mult.")
R("Popcorn", "Common", 5, "+20 Mult, -4 Mult per round played", defaults={"mult": 20},
  main=lambda j, ctx: ctx.add_mult(j.vars["mult"], j), notes="vars.mult = current Mult.")
R("Spare Trousers", "Uncommon", 6, "Gains +2 Mult if played hand contains a Two Pair", defaults={"mult": 0},
  before=trousers_before, main=lambda j, ctx: ctx.add_mult(j.vars["mult"], j),
  notes="Scales before scoring, so the triggering hand already counts. Full House contains Two Pair.")
R("Ancient Joker", "Rare", 8, "Each played card with [suit] gives X1.5 Mult when scored, suit changes at end of round",
  defaults={"suit": "S"},
  on_scored=lambda j, ctx, c: ctx.x_mult(1.5, j) if ctx.is_suit(c, j.vars["suit"]) else None,
  notes="vars.suit (S/H/C/D), default S. Wild cards and Smeared Joker count.")
R("Ramen", "Uncommon", 6, "X2 Mult, loses X0.01 Mult per card discarded", defaults={"x": 2}, main=xm("x"),
  notes="vars.x = current multiplier.")
R("Walkie Talkie", "Common", 4, "Each played 10 or 4 gives +10 Chips and +4 Mult when scored",
  on_scored=walkie_scored)
R("Seltzer", "Uncommon", 6, "Retrigger all cards played for the next 10 hands",
  retrigger=lambda j, ctx, c, area: 1 if area == "play" else 0, notes="Remaining hands not tracked.")
R("Castle", "Uncommon", 6, "Gains +3 Chips per discarded [suit] card, suit changes every round",
  defaults={"chips": 0, "suit": "S"}, main=lambda j, ctx: ctx.add_chips(j.vars["chips"], j),
  notes="vars.chips = current Chips, vars.suit informational (default S).")
R("Smiley Face", "Common", 4, "Played face cards give +5 Mult when scored",
  on_scored=lambda j, ctx, c: ctx.add_mult(5, j) if ctx.is_face(c) else None)
R("Campfire", "Rare", 9, "Gains X0.25 Mult for each card sold, resets when Boss Blind is defeated",
  defaults={"x": 1}, main=xm("x"), notes="vars.x = current multiplier.")
R("Golden Ticket", "Common", 5, "Played Gold cards earn $4 when scored",
  on_scored=lambda j, ctx, c: ctx.add_money(4) if c.enhancement == "gold" else None,
  notes="Pays for cards Midas Mask turned Gold this hand.")
R("Mr. Bones", "Uncommon", 5, "Prevents Death if chips scored are at least 25% of required chips, self destructs",
  blueprint_compat=False, notes="No scoring effect.")
R("Acrobat", "Uncommon", 6, "X3 Mult on final hand of round",
  main=lambda j, ctx: ctx.x_mult(3, j) if ctx.final_hand else None, notes="Final hand = state.hands_left <= 1.")
R("Sock and Buskin", "Uncommon", 6, "Retrigger all played face cards",
  retrigger=lambda j, ctx, c, area: 1 if area == "play" and ctx.is_face(c) else 0)
R("Swashbuckler", "Common", 4, "Adds the sell value of all other owned Jokers to Mult", main=swash_main,
  notes="Sell value = Joker.sell_value if set, else max(1, (cost + edition cost) // 2); "
        "ignores discounts / Egg / Gift Card unless sell_value is given.")
R("Troubadour", "Uncommon", 6, "+2 hand size, -1 hand each round", blueprint_compat=False,
  notes="Hand size / hands only.")
R("Certificate", "Uncommon", 6, "When round begins, add a random playing card with a random seal to your hand",
  notes="No scoring effect.")
R("Smeared Joker", "Uncommon", 7, "Hearts and Diamonds count as the same suit, Spades and Clubs count as the same suit",
  blueprint_compat=False, notes="Passive, engine-side.")
R("Throwback", "Uncommon", 6, "X0.25 Mult for each Blind skipped this run", defaults={"x": 1}, main=xm("x"),
  notes="vars.x = 1 + 0.25 * blinds skipped.")
R("Hanging Chad", "Common", 4, "Retrigger first played card used in scoring 2 additional times",
  retrigger=lambda j, ctx, c, area: 2 if area == "play" and ctx.scoring and c is ctx.scoring[0] else 0,
  notes="1.0.1 value: 2 extra triggers. Debuffed first card is simply skipped.")
R("Rough Gem", "Uncommon", 7, "Played cards with Diamond suit earn $1 when scored",
  on_scored=lambda j, ctx, c: ctx.add_money(1) if ctx.is_suit(c, "D") else None)
R("Bloodstone", "Uncommon", 7, "1 in 2 chance for played cards with Heart suit to give X1.5 Mult when scored",
  on_scored=lambda j, ctx, c: ctx.x_mult(1 + 0.5 * ctx.chance(1, 2), j) if ctx.is_suit(c, "H") else None,
  notes="Expected value: X1.25 per Heart (X1.5 with Oops! All 6s).")
R("Arrowhead", "Uncommon", 7, "Played cards with Spade suit give +50 Chips when scored",
  on_scored=lambda j, ctx, c: ctx.add_chips(50, j) if ctx.is_suit(c, "S") else None)
R("Onyx Agate", "Uncommon", 7, "Played cards with Club suit give +7 Mult when scored",
  on_scored=lambda j, ctx, c: ctx.add_mult(7, j) if ctx.is_suit(c, "C") else None)
R("Glass Joker", "Uncommon", 6, "Gains X0.75 Mult for every Glass Card that is destroyed", defaults={"x": 1},
  main=xm("x"), notes="Glass breaking this hand happens after scoring, so it never counts for this hand.")
R("Showman", "Uncommon", 5, "Joker, Tarot, Planet, and Spectral cards may appear multiple times",
  blueprint_compat=False, notes="Shop only.")
R("Flower Pot", "Uncommon", 6, "X3 Mult if poker hand contains a Diamond, Club, Heart and Spade card",
  main=flower_main, notes="Checks scoring cards only; wild cards fill missing suits; Smeared Joker "
                          "makes it nearly impossible without wild cards.")
R("Blueprint", "Rare", 10, "Copies ability of Joker to the right", notes="Engine-side (engine._resolve).")
R("Wee Joker", "Rare", 8, "Gains +8 Chips when each played 2 is scored", defaults={"chips": 0},
  on_scored=wee_scored, main=lambda j, ctx: ctx.add_chips(j.vars["chips"], j),
  notes="Scales per scored 2 (incl. retriggers) during this hand, then adds its Chips.")
R("Merry Andy", "Uncommon", 7, "+3 discards each round, -1 hand size", blueprint_compat=False)
R("Oops! All 6s", "Uncommon", 4, "Doubles all listed probabilities", blueprint_compat=False,
  notes="Passive, engine-side (ctx.prob).")
R("The Idol", "Uncommon", 6, "Each played [rank] of [suit] gives X2 Mult when scored, card changes every round",
  defaults={"rank": 14, "suit": "S"},
  on_scored=lambda j, ctx, c: ctx.x_mult(2, j) if _rank(c, j.vars["rank"]) and ctx.is_suit(c, j.vars["suit"]) else None,
  notes="vars.rank (2-14) / vars.suit, default Ace of Spades.")
R("Seeing Double", "Uncommon", 6, "X2 Mult if played hand has a scoring Club card and a scoring card of any other suit",
  main=seeing_double_main, notes="Same suit assignment as Flower Pot: a lone wild card counts as one suit.")
R("Matador", "Uncommon", 7, "Earn $8 if played hand triggers the Boss Blind ability", notes="Not modelled.")
R("Hit the Road", "Rare", 8, "Gains X0.5 Mult for every Jack discarded this round", defaults={"x": 1},
  main=xm("x"), notes="vars.x = 1 + 0.5 * Jacks discarded this round.")
R("The Duo", "Rare", 8, "X2 Mult if played hand contains a Pair", main=contains_x("Pair", 2))
R("The Trio", "Rare", 8, "X3 Mult if played hand contains a Three of a Kind", main=contains_x("Three of a Kind", 3))
R("The Family", "Rare", 8, "X4 Mult if played hand contains a Four of a Kind", main=contains_x("Four of a Kind", 4))
R("The Order", "Rare", 8, "X3 Mult if played hand contains a Straight", main=contains_x("Straight", 3))
R("The Tribe", "Rare", 8, "X2 Mult if played hand contains a Flush", main=contains_x("Flush", 2))
R("Stuntman", "Rare", 7, "+250 Chips, -2 hand size", main=lambda j, ctx: ctx.add_chips(250, j))
R("Invisible Joker", "Rare", 8, "After 2 rounds, sell this card to Duplicate a random Joker",
  blueprint_compat=False, notes="No scoring effect.")
R("Brainstorm", "Rare", 10, "Copies the ability of leftmost Joker", notes="Engine-side (engine._resolve).")
R("Satellite", "Uncommon", 6, "Earn $1 at end of round per unique Planet card used this run",
  blueprint_compat=False, notes="End-of-round money only.")
R("Shoot the Moon", "Common", 5, "Each Queen held in hand gives +13 Mult",
  on_held=lambda j, ctx, c: ctx.add_mult(13, j) if _rank(c, 12) else None)
R("Driver's License", "Rare", 7, "X3 Mult if you have at least 16 Enhanced cards in your full deck",
  main=lambda j, ctx: ctx.x_mult(3, j) if sum(bool(c.enhancement) for c in ctx.state.get_full_deck()) >= 16 else None,
  notes="Counts state.get_full_deck().")
R("Cartomancer", "Uncommon", 6, "Create a Tarot card when Blind is selected", notes="No scoring effect.")
R("Astronomer", "Uncommon", 8, "All Planet cards and Celestial Packs in the shop are free",
  blueprint_compat=False, notes="Shop only.")
R("Burnt Joker", "Rare", 8, "Upgrade the level of the first discarded poker hand each round",
  notes="Discard only; reflect in hand_levels.")
R("Bootstraps", "Uncommon", 7, "+2 Mult for every $5 you have",
  main=lambda j, ctx: ctx.add_mult(2 * max(0, int(dollars(ctx) // 5)), j),
  notes="Uses state.money + money earned earlier this hand (expected value).")
R("Canio", "Legendary", 20, "Gains X1 Mult when a face card is destroyed", defaults={"x": 1}, main=xm("x"),
  notes="vars.x = current multiplier.")
R("Triboulet", "Legendary", 20, "Played Kings and Queens each give X2 Mult when scored",
  on_scored=lambda j, ctx, c: ctx.x_mult(2, j) if _rank(c, 12, 13) else None)
R("Yorick", "Legendary", 20, "Gains X1 Mult every 23 cards discarded", defaults={"x": 1}, main=xm("x"),
  notes="vars.x = current multiplier.")
R("Chicot", "Legendary", 20, "Disables effect of every Boss Blind", blueprint_compat=False,
  notes="Passive, engine-side.")
R("Perkeo", "Legendary", 20, "Creates a Negative copy of 1 random consumable card in your possession at the end of the shop",
  notes="No scoring effect.")
