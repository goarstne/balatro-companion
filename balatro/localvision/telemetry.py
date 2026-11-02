"""Visual telemetry strip encoder/decoder (Shadow PC / LÖVE2D `love.draw` hook -> localvision).

The Lua hook in `patch_balatro.bat` serialises `G` into a tab-separated text block, compresses it
with `love.data.compress("string", "zlib", raw)` (RFC 1950 zlib with Adler-32 checksum), prefixes
`"BL" + uint16_be(len)` and draws it as a grid of 320 columns x N rows of 4x4 blocks (dark grey 45 = 0,
bright white 245 = 1) starting one block-row below the top edge of the game canvas.
"""
import zlib

import numpy as np

from ..data import BOSSES, JOKER_NAMES
from ..jokers import get as get_joker
from ..vision import _closest

COLS = 320
ROW_DIV = 240.0
MAX_ROWS = 48  # decoder cap only: the Lua hook draws as many rows as the packet needs (late game + decks ~1 KB = 25 rows)
BLIND_LIST = ["Small Blind", "Big Blind", *BOSSES]

# Jokers in Balatro's part_b.py that store their X-mult in vars["x"] instead of vars["xmult"]
_X_KEY_JOKERS = {"Lucky Cat", "Ramen", "Campfire", "Throwback", "Glass Joker", "Hit the Road", "Canio", "Yorick"}


def decode_strip(canvas):
    """Normalized BGR canvas -> (state_dict, confs, notes) if a valid 'BL' telemetry strip is present, else None."""
    if canvas is None or canvas.ndim != 3 or canvas.shape[0] < 100 or canvas.shape[1] < 640:
        return None
    nh, nw = canvas.shape[:2]
    xs = np.round((np.arange(COLS) + 0.5) * (nw / COLS)).astype(int)
    ys_base = np.round((np.arange(1, 1 + MAX_ROWS) + 0.5) * (nh / ROW_DIV)).astype(int)
    gray = canvas[:min(nh, int(nh / ROW_DIV * (MAX_ROWS + 3))), :, :].mean(axis=2)

    for dy in (0, -1, 1, -2, 2):
        ys = np.clip(ys_base + dy, 0, gray.shape[0] - 1)
        row0 = gray[ys[0], xs[:32]]
        # Fast pre-check on the first 2 bytes ('B' = 01000010, 'L' = 01001100) before unpacking all rows
        thr = ( float(row0.max()) + float(row0.min()) ) * 0.5
        if thr < 80 or float(row0.max() - row0.min()) < 60:
            continue
        hdr = np.packbits(row0 > thr).tobytes()
        if hdr[:2] != b"BL":
            continue
        length = (hdr[2] << 8) | hdr[3]
        if not (8 <= length <= (MAX_ROWS * COLS // 8) - 4):
            continue
        bits = gray[np.ix_(ys, xs)] > thr
        raw = np.packbits(bits.ravel()).tobytes()
        try:
            tsv = zlib.decompress(raw[4:4 + length]).decode("utf-8")
            return parse_tsv(tsv)
        except Exception:
            continue
    return None


def encode_strip(img_bgr, tsv_text):
    """Draw the telemetry strip for `tsv_text` onto a game canvas BGR image (for testing / simulation)."""
    out = img_bgr.copy()
    H, W = out.shape[:2]
    comp = zlib.compress(tsv_text.encode("utf-8"), 9)
    pkt = b"BL" + len(comp).to_bytes(2, "big") + comp
    bits = np.unpackbits(np.frombuffer(pkt, dtype=np.uint8))
    bw, bh = W / COLS, H / ROW_DIV
    rows = (len(bits) + COLS - 1) // COLS
    y_top, y_bot = int(round(1 * bh)), int(round((1 + rows) * bh))
    out[y_top:y_bot, 0:W] = 45
    for idx, b in enumerate(bits):
        if b:
            r, c = divmod(idx, COLS)
            x0, x1 = int(round(c * bw)), int(round((c + 1) * bw))
            y0, y1 = int(round((r + 1) * bh)), int(round((r + 2) * bh))
            out[y0:y1, x0:x1] = 245
    return out


def _int(s, default=0):
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return default


def _float(s, default=0.0):
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


def _deck_card(tok):
    """'KS' or 'KS/<enhancement>/<edition>/<seal>' (Lua _tok) -> card dict for Card.parse."""
    rs, *mods = tok.split("/")
    c = {"rank": rs[:-1], "suit": rs[-1:]}
    for k, v in zip(("enhancement", "edition", "seal", "bonus", "played_this_ante"), mods):
        if v:
            c[k] = _int(v) if k == "bonus" else bool(_int(v)) if k == "played_this_ante" else v
    return c


def parse_tsv(tsv):
    """Parse decompressed TSV telemetry string -> (state_dict, confs, notes)."""
    d = {
        "phase": "other",
        "jokers": [],
        "consumables": [],
        "consumable_items": [],
        "hand": [],
        "shop": [],
        "hand_levels": {},
        "played_counts": {},
        "round_hand_types": [],
        "vouchers": [],
        "telemetry": True,
    }
    confs = {"hud.phase": 1.0}
    booster_cards = []
    booster_hdr = None
    tarots_used = 0

    for line in tsv.splitlines():
        if not line:
            continue
        p = line.split("\t")
        tag = p[0]
        if tag == "B" and len(p) >= 15:
            d["phase"] = p[1] or "other"
            if p[2]:
                d["blind"] = _closest(p[2], BLIND_LIST) or p[2]
                confs["hud.blind"] = 1.0
            for idx, key in ((3, "blind_chips"), (4, "score"), (5, "hands_left"), (6, "discards_left"),
                             (7, "hand_size"), (8, "money"), (9, "ante"), (10, "round"),
                             (11, "reroll_cost"), (12, "joker_slots")):
                if idx < len(p) and p[idx] != "":
                    d[key] = _int(p[idx])
                    if key not in ("hand_size", "joker_slots", "round", "ante"):
                        confs[f"hud.{key}"] = 1.0
            if p[13]:
                d["deck_type"] = p[13]
            if _int(p[14]):
                d["boss_disabled"] = True
            if len(p) > 15 and p[15] != "":
                d["blind_col"] = _int(p[15])
            if len(p) > 16 and p[16] != "":
                tarots_used = _int(p[16])

        elif tag == "M" and len(p) >= 9:
            d.update(telemetry_version=_int(p[1]), seed=p[2] or None, stake=_int(p[3], 1),
                     round_hands=_int(p[4], 4), round_discards=_int(p[5], 3),
                     discards_used=_int(p[6]), cash_out_ready=bool(_int(p[7])), won=bool(_int(p[8])))

        elif tag == "H" and len(p) >= 13:
            c = {
                "rank": p[1] or "2",
                "suit": p[2] or "S",
                "enhancement": p[3] or None,
                "edition": p[4] or None,
                "seal": p[5] or None,
                "debuff": bool(_int(p[6])),
                "face_down": bool(_int(p[7])),
                "forced": bool(_int(p[8])),
                "bonus": _int(p[9]),
                "highlighted": bool(_int(p[10])),
                "pos": [round(_float(p[11]), 4), round(_float(p[12]), 4)],
                "conf": 1.0,
            }
            if len(p) > 13:
                c["played_this_ante"] = bool(_int(p[13]))
            confs[f"card{len(d['hand'])}"] = 1.0
            d["hand"].append({k: v for k, v in c.items() if k == "highlighted" or v not in (None, False, 0)})

        elif tag == "J" and len(p) >= 17:
            raw_name = p[1]
            name = _closest(raw_name, JOKER_NAMES) or raw_name
            jvars = {}
            spec = get_joker(name)
            defaults = spec.defaults if spec else {}
            mult, chips, xmult, rem = _float(p[9]), _float(p[10]), _float(p[11], 1.0), _int(p[12], -1)
            if mult or p[9] != "" and "mult" in defaults:
                jvars["mult"] = int(mult) if mult.is_integer() else mult
            if chips or p[10] != "" and "chips" in defaults:
                jvars["chips"] = int(chips) if chips.is_integer() else chips
            if xmult != 1 or p[11] != "" and defaults.get("x", defaults.get("xmult", 1)) != 1:
                jvars["x" if name in _X_KEY_JOKERS else "xmult"] = round(xmult, 4)
            if rem >= 0 and name == "Loyalty Card":
                jvars["remaining"] = rem
            if p[13] and name == "To Do List":
                jvars["hand"] = p[13]
            if p[14] and name in ("Ancient Joker", "Castle", "The Idol"):
                jvars["suit"] = p[14][:1].upper()
            if p[15] and name in ("The Idol", "Mail-In Rebate"):
                jvars["rank"] = _int(p[15], 14)
            if name == "Fortune Teller" and tarots_used:
                jvars["tarots"] = tarots_used
            if name == "Yorick" and len(p) > 19 and _int(p[19], -1) >= 0:  # counts down from 23
                jvars["discarded"] = 23 - _int(p[19])
            j = {
                "name": name,
                "edition": p[2] or None,
                "debuff": bool(_int(p[3])),
                "sell_value": _int(p[4], 1),
                "eternal": bool(_int(p[5])),
                "highlighted": bool(_int(p[6])),
                "pos": [round(_float(p[7]), 4), round(_float(p[8]), 4)],
                "vars": jvars,
                "rental": len(p) > 17 and bool(_int(p[17])),
                "perish": _int(p[18], -1) if len(p) > 18 and _int(p[18], -1) >= 0 else None,  # rounds used
                "conf": 1.0,
            }
            confs[f"joker{len(d['jokers'])}"] = 1.0
            d["jokers"].append({k: v for k, v in j.items() if k in ("highlighted", "sell_value") or
                                 (k == "perish" and v is not None) or v not in (None, False, {})})

        elif tag == "C" and len(p) >= 5:
            item = {"name": p[1], "kind": p[2], "pos": [round(_float(p[3]), 4), round(_float(p[4]), 4)], "conf": 1.0}
            confs[f"consumable{len(d['consumables'])}"] = 1.0
            d["consumables"].append(p[1])
            d["consumable_items"].append(item)

        elif tag == "S" and len(p) >= 7:
            kind, name = p[1], p[2]
            if kind == "joker":
                name = _closest(name, JOKER_NAMES) or name
            s = {
                "kind": kind,
                "name": name,
                "price": _int(p[3]),
                "edition": p[4] or None,
                "pos": [round(_float(p[5]), 4), round(_float(p[6]), 4)],
                "conf": 1.0,
            }
            for i, k in ((7, "rental"), (8, "perishable")):  # Orange/Gold Stake stickers
                if len(p) > i and _int(p[i]):
                    s[k] = True
            confs[f"shop{len(d['shop'])}"] = 1.0
            d["shop"].append(s)

        elif tag == "P" and len(p) >= 3:
            booster_hdr = (p[1] or "Buffoon Pack", _int(p[2], 1))

        elif tag == "K" and len(p) >= 8:
            kind = p[1]
            pos = [round(_float(p[6]), 4), round(_float(p[7]), 4)]
            if kind == "playing_card":
                rs = p[2]
                card = {
                    "kind": "playing_card",
                    "rank": rs[:-1] or "2",
                    "suit": rs[-1:] or "S",
                    "edition": p[3] or None,
                    "enhancement": p[4] or None,
                    "seal": p[5] or None,
                    "pos": pos,
                    "conf": 1.0,
                }
            else:
                name = _closest(p[2], JOKER_NAMES) or p[2] if kind == "joker" else p[2]
                card = {"kind": kind, "name": name, "edition": p[3] or None, "pos": pos, "conf": 1.0}
            confs[f"pack{len(booster_cards)}"] = 1.0
            booster_cards.append(card)

        elif tag == "L" and len(p) >= 5:
            hname, lvl, played, pr = p[1], _int(p[2], 1), _int(p[3], 0), _int(p[4], 0)
            if lvl > 1:
                d["hand_levels"][hname] = lvl
            if played > 0:
                d["played_counts"][hname] = played
            if pr > 0:
                d["round_hand_types"].append(hname)

        elif tag == "V" and len(p) >= 2:
            d["vouchers"] = [x for x in p[1:] if x]

        elif tag == "X" and len(p) >= 5:
            for v, k in zip(p[1:6], ("last_tarot_planet", "upcoming_boss", "small_tag", "big_tag", "ox_hand")):
                if v:
                    d[k] = v

        elif tag in ("D", "F") and len(p) >= 2:
            d["deck" if tag == "D" else "full_deck"] = [_deck_card(t) for t in p[1].split()]

    if booster_hdr is not None:
        d["booster"] = {"pack": booster_hdr[0], "choose": booster_hdr[1], "cards": booster_cards}
        confs["booster"] = 1.0

    return d, confs, ["telemetrie 0.00s"]


if __name__ == "__main__":
    import cv2
    from ..autopilot import raised, tooltip_is
    from ..engine import score
    from ..live import state_key
    from ..models import State
    from . import recognize_local
    from .cards import read_hand
    from .jokers import read_jokers, read_shop
    from .layout import normalize

    tsv = "\n".join([
        "B\tplaying\tThe Flint\t12000\t4500\t3\t2\t8\t34\t4\t9\t5\t5\tPlasma Deck\t0\t2\t7",
        "H\tK\tH\tsteel\tpoly\tred\t0\t0\t0\t15\t1\t0.3120\t0.6180",
        "H\tQ\tS\tglass\t\tblue\t0\t1\t0\t0\t0\t0.3810\t0.6420",
        "J\tRide the Bus\tholo\t0\t4\t0\t0\t0.4120\t0.2180\t18\t0\t1\t-1\t\t\t\t0",
        "J\tLucky Cat\t\t0\t3\t1\t1\t0.5120\t0.2180\t0\t0\t2.25\t-1\t\t\t\t0",
        "J\tFortune Teller\t\t0\t3\t0\t0\t0.6120\t0.2180\t0\t0\t1\t-1\t\t\t\t0",
        "C\tJupiter\tplanet\t0.9800\t0.2100",
        "L\tPair\t5\t14\t1",
        "V\tGrabber\tCrystal Ball",
    ])
    # Simulate a window capture with top/bottom black letterbox bars + stream blur + JPEG compression
    capture = np.zeros((1042, 1248, 3), np.uint8)
    capture[53:990, :] = 90
    capture[53:990, :] = encode_strip(capture[53:990, :], tsv)
    capture = cv2.GaussianBlur(capture, (3, 3), 0.7)
    _, jpg = cv2.imencode(".jpg", capture, [cv2.IMWRITE_JPEG_QUALITY, 55])
    capture = cv2.imdecode(jpg, cv2.IMREAD_COLOR)

    d, confs, notes = recognize_local(capture)
    assert d["phase"] == "playing" and d["blind"] == "The Flint" and d["money"] == 34
    assert d["hand"][0]["enhancement"] == "steel" and d["hand"][0]["edition"] == "poly" and d["hand"][0]["seal"] == "red"
    assert d["hand"][1]["face_down"] is True and d["hand"][1]["rank"] == "Q"
    assert d["jokers"][0]["vars"] == {"mult": 18} and d["jokers"][1]["vars"] == {"x": 2.25}
    assert d["jokers"][2]["vars"] == {"tarots": 7}
    assert d["hand_levels"] == {"Pair": 5} and d["vouchers"] == ["Grabber", "Crystal Ball"]

    canvas = normalize(capture)
    assert raised(read_hand(canvas)) == {0}
    assert tooltip_is(canvas, (0.512, 0.218), "Lucky Cat")
    assert len(read_jokers(canvas)) == 3 and read_shop(canvas) == []
    # Selecting/deselecting a card must not change state_key
    tsv2 = tsv.replace("\t1\t0.3120", "\t0\t0.3120")
    cap2 = capture.copy()
    cap2[53:990, :] = encode_strip(cap2[53:990, :], tsv2)
    d2, confs2, _ = recognize_local(cap2)
    assert state_key(d, confs) == state_key(d2, confs2)
    st = State.from_dict(d)
    assert score(st, [0]).score > 0
    print("telemetry ok")

