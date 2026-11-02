"""Game state: cards, jokers, and the snapshot the engine scores against."""
from dataclasses import dataclass, field, fields, replace

from .data import RANK_NAMES, SUIT_NAMES

_RANK_PARSE = {v: k for k, v in RANK_NAMES.items()} | {"T": 10, "1": 14}
_SUIT_PARSE = {"S": "S", "H": "H", "C": "C", "D": "D"} | {v.upper(): k for k, v in SUIT_NAMES.items()}


@dataclass
class Card:
    rank: int                       # 2..14 (Ace = 14)
    suit: str                       # S H C D
    enhancement: str | None = None  # bonus mult wild glass steel stone gold lucky
    edition: str | None = None      # foil holo poly negative
    seal: str | None = None         # red gold blue purple
    debuff: bool = False
    bonus: int = 0                  # permanent extra chips (Hiker)
    face_down: bool = False
    forced: bool = False            # Cerulean Bell: must be played
    played_this_ante: bool = False

    @property
    def stone(self):
        return self.enhancement == "stone"

    def base_chips(self):
        if self.stone:
            return 0
        return 11 if self.rank == 14 else min(self.rank, 10)

    def chip_bonus(self):
        """Chips this card adds when scored (rank + enhancement + permanent bonus)."""
        return self.base_chips() + {"bonus": 30, "stone": 50}.get(self.enhancement, 0) + self.bonus

    def is_suit(self, suit, flags=frozenset(), flush=False, bypass_debuff=False):
        """Mirror of Card:is_suit. `flags` = active passive-joker names (Smeared Joker)."""
        if self.stone or (self.debuff and not flush and not bypass_debuff):
            return False
        if self.enhancement == "wild" and not self.debuff:
            return True
        if "Smeared Joker" in flags:
            red = {"H", "D"}
            return (suit in red) == (self.suit in red)
        return self.suit == suit

    def is_face(self, flags=frozenset()):
        if self.debuff:
            return False
        if "Pareidolia" in flags:  # vanilla: true even for Stone cards
            return True
        return not self.stone and self.rank in (11, 12, 13)

    def __str__(self):
        s = "Stone" if self.stone else RANK_NAMES[self.rank] + self.suit
        extras = [x for x in (self.enhancement if not self.stone else None, self.edition,
                              self.seal and self.seal + " seal", "debuffed" if self.debuff else None) if x]
        return s + (f"[{','.join(extras)}]" if extras else "")

    @classmethod
    def parse(cls, obj):
        """Accepts 'KH', '10s', 'Ad' or a dict {rank, suit, enhancement, edition, seal, ...}."""
        if isinstance(obj, Card):
            return obj
        if isinstance(obj, str):
            obj = {"rank": obj[:-1], "suit": obj[-1]}
        d = dict(obj)
        r = str(d.pop("rank")).upper()
        d["rank"] = _RANK_PARSE[r] if r in _RANK_PARSE else int(r)
        s = str(d.pop("suit", "S")).upper()
        d["suit"] = _SUIT_PARSE.get(s, s[:1])
        for k in ("enhancement", "edition", "seal"):
            if d.get(k):
                d[k] = str(d[k]).lower().replace("polychrome", "poly").replace("holographic", "holo")
                d[k] = d[k].removesuffix(" card").removesuffix(" seal")
        names = {f.name for f in fields(cls)}  # recogniser extras (e.g. screen position) aren't card data
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class Joker:
    name: str
    edition: str | None = None       # foil holo poly negative
    vars: dict = field(default_factory=dict)  # current scaling values, e.g. {"mult": 12}
    debuff: bool = False
    sell_value: int | None = None
    eternal: bool = False
    rental: bool = False             # Gold Stake sticker: costs $3 per round
    perish: int | None = None        # Perishable sticker: rounds used (debuffed after 5)

    def __str__(self):
        extra = ",".join(filter(None, [self.edition, "debuffed" if self.debuff else None]
                                + [f"{k}={v}" for k, v in self.vars.items()]))
        return self.name + (f"[{extra}]" if extra else "")

    @classmethod
    def parse(cls, obj):
        if isinstance(obj, Joker):
            return obj
        if isinstance(obj, str):
            return cls(obj)
        d = dict(obj)
        if d.get("edition"):
            d["edition"] = d["edition"].lower().replace("polychrome", "poly").replace("holographic", "holo")
        names = {f.name for f in fields(cls)}  # recogniser extras (screen position) aren't joker data
        return cls(**{k: v for k, v in d.items() if k in names})


def standard_deck():
    return [Card(r, s) for s in "SHCD" for r in range(2, 15)]


@dataclass
class State:
    hand: list                                   # cards in hand, left to right
    jokers: list = field(default_factory=list)  # left to right
    hand_levels: dict = field(default_factory=dict)   # {"Flush": 3}; missing = level 1
    deck: list | None = None        # remaining draw pile; None = full_deck minus hand
    full_deck: list | None = None   # whole deck incl. hand/discards; None = standard 52
    hands_left: int = 4
    discards_left: int = 3
    hand_size: int = 8
    blind: str | None = None        # e.g. "The Flint", "Small Blind"
    blind_chips: int = 0            # score required
    score: int = 0                  # score already made this round
    money: int = 0
    deck_type: str | None = None    # "Plasma Deck" changes scoring
    vouchers: list = field(default_factory=list)
    consumables: list = field(default_factory=list)  # names, e.g. "Pluto"
    joker_slots: int = 5
    played_counts: dict = field(default_factory=dict)  # hand type -> times played this run
    round_hand_types: list = field(default_factory=list)  # hand types played this round
    boss_disabled: bool = False
    last_tarot_planet: str | None = None  # The Fool copies it (telemetry)
    upcoming_boss: str | None = None  # this ante's boss, known in the shop (telemetry)
    ox_hand: str | None = None  # The Ox's hand, fixed at the end of the last boss round (telemetry)
    seed: str | None = None
    stake: int | None = None
    round_hands: int | None = None
    round_discards: int | None = None
    discards_used: int | None = None
    telemetry: bool = False

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["hand"] = [Card.parse(c) for c in d.get("hand", [])]
        d["jokers"] = [Joker.parse(j) for j in d.get("jokers", [])]
        for k in ("deck", "full_deck"):
            if d.get(k) is not None:
                d[k] = [Card.parse(c) for c in d[k]]
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in known})

    def level(self, hand_type):
        return self.hand_levels.get(hand_type, 1)

    def get_full_deck(self):
        return self.full_deck if self.full_deck is not None else standard_deck()

    def get_deck(self):
        """Remaining draw pile. Unknown -> full deck minus cards in hand (by rank/suit)."""
        if self.deck is not None:
            return self.deck
        rest = list(self.get_full_deck())
        for c in self.hand:
            for i, d in enumerate(rest):
                if (d.rank, d.suit) == (c.rank, c.suit):
                    del rest[i]
                    break
        return rest

    def copy(self):
        return replace(self, jokers=[replace(j, vars=dict(j.vars)) for j in self.jokers])
