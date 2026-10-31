"""Opened booster pack: pack name (OCR of the title panel), offered cards, Skip button.

The offered cards sit in one centred row; their positions follow from the card count (measured: 2 cards
148 px apart, 3 cards 140 px, canvas px of the 1280 layout). The count follows from pack type and size minus
the picks already taken (Mega packs: choose 2), so the row is predicted instead of segmented from the
swirling, pack-coloured background.
"""
import difflib
import subprocess

import cv2
import numpy as np

from .cards import read_hand
from .jokers import _conf, match

TITLE = (578, 760, 825, 830)    # "<Size> <Type> Pack" / "Choose N"
SKIP = (865, 765, 942, 795)     # the Skip button
SKIP_BGR = (132, 131, 113)
ROW = (700, 640)                # centre of the offered row
CARD = (118, 158)
PACKS = {"Arcana": (3, 5), "Celestial": (3, 5), "Standard": (3, 5), "Buffoon": (2, 4), "Spectral": (2, 4)}
KINDS = {"Arcana": ("tarot", "spectral"), "Celestial": ("planet", "spectral"), "Buffoon": ("joker",),
         "Spectral": ("spectral",)}
NAMES = [f"{s}{t} Pack" for s in ("", "Jumbo ", "Mega ") for t in PACKS]


def ocr(img, dark_text=False):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    m = np.where(g < 90 if dark_text else g > 170, 0, 255).astype(np.uint8)
    m = cv2.copyMakeBorder(cv2.resize(m, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST),
                           16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)
    try:
        return subprocess.run(["tesseract", "stdin", "stdout", "--psm", "6"], input=cv2.imencode(".png", m)[1].tobytes(),
                              capture_output=True, timeout=5).stdout.decode().strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def read_pack_tooltip(canvas, pos):
    """Name of a clicked shop pack from its tooltip (left of the pack: dark text on a light box), or None.
    The wrapper art is animated and varies per pack, the tooltip text does not."""
    H, W = canvas.shape[:2]
    x, y = pos[0] * W, pos[1] * H
    # title line (light text on dark) of the tooltip; its right edge is fixed, longer names reach further left
    box = canvas[max(0, int(y - 115)):int(y - 35), max(0, int(x - 340)):int(x - 80)]
    best = max(((difflib.SequenceMatcher(None, " ".join(line.split()), n).ratio(), n)
                for line in ocr(box).splitlines() for n in NAMES), default=(0, None))
    return best[1] if best[0] >= 0.6 else None


def is_booster(canvas):
    x0, y0, x1, y1 = SKIP
    return bool(np.abs(canvas[y0:y1, x0:x1].reshape(-1, 3).mean(0) - SKIP_BGR).max() < 18)


def read_title(canvas):
    """-> (pack name or None, picks shown on the panel)."""
    x0, y0, x1, y1 = TITLE
    lines = [line for line in ocr(canvas[y0:y1, x0:x1]).splitlines() if line.strip()]
    hit = difflib.get_close_matches(lines[0], NAMES, n=1, cutoff=0.6) if lines else []
    choose = next((int(c) for c in "".join(lines[1:]) if c.isdigit()), 1)
    return (hit[0] if hit else None), choose


def _centres(n):
    step = {1: 0, 2: 148, 5: 122}.get(n, 140)  # measured for 2, 3, 4 and 5 cards
    return [ROW[0] + (i - (n - 1) / 2) * step for i in range(n)]


def read_booster(canvas, taken=0):
    """-> {"pack", "choose", "cards": [{kind, name, edition, pos, conf} | playing card dict]} or None.
    taken = cards already picked from this pack (Mega packs show one card fewer after the first pick)."""
    if not is_booster(canvas):
        return None
    name, choose = read_title(canvas)
    if name is None:
        return {"pack": None, "choose": choose, "cards": []}
    ptype = name.split()[-2]
    H, W = canvas.shape[:2]
    if ptype == "Standard":  # playing cards: read the corners like a hand, positions from the same reader
        return {"pack": name, "choose": choose, "cards": [{"kind": "playing_card", **c} for c in read_hand(canvas)]}
    # The panel names only the pack type, not Normal/Jumbo/Mega, and a Mega pack shows one card fewer after
    # the first pick: take the largest card count whose every predicted slot matches a sprite well.
    rows = {}
    for k in range(5, 0, -1):
        row = []
        for x in _centres(k):
            m = match(canvas, (x, ROW[1], *CARD, 0), KINDS[ptype])
            row.append({"kind": m["kind"], "name": m["name"], "edition": m["edition"],
                        "pos": [round(x / W, 4), round(ROW[1] / H, 4)], "conf": round(_conf(m), 3)})
        rows[k] = row
        if min(c["conf"] for c in row) >= 0.35:
            best = row
            break
    else:
        best = max(rows.values(), key=lambda r: np.mean([c["conf"] for c in r]))
    return {"pack": name, "choose": choose, "cards": best}


if __name__ == "__main__":
    from .layout import DATASET, normalize
    for stem, want in (("20261027-182000-buffoon-open", ("Buffoon Pack", ["Seltzer", "Smiley Face"])),
                       ("20261027-185043-jumbo-buffoon-open",
                        ("Buffoon Pack", ["Gluttonous Joker", "Raised Fist", "Midas Mask", "Juggler"])),
                       ("20261027-192233-jumbo-arcana-open",
                        ("Arcana Pack", ["The Wheel of Fortune", "Death", "The Star", "The World", "The High Priestess"])),
                       ("20261027-182500-standard-open", ("Standard Pack", ["2C", "JH", "10H"]))):
        b = read_booster(normalize(DATASET / f"{stem}.png"))
        got = [c.get("name") or c["rank"] + c["suit"] for c in b["cards"]]
        print(stem, b["pack"], got)
        assert (b["pack"], got) == want, (b, want)
    from .cards import read_pack_hand
    hand = read_pack_hand(normalize(DATASET / "20261027-192233-jumbo-arcana-open.png"))
    assert [c["rank"] + c["suit"] for c in hand] == ["KS", "QS", "8H", "6C", "5S", "2D"], hand
    assert min(c["conf"] for c in hand) >= 0.6, hand  # complete row: wider pack spacing accepted
    assert not is_booster(normalize(DATASET / "20261027-171159-head.png"))
    print("booster ok")
