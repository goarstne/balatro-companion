"""Jokers 1-75 of data.JOKER_NAMES (Joker .. Obelisk).

Numbers verified against balatrowiki.org/w/Jokers (vanilla 1.0.1) and the
1.0.1 card.lua logic (Raised Fist tie-break, Obelisk/Supernova/Card Sharp use
the hand counts *after* this hand's increment, Loyalty Card countdown).
"""

from . import JokerSpec, register


def reg(name, rarity, cost, desc, **kw):
    register(JokerSpec(name, rarity, cost, desc, **kw))


def _rank_ok(card, ranks):
    return not card.stone and card.rank in ranks


# ------------------------------------------------------------------ flat / conditional
reg("Joker", "Common", 2, "+4 Mult", main=lambda j, ctx: ctx.add_mult(4, j))

for _name, _suit in [("Greedy Joker", "D"), ("Lusty Joker", "H"), ("Wrathful Joker", "S"), ("Gluttonous Joker", "C")]:
    reg(_name, "Common", 5, f"Played {dict(D='Diamond', H='Heart', S='Spade', C='Club')[_suit]} cards give +3 Mult when scored",
        on_scored=lambda j, ctx, card, s=_suit: ctx.add_mult(3, j) if ctx.is_suit(card, s) else None)

for _name, _cost, _hand, _kind, _n in [
        ("Jolly Joker", 3, "Pair", "mult", 8), ("Zany Joker", 4, "Three of a Kind", "mult", 12),
        ("Mad Joker", 4, "Two Pair", "mult", 10), ("Crazy Joker", 4, "Straight", "mult", 12),
        ("Droll Joker", 4, "Flush", "mult", 10), ("Sly Joker", 3, "Pair", "chips", 50),
        ("Wily Joker", 4, "Three of a Kind", "chips", 100), ("Clever Joker", 4, "Two Pair", "chips", 80),
        ("Devious Joker", 4, "Straight", "chips", 100), ("Crafty Joker", 4, "Flush", "chips", 80)]:
    reg(_name, "Common", _cost, f"+{_n} {'Mult' if _kind == 'mult' else 'Chips'} if played hand contains a {_hand}",
        main=lambda j, ctx, h=_hand, k=_kind, n=_n:
            (ctx.add_mult if k == "mult" else ctx.add_chips)(n, j) if h in ctx.contains else None)

reg("Half Joker", "Common", 5, "+20 Mult if played hand contains 3 or fewer cards",
    main=lambda j, ctx: ctx.add_mult(20, j) if len(ctx.played) <= 3 else None)


def _stencil(j, ctx):
    # joker_slots = the in-game limit ("4/5" counter), which already includes Negative jokers' slots
    ctx.x_mult(ctx.state.joker_slots - len(ctx.jokers) + sum(1 for x in ctx.jokers if x.name == "Joker Stencil"), j)


reg("Joker Stencil", "Uncommon", 8, "X1 Mult for each empty Joker slot (Joker Stencil included)", main=_stencil,
    notes="joker_slots must include Negative bonus slots (as the game counter shows); every Stencil counts as empty.")
reg("Four Fingers", "Uncommon", 7, "All Flushes and Straights can be made with 4 cards", blueprint_compat=False,
    notes="Passive, handled in engine.")
reg("Mime", "Uncommon", 5, "Retrigger all card held in hand abilities",
    retrigger=lambda j, ctx, card, area: 1 if area == "held" else 0)
reg("Credit Card", "Common", 1, "Go up to -$20 in debt", blueprint_compat=False, notes="Economy only.")
reg("Ceremonial Dagger", "Uncommon", 6,
    "When Blind is selected, destroy Joker to the right and permanently add double its sell value to this Mult",
    defaults={"mult": 0}, main=lambda j, ctx: ctx.add_mult(j.vars["mult"], j),
    notes="Set vars.mult to the current value; grows at blind select, not during a hand.")
reg("Banner", "Common", 5, "+30 Chips for each remaining discard",
    main=lambda j, ctx: ctx.add_chips(30 * ctx.state.discards_left, j))
reg("Mystic Summit", "Common", 5, "+15 Mult when 0 discards remaining",
    main=lambda j, ctx: ctx.add_mult(15, j) if ctx.state.discards_left == 0 else None)
reg("Marble Joker", "Uncommon", 6, "Adds one Stone card to deck when Blind is selected", notes="No direct scoring.")


def _loyalty(j, ctx):
    if j.vars["remaining"] == 0:
        ctx.x_mult(4, j)


reg("Loyalty Card", "Uncommon", 5, "X4 Mult every 6 hands played", defaults={"remaining": 5}, main=_loyalty,
    notes="vars.remaining = the counter shown in game; 0 ('Active!') means this hand gives X4.")
reg("8 Ball", "Common", 5, "1 in 4 chance for each played 8 to create a Tarot card when scored",
    notes="No scoring effect.")
reg("Misprint", "Common", 4, "+0 to +23 Mult (random)", main=lambda j, ctx: ctx.add_mult(
    ctx.rng.randrange(24) if ctx.rng is not None else 11.5, j),
    notes="Scored as expected value +11.5 Mult.")
reg("Dusk", "Uncommon", 5, "Retrigger all played cards in final hand of round",
    retrigger=lambda j, ctx, card, area: 1 if area == "play" and ctx.final_hand else 0)


def _raised_fist(j, ctx, card):
    low = None
    for c in ctx.held:            # >= : rightmost lowest wins ties, like vanilla
        if not c.stone and (low is None or c.rank <= low.rank):
            low = c
    if card is low and not card.debuff:
        ctx.add_mult(2 * card.base_chips(), j)


reg("Raised Fist", "Common", 5, "Adds double the rank of lowest ranked card held in hand to Mult", on_held=_raised_fist,
    notes="Ace counts 11, faces 10. Ties -> rightmost card. Retriggered by Mime/red seal on that card.")
reg("Chaos the Clown", "Common", 4, "1 free Reroll per shop", blueprint_compat=False, notes="Economy only.")
reg("Fibonacci", "Uncommon", 8, "Each played Ace, 2, 3, 5, or 8 gives +8 Mult when scored",
    on_scored=lambda j, ctx, card: ctx.add_mult(8, j) if _rank_ok(card, (14, 2, 3, 5, 8)) else None)
reg("Steel Joker", "Uncommon", 7, "Gives X0.2 Mult for each Steel Card in your full deck",
    main=lambda j, ctx: ctx.x_mult(1 + 0.2 * sum(c.enhancement == "steel" for c in ctx.state.get_full_deck()), j))
reg("Scary Face", "Common", 4, "Played face cards give +30 Chips when scored",
    on_scored=lambda j, ctx, card: ctx.add_chips(30, j) if ctx.is_face(card) else None)
reg("Abstract Joker", "Common", 4, "+3 Mult for each Joker card",
    main=lambda j, ctx: ctx.add_mult(3 * len(ctx.jokers), j), notes="Counts all owned jokers incl. itself.")
reg("Delayed Gratification", "Common", 4, "Earn $2 per discard if no discards are used by end of the round",
    blueprint_compat=False, notes="Economy only.")
reg("Hack", "Uncommon", 6, "Retrigger each played 2, 3, 4, or 5",
    retrigger=lambda j, ctx, card, area: 1 if area == "play" and _rank_ok(card, (2, 3, 4, 5)) else 0)
reg("Pareidolia", "Uncommon", 5, "All cards are considered face cards", blueprint_compat=False,
    notes="Passive, handled in engine.")
reg("Gros Michel", "Common", 5, "+15 Mult; 1 in 6 chance this is destroyed at end of round",
    main=lambda j, ctx: ctx.add_mult(15, j))
reg("Even Steven", "Common", 4, "Played cards with even rank give +4 Mult when scored (10, 8, 6, 4, 2)",
    on_scored=lambda j, ctx, card: ctx.add_mult(4, j) if _rank_ok(card, (2, 4, 6, 8, 10)) else None)
reg("Odd Todd", "Common", 4, "Played cards with odd rank give +31 Chips when scored (A, 9, 7, 5, 3)",
    on_scored=lambda j, ctx, card: ctx.add_chips(31, j) if _rank_ok(card, (14, 3, 5, 7, 9)) else None)


def _scholar(j, ctx, card):
    if _rank_ok(card, (14,)):
        ctx.add_chips(20, j)
        ctx.add_mult(4, j)


reg("Scholar", "Common", 4, "Played Aces give +20 Chips and +4 Mult when scored", on_scored=_scholar)
reg("Business Card", "Common", 4, "Played face cards have a 1 in 2 chance to give $2 when scored",
    on_scored=lambda j, ctx, card: ctx.add_money(2 * ctx.chance(1, 2), j) if ctx.is_face(card) else None,
    notes="Money as expected value.")
reg("Supernova", "Common", 5, "Adds the number of times poker hand has been played this run to Mult",
    main=lambda j, ctx: ctx.add_mult(ctx.state.played_counts.get(ctx.hand_type, 0) + 1, j),
    notes="Includes the current hand.")


def _bus_before(j, ctx):
    if ctx.copying:
        return
    if any(not c.debuff and ctx.is_face(c) for c in ctx.scoring):
        j.vars["mult"] = 0
    else:
        j.vars["mult"] += 1


reg("Ride the Bus", "Common", 6, "This Joker gains +1 Mult per consecutive hand played without a scoring face card",
    defaults={"mult": 0}, before=_bus_before, main=lambda j, ctx: ctx.add_mult(j.vars["mult"], j),
    notes="vars.mult = value before this hand; resets on a scoring (non-debuffed) face card.")


def _space(j, ctx):
    ctx.extra["level_up"] = ctx.extra.get("level_up", 0) + ctx.chance(1, 4)


reg("Space Joker", "Uncommon", 5, "1 in 4 chance to upgrade level of played poker hand", before=_space,
    notes="Expected value: adds 0.25 (x Oops) of a level to this hand.")
reg("Egg", "Common", 4, "Gains $3 of sell value at end of round", blueprint_compat=False, notes="Economy only.")
reg("Burglar", "Uncommon", 6, "When Blind is selected, gain +3 Hands and lose all discards", notes="No direct scoring.")


def _blackboard(j, ctx):
    if all(c.is_suit("S", ctx.flags, flush=True) or c.is_suit("C", ctx.flags, flush=True) for c in ctx.held):
        ctx.x_mult(3, j)


reg("Blackboard", "Uncommon", 6, "X3 Mult if all cards held in hand are Spades or Clubs", main=_blackboard,
    notes="Empty hand counts as all black; Stone cards held break it.")


def _runner_before(j, ctx):
    if not ctx.copying and "Straight" in ctx.contains:
        j.vars["chips"] += 15


reg("Runner", "Common", 5, "Gains +15 Chips if played hand contains a Straight", defaults={"chips": 0},
    before=_runner_before, main=lambda j, ctx: ctx.add_chips(j.vars["chips"], j))
reg("Ice Cream", "Common", 5, "+100 Chips; -5 Chips for every hand played", defaults={"chips": 100},
    main=lambda j, ctx: ctx.add_chips(j.vars["chips"], j), notes="vars.chips = current value; decays after the hand.")
reg("DNA", "Rare", 8, "If first hand of round has only 1 card, add a permanent copy to deck and draw it to hand",
    notes="No direct scoring.")
reg("Splash", "Common", 3, "Every played card counts in scoring", blueprint_compat=False,
    notes="Passive, handled in engine.")
reg("Blue Joker", "Common", 5, "+2 Chips for each remaining card in deck",
    main=lambda j, ctx: ctx.add_chips(2 * len(ctx.state.get_deck()), j))
reg("Sixth Sense", "Uncommon", 6, "If first hand of round is a single 6, destroy it and create a Spectral card",
    blueprint_compat=False, notes="No direct scoring.")
reg("Constellation", "Uncommon", 6, "Gains X0.1 Mult every time a Planet card is used", defaults={"xmult": 1},
    main=lambda j, ctx: ctx.x_mult(j.vars["xmult"], j))


def _hiker(j, ctx, card):
    card.bonus += 5     # after this trigger's chips: a retrigger already sees the +5


reg("Hiker", "Uncommon", 5, "Every played card permanently gains +5 Chips when scored",
    on_scored=_hiker,
    notes="Bonus lands after the card's chips, so retriggers of the same card gain it immediately.")
reg("Faceless Joker", "Common", 4, "Earn $5 if 3 or more face cards are discarded at the same time",
    notes="Economy only.")


def _green_before(j, ctx):
    if not ctx.copying:
        j.vars["mult"] += 1


reg("Green Joker", "Common", 4, "+1 Mult per hand played, -1 Mult per discard", defaults={"mult": 0},
    before=_green_before, main=lambda j, ctx: ctx.add_mult(j.vars["mult"], j))
reg("Superposition", "Common", 4, "Create a Tarot card if poker hand contains an Ace and a Straight",
    notes="No direct scoring.")
reg("To Do List", "Common", 4, "Earn $4 if poker hand is a [hand], poker hand changes at end of round",
    defaults={"hand": "High Card"},
    before=lambda j, ctx: ctx.add_money(4, j) if ctx.hand_type == j.vars["hand"] else None,
    notes="Set vars.hand to the current target.")
reg("Cavendish", "Common", 4, "X3 Mult; 1 in 1000 chance this card is destroyed at end of round",
    main=lambda j, ctx: ctx.x_mult(3, j))
reg("Card Sharp", "Uncommon", 6, "X3 Mult if played poker hand has already been played this round",
    main=lambda j, ctx: ctx.x_mult(3, j) if ctx.hand_type in ctx.state.round_hand_types else None)
reg("Red Card", "Common", 5, "Gains +3 Mult when any Booster Pack is skipped", defaults={"mult": 0},
    main=lambda j, ctx: ctx.add_mult(j.vars["mult"], j))
reg("Madness", "Uncommon", 7, "When Small Blind or Big Blind is selected, gain X0.5 Mult and destroy a random Joker",
    defaults={"xmult": 1}, main=lambda j, ctx: ctx.x_mult(j.vars["xmult"], j))


def _square_before(j, ctx):
    if not ctx.copying and len(ctx.played) == 4:
        j.vars["chips"] += 4


reg("Square Joker", "Common", 4, "Gains +4 Chips if played hand has exactly 4 cards", defaults={"chips": 0},
    before=_square_before, main=lambda j, ctx: ctx.add_chips(j.vars["chips"], j))
reg("Séance", "Uncommon", 6, "If poker hand is a Straight Flush, create a random Spectral card",
    notes="No direct scoring.")
reg("Riff-Raff", "Common", 6, "When Blind is selected, create 2 Common Jokers", notes="No direct scoring.")


def _vampire_before(j, ctx):
    if ctx.copying:
        return
    for c in ctx.scoring:
        if c.enhancement and not c.debuff:
            j.vars["xmult"] += 0.1
            c.enhancement = None   # stripped before cards score (vanilla set_ability is immediate)


reg("Vampire", "Uncommon", 7,
    "Gains X0.1 Mult per scoring Enhanced card played, removes card Enhancement", defaults={"xmult": 1},
    before=_vampire_before, main=lambda j, ctx: ctx.x_mult(j.vars["xmult"], j),
    notes="Enhancements are removed before scoring: Glass/Mult/Stone etc. don't score this hand.")
reg("Shortcut", "Uncommon", 7, "Allows Straights to be made with gaps of 1 rank", blueprint_compat=False,
    notes="Passive, handled in engine.")
reg("Hologram", "Uncommon", 7, "Gains X0.25 Mult every time a playing card is added to your deck",
    defaults={"xmult": 1}, main=lambda j, ctx: ctx.x_mult(j.vars["xmult"], j))
reg("Vagabond", "Rare", 8, "Create a Tarot card if hand is played with $4 or less", notes="No direct scoring.")


def _baron(j, ctx, card):
    if not card.stone and card.rank == 13:
        ctx.x_mult(1.5, j)


reg("Baron", "Rare", 8, "Each King held in hand gives X1.5 Mult", on_held=_baron)
reg("Cloud 9", "Uncommon", 7, "Earn $1 for each 9 in your full deck at end of round", blueprint_compat=False,
    notes="Economy only.")
reg("Rocket", "Uncommon", 6, "Earn $1 at end of round. Payout increases by $2 when Boss Blind is defeated",
    defaults={"dollars": 1}, blueprint_compat=False, notes="Economy only.")


def _obelisk_before(j, ctx):
    if ctx.copying:
        return
    counts = ctx.state.played_counts
    mine = counts.get(ctx.hand_type, 0) + 1   # vanilla increments before jokers run
    if any(h != ctx.hand_type and n >= mine for h, n in counts.items()):
        j.vars["xmult"] += 0.2
    else:
        j.vars["xmult"] = 1


reg("Obelisk", "Rare", 8,
    "Gains X0.2 Mult per consecutive hand played without playing your most played poker hand",
    defaults={"xmult": 1}, before=_obelisk_before, main=lambda j, ctx: ctx.x_mult(j.vars["xmult"], j),
    notes="Resets if this hand becomes strictly the most played (ties keep scaling).")
