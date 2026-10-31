"""Static game data for Balatro (vanilla 1.0.1)."""

# name: (base chips, base mult, chips per level, mult per level, planet)
# Ordered strongest first: the first contained hand is the played hand type.
HAND_TYPES = {
    "Flush Five":      (160, 16, 50, 3, "Eris"),
    "Flush House":     (140, 14, 40, 4, "Ceres"),
    "Five of a Kind":  (120, 12, 35, 3, "Planet X"),
    "Straight Flush":  (100, 8, 40, 4, "Neptune"),
    "Four of a Kind":  (60, 7, 30, 3, "Mars"),
    "Full House":      (40, 4, 25, 2, "Earth"),
    "Flush":           (35, 4, 15, 2, "Jupiter"),
    "Straight":        (30, 4, 30, 3, "Saturn"),
    "Three of a Kind": (30, 3, 20, 2, "Venus"),
    "Two Pair":        (20, 2, 20, 1, "Uranus"),
    "Pair":            (10, 2, 15, 1, "Mercury"),
    "High Card":       (5, 1, 10, 1, "Pluto"),
}
HAND_ORDER = list(HAND_TYPES)
PLANETS = {v[4]: k for k, v in HAND_TYPES.items()}


def hand_base(hand_type, level=1):
    c, m, dc, dm, _ = HAND_TYPES[hand_type]
    level = max(level, 1)
    return c + dc * (level - 1), m + dm * (level - 1)


RANK_NAMES = {2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
              10: "10", 11: "J", 12: "Q", 13: "K", 14: "A"}
SUIT_NAMES = {"S": "Spades", "H": "Hearts", "C": "Clubs", "D": "Diamonds"}

ENHANCEMENTS = {
    "bonus": "+30 chips",
    "mult": "+4 Mult",
    "wild": "Counts as every suit",
    "glass": "x2 Mult, 1 in 4 chance to break",
    "steel": "x1.5 Mult while held in hand",
    "stone": "+50 chips, no rank or suit, always scores",
    "gold": "$3 if held in hand at end of round",
    "lucky": "1 in 5: +20 Mult, 1 in 15: $20",
}
EDITIONS = {
    "foil": "+50 chips",
    "holo": "+10 Mult",
    "poly": "x1.5 Mult",
    "negative": "+1 joker slot (jokers) / +1 hand size (cards)",
}
SEALS = {
    "red": "Retrigger this card",
    "gold": "$3 when scored",
    "blue": "Creates the planet of the final played hand if held at end of round",
    "purple": "Creates a Tarot when discarded",
}

# Boss blinds: name -> effect. Scoring-relevant ones are handled in engine.py.
BOSSES = {
    "The Hook": "Discards 2 random held cards after each hand",
    "The Ox": "Playing your most played hand sets money to $0",
    "The House": "First hand is drawn face down",
    "The Wall": "Extra large blind (4x base)",
    "The Wheel": "1 in 7 cards drawn face down",
    "The Arm": "Decrease level of played hand",
    "The Club": "All Clubs are debuffed",
    "The Fish": "Cards drawn face down after each hand",
    "The Psychic": "Must play 5 cards",
    "The Goad": "All Spades are debuffed",
    "The Water": "Start with 0 discards",
    "The Window": "All Diamonds are debuffed",
    "The Manacle": "-1 hand size",
    "The Eye": "No repeat hand types this round",
    "The Mouth": "Play only 1 hand type this round",
    "The Plant": "All face cards are debuffed",
    "The Serpent": "Always draw 3 cards after play or discard",
    "The Pillar": "Cards played previously this ante are debuffed",
    "The Needle": "Play only 1 hand",
    "The Head": "All Hearts are debuffed",
    "The Tooth": "Lose $1 per card played",
    "The Flint": "Base chips and mult are halved",
    "The Mark": "All face cards are drawn face down",
    "Amber Acorn": "Flips and shuffles all jokers",
    "Verdant Leaf": "All cards debuffed until 1 joker sold",
    "Violet Vessel": "Very large blind (6x base)",
    "Crimson Heart": "One random joker disabled every hand",
    "Cerulean Bell": "Forces 1 card to always be selected",
}
SUIT_DEBUFF_BOSSES = {"The Club": "C", "The Goad": "S", "The Window": "D", "The Head": "H"}

# All 150 jokers in collection order. Effects live in balatro/jokers/.
JOKER_NAMES = [
    "Joker", "Greedy Joker", "Lusty Joker", "Wrathful Joker", "Gluttonous Joker",
    "Jolly Joker", "Zany Joker", "Mad Joker", "Crazy Joker", "Droll Joker",
    "Sly Joker", "Wily Joker", "Clever Joker", "Devious Joker", "Crafty Joker",
    "Half Joker", "Joker Stencil", "Four Fingers", "Mime", "Credit Card",
    "Ceremonial Dagger", "Banner", "Mystic Summit", "Marble Joker", "Loyalty Card",
    "8 Ball", "Misprint", "Dusk", "Raised Fist", "Chaos the Clown",
    "Fibonacci", "Steel Joker", "Scary Face", "Abstract Joker", "Delayed Gratification",
    "Hack", "Pareidolia", "Gros Michel", "Even Steven", "Odd Todd",
    "Scholar", "Business Card", "Supernova", "Ride the Bus", "Space Joker",
    "Egg", "Burglar", "Blackboard", "Runner", "Ice Cream",
    "DNA", "Splash", "Blue Joker", "Sixth Sense", "Constellation",
    "Hiker", "Faceless Joker", "Green Joker", "Superposition", "To Do List",
    "Cavendish", "Card Sharp", "Red Card", "Madness", "Square Joker",
    "Séance", "Riff-Raff", "Vampire", "Shortcut", "Hologram",
    "Vagabond", "Baron", "Cloud 9", "Rocket", "Obelisk",
    "Midas Mask", "Luchador", "Photograph", "Gift Card", "Turtle Bean",
    "Erosion", "Reserved Parking", "Mail-In Rebate", "To the Moon", "Hallucination",
    "Fortune Teller", "Juggler", "Drunkard", "Stone Joker", "Golden Joker",
    "Lucky Cat", "Baseball Card", "Bull", "Diet Cola", "Trading Card",
    "Flash Card", "Popcorn", "Spare Trousers", "Ancient Joker", "Ramen",
    "Walkie Talkie", "Seltzer", "Castle", "Smiley Face", "Campfire",
    "Golden Ticket", "Mr. Bones", "Acrobat", "Sock and Buskin", "Swashbuckler",
    "Troubadour", "Certificate", "Smeared Joker", "Throwback", "Hanging Chad",
    "Rough Gem", "Bloodstone", "Arrowhead", "Onyx Agate", "Glass Joker",
    "Showman", "Flower Pot", "Blueprint", "Wee Joker", "Merry Andy",
    "Oops! All 6s", "The Idol", "Seeing Double", "Matador", "Hit the Road",
    "The Duo", "The Trio", "The Family", "The Order", "The Tribe",
    "Stuntman", "Invisible Joker", "Brainstorm", "Satellite", "Shoot the Moon",
    "Driver's License", "Cartomancer", "Astronomer", "Burnt Joker", "Bootstraps",
    "Canio", "Triboulet", "Yorick", "Chicot", "Perkeo",
]
assert len(JOKER_NAMES) == 150 == len(set(JOKER_NAMES))
