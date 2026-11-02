"""Compare deck edits with the same sampled draws, using the existing score engine."""
import itertools
import random
from dataclasses import replace

from .engine import active_flags, detect
from .models import Card
from .optimizer import _best_score


def card_key(card):
    return card.rank, card.suit, card.enhancement, card.edition, card.seal, card.bonus


def _index(variant, card):
    exact = next((i for i, (_, c) in enumerate(variant) if card_key(c) == card_key(card)), None)
    if exact is not None:
        return exact
    # shortcut: v2 strips omit permanent deck bonuses; re-patch to v3 for exact duplicate-card matching.
    return next((i for i, (_, c) in enumerate(variant) if card_key(c)[:5] == card_key(card)[:5]), None)


def outlook(state, remove=(), copy=None, add=None, replacement=None, samples=12):
    """Expected best score and focus draw chance after one deck edit. Inputs are cards, never screen indices."""
    from .strategy import focus_hand
    deck = state.get_full_deck()
    variant = list(enumerate(deck))
    for card in remove:
        match = _index(variant, card)
        if match is None:
            raise ValueError("Card to remove is not in the known full deck")
        variant.pop(match)
    if replacement:
        old, new = replacement
        match = _index(variant, old)
        if match is None:
            raise ValueError("Card to replace is not in the known full deck")
        variant[match] = variant[match][0], new
    if copy is not None or add is not None:
        variant.append((len(deck), Card.parse(copy if copy is not None else add)))
    sim = replace(state, blind=None, boss_disabled=True, blind_chips=0, score=0, deck=None, full_deck=[c for _, c in variant])
    focus, flags = focus_hand(state), active_flags(state)
    points, hits = [], 0
    for n in range(samples):
        rng = random.Random(n)
        priority = [rng.random() for _ in range(len(deck) + 1)]
        hand = [c for _, c in sorted(variant, key=lambda pair: priority[pair[0]])[:state.hand_size]]
        points.append(_best_score(replace(sim, hand=hand)))
        # Flush/Pair/etc. may be present even when a different hand scores more.
        hits += any(focus in detect(list(cards), flags)[2] for k in range(1, min(5, len(hand)) + 1)
                    for cards in itertools.combinations(hand, k))
    return {"expected": sum(points) / len(points), "focus_chance": hits / samples, "samples": samples,
            "focus": focus, "deck_size": len(variant)}


def compare(state, **change):
    before = outlook(state)
    after = outlook(state, **change)
    return {**after, "gain": after["expected"] / max(before["expected"], 1) - 1,
            "focus_delta": after["focus_chance"] - before["focus_chance"]}


def advice(state):
    """Rank removals and copies across the entire known deck."""
    if state.full_deck is None:
        return {"known": False, "changes": [], "note": "Full deck unknown; deck edits need telemetry or a full_deck snapshot."}
    baseline = outlook(state)
    unique = {}
    for card in state.full_deck:
        unique.setdefault(card_key(card), card)
    changes = []
    for card in unique.values():
        for kind, edit in (("remove", {"remove": [card]}), ("copy", {"copy": card})):
            result = outlook(state, **edit)
            changes.append({"action": kind, "card": str(card), **result,
                            "gain": result["expected"] / max(baseline["expected"], 1) - 1,
                            "focus_delta": result["focus_chance"] - baseline["focus_chance"]})
    changes.sort(key=lambda c: (c["gain"], c["focus_delta"]), reverse=True)
    return {"known": True, "baseline": baseline, "changes": changes}
