"""Booster-pack and consumable decisions.

`choose_from_pack` picks the card to take from an opened pack (`index` into `cards`,
`None` = skip). `use_consumable` decides whether to use a card already sitting in a
consumable slot now (`index` 0) or keep it (`None`). `targets` are indices into
`state.hand` (the drawn cards shown in Arcana/Spectral packs) to select before "Use".

Values are German user-facing reasons; a value <= 0 means skip/hold.
"""
import random
from collections import Counter
from dataclasses import dataclass, replace

from .data import PLANETS
from .models import Card, Joker
from .shop import TIER, TIER_POINTS, _avg_best, _sim_state, consumable_slots
from .strategy import focus_hand

_SAMPLES = 20
_SEED = 0

# Suit-converting tarots and suit-bound jokers (target suit for flush builds).
SUIT_TAROT = {"The Star": "D", "The Moon": "C", "The Sun": "H", "The World": "S"}
SUIT_JOKER = {"Bloodstone": "H", "Arrowhead": "S", "Onyx Agate": "C", "Rough Gem": "D"}
SUIT_MULT_JOKER = {"Greedy Joker": "D", "Lusty Joker": "H", "Wrathful Joker": "S", "Gluttonous Joker": "C"}
SET_HANDS = ("Pair", "Two Pair", "Three of a Kind", "Four of a Kind", "Five of a Kind",
             "Full House", "Flush House", "Flush Five")
ENHANCE = {  # name -> (label, max targets, base value)
    "The Empress": ("Mult (+4)", 2, 4),
    "The Hierophant": ("Bonus (+30 Chips)", 2, 3),
    "The Magician": ("Lucky", 2, 1),
    "The Lovers": ("Wild", 1, 3),
    "The Chariot": ("Steel", 1, 4),
    "Justice": ("Glass", 1, 3),
    "The Devil": ("Gold", 1, 2),
    "The Tower": ("Stone", 1, 1),
}


@dataclass
class PackChoice:
    index: int | None
    targets: list[int]
    why: str


def _names(state):
    return {j.name for j in state.jokers if not j.debuff}


def _free_joker_slots(state):
    return state.joker_slots - len(state.jokers)


def _free_consumable_slots(state):
    return consumable_slots(state) - len(state.consumables)


def _sample_hands(state, samples=_SAMPLES):
    rng = random.Random(_SEED)
    deck = state.get_full_deck()
    return [rng.sample(deck, min(state.hand_size, len(deck))) for _ in range(samples)]


def _focus_rank(state):
    ranks = Counter(c.rank for c in state.hand if not c.stone)
    if not ranks:
        return None
    if focus_hand(state) == "High Card":
        return max(ranks)
    return ranks.most_common(1)[0][0]


def _dominant_suit(state):
    for name, suit in SUIT_JOKER.items():
        if name in _names(state):
            return suit
    suits = Counter(c.suit for c in state.hand if not c.stone)
    return suits.most_common(1)[0][0] if suits else "H"


# --------------------------------------------------------------------------- planets
def _choose_planet(state, cards):
    focus = focus_hand(state)
    hands = _sample_hands(state)
    base_state = _sim_state(state)
    base = _avg_best(base_state, hands) or 1
    constellation = "Constellation" in _names(state)
    best = None
    for i, c in enumerate(cards):
        name = c["name"]
        if name not in PLANETS:
            continue
        hand = PLANETS[name]
        lv = dict(base_state.hand_levels, **{hand: base_state.level(hand) + 1})
        gain = _avg_best(replace(base_state, hand_levels=lv), hands) / base - 1
        value = min(gain * 10, 5) + (3 if hand == focus else 0) + (2 if constellation else 0)
        why = f"{hand} +1 level ({gain:+.0%} score/hand)"
        if hand == focus:
            why += f", focus hand {focus}"
        elif constellation:
            why += ", grows Constellation"
        if best is None or value > best[0]:
            best = (value, i, [], why)
    if best is None:
        return PackChoice(None, [], "No planet recognised.")
    return PackChoice(best[1], [], best[3])


# ----------------------------------------------------------------------------- tarot
def _enhance_targets(state, n, name):
    hand = state.hand
    cand = [i for i, c in enumerate(hand) if c.enhancement is None and not c.stone]
    if name == "The Chariot":
        cand.sort(key=lambda i: (hand[i].rank != 13, -hand[i].rank))  # Kings first (Baron)
    elif name == "The Tower":
        cand.sort(key=lambda i: hand[i].chip_bonus())  # junk card to stone
    else:
        rank = _focus_rank(state)
        cand.sort(key=lambda i: (-hand[i].rank) if rank is None else (hand[i].rank != rank, -hand[i].rank))
    return cand[:n]


def _strength_targets(state):
    hand = state.hand
    ranks = Counter(c.rank for c in hand if not c.stone)
    target = _focus_rank(state)
    cand = []
    for i, c in enumerate(hand):
        if c.stone or c.rank == 14:
            continue
        benefit = 3 if c.rank + 1 in ranks else 2 if (target and c.rank + 1 == target) else 0
        if benefit:
            cand.append((benefit, c.rank, i))
    cand.sort(key=lambda t: (-t[0], -t[1]))
    return [i for _, _, i in cand[:2]]


def _death_targets(state):
    """Death turns the LEFT selected card into a copy of the RIGHT one (vanilla: the rightmost highlighted
    card by screen position is the source, whatever the click order) -> [weak, strong] with weak < strong."""
    hand = state.hand
    target = _focus_rank(state)

    def value(c):
        return c.chip_bonus() + 30 * bool(c.enhancement) + 30 * bool(c.seal) + 30 * bool(c.edition), c.rank

    pairs = [(value(hand[s])[0] - value(hand[w])[0], value(hand[s]), w, s)
             for s in range(len(hand)) for w in range(s)
             if (hand[s].enhancement or hand[s].seal or hand[s].edition or hand[s].rank == target)
             and value(hand[s]) > value(hand[w])]
    if not pairs:
        return []  # nothing worth copying sits right of a weaker card
    _, _, weak, strong = max(pairs)
    return [weak, strong]


def _hanged_targets(state):
    hand = state.hand
    cand = [i for i, c in enumerate(hand)
            if not c.stone and c.enhancement is None and c.edition is None and c.seal is None]
    cand.sort(key=lambda i: (hand[i].chip_bonus(), hand[i].rank))
    return cand[:2]


def _money_points(dollars):
    """$ -> the 1-10 pack point scale: $6 = 1 point, at most 5 (+$20 from The Hermit = 3.3: below Chariot/Empress/
    Death, above Justice/Hierophant). ponytail: flat rate; money is worth more early than late, the state has no ante."""
    return min(dollars / 6, 5)


def _tarot_choice(state, name):
    names = _names(state)
    hand = state.hand
    focus = focus_hand(state)
    if name == "The Hermit":
        gain = min(state.money, 20)
        return (_money_points(gain), [], f"+${gain} (double money, max $20)")
    if name == "Temperance":
        gain = min(sum(j.sell_value if j.sell_value is not None else 1 for j in state.jokers), 50)
        return (_money_points(gain), [], f"+${gain} (sell value of the jokers)")
    if name == "The Fool":
        last = state.last_tarot_planet
        if not last or last == "The Fool":  # nothing to copy: USE stays grey in the game
            return (0, [], "The Fool: no last tarot/planet card to copy")
        if _free_consumable_slots(state) < 1:
            return (0, [], "No free consumable slot")
        copied = 4 if last in PLANETS else max(1, min(_tarot_choice(state, last)[0], 6))
        return (copied, [], f"Copies {last}")
    if name == "The High Priestess":
        slots = _free_consumable_slots(state)
        if slots < 1:
            return (0, [], "No free consumable slot")
        return (6 if slots >= 2 else 3, [], "2 random planets")
    if name == "The Emperor":
        slots = _free_consumable_slots(state)
        if slots < 1:
            return (0, [], "No free consumable slot")
        return (5 if slots >= 2 else 2, [], "2 random tarots")
    if name == "The Wheel of Fortune":
        if not any(not j.edition for j in state.jokers if not j.debuff):
            return (0, [], "No joker without an edition for the Wheel")
        return (3, [], "25% chance of Foil/Holo/Polychrome on a joker")
    if name == "Judgement":
        if _free_joker_slots(state) < 1:
            return (0, [], "No free joker slot")
        return (5, [], "Random joker")
    if name in SUIT_TAROT:
        suit = SUIT_TAROT[name]
        if not (focus in ("Flush", "Straight Flush", "Flush Five", "Flush House")
                or any(k in names for k in SUIT_JOKER)):
            return (0, [], f"{name} without a Flush focus – skip")
        if suit != _dominant_suit(state):
            return (0, [], f"Wrong suit: building on {_dominant_suit(state)}, not {suit}")
        targets = [i for i, c in enumerate(hand) if not c.stone and c.suit != suit][:3]
        if not targets:
            return (0, [], "All cards already have this suit")
        return (5, targets, f"Recolour {len(targets)} cards to {suit} (Flush)")
    if name == "The Hanged Man":
        if focus in ("Straight", "Straight Flush"):
            return (0, [], "Straight focus: removing cards is risky")
        targets = _hanged_targets(state)
        if not targets:
            return (0, [], "No weak cards to remove")
        return (2, targets, f"Remove {len(targets)} weak cards")
    if name == "Death":
        targets = _death_targets(state)
        if not targets:
            return (0, [], "No card worth copying")
        return (4, targets, "turn a weak card into a strong one")
    if name == "Strength":
        targets = _strength_targets(state)
        if not targets:
            return (0, [], "No card benefits from rank +1")
        return (3, targets, f"{len(targets)} card(s) rank +1")
    if name in ENHANCE:
        label, n, value = ENHANCE[name]
        targets = _enhance_targets(state, n, name)
        if not targets:
            return (0, [], f"No plain card for {label}")
        return (value, targets, f"{len(targets)} card(s) → {label}")
    return (0, [], f"Tarot {name} unknown")


# --------------------------------------------------------------------------- spectral
def _seal_target(state, key):
    cand = [i for i, c in enumerate(state.hand) if c.seal is None]
    if not cand:
        return None
    return max(cand, key=lambda i: key(state.hand[i]))


def _single_strong_joker(state):
    js = [j for j in state.jokers if not j.debuff]
    return len(js) == 1 and TIER.get(js[0].name, "C") in ("S", "A", "B")


def _spectral_choice(state, name):
    hand = state.hand
    focus = focus_hand(state)
    if name == "Black Hole":
        return (10, [], "All hands +1 level – always take")
    if name == "Cryptid":
        i = _seal_target(state, lambda c: (1 if (c.enhancement or c.edition) else 0, c.chip_bonus()))
        if i is None:
            return (0, [], "No card to copy")
        return (5, [i], "copy a strong card twice")
    if name == "Deja Vu":
        i = _seal_target(state, lambda c: (c.enhancement in ("glass", "steel"), c.chip_bonus()))
        if i is None:
            return (0, [], "No card for a Red Seal")
        return (4, [i], "Red Seal (Retrigger)")
    if name == "Talisman":
        i = _seal_target(state, lambda c: (c.rank == _focus_rank(state), c.chip_bonus()))
        if i is None:
            return (0, [], "No card for a Gold Seal")
        return (4, [i], "Gold Seal ($3 when scored)")
    if name == "Trance":
        if _free_consumable_slots(state) < 1:
            return (0, [], "No consumable slot for a Blue Seal")
        i = _seal_target(state, lambda c: c.chip_bonus())
        if i is None:
            return (0, [], "No card for a Blue Seal")
        return (4, [i], "Blue Seal (planet at round end)")
    if name == "Medium":
        i = _seal_target(state, lambda c: -c.chip_bonus())
        if i is None:
            return (0, [], "No card for a Purple Seal")
        return (3, [i], "Purple Seal (tarot on discard)")
    if name == "Aura":
        cand = [i for i, c in enumerate(hand) if c.edition is None]
        if not cand:
            return (0, [], "All cards already have an edition")
        i = max(cand, key=lambda i: (1 if (hand[i].enhancement or hand[i].seal) else 0, hand[i].chip_bonus()))
        return (3, [i], "Edition on a card (Foil/Holo/Poly)")
    if name == "Familiar":
        return (3, [], "3 enhanced face cards into the deck (1 random hand card destroyed)")
    if name == "Grim":
        return (3, [], "2 enhanced aces into the deck (1 random hand card destroyed)")
    if name == "Incantation":
        return (3, [], "4 enhanced number cards into the deck (1 random hand card destroyed)")
    if name == "Immolate":
        if state.money < 20 and len(hand) >= 5:
            return (5, [], "+$20 (5 random hand cards destroyed)")
        return (0, [], "Immolate destroys good cards – only early when money is needed")
    if name == "Sigil":
        if focus in ("Flush", "Straight Flush", "Flush Five", "Flush House"):
            return (4, [], "All hand cards to one suit (Flush)")
        return (0, [], "Sigil without a Flush focus")
    if name == "Ouija":
        if focus in ("Three of a Kind", "Four of a Kind", "Five of a Kind", "Full House",
                     "Flush Five", "Flush House"):
            return (3, [], "All hand cards to one rank (set) – but -1 hand size")
        return (0, [], "Ouija without a set focus")
    if name == "Wraith":
        if state.money <= 5 and _free_joker_slots(state) >= 1:
            return (5, [], "Random rare joker, money to $0")
        return (0, [], "Wraith: losing the money costs too much")
    if name == "Ankh":
        if _single_strong_joker(state):
            return (4, [], "Duplicate the single strong joker")
        return (0, [], "Ankh: no clear single joker to copy")
    if name == "Hex":
        if _single_strong_joker(state):
            return (3, [], "Polychrome on the only joker")
        return (0, [], "Hex: no clear single joker")
    if name == "Ectoplasm":
        return (0, [], "Ectoplasm: -1 hand size is rarely worth it")
    if name == "The Soul":
        if _free_joker_slots(state) >= 1:
            return (9, [], "Legendary joker")
        return (0, [], "No free joker slot")
    return (0, [], f"Spectral {name} unknown")


# ------------------------------------------------------------------------------ joker
def _choose_joker(state, cards):
    free = _free_joker_slots(state)
    hands = _sample_hands(state)
    base_state = _sim_state(state)
    base = _avg_best(base_state, hands) or 1
    best = None
    for i, c in enumerate(cards):
        name, ed = c["name"], c.get("edition")
        if name not in TIER:
            continue
        tier = TIER[name]
        gain = _avg_best(replace(base_state, jokers=base_state.jokers + [Joker(name, edition=ed)]),
                         hands) / base - 1
        value = TIER_POINTS[tier] + min(gain * 4, 4) + {"poly": 2, "holo": 1, "foil": 0.5, "negative": 2}.get(ed, 0)
        if value < 3 or (free <= 0 and ed != "negative"):
            continue
        why = f"Tier {tier}, now {gain:+.0%} score/hand" + (f", {ed}" if ed else "")
        if best is None or value > best[0]:
            best = (value, i, why)
    if best is None:
        return PackChoice(None, [], "No free joker slot" if free <= 0 else "No joker is worth it")
    return PackChoice(best[1], [], best[2])


# ---------------------------------------------------------------------------- standard
def _fits_focus(state, card, focus):
    if focus == "Flush":
        return card.suit == _dominant_suit(state)
    if focus == "Straight Flush":
        return card.suit == _dominant_suit(state) and 5 <= card.rank <= 10
    if focus in SET_HANDS:
        return card.rank == _focus_rank(state)
    if focus == "Straight":
        return 5 <= card.rank <= 10
    if focus == "High Card":
        return card.rank >= 10
    return True


def _standard_value(state, c, focus):
    card = Card.parse(c)
    label = c.get("name") or str(card)
    if card.seal:
        pts = {"red": 5, "gold": 4, "blue": 5, "purple": 4}.get(card.seal, 3)
        if card.seal == "blue" and _free_consumable_slots(state) < 1:
            pts -= 3  # blue seal needs a free consumable slot to pay off
        return (pts, f"{label}: {card.seal} seal")
    if card.edition:
        if not _fits_focus(state, card, focus):
            return (0, f"{label}: edition doesn't fit focus hand {focus}")
        return (3, f"{label}: {card.edition} edition, fits focus hand {focus}")
    if card.enhancement:
        if not _fits_focus(state, card, focus):
            return (0, f"{label}: enhancement doesn't fit focus hand {focus}")
        pts = {"steel": 3, "glass": 3, "mult": 3, "bonus": 2, "gold": 2, "lucky": 2,
               "wild": 2, "stone": 1}.get(card.enhancement, 2)
        if card.enhancement == "steel" and _names(state) & {"Baron", "Mime"}:
            pts += 2
        return (pts, f"{label}: {card.enhancement} enhancement, fits {focus}")
    return (0, f"{label}: a plain card dilutes the deck")


def _choose_standard(state, cards):
    focus = focus_hand(state)
    best = None
    for i, c in enumerate(cards):
        value, why = _standard_value(state, c, focus)
        if value > 0 and (best is None or value > best[0]):
            best = (value, i, why)
    if best is None:
        return PackChoice(None, [], "No card fits the focus hand (don't dilute the deck)")
    return PackChoice(best[1], [], best[2])


# --------------------------------------------------------------------------- dispatch
def _best_of(state, cards, chooser):
    best = None
    for i, c in enumerate(cards):
        value, targets, why = chooser(state, c["name"])
        if value > 0 and (best is None or value > best[0]):
            best = (value, i, targets, why)
    if best is None:
        return PackChoice(None, [], "No card fits this hand")
    return PackChoice(best[1], best[2], best[3])


def choose_from_pack(state, pack_name: str, cards: list[dict], picks_left: int = 1) -> PackChoice:
    if not cards:
        return PackChoice(None, [], "The pack is empty.")
    kinds = Counter(c["kind"] for c in cards)
    if len(kinds) > 1:  # The Soul in an Arcana pack, Black Hole in a Celestial pack: rate each card by its kind
        for i, c in enumerate(cards):
            if c["kind"] == "spectral":
                value, targets, why = _spectral_choice(state, c["name"])
                if value >= 9:
                    return PackChoice(i, targets, why)
        rest = [i for i, c in enumerate(cards) if c["kind"] != "spectral"]
        ch = choose_from_pack(state, pack_name, [cards[i] for i in rest], picks_left)
        return replace(ch, index=None if ch.index is None else rest[ch.index])
    kind = cards[0]["kind"]
    if kind == "planet":
        return _choose_planet(state, cards)
    if kind == "tarot":
        return _best_of(state, cards, _tarot_choice)
    if kind == "spectral":
        return _best_of(state, cards, _spectral_choice)
    if kind == "joker":
        return _choose_joker(state, cards)
    if kind == "playing_card":
        return _choose_standard(state, cards)
    return PackChoice(None, [], f"Unknown card type {kind}")


def use_consumable(state, name: str, kind: str) -> PackChoice:
    if kind == "planet":
        return PackChoice(0, [], f"Use {name} now (level the hand)")
    full = len(state.consumables) >= consumable_slots(state)
    held = list(state.consumables)
    if name in held:  # using a held card frees its own slot first (High Priestess/Emperor/Fool with a full tray)
        held.remove(name)
        state = replace(state, consumables=held)
    chooser = _tarot_choice if kind == "tarot" else _spectral_choice
    value, targets, why = chooser(state, name)
    if value <= 0 and full and name in SUIT_TAROT:
        # live run 20261030-194631: The World + The Star (no flush focus) sat in both slots for the rest of the
        # run and blocked every planet/tarot buy. Recolour one junk card instead: harmless, frees the slot.
        t = _junk_recolour(state, SUIT_TAROT[name])
        if t is not None:
            return PackChoice(0, [t], f"{name}: free the consumable slot (recolour 1 filler card, no Flush focus)")
    return PackChoice(0 if value > 0 else None, targets, why)


def _junk_recolour(state, suit):
    """Weakest plain card whose suit no owned joker cares about (and isn't `suit` already), or None."""
    cared = {s for n, s in {**SUIT_JOKER, **SUIT_MULT_JOKER}.items() if n in _names(state)}
    cand = [i for i, c in enumerate(state.hand) if not c.stone and c.suit != suit and c.suit not in cared
            and not (c.enhancement or c.edition or c.seal) and not c.debuff]
    return min(cand, key=lambda i: (state.hand[i].chip_bonus(), state.hand[i].rank), default=None)
