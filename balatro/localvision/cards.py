"""Local recognition of the hand cards (rank + suit) from a normalized canvas.

Approach: corner-glyph template matching on an "ink" image (255 - min(B,G,R): white card face -> 0,
any coloured or dark glyph -> high), so the match does not depend on the suit colours.
1. A generic corner template (mean of all 52 sprite corners) finds the card scale and the top-left
   corner of every card; candidates must be mostly white and not sit inside another card's body.
2. Each corner: rank = best of 13 rank-glyph templates, suit = glyph colour, cross-checked with the
   4 suit-glyph shapes. conf drops on weak/ambiguous matches, colour/shape disagreement, non-white
   card faces (enhancement/edition/debuff) and when the row of cards has a gap or is off-centre
   (a card without a readable corner glyph: stone, face down, tooltip, mid-animation).
"""
from functools import lru_cache

import cv2
import numpy as np

from .layout import ASSETS

CACHE = ASSETS / "cards" / "corners.npz"
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUITS = "SHCD"
SPRITE_PX = 12                          # wiki sprites: 1 game pixel = 12x12 sprite pixels
CORNER = (64, 324, 54, 194)             # sprite rows/cols of the corner incl. white margin (no border)
RANK_BOX = (84, 204, 72, 152)           # sprite rows/cols of the rank glyph (+1 game px margin)
SUIT_BOX = (204, 268, 72, 152)          # ... and of the small suit glyph below it
SCALES = [round(s, 2) for s in np.arange(1.3, 2.21, 0.1)]  # canvas px per game px (~1.65 at 1280 wide)
STRIP = (0.2, 0.4, 0.9, 0.9)            # hand search area (fractions of the canvas)
HAND_CENTER = 0.545                     # x of the hand's centre (fraction of canvas width), measured
HAND_SPAN = 0.482                       # first to last card left edge for >= 7 cards (fraction of width)
HAND_GAP_MAX = 0.083                    # spacing cap for few cards; ponytail: measured on one 3-card frame
# Arcana/Spectral packs deal the hand ABOVE the offered cards, spread wider (ponytail: one 6-card Jumbo Arcana frame)
PACK_STRIP = (0.2, 0.3, 0.9, 0.6)
PACK_GAP_MAX = 0.1
SCALE_STEPS = (-0.1, 0, 0.1)            # per-card scale tried around the frame scale
MIN_CORNER = 0.6                        # generic corner NCC needed to look closer
MIN_RANK = 0.80                         # rank-glyph NCC below this = not a corner glyph
# BGR of glyph ink in the high-contrast deck as seen through the stream (median over the dataset)
SUIT_BGR = {"S": (77, 77, 65), "H": (46, 37, 179), "C": (189, 140, 67), "D": (56, 130, 183)}


def debuff_stripes(img):
    """Pixels of the salmon-red X drawn over debuffed cards (BGR ~ (110, 100, 215)); heart glyphs are a darker
    red (G ~ 35), diamond glyphs orange (B ~ 55)."""
    b, g, r = (img[..., i].astype(np.int16) for i in range(3))
    m = ((r > 180) & (g > 70) & (g < 160) & (b > 70) & (b < 170) & (r - g > 60)).astype(np.uint8)
    # the X is two long bands; anti-aliased edges of red glyphs have the same colour but are small
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    keep = np.zeros(n, bool)
    keep[1:] = np.maximum(st[1:, cv2.CC_STAT_WIDTH], st[1:, cv2.CC_STAT_HEIGHT]) >= 40
    return keep[lab]


def _ink(img, blur=True, stripes=None):
    ink = (255 - img.min(axis=2)).astype(np.float32)
    ink[debuff_stripes(img) if stripes is None else stripes] = 0  # read the card under the X like a plain card
    return cv2.GaussianBlur(ink, (3, 3), 0) if blur else ink


@lru_cache(maxsize=1)
def _sprites():
    """key -> sprite crop CORNER (the top-left corner is all we need). Decoding the 52 full PNGs
    takes ~0.45 s, so the crops are cached in CACHE next to the sprites."""
    try:
        with np.load(CACHE) as z:
            return {k: z[k] for k in z.files}
    except (OSError, ValueError):
        pass
    out = {}
    for r in RANKS:
        for s in SUITS:
            im = cv2.imread(str(ASSETS / "cards" / f"{r}{s}.png"), cv2.IMREAD_UNCHANGED)
            if im is None:
                raise FileNotFoundError(ASSETS / "cards" / f"{r}{s}.png")
            out[r + s] = np.ascontiguousarray(im[CORNER[0]:CORNER[1], CORNER[2]:CORNER[3], :3])
    try:
        np.savez(CACHE, **out)
    except OSError:
        pass
    return out


def _cut(im, box, scale, blur=True):
    y0, y1, x0, x1 = (v - CORNER[i // 2 * 2] for i, v in enumerate(box))  # sprites are cut to CORNER
    c = im[y0:y1, x0:x1]
    size = (round(c.shape[1] / SPRITE_PX * scale), round(c.shape[0] / SPRITE_PX * scale))
    return _ink(cv2.resize(c, size, interpolation=cv2.INTER_AREA), blur)


@lru_cache(maxsize=32)
def _generic(scale):
    """Mean of all 52 corners at `scale` canvas px per game px."""
    return np.mean([_cut(im, CORNER, scale) for im in _sprites().values()], axis=0).astype(np.float32)


@lru_cache(maxsize=32)
def _templates(scale):
    """{'*': generic corner, 'R'/'r': {rank: blurred/sharp tpl}, 'S': {suit: tpl}} at `scale`."""
    sp = _sprites()
    return {"*": _generic(scale),
            "R": {r: _cut(sp[r + "S"], RANK_BOX, scale) for r in RANKS},
            "r": {r: _cut(sp[r + "S"], RANK_BOX, scale, False) for r in RANKS},  # sharp: separates ranks better
            "S": {s: _cut(sp["A" + s], SUIT_BOX, scale, False) for s in SUITS}}


def _offset(box, scale):
    return round((box[0] - CORNER[0]) / SPRITE_PX * scale), round((box[2] - CORNER[2]) / SPRITE_PX * scale)


def _match(ink, tpl, y, x, pad=2):
    """Best NCC of tpl around top-left (y, x) +-pad."""
    h, w = tpl.shape
    y0, x0 = max(0, y - pad), max(0, x - pad)
    win = ink[y0:y + h + pad, x0:x + w + pad]
    if win.shape[0] < h or win.shape[1] < w:
        return -1.0
    return float(cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED).max())


def _suit_from_colour(px):
    """px: Nx3 BGR glyph pixels -> (suit, distance ratio best/second; small = unambiguous)."""
    if len(px) < 5:
        return None, 1.0
    m = np.median(px, axis=0)
    d = {s: np.linalg.norm(m - np.array(c)) for s, c in SUIT_BGR.items()}
    a, b = sorted(d.values())[:2]
    return min(d, key=d.get), a / max(b, 1e-6)


def _corner_candidates(ink):
    """[(x, y, score)], scale — generic-template peaks with 2-D non-maximum suppression. The frame scale is
    the one with the best three peaks: a single strong blob (white patch, tooltip) must not pick it."""
    best = None
    for s in SCALES:
        T = _generic(s)
        r = cv2.matchTemplate(ink, T, cv2.TM_CCOEFF_NORMED)
        # a corner is mostly white card face: drop positions whose mean ink is far above the template's
        mean = cv2.boxFilter(ink, -1, T.shape[::-1], anchor=(0, 0), normalize=True, borderType=cv2.BORDER_CONSTANT)
        r[mean[:r.shape[0], :r.shape[1]] > T.mean() * 1.6 + 15] = 0
        out = _peaks(r, *T.shape)
        total = sum(sorted((m for _, _, m in out), reverse=True)[:3]) / 3  # top 3: a hand has >= 3 corners
        if best is None or total > best[0]:
            best = total, out, s
    return best[1], best[2]


def _peaks(r, th, tw):
    out = []
    while True:
        _, m, _, (x, y) = cv2.minMaxLoc(r)
        if m < MIN_CORNER:
            return out
        out.append((x, y, m))
        r[max(0, y - th // 2):y + th // 2 + 1, max(0, x - int(tw * 0.8)):x + int(tw * 0.8) + 1] = -1


def _read_corner(ink, sharp, img, x, y, scale, stripes):
    """Classify the corner whose generic-template top-left is (x, y). None if it is no corner glyph."""
    th, tw = _generic(scale).shape
    # glyph size varies a little per card (the frame scale is on a 0.1 grid; CRT curvature): try neighbouring
    # scales. (The fan rotation is only ~1-2 deg; de-rotating the corner made matches worse on the dataset.)
    best = None
    for sc in (round(scale + d, 2) for d in SCALE_STEPS):
        Ts = _templates(sc)
        oy, ox = _offset(RANK_BOX, sc)
        # blurred match = "is this a corner glyph at all", sharp match = "which rank"
        rs = {r: _match(sharp, t, y + oy, x + ox) for r, t in Ts["r"].items()}
        ranked = sorted(rs, key=rs.get, reverse=True)
        rb = _match(ink, Ts["R"][ranked[0]], y + oy, x + ox)
        if best is None or rb > best[0]:
            best = rb, sc, ranked, rs
    rb, sc, ranked, rs = best
    rank, r1, r2 = ranked[0], rs[ranked[0]], rs[ranked[1]]
    if rb < MIN_RANK:
        return None
    T = _templates(sc)
    sy, sx = _offset(SUIT_BOX, sc)
    ss = {s: _match(sharp, t, y + sy, x + sx) for s, t in T["S"].items()}
    shape_suit = max(ss, key=ss.get)
    patch, pink = img[y:y + th, x:x + tw], ink[y:y + th, x:x + tw]
    csuit, cratio = _suit_from_colour(patch[pink > 120])
    suit = csuit or shape_suit
    # correct ranks on the dataset: blurred NCC >= 0.845 (junk <= 0.76), sharp margin to runner-up >= 0.09
    conf = min(1.0, (rb - MIN_RANK) / 0.06) * float(np.clip((r1 - r2 - 0.03) / 0.07, 0, 1))
    if cratio > 0.6:  # colour ambiguous (odd lighting/edition): trust it less, more so if the shape disagrees
        conf *= 0.5 if csuit == shape_suit or ss[shape_suit] - ss.get(csuit, -1) < 0.05 else 0.2
    # plain card face is near-white & unsaturated; anything else = enhancement/edition -> unsure.
    # The X of a debuffed card (boss blinds) is recognised by its colour and doesn't count as face.
    stripes = stripes[y:y + th, x:x + tw]
    debuff = bool(stripes.mean() > 0.03)
    face = patch[(pink < 40) & ~stripes]
    fm = np.median(face, axis=0) if len(face) > 20 else np.zeros(3)
    if not debuff and (fm.min() < 170 or fm.max() - fm.min() > 45):  # a debuffed card is tinted all over
        conf = min(conf, 0.4)
    return {"rank": rank, "suit": suit, "enhancement": None, "edition": None, "seal": None,
            "debuff": debuff, "face_down": False, "conf": round(max(0.0, conf), 3),
            "_dbg": {"xy": (x, y), "scale": sc, "rank": (round(rb, 3), round(r1, 3), ranked[1], round(r2, 3)),
                     "suit": (shape_suit, {k: round(v, 2) for k, v in ss.items()}, csuit, round(cratio, 2)),
                     "face": [int(v) for v in fm]}}


def _read_tilted(ink, sharp, img, x, y, scale, stripes, corner, angles=(-6, -3, 3, 6)):
    """_read_corner, and for an unsure or unreadable but clearly corner-shaped spot (generic match `corner`)
    also de-rotated: the outer cards of a big hand (9+ cards) are tilted more than the templates tolerate.
    Weaker spots are often pips of a card body, which turned (wrongly) readable when rotated."""
    best = _read_corner(ink, sharp, img, x, y, scale, stripes)
    if (best and best["conf"] >= 0.6) or corner < 0.9:
        return best
    th, tw = _generic(scale).shape
    for a in angles:
        M = cv2.getRotationMatrix2D((x + tw / 2, y + th / 2), a, 1)

        def rot(im, interp=cv2.INTER_LINEAR):
            return cv2.warpAffine(im, M, im.shape[1::-1], flags=interp, borderMode=cv2.BORDER_REPLICATE)
        r = _read_corner(rot(ink), rot(sharp), rot(img), x, y, scale,
                         rot(stripes.astype(np.uint8), cv2.INTER_NEAREST).astype(bool))
        if r and (best is None or r["conf"] > best["conf"]):
            best = r
    return best


DECK = (1088, 665, 1185, 790)  # the draw pile (bottom right): shows the current deck's card back


def hand_size(canvas):
    """N of the "n/N" counter under the hand (the first number reads unreliably), or None."""
    import re
    from .booster import ocr
    m = re.search(r"/\s*(\d+)", ocr(canvas[701:724, 666:733]))
    return int(m.group(1)) if m else None


def _add_face_down(canvas, cards, X0, Y0, scale):
    """Face-down cards (The Fish/House/Wheel/Mark) have no corner glyph. The "/N" counter gives the slot count,
    the fan layout the slot positions; a slot showing the deck's back colour holds a face-down card. It is
    returned as an unknown card: a debuffed Stone card (scores nothing, forms no hand, can be discarded)."""
    H, W = canvas.shape[:2]
    n = hand_size(canvas)
    if not n or n <= len(cards) or n > 15:
        return cards
    x0, y0, x1, y1 = DECK
    back = canvas[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32).mean(0)
    gap = min(HAND_GAP_MAX, HAND_SPAN / (n - 1)) * W
    left0 = HAND_CENTER * W - ((n - 1) * gap + 69 * scale) / 2
    taken = [X0 + c["_dbg"]["xy"][0] - 2.5 * scale for c in cards]
    ys = [c["_dbg"]["xy"][1] for c in cards]
    out = list(cards)
    for i in range(n):
        left = left0 + i * gap
        if any(abs(t - left) < gap / 2 for t in taken):
            continue
        col = canvas[int(0.5 * H):int(0.8 * H), int(left + 15):int(left + gap - 10)].astype(np.float32)
        rows = np.flatnonzero(np.linalg.norm(col.mean(1) - back, axis=1) < 40)
        if len(rows) < 0.1 * H:
            continue
        top = int(0.5 * H) + rows[0]  # card top edge -> corner row like a readable card's
        y = top + 8 - Y0 if not ys else min(max(top + 8 - Y0, min(ys) - 60), max(ys) + 60)
        out.append({"rank": "2", "suit": "S", "enhancement": "stone", "edition": None, "seal": None,
                    "debuff": True, "face_down": True, "conf": 1.0,
                    "_dbg": {"xy": (int(left + 2.5 * scale - X0), int(y)), "scale": scale, "rank": (1.0, 1.0, "", 0.0),
                             "suit": ("", {}, None, 0.0), "face": [0, 0, 0]}})
    return sorted(out, key=lambda c: c["_dbg"]["xy"][0])


def read_pack_hand(canvas):
    """The hand dealt with an opened Arcana/Spectral pack (the targets of its cards)."""
    return read_hand(canvas, strip=PACK_STRIP, gap_max=PACK_GAP_MAX)


def read_hand(canvas, debug=False, strip=STRIP, gap_max=HAND_GAP_MAX):
    """Hand cards left to right (see module docstring); [] if no hand is visible."""
    if not debug:
        from .telemetry import decode_strip
        tel = decode_strip(canvas)
        if tel is not None:
            return tel[0].get("hand", [])
    H, W = canvas.shape[:2]
    X0, Y0, X1, Y1 = int(strip[0] * W), int(strip[1] * H), int(strip[2] * W), int(strip[3] * H)
    img = canvas[Y0:Y1, X0:X1]
    stripes = debuff_stripes(img)
    ink, sharp = _ink(img, stripes=stripes), _ink(img, False, stripes)
    cands, scale = _corner_candidates(ink)
    cards = [c for x, y, m in cands if (c := _read_tilted(ink, sharp, img, x, y, scale, stripes, m))]
    # a big pip in a card's body can look like a corner glyph: drop candidates lying below a stronger corner
    kept = []
    for c in sorted(cards, key=lambda c: -c["_dbg"]["rank"][0]):
        x, y = c["_dbg"]["xy"]
        # ... or right next to one (a pip of the card body in the same row; corners are >= 35 px apart)
        if not any(abs(x - k[0]) < 20 * scale and 30 * scale < y - k[1] < 95 * scale
                   or abs(x - k[0]) < 22 * scale and abs(y - k[1]) <= 30 * scale
                   for k in (d["_dbg"]["xy"] for d in kept)):
            kept.append(c)
    cards = kept
    if not cards:
        return []
    # hand = the fullest row of corners, lowest on ties (played cards sit ~1.5 card heights higher, selected
    # ones are raised a bit; a stray match inside a card body must not count as a row of its own)
    cards.sort(key=lambda c: c["_dbg"]["xy"][1])
    rows = [[cards[0]]]
    for c in cards[1:]:
        if c["_dbg"]["xy"][1] - rows[-1][-1]["_dbg"]["xy"][1] > 0.6 * 93 * scale:  # 0.6 card heights
            rows.append([])
        rows[-1].append(c)
    row = max(reversed(rows), key=len)
    cards = sorted(row, key=lambda c: c["_dbg"]["xy"][0])

    # completeness: the hand is centred and evenly spaced, spacing given by the card count. A card without a
    # readable corner (stone, face down, covered by a tooltip) leaves a gap, shifts the centre or makes the
    # spacing wrong for the count -> whole hand unsure. Face-down cards are found first (only then: OCR).
    def layout(cards):
        lefts = [X0 + c["_dbg"]["xy"][0] - 2.5 * scale for c in cards]
        centre = (lefts[0] + lefts[-1] + 69 * scale) / 2 / W
        gaps = np.diff(lefts) / W
        want = min(gap_max, HAND_SPAN / max(1, len(cards) - 1))
        return lefts, centre, abs(centre - HAND_CENTER) < 0.015 and (
            not len(gaps) or (abs(np.median(gaps) - want) < 0.1 * want and gaps.max() < 1.4 * want))
    lefts, centre, complete = layout(cards)
    if not complete:
        cards = _add_face_down(canvas, cards, X0, Y0, scale)
        lefts, centre, complete = layout(cards)
    for c, left in zip(cards, lefts):
        # card centre (fractions of the canvas) for clicking; the corner glyph sits ~40 px below the card top
        c["pos"] = [round((left + 34.5 * scale) / W, 4), round((Y0 + c["_dbg"]["xy"][1] + 40 * scale) / H, 4)]
        if not complete:
            c["conf"] = round(c["conf"] * 0.3, 3)
        d = c.pop("_dbg")
        if debug:
            c["debug"] = d | {"corner": (X0 + d["xy"][0], Y0 + d["xy"][1]), "centre": round(centre, 4),
                              "complete": bool(complete)}
    return cards
