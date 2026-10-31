"""Joker registry.

Each joker is a JokerSpec with optional hooks. Hook signatures (j = the Joker
instance whose vars to use; for Blueprint/Brainstorm copies it is the *copied*
joker and ctx.copying is True -> never scale/mutate vars, never self-destruct):

  before(j, ctx)                 once, after the hand type is known, before any
                                 card scores (Ride the Bus, Green Joker, Space
                                 Joker, Midas Mask, Runner, Square Joker ...).
                                 Scaling done here counts for this hand.
  retrigger(j, ctx, card, area)  -> int extra triggers for `card`;
                                 area is "play" (scored) or "held".
  on_scored(j, ctx, card)        each trigger of each scoring card, after the
                                 card's own chips/mult/edition (Greedy Joker,
                                 Scholar, Photograph, Hiker ...).
  on_held(j, ctx, card)          each trigger of each card held in hand
                                 (Baron, Shoot the Moon, Raised Fist).
  main(j, ctx)                   the joker's own scoring step, jokers left to
                                 right (Joker +4 Mult, Cavendish x3 ...).
  on_other_joker(j, ctx, other)  after `other`'s main step (Baseball Card).

Use ctx.add_chips / ctx.add_mult / ctx.x_mult / ctx.add_money and
ctx.chance(numerator, denominator) -> probability (handles Oops! All 6s).
Random effects are scored as expected value.
Passive jokers (Four Fingers, Shortcut, Smeared Joker, Pareidolia, Splash,
Oops! All 6s, Blueprint, Brainstorm, Chicot) are implemented in the engine and
only need a data entry here.
"""
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class JokerSpec:
    name: str
    rarity: str          # Common Uncommon Rare Legendary
    cost: int
    desc: str
    defaults: dict = field(default_factory=dict)   # default vars (current scaling values)
    before: Callable | None = None
    retrigger: Callable | None = None
    on_scored: Callable | None = None
    on_held: Callable | None = None
    main: Callable | None = None
    on_other_joker: Callable | None = None
    blueprint_compat: bool = True
    notes: str = ""      # interactions / caveats worth knowing


REGISTRY: dict[str, JokerSpec] = {}


def register(spec):
    assert spec.name not in REGISTRY, f"duplicate joker {spec.name}"
    REGISTRY[spec.name] = spec
    return spec


def get(name):
    return REGISTRY.get(name)


from . import part_a, part_b  # noqa: E402,F401  (registers all jokers)
