"""Hand detection and scoring, following the vanilla evaluate_play order:

1. hand type + scoring cards (Splash / Stone cards added), boss rules
2. joker `before` hooks
3. each scoring card L->R, per trigger: chips, enh. mult, glass, edition, jokers on_scored
4. each held card L->R, per trigger: steel, jokers on_held
5. each joker L->R: foil/holo, main, all on_other_joker, polychrome
6. Observatory planets, Plasma deck, floor
"""
from dataclasses import dataclass, field, replace

from . import jokers as J
from .data import HAND_ORDER, PLANETS, SUIT_DEBUFF_BOSSES, hand_base

MUTATES_CARDS = {"Hiker", "Vampire", "Midas Mask"}  # hooks that change scored cards in place
PASSIVE = {"Four Fingers", "Shortcut", "Smeared Joker", "Pareidolia", "Splash", "Oops! All 6s", "Chicot"}


def stochastic(state):
    names = {j.name for j in state.jokers if not j.debuff}
    return bool(names & {"Misprint", "Bloodstone", "Space Joker"}) or bool(
        names & {"Bull", "Bootstraps"} and names & {"Business Card", "Reserved Parking"}) or any(
            c.enhancement == "lucky" and not c.debuff for c in state.hand)


def most_played(state):
    """The Ox's hand. Vanilla fixes it in end_round after each boss, so live counts can disagree: run
    20261101-212446 Z241 played Pair (8x, the Ox hand) for $43 -> $0 because High Card had since reached 9x.
    Without telemetry: most played this run, ties to the stronger hand."""
    if state.ox_hand:
        return state.ox_hand
    return max(HAND_ORDER, key=lambda h: state.played_counts.get(h, 0))


def active_flags(state):
    return frozenset(j.name for j in state.jokers if not j.debuff and j.name in PASSIVE)


# ---------------------------------------------------------------- hand detection
def _straight(cards, flags):
    """Port of vanilla get_straight. Returns the straight's cards or []."""
    need = 4 if "Four Fingers" in flags else 5
    if not need <= len(cards) <= 5:
        return []
    ids = {}
    for c in cards:
        if not c.stone:
            ids.setdefault(c.rank, []).append(c)
    t, length, straight, skipped = [], 0, False, False
    can_skip = "Shortcut" in flags
    for j in range(1, 15):
        r = 14 if j == 1 else j
        if r in ids:
            length += 1
            skipped = False
            t += ids[r]
        elif can_skip and not skipped and j != 14:
            skipped = True
        else:
            length, skipped = 0, False
            if straight:
                break
            t = []
        if length >= need:
            straight = True
    return t if straight else []


def _flush(cards, flags):
    need = 4 if "Four Fingers" in flags else 5
    if not need <= len(cards) <= 5:
        return []
    for s in "SHCD":
        t = [c for c in cards if c.is_suit(s, flags, flush=True)]
        if len(t) >= need:
            return t
    return []


def detect(cards, flags=frozenset()):
    """-> (hand_type, scoring_cards, contained_hand_types). `cards` in play order."""
    if not cards:
        return None, [], set()
    groups = {}
    for c in cards:
        if not c.stone:
            groups.setdefault(c.rank, []).append(c)
    sizes = sorted((len(g) for g in groups.values()), reverse=True) + [0, 0]
    pairs = sum(1 for s in sizes if s >= 2)
    flush, straight = _flush(cards, flags), _straight(cards, flags)
    full = sizes[0] >= 3 and pairs >= 2
    contains = {"High Card"}
    for name, ok in [("Pair", pairs >= 1), ("Two Pair", pairs >= 2), ("Three of a Kind", sizes[0] >= 3),
                     ("Straight", straight), ("Flush", flush), ("Full House", full),
                     ("Four of a Kind", sizes[0] >= 4), ("Straight Flush", flush and straight),
                     ("Five of a Kind", sizes[0] >= 5), ("Flush House", full and flush),
                     ("Flush Five", sizes[0] >= 5 and flush)]:
        if ok:
            contains.add(name)
    hand_type = next(h for h in HAND_ORDER if h in contains)
    grouped = [c for c in cards if len(groups.get(c.rank, [])) >= 2 and not c.stone]
    if hand_type in ("Flush Five", "Flush House"):
        scoring = list(cards)
    elif hand_type == "Straight Flush":
        scoring = flush + [c for c in straight if c not in flush]
    elif hand_type == "Four of a Kind":
        scoring = [c for c in cards if not c.stone and len(groups[c.rank]) >= 4]
    elif hand_type == "Flush":
        scoring = flush
    elif hand_type == "Straight":
        scoring = straight
    elif hand_type == "High Card":
        ranked = [c for c in cards if not c.stone]
        scoring = [max(ranked, key=lambda c: c.rank)] if ranked else []
    else:
        scoring = grouped
    ids = {id(c) for c in scoring}
    extra = [c for c in cards if id(c) not in ids and ("Splash" in flags or c.stone)]
    order = {id(c): i for i, c in enumerate(cards)}
    scoring = sorted(scoring + extra, key=lambda c: order[id(c)])
    return hand_type, scoring, contains


# ---------------------------------------------------------------- scoring
@dataclass
class Ctx:
    state: object
    played: list
    scoring: list
    held: list
    hand_type: str
    contains: set
    flags: frozenset
    chips: float = 0
    mult: float = 0
    money: float = 0            # money gained during this hand
    prob: int = 1               # Oops! All 6s multiplier
    copying: bool = False
    joker: object = None        # joker currently acting (the real instance, not the copied one)
    jokers: list = field(default_factory=list)
    log: list = field(default_factory=list)
    verbose: bool = False
    final_hand: bool = False
    # mutable per-hand extras jokers may use (e.g. cards destroyed, levels gained)
    extra: dict = field(default_factory=dict)
    hooks: dict = field(default_factory=dict)  # hook -> resolved _calls list
    rng: object = None

    def _note(self, what, src):
        if self.verbose:
            name = getattr(src, "name", src) if src is not None else ""
            self.log.append(f"{what:>10} {str(name):<22} -> {self.chips:g} x {self.mult:g}")

    def add_chips(self, n, src=None):
        if n:
            self.chips += n
            self._note(f"+{n:g} chips", src)

    def add_mult(self, n, src=None):
        if n:
            self.mult += n
            self._note(f"+{n:g} mult", src)

    def x_mult(self, x, src=None):
        if x != 1:
            self.mult *= x
            self._note(f"x{x:g} mult", src)

    def add_money(self, n, src=None):
        self.money += n

    def chance(self, num, den):
        p = min(1.0, num * self.prob / den)
        return int(self.rng.random() < p) if self.rng is not None else p

    def is_face(self, card):
        return card.is_face(self.flags)

    def is_suit(self, card, suit):
        return card.is_suit(suit, self.flags)

    def level(self, hand_type=None):
        """Current level; `extra["level_up"]` holds this hand's change (Space Joker, The Arm)."""
        hand_type = hand_type or self.hand_type
        bonus = self.extra.get("level_up", 0) if hand_type == self.hand_type else 0
        return max(self.state.level(hand_type) + bonus, 1)


def _resolve(jokers, i, seen=()):
    """Joker index whose behaviour position i uses (Blueprint -> right, Brainstorm -> leftmost)."""
    name = jokers[i].name
    if name not in ("Blueprint", "Brainstorm"):
        return i
    t = i + 1 if name == "Blueprint" else 0
    if t >= len(jokers) or t == i or t in seen or jokers[t].debuff:
        return None
    r = _resolve(jokers, t, seen + (i,))
    if r is None:
        return None
    spec = J.get(jokers[r].name)
    return r if spec and spec.blueprint_compat else None


def _calls(ctx, hook):
    """[(actor, target_joker, fn, copying)] for every non-debuffed joker with this hook, L->R.
    Resolved once per hand: hooks never change joker names, order or debuffs mid-hand."""
    out = ctx.hooks.get(hook)
    if out is None:
        js, out = ctx.jokers, []
        for i, actor in enumerate(js):
            if actor.debuff:
                continue
            r = _resolve(js, i)
            if r is None:
                continue
            spec = J.get(js[r].name)
            fn = spec and getattr(spec, hook)
            if fn:
                out.append((actor, js[r], fn, r != i))
        ctx.hooks[hook] = out
    return out


def _run(ctx, hook, *args):
    total = 0
    for actor, target, fn, copying in _calls(ctx, hook):
        ctx.copying, ctx.joker = copying, actor
        r = fn(target, ctx, *args)
        if hook == "retrigger":
            total += r or 0
    ctx.copying, ctx.joker = False, None
    return total


def apply_boss_debuffs(state):
    """Return a copy of state.hand with boss-blind suit/face debuffs applied."""
    if state.boss_disabled or any(j.name == "Chicot" and not j.debuff for j in state.jokers):
        return state.hand
    flags = active_flags(state)
    suit = SUIT_DEBUFF_BOSSES.get(state.blind)
    out = []
    for c in state.hand:
        d = c.debuff or (suit and c.is_suit(suit, flags)) or (state.blind == "The Plant" and c.is_face(flags)) \
            or state.blind == "Verdant Leaf" or state.blind == "The Pillar" and c.played_this_ante
        out.append(replace(c, debuff=bool(d)) if d != c.debuff else c)
    return out


@dataclass
class Result:
    score: int
    chips: float
    mult: float
    hand_type: str | None
    scoring: list
    money: float = 0
    log: list = field(default_factory=list)
    reason: str = ""
    scoring_idx: list = field(default_factory=list)  # indices into state.hand
    joker_vars: list = field(default_factory=list)
    level_delta: float = 0


def score(state, played_idx, verbose=False, rng=None):
    """Score playing state.hand[i] for i in played_idx (in hand order). Does not mutate state."""
    played_idx = tuple(played_idx)
    if len(played_idx) > 5 or len(set(played_idx)) != len(played_idx) or any(
            type(i) is not int or not 0 <= i < len(state.hand) for i in played_idx):
        raise ValueError("Play 1–5 distinct, valid card indices")
    hand = apply_boss_debuffs(state)
    if any(j.name in MUTATES_CARDS for j in state.jokers):  # copy only when a joker modifies cards
        hand = [replace(c) for c in hand]
    played_idx = sorted(played_idx)
    played = [hand[i] for i in played_idx]
    chosen = set(played_idx)
    held = [c for i, c in enumerate(hand) if i not in chosen]
    flags = active_flags(state)
    hand_type, scoring, contains = detect(played, flags)
    pos = {id(c): i for i, c in zip(played_idx, played)}
    scoring_idx = [pos[id(c)] for c in scoring]
    boss = None if (state.boss_disabled or "Chicot" in flags) else state.blind
    if hand_type is None:
        return Result(0, 0, 0, None, [], reason="no cards")
    if boss == "The Psychic" and len(played) < 5:
        return Result(0, 0, 0, hand_type, scoring, reason="The Psychic: must play 5 cards")
    if boss == "The Eye" and hand_type in state.round_hand_types:
        return Result(0, 0, 0, hand_type, scoring, reason="The Eye: hand type already played")
    if boss == "The Mouth" and state.round_hand_types and hand_type not in state.round_hand_types:
        return Result(0, 0, 0, hand_type, scoring, reason="The Mouth: only one hand type")
    if boss == "The Ox" and hand_type == most_played(state):
        state = replace(state, money=0)  # set before any joker scores (Bull, Bootstraps see $0)

    jokers = [replace(j, vars={**(J.get(j.name).defaults if J.get(j.name) else {}), **j.vars})
              for j in state.jokers]
    if boss == "The Hook" and held:
        # the Hook's discard runs the discard context before scoring (vanilla): Green Joker loses 1 first
        for j in jokers:
            if j.name == "Green Joker" and not j.debuff:
                j.vars["mult"] = max(0, j.vars["mult"] - 1)
    ctx = Ctx(state, played, scoring, held, hand_type, contains, flags, jokers=jokers, verbose=verbose,
              prob=2 ** sum(1 for j in state.jokers if j.name == "Oops! All 6s" and not j.debuff),
              final_hand=state.hands_left <= 1, rng=rng)
    if boss == "The Arm":
        ctx.extra["level_up"] = -1 if state.level(hand_type) > 1 else 0

    _run(ctx, "before")
    ctx.chips, ctx.mult = hand_base(hand_type, ctx.level())
    if boss == "The Flint":
        ctx.chips, ctx.mult = int(ctx.chips / 2 + 0.5), max(int(ctx.mult / 2 + 0.5), 1)
    ctx._note(hand_type, f"lvl {ctx.level()}")

    for card in scoring:
        if card.debuff:
            continue
        reps = 1 + (card.seal == "red") + _run(ctx, "retrigger", card, "play")
        for _ in range(reps):
            ctx.extra["lucky_triggered"] = False
            ctx.add_chips(card.chip_bonus(), card)
            if card.enhancement == "mult":
                ctx.add_mult(4, card)
            elif card.enhancement == "lucky":
                lucky_mult, lucky_money = ctx.chance(1, 5), ctx.chance(1, 15)
                ctx.extra["lucky_triggered"] = bool(lucky_mult or lucky_money)
                ctx.add_mult(20 * lucky_mult, card)
                ctx.add_money(20 * lucky_money)
            elif card.enhancement == "glass":
                ctx.x_mult(2, card)
            if card.seal == "gold":
                ctx.add_money(3)
            _card_edition(ctx, card)
            _run(ctx, "on_scored", card)

    for card in held:
        if card.debuff:
            continue
        reps = 1 + (card.seal == "red") + _run(ctx, "retrigger", card, "held")
        for _ in range(reps):
            if card.enhancement == "steel":
                ctx.x_mult(1.5, card)
            _run(ctx, "on_held", card)

    for i, actor in enumerate(jokers):
        if not actor.debuff:
            if actor.edition == "foil":
                ctx.add_chips(50, actor)
            elif actor.edition == "holo":
                ctx.add_mult(10, actor)
            r = _resolve(jokers, i)
            spec = J.get(jokers[r].name) if r is not None else None
            if spec and spec.main:
                ctx.copying, ctx.joker = r != i, actor
                spec.main(jokers[r], ctx)
        for actor2, target2, fn, copying in _calls(ctx, "on_other_joker"):
            ctx.copying, ctx.joker = copying, actor2
            fn(target2, ctx, actor)
        ctx.copying, ctx.joker = False, None
        if actor.edition == "poly" and not actor.debuff:
            ctx.x_mult(1.5, actor)

    if "Observatory" in state.vouchers:
        for c in state.consumables:
            if PLANETS.get(c) == hand_type:
                ctx.x_mult(1.5, c)
    if (state.deck_type or "").startswith("Plasma"):
        ctx.chips = ctx.mult = int((ctx.chips + ctx.mult) / 2)
    return Result(int(ctx.chips * ctx.mult), ctx.chips, ctx.mult, hand_type, scoring, ctx.money, ctx.log,
                  scoring_idx=scoring_idx, joker_vars=[dict(j.vars) for j in jokers],
                  level_delta=ctx.extra.get("level_up", 0))


def _card_edition(ctx, card):
    if card.edition == "foil":
        ctx.add_chips(50, card)
    elif card.edition == "holo":
        ctx.add_mult(10, card)
    elif card.edition == "poly":
        ctx.x_mult(1.5, card)
