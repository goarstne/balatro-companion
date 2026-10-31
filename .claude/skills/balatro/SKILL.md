---
name: balatro
description: Analyses a Balatro screenshot or game state and recommends the best move (play/discard, joker order). Use when the user shows a Balatro screenshot, asks for the best move in Balatro, or says "what should I play", "what should I discard", "which hand" or similar.
---

# Balatro: screenshot -> recommendation

Workflow (no API key needed – you read the image yourself):

1. **Read the screenshot** with the Read tool.
2. **Write the state as JSON** to `state.json` in the project folder (schema below).
3. **Evaluate:** `.venv/bin/python -m balatro analyze state.json`
   (check specific cards: `.venv/bin/python -m balatro score state.json 0 2 4`,
   look up a joker: `.venv/bin/python -m balatro joker "<name>"`).
4. **Answer in the user's language:** the recommendation (which cards to play or discard, joker reordering if any),
   expected vs. required points, a short reason. Name cards as they lie in the game ("Ace of Hearts, King of
   Hearts …") and mention any assumptions you had to make.

## Screen layout

- **Left:** blind name (coloured banner, e.g. "Big Blind", "The Flint" + effect text), "Score at least N"
  (= `blind_chips`), "Round score" (= `score`), Hands (blue, `hands_left`), Discards (red, `discards_left`),
  `$N` (`money`), Ante/Round.
- **Top:** jokers left to right, below them a counter "4/5" (= count / `joker_slots`, incl. negative slots).
  Top right: consumables (tarot/planet/spectral), counter "0/2".
- **Bottom:** hand cards left to right, counter "8/8" (= cards / `hand_size`). Raised (selected) cards belong to
  the hand. Cards in the middle of the table are being played – don't count them as hand cards.
- **Enhancements:** Bonus = pale blue (+30 chips), Mult = reddish (+4 mult), Wild = multicoloured,
  Glass = translucent, Steel = grey metal, Stone = grey stone slab without rank/suit, Gold = golden,
  Lucky = clover pattern. **Editions:** Foil = silver-blue sheen, Holographic = rainbow stripes,
  Polychrome = strong rainbow colours, Negative = inverted/dark. **Seals:** small coloured dot
  (red/gold/blue/purple). Debuffed cards: red X / greyed out. Face-down cards show the back (`"face_down": true`).

## Schema (`State.from_dict`, balatro/models.py)

Cards: short form `"KH"`, `"10S"`, `"AD"` (suits S H C D) or an object
`{"rank": "Q", "suit": "D", "enhancement": "glass", "edition": "foil", "seal": "red", "debuff": true, "face_down": true}`.
Values: enhancement ∈ bonus mult wild glass steel stone gold lucky; edition ∈ foil holo poly negative;
seal ∈ red gold blue purple. Only give what applies.

Jokers: `{"name": "Blueprint", "edition": "negative", "vars": {"mult": 12}}` – name exactly as in
`balatro/data.py` `JOKER_NAMES`; `.venv/bin/python -m balatro jokers` lists them all.

```json
{
  "hand": ["AH", "KH", "9H", "7H", "4H", "QS", {"rank": "Q", "suit": "C", "seal": "red"}, "2D"],
  "jokers": [{"name": "Blueprint"}, {"name": "Joker", "edition": "foil"}, {"name": "Green Joker", "vars": {"mult": 6}}],
  "joker_slots": 5,
  "hand_levels": {"Flush": 3},
  "hands_left": 3, "discards_left": 2, "hand_size": 8,
  "blind": "Big Blind", "blind_chips": 4500, "score": 1200,
  "money": 14, "consumables": ["Jupiter"], "vouchers": [], "deck_type": null
}
```

More optional fields: `deck` / `full_deck` (card lists; default = 52-card deck minus the hand),
`played_counts`, `round_hand_types` (for The Eye / The Mouth), `boss_disabled`. More examples: `examples/*.json`.

## What the screenshot doesn't show

- **Current values of scaling jokers** (Ride the Bus, Green Joker, Ice Cream, Runner, Constellation, Obelisk,
  Hologram, Lucky Cat …) are only in the tooltip. If the value can change the recommendation, **ask the user**
  (or for a screenshot with the tooltip); otherwise the engine uses defaults – then say explicitly that defaults
  were used.
- **Hand levels** are only visible for the currently selected hand ("lvl.3") or under Run Info → Poker Hands.
  Unknown = level 1; ask in planet-heavy runs.
- **Deck contents** (for discard odds) are unknown → a standard deck is assumed.
- For uncertainly recognised cards/jokers: put your best guess in the JSON and mention it in the answer.
