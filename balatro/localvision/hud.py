"""Local (no AI) reader for the left HUD panel and the game phase.

read_hud(normalize(path)) -> {phase, blind, blind_chips, score, hands_left, discards_left, money, ante,
round, reroll_cost, conf: {field: 0..1}}.

Numbers: every field sits at a fixed spot of the 1280 px canvas. A colour mask isolates the digits, the
connected components are the glyphs (Balatro's pixel font never joins them), and each glyph is matched
against digit templates harvested from labelled frames (_TEMPLATES below). A glyph that matches no
template well gives low confidence instead of a guess. Blind name: tesseract on the banner + difflib.
Phase: colour checks of fixed buttons (Run Info/Options, Reroll, Sort Hand, ...).
"""
import difflib
import subprocess

import cv2
import numpy as np

from ..data import BOSSES
from .layout import REF_W

GH, GW = 20, 16  # glyph template size

# Field boxes in pixels of the 1280 px wide canvas (y from the top of the game area), mask mode.
FIELDS = {
    "blind_chips": ((150, 245, 305, 350), "red"),
    "score": ((110, 375, 305, 435), "white"),
    "hands_left": ((135, 612, 215, 668), "color"),
    "discards_left": ((225, 612, 308, 668), "color"),
    "money": ((140, 676, 305, 740), "color"),
    "ante": ((135, 768, 215, 824), "color"),
    "round": ((225, 768, 308, 824), "color"),
    "reroll_cost": ((380, 470, 540, 562), "white"),
}
BANNER = (25, 160, 305, 208)
BLINDS = ["Small Blind", "Big Blind", *BOSSES]

# Harvested glyph templates: class -> list of 20x16 bitmaps as hex (see _glyph / _hex).
# Extended 2026-10-27 from Claude-labelled stream frames (7 and 9 were missing).
_TEMPLATES = {
    '$': ['03800380078007f00ff03ff03c003c003fc03fe00ff007f8007800783ff03fe03fc0038003800380', '0380038003800ff00ff81ff83e003c003fc01ff00ff807f800781ff81ff01fe01fe003c003c00380', '01800180018003c007f80ff81ff81e001f001fe00ff003f8003800781ff81fe00fe0018001c00180', '01000380038007f00ff01ff01ff01c001e001fc00ff000f000701ff01ff01fe01fc0038003800380', '018001c003c003c00ff81ff81ff81e001fe01ff00ff0007800380ff81ff81ff003e0018001800180', '03800380038003800ff01ff01ff01c001fc01fe01fe00ff0007000701ff01ff01fe0038003800380'],
    '0': ['0fe01fe03ff03ff03c783878387838703870387038703870387038703870387038703ff03ff01fe0', '0fe00ff01ff83ff83c783c783c783c783c783c783c783c783c783c783c783c783c783ff81ff00fe0', '07e007f01ff81c181c181c181c181c181c181c181c181c181c181c181c181c181c181ff807f00200', '07e00ff01ff81ff81c781c381838183818381838383838383838183818383c383e781ff81ff007e0', '03e007f00ff00ff80c1818181818081808180818081818181818081808180c180e780ff80ff007e0', '0ff03ff83ff83c383c383c383c383c383c383c383c383c383c383c383c383c383ff81ff007f007e0', '0fe03ff83ff83c783838383838383838383838383838383838383838383838383ff81ff00fe00fe0', '07f00ff81ff83ffc3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3c3ffc1ffc1ff80ff0', '07c00fe00ff01ff81c781c781c781c781c781c781c781c781c781c781c781c781ff81ff80ff007e0', '0ff00ff01ff81e781c381c381c381c381c381c381c381c381c381c381c381c381ff81ff81ff00060'],
    '1': ['010007803f803f803f801f8003800380038003800380038003800380038003800fc01ff81ff81ff0', '07803f803f803f803f80078007800780078007800780078007800780078007800fc03ff83ff80070', '03c01fc03fc03fc03fc007c003c003c003c003c003c007c007c003c003c003c01fe03ff83ffc3070', '078007803f803f803f800f8007800780078007800780078007800780078007800fe03ff83ff83ff0', '038007801f803f803f80078003800380038003800380038003800380038003c00ff01ff83ffc1ff8', '03000f801f803f803f80078007800780078007800780078007800780078007c01ff03ff83ff83ff0', '03803f803f803f803f80038003800380038003800380038003800380038003803ff83ff83ff81ff8'],
    '2': ['0fe01ff03ff83ff83c78007800780078007800f801f803f007e00fc01f803f003fe03ff03ff81ff0', '0fc01fc03ff03ff03870107000700070007000f001f003e007e00f801f003e003ff03ff83ff03ff0', '3ff83ff83ff81ff800780078007800f801f801e001c007c007800780078007800780078007800780'],
    '3': ['0fe00ff01ff03ff83c781078007801f003f003f003f8007800780078007838783c783ff81ff00fe0', '0fc01fe03ff03ff038700070007003e007e007f803f8007800780078007818781ff81ff00ff00fe0', '0fc01ff03ff83e783c781878007801f003f003f801f800780078007808781c781ff81ff807f00700', '07e00ff03ff03ef03c701878007803f003e007f003f800780078007818781c701ef81ff00ff007e0', '0fe03ff83ff83c783c780078007003f003f003f801f800780078007818783c783ff81ff00ff00fe0', '0fe01fe03ff83ff838780078007800f007e007f803f8007800780078007838783ff83ff81ff00fc0', '03e007f80ff81ffc1c3c003c003c003801f003f801fc003c003c003800383c383ff83ff807f007e0'],
    '4': ['387038783c78387838783878387838783c783ff83ff81ff80ff80078003800780078007800380030', '00603cf83cf83cf03cf03cf03cf03c703df03ff01ff00ff00ff00070007000700030003000300030', '1c381c381c381c381c381c381c381c381c381ff80ff80ff807f80038003800380038003800380038', '1c383c783c7c3c783c783c783c783c783ff83ff81ff80ff807f80078003800380038003800380010'],
    '5': ['3ff03ff83ff83ff03c003c003c003fe03fe03ff03ff8007800780078007800781ff83ff03fe03fe0', '3ff03ff03ff03c003c003c003c003fe03ff03ff81ff8003800380038003800381ff81ff01fe00800', '3ff03ff83ff03ff0380038003c003fc03fe03ff03ff8007800700070007000f01ff03ff03fe03fc0', '1ff03ff83ff83ff03c003c003c003f003fe03ff01ff800f800f000f000f000f03ff03ff03fe01800', '3ff83ff83ff83c003800380038003fe03fe03ff81ff80038003800380038003800783ff83fe01fc0', '3ff83ff83ffc3ff03c003c003c003fe03ff03ff818f8003c00380038003800381ff83ff83ff00800'],
    '6': ['03c007f00ff01ff81e781c381c001e001f001fe01ff01ef81c781c381c381c381c781ff81ff00fe0', '0fc01ff03ff03ff83c703c003c003fc03fe03ff03ff83c783c783c783c783cf83ff83ff01ff00fc0', '0fe03ff83ff83c78383838003c003fc03fe03ff83ff8387838783878387838783ff81ff00fc00fc0', '07c00ff01ff81ff83c783c103c003fc03ff03ff83ff83c783c783c783c783c783e783ff83ff81ff0', '1fe03ff03ff83ff8387838781c701fe01fe03ff83ff8387838783878387838f83ff83ff81ff00fc0'],
    '7': ['3ff83ff83ff8007800380038003800f800f800e001e003e003c003c003c003c003c003c003c00380', '3ff83ff83ff8007800780078007800f800f800e001c007c007800780078007800780078007800380', '3ff83ff83ff8007800780078007800f800f800e001e007e007800780078007800780078007800780'],
    '8': ['0fe03ff83ff83c783c383c380c7007f00ff03ff83e783c383c383c383c383c383ff81ff007f007e0', '0fe03ff83ff83c783878387818700fc00fe03ff83ff8387838783878387838783ff81ff00fc00fc0', '07c00ff01ff83ff83c783c781c781ff01ff01ff81ff83c783c783c783c783e783ff81ff80fe007c0', '07e00fe03ff83e783c383c383c381c700fe03ff83ff83c783c383c383c383c783e783ff00fe00fc0'],
    '9': ['07f00ff01ff81e781c381c381c381c381c381ff81ff80ff807f800380c381c381e781ff80ff00ff0'],
}


def _mask(crop, mode):
    c = crop.astype(np.int16)
    b, g, r = c[..., 0], c[..., 1], c[..., 2]
    lo, hi = c.min(axis=2), c.max(axis=2)
    if mode == "red":
        return (r > 140) & (r - g > 70) & (r - b > 50)
    if mode == "white":
        return (lo > 170) & (hi - lo < 70)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return (hsv[..., 1] > 100) & (hsv[..., 2] > 140)


def _glyphs(canvas, box, mode):
    """Glyph bitmaps (GHxGW float) of the tallest text row in box, left to right."""
    x0, y0, x1, y1 = box
    m = _mask(canvas[y0:y1, x0:x1], mode).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    h, w = m.shape
    comps = [(x, y, cw, ch, i) for i, (x, y, cw, ch, a) in enumerate(st) if i and a >= 15
             and x > 0 and y > 0 and x + cw < w and y + ch < h  # touching the crop edge = panel border
             and 18 <= ch <= 45 and cw <= 0.8 * ch]  # chip icon, labels, commas, "/8" are dropped
    if not comps:
        return []
    top = max(comps, key=lambda c: c[3])
    mid = top[1] + top[3] / 2
    comps = sorted(c for c in comps if c[3] >= 0.75 * top[3] and abs(c[1] + c[3] / 2 - mid) < top[3] / 3)
    return [_glyph(lab[y:y + ch, x:x + cw] == i) for x, y, cw, ch, i in comps]


def _glyph(bits):
    h, w = bits.shape
    nw = max(1, min(GW, round(w * GH / h)))
    g = cv2.resize(bits.astype(np.float32), (nw, GH), interpolation=cv2.INTER_AREA)
    out = np.zeros((GH, GW), np.float32)
    out[:, (GW - nw) // 2:(GW - nw) // 2 + nw] = g
    return out


def _hex(g):
    return np.packbits(g.flatten() > 0.5).tobytes().hex()


_TPL = [(k, np.unpackbits(np.frombuffer(bytes.fromhex(h), np.uint8))[:GH * GW].reshape(GH, GW).astype(np.float32))
        for k, hs in _TEMPLATES.items() for h in hs]


def _classify(g):
    """(char, confidence). Confidence drops with distance to the best template and with ambiguity."""
    best = {}
    for k, t in _TPL:
        d = float(np.abs(g - t).mean())
        best[k] = min(best.get(k, 1.0), d)
    if not best:
        return "?", 0.0
    ranked = sorted(best.items(), key=lambda kv: kv[1])
    (k, d1), d2 = ranked[0], ranked[1][1] if len(ranked) > 1 else 1.0
    # measured on 767 labelled glyphs (2026-10-27): every glyph with margin d2 - d1 >= 0.03 was right
    conf = float(np.clip((0.22 - d1) / 0.12, 0, 1) * np.clip((d2 - d1) / 0.03, 0, 1))
    return k, conf


def _number(canvas, field):
    box, mode = FIELDS[field]
    chars = [_classify(g) for g in _glyphs(canvas, box, mode)]
    if field in ("money", "reroll_cost") and chars and chars[0][0] == "$":
        chars = chars[1:]
    if not chars or len(chars) > 12:
        return None, 0.0
    text = "".join(c for c, _ in chars)
    if not text.isdigit():
        return None, 0.0
    return int(text), min(c for _, c in chars)


# --- phase -------------------------------------------------------------------------------------------

def _mean(canvas, box):
    x0, y0, x1, y1 = box
    return canvas[y0:y1, x0:x1].reshape(-1, 3).mean(axis=0)  # BGR


def _is(bgr, colour):
    b, g, r = bgr
    return {
        "red": r > 150 and r - g > 70 and r - b > 60,
        "orange": r > 170 and 90 < g < 190 and b < 90 and r - g > 40,
        "green": g > 120 and g - r > 40 and g - b > 15,
        "grey": 60 < (b + g + r) / 3 < 130 and max(b, g, r) - min(b, g, r) < 15,  # a disabled button
    }[colour]


CASH_OUT = (502, 402, 534, 428)
SELECT_COLS = (454, 699, 944)  # x of the Small / Big / Boss columns on the blind select screen (1280 layout)


def blind_select_column(canvas):
    """Index of the blind column showing its orange 'Select' button, None if this is no blind select screen."""
    from .telemetry import decode_strip
    tel = decode_strip(canvas)
    if tel is not None and "blind_col" in tel[0]:
        return tel[0]["blind_col"] if tel[0].get("phase") == "blind_select" else None
    for i, x in enumerate(SELECT_COLS):
        if _is(_mean(canvas, (x - 50, 350, x + 50, 366)), "orange"):
            return i
    return None


def _phase(canvas):
    """(phase, confidence)."""
    if canvas.shape[0] < 0.6 * REF_W or canvas.shape[0] > 0.9 * REF_W:
        return "other", 0.9
    hud = _is(_mean(canvas, (45, 610, 110, 680)), "red") and _is(_mean(canvas, (45, 725, 110, 795)), "orange")
    if not hud:
        return "other", 0.85
    reroll = _mean(canvas, (390, 485, 530, 545))  # green, or grey when the reroll is unaffordable
    if (_is(reroll, "green") or _is(reroll, "grey")) and _is(_mean(canvas, (390, 395, 530, 450)), "red"):
        return "shop", 0.95
    from .booster import is_booster  # booster -> cards/jokers -> hud
    if is_booster(canvas):  # first: a pack's swirling background can look like the orange Select button
        return "booster_pack", 0.95
    if blind_select_column(canvas) is not None:
        return "blind_select", 0.95
    if _is(_mean(canvas, CASH_OUT), "orange"):  # left end of the wide orange "Cash Out: $N" button
        return "cash_out", 0.95
    return "playing", 0.6


# --- blind name -----------------------------------------------------------------------------------------

def _blind(canvas):
    x0, y0, x1, y1 = BANNER
    gray = cv2.cvtColor(canvas[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    m = np.where(gray > 200, 0, 255).astype(np.uint8)
    if (m == 0).mean() < 0.03:
        return None, 0.0
    m = cv2.copyMakeBorder(cv2.resize(m, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST),
                           16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)
    ok, buf = cv2.imencode(".png", m)
    try:
        text = subprocess.run(["tesseract", "stdin", "stdout", "--psm", "7", "-c",
                               "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz "],
                              input=buf.tobytes(), capture_output=True, timeout=5).stdout.decode().strip()
    except (OSError, subprocess.TimeoutExpired):
        return None, 0.0
    hit = difflib.get_close_matches(text, BLINDS, n=2, cutoff=0.6)
    if not hit:
        return None, 0.0
    r1 = difflib.SequenceMatcher(None, text, hit[0]).ratio()
    r2 = difflib.SequenceMatcher(None, text, hit[1]).ratio() if len(hit) > 1 else 0.0
    return hit[0], float(np.clip((r1 - 0.6) / 0.3, 0, 1) * np.clip((r1 - r2) / 0.15, 0, 1))


def read_hud(canvas):
    keys = ["blind", *FIELDS]
    out = {"phase": None, **{k: None for k in keys}, "conf": dict.fromkeys(keys, 0.0)}
    phase, pconf = _phase(canvas)
    if phase == "playing":  # no shop, HUD present: a readable blind banner confirms a running round
        out["blind"], out["conf"]["blind"] = _blind(canvas)
        sort_btns = all(_is(_mean(canvas, b), "orange") for b in ((645, 768, 690, 795), (702, 768, 745, 795)))
        # Blind select / opened packs are not in the dataset yet; without a readable banner or the
        # Sort Hand buttons we cannot tell them apart from playing, so say so via low confidence.
        # ponytail: add positive detectors for blind_select/booster_pack once labelled frames exist
        readable = out["conf"]["blind"] >= 0.8
        pconf = 0.95 if readable and sort_btns else 0.75 if readable or sort_btns else 0.3
    out["phase"], out["conf"]["phase"] = phase, pconf
    if phase == "other":
        return out
    for k in FIELDS:
        if (k == "reroll_cost") == (phase == "shop") or (k != "reroll_cost" and k != "blind_chips"):
            out[k], out["conf"][k] = _number(canvas, k)
    if out["blind"] and out["ante"] and out["conf"]["ante"] >= 0.6:
        # the target follows from ante and blind: confirms the reading, or stands in for an unreadable one
        # ponytail: assumes the white-stake ante scaling (higher stakes scale faster from stake 2 on)
        from ..strategy import required_score
        kind = {"Small Blind": "small", "Big Blind": "big"}.get(out["blind"], out["blind"])
        want = required_score(out["ante"], kind)
        if out["blind_chips"] == want:
            out["conf"]["blind_chips"] = max(out["conf"]["blind_chips"], 0.95)
        elif out["conf"]["blind_chips"] < 0.6:
            out["blind_chips"], out["conf"]["blind_chips"] = want, 0.7
    return out
