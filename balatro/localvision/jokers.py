"""Local recognition of jokers, consumables and shop items (no AI).

1. find_cards: cards are blobs of strong edges (the card outline survives the stream's CRT shader and
   compression; in the shop: bright blobs on the dark panel); holes are filled, glued-on price tags /
   counter text are cut off, minAreaRect gives the box. Touching/overlapping jokers (5+) are placed with a
   row model (_row_quads); unexplained card-high blobs become low-confidence unknowns.
2. match: the box (plus margin) is scaled so the card is TH pixels high and every sprite of the allowed kinds
   is slid over it (cv2.matchTemplate, normalised correlation of blurred grayscale) - a coarse pass at one
   scale picks the top candidates, a fine pass searches scale/rotation. A colour term (Lab a/b distance at
   the best alignment) separates jokers that share their art (Wily/Devious/Clever ... Joker).
   Negative jokers are drawn by a shader (dark blue, light lines, swirls): they are detected by colour and
   matched on the high-passed, inverted grayscale only - several jokers share the jester art, so a
   negative's name is usually ambiguous and its conf stays low.
3. conf (_conf) = match quality x margin to the best other name; colour mismatch (foil/holo/poly, which are
   not identified) caps it. Prices: '$N' tag glyphs vs. the HUD digit templates (+ tag-font extras).
Booster packs: sprites via download_packs() (python -m balatro.localvision.jokers). Opened packs: not read.
"""
from functools import lru_cache

import cv2
import numpy as np

from .layout import ASSETS

TH = 76  # card height (px) during matching
MARGIN = 1.2  # search window around the detected box
SCALES = (0.86, 0.9, 0.94, 0.98, 1.02)  # template height / box height (the box includes the card's glow)
ROTS = (-4, 0, 4)
TOPK = 8
CARD_AR = 142 / 190
CARD_H = 152  # box height of a full-size joker in the 1280 px canvas (ponytail: fixed layout scale)
PACK_TYPES = ["Arcana", "Celestial", "Spectral", "Standard", "Buffoon"]
PACK_SIZES = {"Normal": "", "Jumbo": "Jumbo ", "Mega": "Mega "}
DIRS = {"joker": "jokers", "planet": "planets", "tarot": "tarots", "spectral": "spectrals", "voucher": "vouchers",
        "booster_pack": "packs"}


# ---------------------------------------------------------------- features

def _gray(bgr):
    return cv2.GaussianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32), (3, 3), 0)


def _highpass(g):
    return g - cv2.GaussianBlur(g, (0, 0), 3)


def _ab(bgr):
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)[..., 1:].astype(np.float32)


def _sprite_bgr(path):
    """Opaque part of a sprite (BGR) and its height relative to a full card of the same file."""
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim != 3:
        return None, 1.0
    full = img.shape[0]
    if img.shape[2] == 4:  # crop to the opaque card (Half/Square/Wee Joker are smaller), corners onto grey
        ys, xs = np.nonzero(img[..., 3] > 128)
        if len(ys):
            img = img[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        a = img[..., 3:4].astype(np.float32) / 255
        img = (img[..., :3] * a + 90 * (1 - a)).astype(np.uint8)
    return img, img.shape[0] / (full * 186 / 190)


class _Templates(dict):
    """scale -> (gray, highpass(gray), Lab a/b) of one sprite, built on first use."""

    def __init__(self, img):
        super().__init__()
        self.img = cv2.resize(img, (round(img.shape[1] * 2 * TH / img.shape[0]), 2 * TH), interpolation=cv2.INTER_AREA)

    def __missing__(self, s):
        h, w = self.img.shape[:2]
        th = round(TH * s)
        small = cv2.resize(self.img, (max(1, round(w * th / h)), th), interpolation=cv2.INTER_AREA)
        g = _gray(small)
        self[s] = (g, _highpass(g), _ab(small))
        return self[s]


@lru_cache(maxsize=None)
def _library(kind):
    """kind -> list of sprite entries {kind, name, t: _Templates, rel: height relative to a full card}."""
    lib = []
    for p in sorted((ASSETS / DIRS[kind]).glob("*.png")):
        img, rel = _sprite_bgr(p)
        if img is not None:
            lib.append({"kind": kind, "name": p.stem.split("_")[0], "t": _Templates(img), "rel": min(rel, 1.0)})
    return lib


def assets_ok():
    return len(_library("joker")) > 100


# ---------------------------------------------------------------- card detection

def _grad(ch):
    return np.hypot(cv2.Sobel(ch, cv2.CV_32F, 1, 0), cv2.Sobel(ch, cv2.CV_32F, 0, 1))


def _edge_map(img, colour=False):
    if colour:  # strongest colour-channel edge: a red card on the green felt has almost no grey edge
        f = img.astype(np.float32)
        m = np.max([_grad(f[..., i]) for i in range(3)], axis=0)
    else:
        m = _grad(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32))
    return cv2.boxFilter(m, -1, (5, 5))


def _longest_run(widths, frac=0.85):
    """[start, end) of the longest run of entries >= frac * max (rows/columns that span the whole card)."""
    full = widths >= frac * widths.max()
    edges = np.flatnonzero(np.diff(np.r_[0, full.astype(np.int8), 0]))
    return max(zip(edges[::2], edges[1::2]), key=lambda r: r[1] - r[0])


def find_cards(canvas, region, card_h=(120, 185), wide=None, masks=None, dark_bg=False):
    """Card quads in region (x0, y0, x1, y1 in canvas pixels) -> list of (cx, cy, w, h, angle) sorted by x.

    Card-high blobs that are no single card (merged/overlapping cards, odd shapes) go to `wide` as (x, y, w, h)."""
    x0, y0, x1, y1 = region
    sub = canvas[y0:y1, x0:x1]
    if dark_bg:  # shop: the panels are dark grey, every item for sale is bright somewhere on its face
        return _find(sub, (sub.max(2) > 125).astype(np.uint8) * 255, region, card_h, wide, masks)
    out = _find(sub, (_edge_map(sub) > 45).astype(np.uint8) * 255, region, card_h, wide, masks)
    # cards with hardly any grey contrast to the felt (red Gift Card) only show up in colour edges; they are
    # added where the grey pass found nothing (colour edges alone merge neighbours of a packed row)
    taken = [(q[0], q[2]) for q in out] + [(b[0] + b[2] / 2, b[2]) for b in (wide or [])]
    for q in _find(sub, (_edge_map(sub, True) > 45).astype(np.uint8) * 255, region, card_h, None, None):
        if all(abs(q[0] - cx) > 0.5 * (q[2] + w) for cx, w in taken):
            out.append(q)
    return sorted(out)


def _find(sub, mask, region, card_h, wide, masks):
    x0, y0 = region[:2]
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    # fill the card interiors (only card-sized holes; background swirls can enclose big ones)
    n, lab, st, _ = cv2.connectedComponentsWithStats(255 - mask, connectivity=4)
    hole = (st[:, cv2.CC_STAT_WIDTH] <= 0.85 * card_h[1]) & (st[:, cv2.CC_STAT_HEIGHT] <= card_h[1])
    hole &= (st[:, 0] > 0) & (st[:, 1] > 0) & (st[:, 0] + st[:, 2] < mask.shape[1]) & (st[:, 1] + st[:, 3] < mask.shape[0])
    mask = np.where(hole[lab], 255, mask).astype(np.uint8)
    # cut off thin attachments (counter text, tray lines, price tags) with an opening
    k = int(card_h[0] * 0.3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
    if masks is not None:
        masks.append(mask)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, n):
        bx, by, bw, bh, _ = stats[i]
        if bh < card_h[0] * 0.45 or bw < card_h[0] * 0.4:
            continue
        # keep the rows that are (nearly) as wide as the card: drops price tags / counter text glued on
        blob = lab[by:by + bh, bx:bx + bw] == i
        r0, r1 = _longest_run(blob.sum(1))
        c0, c1 = _longest_run(blob[r0:r1].sum(0), 0.5)
        blob = blob[r0:r1, c0:c1]
        ys, xs = np.nonzero(blob)
        pts = np.c_[xs + c0, ys + r0].astype(np.float32)
        (cx, cy), _, ang = cv2.minAreaRect(pts)
        ang = (ang + 45) % 90 - 45  # tilt within +-45 deg; w/h = extents along the tilted axes
        t = np.deg2rad(ang)
        u = (pts - (cx, cy)) @ np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]], np.float32)
        w, h = np.ptp(u, axis=0) + 1
        # normal cards; Half/Square/Wee Joker are shorter (ponytail: their size is not checked further)
        ok = (0.45 * card_h[0] <= h <= card_h[1] and 0.4 * card_h[0] <= w <= 0.85 * card_h[1] and w / h <= 1.6
              and blob.sum() >= 0.85 * w * h)
        if ok:
            out.append((cx + bx + x0, cy + by + y0, w, h, ang))
        elif wide is not None and 0.8 * card_h[0] <= r1 - r0 <= 1.2 * card_h[1] and c1 - c0 > 0.4 * card_h[0]:
            wide.append((c0 + bx + x0, r0 + by + y0, c1 - c0, r1 - r0))
    return sorted(out)


# ---------------------------------------------------------------- matching

def is_negative(card_bgr):
    """Negative edition: the card face is dark blue (normal cards have a light face; Blackboard is grey)."""
    h, w = card_bgr.shape[:2]
    inner = card_bgr[h // 6:-h // 6, w // 6:-w // 6].reshape(-1, 3).astype(np.float32)
    b, g, r = np.median(inner, 0)
    return b - r > 35 and r < 110


def _window(canvas, quad):
    """The box plus margin, deskewed and scaled so the box is TH px high."""
    cx, cy, w, h, ang = quad
    scale = TH / h
    ww, wh = round(w * scale * MARGIN), round(TH * MARGIN)
    M = cv2.getRotationMatrix2D((cx, cy), ang, scale)
    M[:, 2] += (ww / 2 - cx, wh / 2 - cy)
    return cv2.warpAffine(canvas, M, (ww, wh), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE)


def _rank(win, kinds, neg, box_h=None):
    """Sprites of `kinds` ranked against the card window -> best match dict."""
    g = _gray(win)
    if neg:
        g = _highpass(255 - g)
    ab = _ab(win)
    wh, ww = g.shape
    lib = [e for k in kinds for e in _library(k)]
    if box_h:  # Wee/Half/Square Joker & co. are smaller cards: the absolute size rules them in or out
        lib = [e for e in lib if e["kind"] != "joker" or 0.75 < box_h / (e["rel"] * CARD_H) < 1.3]

    def corr(img, e, s):
        t = e["t"][s][1 if neg else 0]
        if t.shape[0] > img.shape[0] or t.shape[1] > img.shape[1]:
            return -1.0, (0, 0)
        _, v, _, loc = cv2.minMaxLoc(cv2.matchTemplate(img, t, cv2.TM_CCOEFF_NORMED))
        return v, loc

    coarse = np.argsort([corr(g, e, 0.94)[0] for e in lib])[::-1][:TOPK]
    rotated = {0: (g, ab)}
    for r in ROTS:
        if r:
            R = cv2.getRotationMatrix2D((ww / 2, wh / 2), r, 1)
            rotated[r] = (cv2.warpAffine(g, R, (ww, wh), borderMode=cv2.BORDER_REPLICATE),
                          cv2.warpAffine(ab, R, (ww, wh), borderMode=cv2.BORDER_REPLICATE))
    res = []
    for i in coarse:
        e = lib[i]
        v, loc, s, r = max((*corr(rotated[r][0], e, s), s, r) for s in SCALES for r in ROTS)
        score = v
        if v > 0 and not neg:  # colour agreement at the best alignment (Lab a/b, 128 = neutral)
            t_ab = e["t"][s][2]
            x, y = loc
            d = np.abs(rotated[r][1][y:y + t_ab.shape[0], x:x + t_ab.shape[1]] - t_ab).mean()
            score = 0.6 * v + 0.4 * float(np.exp(-d / 12))
        res.append((score, e["kind"], e["name"], v))
    if not res:
        return {"kind": kinds[0], "name": "?", "edition": None, "score": 0.0, "gray": 0.0, "margin": 0.0, "alt": ""}
    res.sort(reverse=True)
    best = res[0]
    second = next((r for r in res[1:] if r[2] != best[2]), (0, "", "", 0))
    return {"kind": best[1], "name": best[2], "edition": "negative" if neg else None, "score": best[0],
            "gray": best[3], "margin": best[0] - second[0], "alt": second[2]}


def match(canvas, quad, kinds):
    """Best sprite for the card at quad -> dict(kind, name, edition, score, gray, margin, alt)."""
    win = _window(canvas, quad)
    m = _rank(win, kinds, False, box_h=quad[3])
    if "joker" in kinds and m["score"] < 0.7 and is_negative(win):
        m = _rank(win, ("joker",), True, box_h=quad[3])
    elif m["kind"] == "joker" and is_holo(win, m["name"]):
        m["edition"] = "holo"
    return m


def _bright_ab(bgr):
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    bright = lab[lab[:, 0] > 170]
    return (bright[:, 1].mean(), bright[:, 2].mean()) if len(bright) >= 500 else None


@lru_cache(maxsize=None)
def _sprite_ab(name):
    img, _ = _sprite_bgr(ASSETS / DIRS["joker"] / f"{name}.png")
    return _bright_ab(img) if img is not None else None


def is_holo(card_bgr, name=None):
    """Holographic edition: the card's light areas carry a pink/blue hex shimmer (Lab a up, b down).
    Measured 2026-10-27: holo a 136-139 / b 117-118, plain cards a 126-129 / b 128-132. Relative to the
    joker's own sprite, so naturally lilac art (Arrowhead) is no holo.
    ponytail: foil and polychrome are not detected yet (no labelled frames)."""
    ab = _bright_ab(card_bgr)
    if ab is None or not (ab[0] > 133 and ab[1] < 123):
        return False
    ref = _sprite_ab(name) if name else None
    return ref is None or (ab[0] - ref[0] > 4 and ref[1] - ab[1] > 4)


# ---------------------------------------------------------------- price tags

# Extra glyphs for the price-tag digits (hud.py's set lacks 7/9): 3/6/8 harvested from the tags of a shop frame
# (the small tag font blurs differently than the big HUD digits), 9 = 6 rotated, 7 drawn in the stroke width.
_EXTRA = {"3": "03e007e00ff01ff81e780c78007801f001f000f000780078007800780c781e781ff80ff007e003e0",
          "6": "03c00fe01ff01ff8383838383c003f803fe03fe03ff83c78383838383c383c783c783ff80ff00380",
          "7": "3ff83ff83ff83ff80078007800f000f001e001e003c003c003c00780078007800780078007800780",
          "8": "1fc01fe01ff01ff81c781c781c701fe01fe01fe01ff01c78383838783c383c783c783ff81fe00fc0",
          "9": "07f00ff81ff81e381c381c381c381e381f780ff807f800f8007800381c381e781ff80ff00fe003c0"}


@lru_cache(maxsize=1)
def _glyph_templates():
    from .hud import GH, GW, _TPL
    extra = [(k, np.unpackbits(np.frombuffer(bytes.fromhex(h), np.uint8))[:GH * GW].reshape(GH, GW).astype(np.float32))
             for k, h in _EXTRA.items()]
    return _TPL + extra


def _classify(g):
    """Glyph bitmap -> (char, conf); same scoring as hud._classify, extended template set."""
    best = {}
    for k, t in _glyph_templates():
        best[k] = min(best.get(k, 1.0), float(np.abs(g - t).mean()))
    (k, d1), (_, d2) = sorted(best.items(), key=lambda kv: kv[1])[:2]
    return k, float(np.clip((0.22 - d1) / 0.12, 0, 1) * np.clip((d2 - d1) / 0.08, 0, 1))


def read_price(canvas, quad):
    """'$N' tag above a shop item -> (N, conf) or (None, 0)."""
    from .hud import _glyph, _mask
    cx, cy, w, h, _ = quad
    top = int(cy - h / 2)
    crop = canvas[max(0, top - 45):top + 2, max(0, int(cx - 45)):int(cx + 45)]
    m = _mask(crop, "color").astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    comps = sorted((x, y, cw, ch, i) for i, (x, y, cw, ch, a) in enumerate(st)
                   if i and 12 <= ch <= 32 and cw <= ch and a >= 20 and y > 0 and y + ch < m.shape[0])
    if not comps:
        return None, 0.0
    mid = np.median([y + ch / 2 for x, y, cw, ch, i in comps])
    chars = [_classify(_glyph(lab[y:y + ch, x:x + cw] == i)) for x, y, cw, ch, i in comps
             if abs(y + ch / 2 - mid) < 6]
    if chars and chars[0][0] == "$":
        chars = chars[1:]
    text = "".join(c for c, _ in chars)
    if not text.isdigit() or len(text) > 2:
        return None, 0.0
    return int(text), min(c for _, c in chars)


# ---------------------------------------------------------------- public API

TOP = (320, 60, 1240, 380)  # joker + consumable trays (canvas px)
JOKER_TRAY = (333, 923)  # x extent of the joker tray (cards of a full row span exactly this)
CONS_TRAY = (932, 1205)
SHOP_TOP = (540, 345, 1040, 590)  # cards for sale
SHOP_VOUCHER = (370, 590, 705, 850)
SHOP_PACKS = (705, 590, 1040, 850)
CONSUMABLES = ("planet", "tarot", "spectral")
UNCERTAIN_CAP = 0.75  # conf ceiling for cards placed by the row model instead of seen as separate cards


def _conf(m):
    """Honest confidence from match quality and the margin to the best other name."""
    if m["edition"] == "negative":  # colourless match; jokers sharing their art are indistinguishable
        return float(np.clip((m["score"] - 0.45) / 0.3, 0, 1) * np.clip(m["margin"] / 0.08, 0, 1))
    if m["kind"] == "booster_pack":  # shiny animated wrappers: shape only (ponytail: tuned on one shop frame)
        return float(min(0.8, np.clip((m["gray"] - 0.6) / 0.2, 0, 1) * np.clip(m["margin"] / 0.08, 0, 1)))
    if m["edition"] == "holo":  # the shimmer spoils the colour check: judge by shape alone
        return float(np.clip((m["gray"] - 0.6) / 0.2, 0, 1) * np.clip(m["margin"] / 0.06, 0, 1))
    q = np.clip((m["score"] - 0.6) / 0.2, 0, 1) * np.clip(m["margin"] / 0.06, 0, 1)
    colour = (m["score"] - 0.6 * m["gray"]) / 0.4
    if colour < 0.45:  # shape fits, colours don't: foil/holo/poly edition (not identified) or a lookalike
        q = min(q, 0.4)
    return float(q)


def _row_quads(canvas, region, tray, kinds):
    """Card quads of a tray row -> [(quad, uncertain)].

    Cards that are packed edge to edge (5+ jokers) or overlap (6+) merge into wide blobs. Balatro spreads the
    cards of a full row evenly from the tray's left to its right edge, so for n cards the centres are fixed;
    n is chosen so the predicted centres agree with the separately detected cards and the 2nd predicted card
    matches a sprite best. Card-high blobs no card explains become `uncertain` unknown cards."""
    wide, masks = [], []
    left, right = tray
    quads = [q for q in find_cards(canvas, region, wide=wide, masks=masks) if left - 20 <= q[0] <= right + 20]
    wide = [b for b in wide if left - 20 <= b[0] + b[2] / 2 <= right + 20]
    # card-high stuff that no card explains (unusual shapes, cut-off cards): report it as an unknown card
    ch = float(np.median([q[3] for q in quads])) if quads else CARD_H
    cy = float(np.median([q[1] for q in quads])) if quads else 218  # ponytail: tray row of the 1280 layout
    band = masks[0][max(0, int(cy - ch / 2 - region[1])):int(cy + ch / 2 - region[1])]
    cover = band.sum(0) / 255 > 0.8 * ch
    for q in quads:
        cover[max(0, int(q[0] - q[2] / 2 - region[0])):int(q[0] + q[2] / 2 - region[0]) + 1] = False
    for b in wide:
        cover[max(0, b[0] - region[0]):b[0] + b[2] - region[0] + 1] = False
    cover[:max(0, left - region[0])] = False
    cover[right - region[0]:] = False
    lost = [r for r in zip(*[iter(np.flatnonzero(np.diff(np.r_[0, cover.astype(np.int8), 0])))] * 2) if r[1] - r[0] > 30]
    gaps = np.diff([q[0] for q in quads])
    if not wide and not (len(gaps) and gaps.min() < 1.3 * np.median([q[2] for q in quads])):
        return sorted([(q, False) for q in quads] +
                      [((region[0] + (a + b) / 2, cy, 0.75 * CARD_H, CARD_H, 0), True) for a, b in lost])
    if quads:
        h = float(np.median([b[3] for b in wide] + [q[3] for q in quads]))
        cw = float(np.median([q[2] for q in quads]))
        cy = float(np.median([b[1] + b[3] / 2 for b in wide] + [q[1] for q in quads]))
    else:  # only merged blobs: their top edge is ragged (shorter than a card), the bottom edge is the cards'
        h, cw = CARD_H, 0.77 * CARD_H
        cy = float(np.median([b[1] + b[3] for b in wide])) - h / 2
    best = None
    for n in range(2, 10):
        step = (right - left - cw) / (n - 1)
        if step > 1.15 * cw:
            continue
        centres = left + cw / 2 + step * np.arange(n)
        if any(np.min(np.abs(centres - q[0])) > 0.2 * cw for q in quads):
            continue
        # the cards sit at different places for each n: the n whose cards look most like sprites wins
        # (all of them: one lucky match of a wrong n's 2nd card must not decide)
        score = np.mean([_rank(_window(canvas, (c, cy, cw, h, 0)), kinds, False)["score"] for c in centres]) \
            - 0.02 * n
        if best is None or score > best[0]:
            best = (score, centres, step)
    if best is None:  # cards we cannot place: report one unknown so the caller falls back
        return sorted([(q, False) for q in quads] + [((b[0] + b[2] / 2, cy, cw, h, 0), True) for b in wide])
    # ponytail: the count of a packed row is inferred, not seen; split cards are never fully trusted
    _, centres, step = best
    out = []
    for c in centres:
        near = [q for q in quads if abs(q[0] - c) <= 0.2 * cw]
        out.append((near[0], False) if near else ((c, cy, cw, h, 0), True))
    return out


def _read_row(canvas, region, kinds, tray):
    H, W = canvas.shape[:2]
    out = []
    for q, uncertain in _row_quads(canvas, region, tray, kinds):
        m = match(canvas, q, kinds)
        out.append({**m, "conf": min(_conf(m), UNCERTAIN_CAP if uncertain else 1.0),
                    "pos": [round(q[0] / W, 4), round(q[1] / H, 4)]})
    return out


def read_jokers(canvas):
    """Jokers left to right: [{name, edition, debuff, conf, pos}]."""
    from .telemetry import decode_strip
    tel = decode_strip(canvas)
    if tel is not None:
        return tel[0].get("jokers", [])
    return [{"name": m["name"], "edition": m["edition"], "debuff": False, "conf": m["conf"], "pos": m["pos"]}
            for m in _read_row(canvas, TOP, ("joker",), JOKER_TRAY)]


def read_consumables(canvas):
    """Consumables left to right: [{name, kind, conf, pos}]."""
    from .telemetry import decode_strip
    tel = decode_strip(canvas)
    if tel is not None:
        return tel[0].get("consumable_items", [])
    return [{"name": m["name"], "kind": m["kind"], "conf": m["conf"], "pos": m["pos"]}
            for m in _read_row(canvas, TOP, CONSUMABLES, CONS_TRAY)]


def is_shop(canvas):
    """The shop panel's red 'Next Round' and green 'Reroll' buttons."""
    _, ng, nr = canvas[385:455, 390:530].reshape(-1, 3).mean(0)
    rb, rg, rr = canvas[480:555, 390:530].reshape(-1, 3).mean(0)
    green = rg > 160 and rg - rr > 70
    grey = 60 < (rb + rg + rr) / 3 < 130 and max(rb, rg, rr) - min(rb, rg, rr) < 15  # reroll unaffordable
    return nr > 170 and nr - ng > 90 and (green or grey)


def read_shop(canvas):
    """Shop items [{kind, name, price, edition, conf}] (cards for sale, voucher, packs); [] when no shop."""
    from .telemetry import decode_strip
    tel = decode_strip(canvas)
    if tel is not None:
        return tel[0].get("shop", [])
    if not is_shop(canvas):
        return []
    out = []
    for region, kinds, hs in ((SHOP_TOP, ("joker",) + CONSUMABLES, (110, 185)),
                              (SHOP_VOUCHER, ("voucher",), (110, 185)), (SHOP_PACKS, ("booster_pack",), (140, 230))):
        for q in find_cards(canvas, region, card_h=hs, dark_bg=True):
            m = match(canvas, q, kinds)
            price, pc = read_price(canvas, q)
            H, W = canvas.shape[:2]
            out.append({"kind": m["kind"], "name": m["name"], "price": price, "edition": m["edition"],
                        "conf": min(_conf(m), pc), "pos": [round(q[0] / W, 4), round(q[1] / H, 4)]})
    return out


# ---------------------------------------------------------------- booster-pack sprites

def download_packs():
    """Fetch booster-pack art ("<Type> <Size> <n>.png" on balatrowiki) to ASSETS/packs/<Pack name>_<n>.png."""
    import io

    from PIL import Image

    from .assets import _get, image_urls
    d = ASSETS / "packs"
    d.mkdir(parents=True, exist_ok=True)
    titles = {f"{t} {s} {i}": f"{p}{t} Pack_{i}" for t in PACK_TYPES for s, p in PACK_SIZES.items() for i in range(1, 5)}
    for title, url in image_urls(list(titles)).items():
        if title in titles and not (d / f"{titles[title]}.png").exists():
            try:  # untrusted download: keep only real images
                img = Image.open(io.BytesIO(_get(url)))
                img.load()
                img.convert("RGBA").save(d / f"{titles[title]}.png")
            except Exception as e:
                print("skip", title, e)
    return sorted(p.stem for p in d.glob("*.png"))


if __name__ == "__main__":
    print(download_packs())
