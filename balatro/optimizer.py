"""Play/discard search on top of engine.score: best plays, joker order, Monte-Carlo discards, advice."""
import itertools
import random
import time
from collections import Counter
from dataclasses import dataclass, field, replace

from .data import RANK_NAMES
from .engine import active_flags, apply_boss_debuffs, detect, most_played, score, stochastic
from .jokers import get as get_joker

SUIT_SYMBOLS = {"S": "♠", "H": "♥", "C": "♣", "D": "♦"}
# Jokers/bosses for which playing extra non-scoring cards ("kickers") can raise the score.
KICKER_JOKERS = {"Square Joker", "Blackboard", "Raised Fist"}


def _boss(state):
    return None if state.boss_disabled or "Chicot" in active_flags(state) else state.blind


def _joker_plan(state, play_idx=None, discard_idx=None, hook=False):
    """Projected state and a normalized future-value bonus; never changes the snapshot."""
    if (play_idx is None) == (discard_idx is None):
        return state, 0.0, []
    playing = play_idx is not None
    idx = tuple(play_idx if playing else discard_idx)
    if not idx:
        return state, 0.0, []
    flags = active_flags(state)
    after = state.copy()
    after.hand = [replace(c) for c in apply_boss_debuffs(state)]
    chosen = [after.hand[i] for i in idx]
    ht, scoring, contains = detect(chosen, flags)
    scoring = [c for c in scoring if not c.debuff]
    names = [j.name for j in state.jokers if not j.debuff]
    prob = 2 ** names.count("Oops! All 6s")
    utility, reasons = 0.0, []

    def bonus(value, text):
        nonlocal utility
        if value:
            utility += value
            reasons.append(text)

    def grow(j, key, delta, scale, text):
        old = j.vars[key]
        j.vars[key] += delta
        bonus(delta / max(scale + old, 1), f"{j.name}: {text}")

    def triggers(c):
        return (1 + (c.seal == "red") + names.count("Seltzer")
                + names.count("Hack") * (not c.stone and c.rank in (2, 3, 4, 5))
                + names.count("Sock and Buskin") * c.is_face(flags)
                + names.count("Dusk") * (state.hands_left == 1)
                + 2 * names.count("Hanging Chad") * (c is scoring[0]))

    for j in after.jokers:
        if j.debuff:
            continue
        spec = get_joker(j.name)
        j.vars = {**(spec.defaults if spec else {}), **j.vars}
        name, v = j.name, j.vars
        if playing:
            if name == "Ride the Bus":
                reset = any(c.is_face(flags) for c in scoring)
                grow(j, "mult", -v["mult"] if reset else 1, 10,
                     "a face card resets the counter" if reset else "+1 mult without a scored face card")
            elif name == "Green Joker":
                grow(j, "mult", 1, 10, "+1 mult per hand played")
            elif name == "Runner" and "Straight" in contains:
                grow(j, "chips", 15, 100, "+15 chips from a Straight")
            elif name == "Square Joker" and len(idx) == 4:
                grow(j, "chips", 4, 100, "+4 chips with exactly four cards")
            elif name == "Spare Trousers" and "Two Pair" in contains:
                grow(j, "mult", 2, 10, "+2 mult from two pairs")
            elif name == "Wee Joker":
                n = sum(triggers(c) for c in scoring if not c.stone and c.rank == 2)
                grow(j, "chips", 8 * n, 100, f"+{8 * n} chips from scored twos")
            elif name == "Supernova":
                count = state.played_counts.get(ht, 0)
                bonus(0.1 * (count + 1) / (sum(state.played_counts.values()) + 1),
                      f"Supernova: build {ht} up for more repeats")
            elif name == "Obelisk":
                mine = state.played_counts.get(ht, 0) + 1
                safe = any(h != ht and n >= mine for h, n in state.played_counts.items())
                grow(j, "xmult", 0.2 if safe else 1 - v["xmult"], 0,
                     "+0.2 ×mult without the most played hand" if safe else "the most played hand resets ×mult")
            elif name == "Card Sharp" and state.hands_left > 1 and _boss(state) not in ("The Eye", "The Needle"):
                bonus(0.2, f"Card Sharp: set up {ht} for ×3 on a repeat")
            elif name == "Ice Cream":
                grow(j, "chips", -min(5, v["chips"]), 100, "−5 chips per hand played")
            elif name == "Popcorn" and score(state, idx).score >= state.blind_chips - state.score > 0:
                grow(j, "mult", -min(4, v["mult"]), 10, "−4 mult at round end")
            elif name == "Hiker":
                n = sum(triggers(c) for c in scoring)
                for c in scoring:
                    c.bonus += 5 * triggers(c)
                bonus(5 * n / 100, f"Hiker: +{5 * n} permanent card chips")
            elif name == "Lucky Cat":
                n = sum(triggers(c) for c in scoring if c.enhancement == "lucky" and "Vampire" not in names)
                chance = 1 - (1 - min(1, prob / 5)) * (1 - min(1, prob / 15))
                grow(j, "x", 0.25 * n * chance, 0, "lucky cards raise the expected ×mult")
            elif name == "Vampire":
                enhanced = [c for c in scoring if c.enhancement]
                grow(j, "xmult", 0.1 * len(enhanced), 0, "turn enhancements into permanent ×mult")
                for c in enhanced:
                    bonus(-0.03, "Vampire: the card enhancement is lost")
                    c.enhancement = None
            elif name == "Hologram" and "DNA" in names and not state.round_hand_types and len(idx) == 1:
                grow(j, "xmult", 0.25, 0, "+0.25 ×mult from the DNA copy")
            # Constellation/Madness grow at planet use/blind selection, never just by playing a hand.
        else:
            # shortcut: infer first discard; use vars.first_discard until snapshots include discard history.
            initial = (3 + (state.deck_type == "Red Deck") + names.count("Drunkard")
                       + 3 * names.count("Merry Andy")
                       + sum(voucher in state.vouchers for voucher in ("Wasteful", "Recyclomancy")))
            first = state.discards_used == 0 if state.discards_used is not None else v.get(
                "first_discard", state.discards_left == initial)
            if name == "Green Joker":
                grow(j, "mult", -min(1, v["mult"]), 10, "a discard costs 1 mult")
            elif name == "Ramen":
                grow(j, "x", max(1, v["x"] - 0.01 * len(idx)) - v["x"], 0,
                     "a discard wears ×mult down")
            elif name == "Castle":
                n = sum(c.is_suit(v["suit"], flags) for c in chosen)
                grow(j, "chips", 3 * n, 100, f"+{3 * n} chips from the matching suit")
            elif name == "Mail-In Rebate":
                n = sum(not c.stone and not c.debuff and c.rank == v.get("rank", 14) for c in chosen)
                after.money += 5 * n
                bonus(n, f"Mail-In Rebate: +${5 * n} from the matching rank")
            elif name == "Faceless Joker" and sum(c.is_face(flags) for c in chosen) >= 3:
                after.money += 5
                bonus(1, "Faceless Joker: +$5 for at least three face cards")
            elif name == "Hit the Road":
                n = sum(not c.stone and not c.debuff and c.rank == 11 for c in chosen)
                grow(j, "x", 0.5 * n, 0, "discarded jacks raise this round's ×mult")
            elif name == "Trading Card" and first and len(idx) == 1:
                after.money += 3
                after.full_deck = list(after.get_full_deck())
                for i, c in enumerate(after.full_deck):
                    if (c.rank, c.suit) == (chosen[0].rank, chosen[0].suit):
                        del after.full_deck[i]
                        break
                bonus(0.6 + (0.1 if not (chosen[0].enhancement or chosen[0].edition or chosen[0].seal) else -0.5),
                      "Trading Card: remove a single card and +$3 on the first discard")
            elif name == "Yorick":
                discarded = v.get("discarded", 0)
                v["discarded"] = (discarded + len(idx)) % 23
                grow(j, "x", (discarded + len(idx)) // 23, 0, "+1 ×mult after 23 discarded cards")
                bonus(len(idx) / (23 * max(v["x"], 1)), "Yorick: progress towards 23 discarded cards")
            elif name == "Burnt Joker" and first and not hook:
                after.hand_levels = {**after.hand_levels, ht: after.level(ht) + 1}
                bonus(0.5, f"Burnt Joker: level {ht} up on the first discard")
            if name in ("Trading Card", "Burnt Joker") and not hook:
                v["first_discard"] = False
    if playing:
        after.played_counts = {**state.played_counts, ht: state.played_counts.get(ht, 0) + 1}
        after.round_hand_types = [*state.round_hand_types, ht]
        after.hands_left -= 1
        if _boss(state) == "The Tooth":
            after.money -= len(idx)
        if _boss(state) == "The Ox" and ht == most_played(state) and state.money > 0:
            after.money = 0
            bonus(-min(state.money, 50) / 25, f"The Ox: {ht} is the most played hand – money drops to $0")
        if _boss(state) == "The Arm" and state.level(ht) > 1:
            # permanent level loss, the mirror of Burnt Joker's +0.5 (run 20261031-194150: Pair lvl 3 -> 1)
            after.hand_levels = {**after.hand_levels, ht: state.level(ht) - 1}
            bonus(-0.5, f"The Arm: {ht} loses a level")
    elif not hook:
        after.discards_left -= 1
        if after.discards_used is not None:
            after.discards_used += 1
    return after, utility, reasons


def joker_utility(state, play_idx=None, discard_idx=None):
    """(normalized future value, reasons) for one play or discard.

    shortcut: relative chips/Mult/×Mult and $5 units; calibrate with measured runs.
    Copied growth, future shops and unknown counters need richer snapshots before exact planning.
    """
    _, value, reasons = _joker_plan(state, play_idx, discard_idx)
    return value, reasons


def card_name(c):
    s = "Stone" if c.stone else RANK_NAMES[c.rank] + SUIT_SYMBOLS.get(c.suit, c.suit)
    extras = [x for x in (None if c.stone else c.enhancement, c.edition, c.seal and c.seal + " seal",
                          "debuffed" if c.debuff else None, "face down" if c.face_down else None) if x]
    return s + (f" ({', '.join(extras)})" if extras else "")


def cards_str(cards):
    return " ".join(card_name(c) for c in cards)


def _subsets(state):
    """Every legal play: 1-5 hand cards, Cerulean Bell forced cards always included."""
    n = len(state.hand)
    forced = {i for i, c in enumerate(state.hand) if c.forced}
    psychic = _boss(state) == "The Psychic"
    for k in range(max(1, len(forced)), min(5, n) + 1):
        for idx in itertools.combinations(range(n), k):
            if forced <= set(idx) and (not psychic or k == 5):
                yield idx


def best_plays(state, top=5):
    """Top-N (indices, Result) over all legal plays, best first (ties: fewer cards)."""
    res = [(idx, score(state, idx)) for idx in _subsets(state)]
    res = [(idx, r) for idx, r in res if not r.reason]
    res.sort(key=lambda t: (-t[1].score, len(t[0])))
    seen, out = set(), []
    square = any(j.name == "Square Joker" and not j.debuff for j in state.jokers)
    # drop kicker variants of the same scoring cards: the best-scoring one comes first; a kicker that lowers
    # the score (Baron/Steel/Mime held card, Half Joker) is never worth it, as in add_kickers
    for idx, r in res:
        key = (r.hand_type, tuple(map(str, r.scoring)), square and len(idx) == 4)
        if key not in seen:
            seen.add(key)
            out.append((idx, r))
    return out[:top]


def _best_play(state):
    """(score, play indices, scoring indices) of the best play, skipping plays with non-scoring kickers
    when kickers can't help. Used inside Monte Carlo where full enumeration (218 x score) is too slow."""
    names = {j.name for j in state.jokers if not j.debuff}
    prune = not (names & KICKER_JOKERS) and state.blind != "The Psychic"
    hand, flags, best = apply_boss_debuffs(state), active_flags(state), (0, (), ())
    need = 4 if "Four Fingers" in flags else 5
    prune = prune and "Splash" not in flags  # Splash: every played card scores
    for idx in _subsets(state):
        if prune and not _all_score([hand[i] for i in idx], flags, need):
            continue
        r = score(state, idx)
        if not r.reason and (not best[1] or r.score > best[0]):
            best = (r.score, idx, r.scoring_idx or idx)
    return best


def _all_score(played, flags, need):
    """Does every played card (except Cerulean Bell's forced ones) score? Mirrors detect(): when no
    straight/flush is possible (too few cards or ranks/suits), the rank groups decide without detect()."""
    ranks = [c.rank for c in played if not c.stone]
    distinct = set(ranks)
    if len(played) >= need and (len(distinct) >= need or _flush_possible(played, flags, need)):
        ids = {id(c) for c in detect(played, flags)[1]}
        return all(id(c) in ids or c.forced for c in played)
    if len(distinct) < len(ranks):  # pair-type hand: grouped cards (+ stones) score
        return all(c.stone or c.forced or ranks.count(c.rank) >= 2 for c in played)
    top = max(ranks, default=None)  # High Card: only the (unique) highest card scores
    return all(c.stone or c.forced or c.rank == top for c in played)


def _flush_possible(played, flags, need):
    """Cheap upper bound for _flush: wild cards count for every suit, Smeared Joker folds suits to colours."""
    smeared = "Smeared Joker" in flags
    suits = [("R" if c.suit in "HD" else "B") if smeared else c.suit
             for c in played if not c.stone and c.enhancement != "wild"]
    wild = sum(c.enhancement == "wild" for c in played)
    return max(map(suits.count, set(suits)), default=0) + wild >= need


_SCORE_MEMO = {}


def _best_score(state):
    """Best single-play score, memoised by the full state: shop/pack valuation re-scores the same sampled
    hands for every offer, baseline and estimate. ponytail: repr key, cleared wholesale when large."""
    key = repr(state)
    value = _SCORE_MEMO.get(key)
    if value is None:
        value = _best_play(state)[0]
        if len(_SCORE_MEMO) > 50_000:
            _SCORE_MEMO.clear()
        _SCORE_MEMO[key] = value
    return value


def add_kickers(state, idx, deck_left=True):
    """Fill a play up to 5 cards with cards the hand doesn't need. They don't score, but leave the hand and
    are replaced from the deck: a free discard that costs no discard. Pairs, cards of a suit held 3+ times
    and enhanced/edition/seal cards are kept; a kicker is only added if the score stays the same (Half Joker,
    Square Joker, held-card effects such as Steel or Baron).
    ponytail: does not protect straight draws."""
    hand = state.hand
    boss = _boss(state)
    if boss in ("The Tooth", "The Serpent"):  # Serpent draws 3 whatever leaves: kickers only shrink the hand
        return tuple(idx)
    if boss == "The Psychic" and len(idx) < 5:
        candidates = (tuple(sorted((*idx, *extra))) for extra in itertools.combinations(
            [i for i in range(len(hand)) if i not in idx], 5 - len(idx)))
        return max(candidates, key=lambda cand: score(state, cand).score, default=tuple(idx))
    if len(idx) >= 5 or not deck_left:
        return tuple(idx)
    rest = [i for i in range(len(hand)) if i not in idx and not hand[i].forced]
    ranks = Counter(hand[i].rank for i in rest if not hand[i].stone)
    suits = Counter(hand[i].suit for i in rest if not hand[i].stone)
    junk = sorted((i for i in rest if ranks[hand[i].rank] < 2 and suits[hand[i].suit] < 3
                   and not (hand[i].enhancement or hand[i].edition or hand[i].seal)),
                  key=lambda i: _value(hand[i]))
    out, base = tuple(idx), score(state, idx).score
    utility = joker_utility(state, play_idx=idx)[0]
    for i in junk:
        if len(out) >= 5:
            break
        cand = tuple(sorted(out + (i,)))
        s = score(state, cand).score
        # base follows each accepted card: a card that formed a pair raised it, a later kicker must not undo that
        if s >= base and joker_utility(state, play_idx=cand)[0] >= utility:
            out, base = cand, s
            utility = joker_utility(state, play_idx=out)[0]
    return out


def _draw(kept, deck, state):
    """Refill after a play/discard: up to hand size, or always 3 cards under The Serpent."""
    n = 3 if _boss(state) == "The Serpent" else max(state.hand_size - len(kept), 0)
    return kept + deck[:n], deck[n:]


def _after_play(state, idx, rng):
    scored = state
    if _boss(state) == "The Hook":
        held = [i for i in range(len(state.hand)) if i not in idx]
        drop = set(rng.sample(held, min(2, len(held))))
        if drop:
            # Hook discards before scoring, without spending a discard or triggering Burnt Joker.
            state, _, _ = _joker_plan(state, discard_idx=sorted(drop), hook=True)
            kept = [i for i in range(len(state.hand)) if i not in drop]
            idx = tuple(kept.index(i) for i in idx)
            state = replace(state, hand=[state.hand[i] for i in kept])
            scored = replace(state, boss_disabled=True)  # Hook already applied; score() must not repeat it
    result = score(scored, idx, rng=rng)
    after, _, _ = _joker_plan(state, play_idx=idx)
    after.money += result.money
    for j, values in zip(after.jokers, result.joker_vars):
        if j.name == "Lucky Cat":
            j.vars = values
    if result.level_delta:
        after.hand_levels = {**after.hand_levels, result.hand_type: max(1, state.level(result.hand_type) + result.level_delta)}
    rest = [c for i, c in enumerate(after.hand) if i not in idx]
    return after, rest, result


def _round_total(state, hand, deck, hands, discards, need, total=0, rng=None):
    """Greedy round rollout with evolving joker counters and boss hand history.

    shortcut: no future rearrangement or card creation; expand for exact rollouts.
    """
    rng = rng or random.Random(0)
    if _boss(state) == "The Needle":
        hands = min(hands, 0 if state.round_hand_types else 1)
    while hands > 0 and total < need:
        state = replace(state, hand=hand, deck=deck, hands_left=hands, discards_left=discards)
        s, idx, keep = _best_play(state)
        if s < (need - total) / hands and discards > 0 and deck:
            drop = _with_forced(hand, sorted((i for i in range(len(hand)) if i not in keep and not hand[i].forced),
                                             key=lambda i: _value(hand[i])))
            if drop:
                state, _, _ = _joker_plan(state, discard_idx=drop)
                hand, deck = _draw([c for i, c in enumerate(state.hand) if i not in drop], deck, state)
                discards -= 1
                continue
        if not idx:
            break
        if total + s < need and hands > 1:
            idx = add_kickers(state, idx, bool(deck))
        state, rest, result = _after_play(state, idx, rng)
        total += result.score
        hands -= 1
        hand, deck = _draw(rest, deck, state)
    return total


def _decks(state, samples, seed):
    """Shuffled draw piles, shared by all options (common random numbers)."""
    deck, rng = state.get_deck(), random.Random(seed)
    return [rng.sample(deck, len(deck)) for _ in range(samples)]


def play_now_odds(state, idx, now, samples=40, seed=0):
    """(round win chance, expected round score) if idx is played now and the round is played out."""
    need = state.blind_chips - state.score
    if now >= need and _boss(state) != "The Hook" and not stochastic(state):
        return 1.0, float(now)
    totals = []
    for n, deck in enumerate(_decks(state, samples, seed)):
        rng = random.Random(seed + n)
        after, rest, result = _after_play(state, idx, rng)
        totals.append(_round_total(after, *_draw(rest, deck, state), after.hands_left,
                                   after.discards_left, need, total=result.score, rng=rng))
    return sum(t >= need for t in totals) / len(totals), sum(totals) / len(totals)


def best_joker_order(state, played_idx):
    """-> (jokers, Result) of the best arrangement, or None if the current order is already best."""
    current = score(state, played_idx)
    js, best, best_order = state.jokers, current.score, None
    if len(js) < 2:
        return None
    if len(js) <= 6:
        seen = set()
        for perm in itertools.permutations(js):
            key = tuple(map(str, perm))
            if key in seen:
                continue
            seen.add(key)
            s = score(replace(state, jokers=list(perm)), played_idx).score
            if s > best:
                best, best_order = s, list(perm)
    else:
        # ponytail: hill-climb over single moves (catches Blueprint adjacency, xMult-right);
        # can stick in local optima, upgrade = beam search if >6 jokers becomes common.
        order, improved = list(js), True
        while improved:
            improved = False
            for i, j in itertools.permutations(range(len(order)), 2):
                cand = order[:]
                cand.insert(j, cand.pop(i))
                s = score(replace(state, jokers=cand), played_idx).score
                if s > best:
                    best, order, best_order, improved = s, cand, cand, True
    if best_order is None:
        return None
    return best_order, score(replace(state, jokers=best_order), played_idx, verbose=True)


def best_card_order(state, played_idx):
    """Improve played and held order without changing which cards are played."""
    idx = tuple(sorted(played_idx))
    order = list(range(len(state.hand)))
    best = score(state, idx).score
    for positions in (list(idx), [i for i in order if i not in idx]):
        improved = True
        while improved:
            improved = False
            for a, b in itertools.permutations(positions, 2):
                candidate = order[:]
                candidate[a], candidate[b] = candidate[b], candidate[a]
                value = score(replace(state, hand=[state.hand[i] for i in candidate]), idx).score
                if value > best:
                    best, order, improved = value, candidate, True
    # shortcut: pair-swap hill climb; use a wider search if logged hands expose a local optimum.
    if order == list(range(len(state.hand))):
        return None
    return tuple(order), score(replace(state, hand=[state.hand[i] for i in order]), idx, verbose=True)


def _with_forced(hand, drop):
    """Discard indices as the game takes them: Cerulean Bell's forced card stays highlighted and goes too
    (state_events.lua discard_cards_from_highlighted), within the 5-card limit. drop: candidates, best first."""
    forced = [i for i, c in enumerate(hand) if c.forced]
    return tuple(sorted(forced + list(drop)[:5 - len(forced)]))


def _value(c):
    # ponytail: keep-value = chips + flat bonus for enhanced/edition/seal; ignores joker synergies.
    return c.chip_bonus() + 15 * bool(c.enhancement or c.edition or c.seal), c.rank


def _discard_candidates(state, plays):
    """Candidate discard index sets: complement of 'keep' sets, at most 5 lowest-value cards."""
    hand = state.hand
    n = len(hand)
    flags = active_flags(state)
    keeps = {}

    def add(label, keep):
        rest = sorted((i for i in range(n) if i not in keep and not hand[i].forced), key=lambda i: _value(hand[i]))
        if rest:
            keeps.setdefault(_with_forced(hand, rest), label)

    def drop(label, indices):
        d = sorted(i for i in indices if not hand[i].forced)
        if d:
            d = _with_forced(hand, d)
            keeps.setdefault(d, label)

    for j in state.jokers:
        if j.debuff:
            continue
        if j.name == "Mail-In Rebate":
            drop("Mail-In Rebate: discard the target rank", [i for i, c in enumerate(hand)
                 if not c.stone and not c.debuff and c.rank == j.vars.get("rank", 14)])
        elif j.name == "Castle":
            drop("Castle: discard the target suit", [i for i, c in enumerate(hand)
                 if c.is_suit(j.vars.get("suit", "S"), flags)])
        elif j.name == "Faceless Joker":
            drop("Faceless Joker: discard face cards", [i for i, c in enumerate(hand) if c.is_face(flags)])
        elif j.name == "Hit the Road":
            drop("Hit the Road: discard jacks", [i for i, c in enumerate(hand)
                 if not c.stone and not c.debuff and c.rank == 11])
        elif j.name == "Trading Card":
            for i in range(n):
                drop("Trading Card: Einzelkarte entfernen", [i])
        elif j.name == "Burnt Joker":
            for idx, r in plays:
                drop(f"Burnt Joker: {r.hand_type} aufwerten", idx)

    for idx, r in plays:  # keep the scoring cards of each top play
        add(f"keep {r.hand_type}", set(r.scoring_idx or idx))
    for s in "SHCD":  # flush draws
        suited = sorted((i for i, c in enumerate(hand) if c.is_suit(s, flags)), key=lambda i: -hand[i].rank)
        if len(suited) >= 3:
            add(f"flush draw {SUIT_SYMBOLS[s]}", set(suited[:5]))
    for lo in range(1, 11):  # straight draws (lo=1: A-2-3-4-5)
        want = {14 if r == 1 else r for r in range(lo, lo + 5)}
        keep = {}
        for i, c in enumerate(hand):
            if not c.stone and c.rank in want:
                keep.setdefault(c.rank, i)
        if len(keep) >= 3:
            add(f"straight draw from {RANK_NAMES[14 if lo == 1 else lo]}", set(keep.values()))
    groups = {}
    for i, c in enumerate(hand):
        if not c.stone:
            groups.setdefault(c.rank, []).append(i)
    multi = {i for g in groups.values() if len(g) >= 2 for i in g}
    if multi:
        add("keep pairs/trips", multi)
    by_value = sorted(range(n), key=lambda i: _value(hand[i]))
    for k in (2, 3, 5):  # junk discards
        add(f"discard the {k} weakest", set(by_value[k:]))
    return [(label, list(d)) for d, label in keeps.items()]


@dataclass
class DiscardOption:
    label: str
    discard_idx: list
    discarded: list
    expected: float
    p_win: float
    samples: int


def discard_options(state, samples=40, seed=0, plays=None):
    """Monte Carlo over candidate discards: discard, refill to hand_size, then play the round out
    (_round_total). expected = mean round score, p_win = chance to beat the blind this round."""
    need = state.blind_chips - state.score
    plays = plays if plays is not None else best_plays(state, top=5)
    decks = _decks(state, samples, seed)
    out = []
    for label, d in _discard_candidates(state, plays):
        base, _, _ = _joker_plan(state, discard_idx=d)
        kept = [c for i, c in enumerate(base.hand) if i not in d]
        totals = [_round_total(base, *_draw(kept, deck, state), state.hands_left,
                               base.discards_left, need, rng=random.Random(seed + n))
                  for n, deck in enumerate(decks)]
        out.append(DiscardOption(label, d, [state.hand[i] for i in d], sum(totals) / len(totals),
                                 sum(t >= need for t in totals) / len(totals), len(totals)))
    out.sort(key=lambda o: (-o.p_win, -o.expected))
    return out


def _budget_samples(state, plays, samples, budget=2.5, play_count=1):
    """Fewer simulations when one best-play search is slow (9+ cards, Splash: no kicker pruning), so a
    recommendation stays within ~budget seconds. ponytail: estimate = one timed search x rollout length."""
    t = time.perf_counter()
    _best_play(state)
    one = time.perf_counter() - t
    per_sample = one * (state.hands_left + state.discards_left) * (len(_discard_candidates(state, plays)) + play_count)
    # fewer than 8 samples make the odds noise - unless the caller asked for fewer
    return min(samples, max(8, int(budget / max(per_sample, 1e-6))))


@dataclass
class Recommendation:
    action: str                     # "play" | "discard"
    reason: str
    need: int
    play_idx: tuple
    play: object                    # Result (verbose) of the best play now
    alternatives: list              # [(idx, Result)]
    joker_order: tuple | None = None   # (jokers, Result) if rearranging beats current order
    discard: DiscardOption | None = None
    discard_options: list = field(default_factory=list)
    hand: list = field(default_factory=list)
    kickers: tuple = ()             # hand indices played along without scoring (see add_kickers)
    play_win: float | None = None   # simulated chance for the round; None if not simulated
    play_expected: float | None = None
    samples: int = 0
    card_order: tuple | None = None


def recommend(state, samples=40, seed=0, reorder=True):
    """reorder=False: plan with the jokers as they are (the autopilot can't drag jokers yet)."""
    from .strategy import focus_hand

    if _boss(state) == "The Needle":
        state = replace(state, hands_left=min(state.hands_left, 1))
    plays = best_plays(state, top=None)
    if not plays:
        raise ValueError("no playable hand")
    need = state.blind_chips - state.score
    focus = focus_hand(state)
    utilities = {idx: joker_utility(state, play_idx=idx) for idx, _ in plays}

    def preference(play):
        idx, r = play
        return utilities[idx][0], r.hand_type == focus, r.score, -len(idx)

    # Every immediate winner is considered, even if it was outside the old top-five score list.
    winners = [(idx, r) for idx, r in plays if r.score >= need > 0]
    idx, now = max(winners, key=preference) if winners else plays[0]
    order = best_joker_order(state, idx) if reorder else None
    now_best = order[1].score if order else now.score
    card_order = best_card_order(replace(state, jokers=order[0]) if order else state, idx) if reorder else None
    if card_order:
        now_best = max(now_best, card_order[1].score)
    opts, chosen_discard = [], None
    p_play = e_play = None
    used_samples = 0
    if state.blind_chips <= 0:
        reason = "Blind target unknown: play the highest score."
    elif now_best >= need and _boss(state) != "The Hook" and not stochastic(state) and not any(
            joker_utility(state, discard_idx=d)[0] > utilities[idx][0]
            for _, d in _discard_candidates(state, plays[:5]) if state.discards_left > 0):
        reason = f"{now_best:,} ≥ {need:,} needed: wins the blind."
    else:
        # shortcut: bounded shortlist plus the best winner; expand if runs miss a safe growth play.
        candidates = list(plays[:5])
        if (idx, now) not in candidates:
            candidates.append((idx, now))
        for candidate in [*sorted(plays, key=preference, reverse=True)[:2],
                          next((p for p in plays if p[1].hand_type == focus), plays[0])]:
            if candidate[0] not in {p[0] for p in candidates}:
                candidates.append(candidate)
        prepared = {}
        for original, r in candidates:
            full = (add_kickers(state, original, bool(state.get_deck()))
                    if state.hands_left > 1 and r.score < need else original)
            prepared[full] = score(state, full)
            utilities[full] = joker_utility(state, play_idx=full)
        candidates = list(prepared.items())
        samples = _budget_samples(state, candidates, samples, play_count=len(candidates))
        used_samples = samples
        odds = [(p, *play_now_odds(state, p[0], p[1].score, samples, seed)) for p in candidates]
        if state.discards_left > 0:
            opts = discard_options(state, samples=samples, seed=seed, plays=candidates)
        best_chance = max([p for _, p, _ in odds] + [o.p_win for o in opts])
        eligible = [(p, chance, expected) for p, chance, expected in odds if chance >= best_chance - 0.05 - 1e-9]
        # At poor odds expected score remains useful; near a win additional overkill has no value.
        if eligible and best_chance < 0.1:  # all but lost: the score still matters more than future growth
            (idx, now), p_play, e_play = max(eligible, key=lambda item: (
                item[2], item[1], utilities[item[0][0]][0], item[0][1].score))
        elif eligible:
            # joker growth only decides between comparable hands: Ride the Bus kept High Card 180 over
            # Two Pair 834 at a "100 %" the round then barely won (run 082933 move 83)
            top = max(item[0][1].score for item in eligible)
            eligible = [item for item in eligible if item[0][1].score >= 0.5 * top]
            # focus only breaks ties after chance and expected score: it played Pair 2,080 over High Card 4,320
            # (run 101337 Z238) and Pair 6,435 over Flush 11,547 (run 115250 Z292) at equal chance
            (idx, now), p_play, e_play = max(eligible, key=lambda item: (
                utilities[item[0][0]][0], item[1], item[2],
                item[0][1].hand_type == focus, item[0][1].score))
        else:
            (idx, now), p_play, e_play = max(odds, key=lambda item: (item[1], item[2]))
        order = best_joker_order(state, idx) if reorder else None
        now_best = order[1].score if order else now.score
        if opts:
            best = max((o for o in opts if o.p_win >= best_chance - 0.05 - 1e-9),
                       # no "discarded cards form the focus hand" bonus: it threw away the Pair a Pair build
                       # needs (runs 094727/101337); Burnt Joker's level-up already counts via joker_utility
                       key=lambda o: (joker_utility(state, discard_idx=o.discard_idx)[0], o.p_win, o.expected),
                       default=None)
            if best:
                u_discard = joker_utility(state, discard_idx=best.discard_idx)[0]
                u_play = utilities[idx][0]
                tied = abs(best.p_win - p_play) <= 0.05 + 1e-9
                if (p_play < best_chance - 0.05 - 1e-9
                        or best.p_win > p_play + 0.05 + 1e-9
                        or (tied and u_discard > u_play)
                        or (tied and u_discard == u_play and best.p_win >= p_play
                            and now_best < need / max(state.hands_left, 1))
                        # a junk play wastes a hand for a trifle a joker loses per discard (Ramen -0.015: The
                        # Ox, High Card 192 of 70,000 three times with 4 discards left, run 101337); Green
                        # Joker's -1 Mult (0.1) still counts
                        or (tied and best.p_win >= p_play and u_discard >= u_play - 0.03
                            and now_best < 0.5 * need / max(state.hands_left, 1))
                        or (u_discard == u_play and max(best.p_win, p_play) < 0.1
                            and best.expected > 1.1 * e_play)):
                    chosen_discard = best
        if chosen_discard:
            reason = (f"Round win chance: discard {chosen_discard.p_win:.0%} vs. play now {p_play:.0%} "
                      f"(expected {chosen_discard.expected:,.0f} instead of {e_play:,.0f} round points).")
        elif now_best >= need and p_play == 1:
            reason = f"All {used_samples} simulations won; this is an estimate, not a guarantee."
        else:
            reason = f"Play now: round win chance {p_play:.0%}" + (
                "; keep the discards for later." if state.discards_left > 0 else "; no discards left.")
    action = "discard" if chosen_discard else "play"
    _, reasons = (joker_utility(state, discard_idx=chosen_discard.discard_idx) if chosen_discard
                  else joker_utility(state, play_idx=idx))
    if _boss(state) == "The Hook":
        reason += " The Hook: two random discards before scoring simulated; display before the Hook effect."
    if reasons:
        reason += " Jokers: " + "; ".join(dict.fromkeys(reasons)) + "."
    if action == "play" and now.hand_type == focus and state.blind_chips > 0:
        reason += f" Focus hand: {focus}."
    kickers = tuple(i for i in idx if i not in now.scoring_idx) if now.score < need else ()
    card_order = best_card_order(replace(state, jokers=order[0]) if order else state, idx) if reorder else None
    return Recommendation(action, reason, need, idx, score(state, idx, verbose=True),
                          [p for p in plays[:5] if p[0] != idx], order, chosen_discard, opts, state.hand, kickers,
                          play_win=p_play, play_expected=e_play, samples=used_samples, card_order=card_order)


def format_recommendation(rec):
    hand = rec.hand
    play_cards = cards_str([hand[i] for i in rec.play_idx])
    out = []
    if rec.action == "play":
        out.append(f"RECOMMENDATION: Play Hand {play_cards} — {rec.play.hand_type}, {rec.play.score:,} points")
        if rec.kickers:
            out.append(f"  kickers {cards_str([hand[i] for i in rec.kickers])}: don't score, but leave the hand "
                       f"and get redrawn (a free discard)")
    else:
        d = rec.discard
        out.append(f"RECOMMENDATION: Discard {cards_str(d.discarded)} ({d.label})")
        out.append(f"  Round: expected {d.expected:,.0f} points, win chance {d.p_win:.0%} "
                   f"({d.samples} simulations)")
    out.append(f"Why: {rec.reason}")
    if rec.need > 0:
        out.append(f"Still needed: {rec.need:,}")
    if rec.joker_order:
        js, r = rec.joker_order
        out.append(f"Reorder jokers: {' | '.join(map(str, js))} → {r.score:,} points")
    if rec.card_order:
        order, result = rec.card_order
        out.append(f"Reorder hand: {' | '.join(str(hand[i]) for i in order)} → {result.score:,} points")
    out.append("")
    out.append(f"Scoring of the best hand now ({play_cards}):")
    out += ["  " + line for line in rec.play.log]
    out.append(f"  = {rec.play.chips:g} x {rec.play.mult:g} = {rec.play.score:,}")
    if rec.alternatives:
        out.append("")
        out.append("Alternatives:")
        for idx, r in rec.alternatives:
            out.append(f"  {r.score:>10,}  {r.hand_type:<16} {cards_str([hand[i] for i in idx])}")
    if rec.discard_options:
        out.append("")
        out.append("Discard options (expected round points / round win chance):")
        for o in rec.discard_options[:6]:
            out.append(f"  {o.expected:>10,.0f}  {o.p_win:>4.0%}  {o.label:<26} out: {cards_str(o.discarded)}")
    return "\n".join(out)
