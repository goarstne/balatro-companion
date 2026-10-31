"""Local joker / consumable / shop recognition. Run: .venv/bin/python -m tests.test_local_jokers

Prints predictions vs. the (Claude-made, often wrong) dataset labels for every frame, asserts on frames
whose jokers/shop were checked by eye (VERIFIED), and renders wiki sprites onto an empty-tray frame to
check the whole library: a confidently wrong answer (conf >= MIN_CONF) is the failure that counts.
"""
import json
import random
import time

import cv2
import numpy as np

from balatro.data import JOKER_NAMES
from balatro.localvision import MIN_CONF, jokers as J
from balatro.localvision.layout import ASSETS, DATASET, normalize

NEG, WILY, MIME = ("Joker", "negative"), ("Wily Joker", None), ("Mime", None)  # dataset labels say "Jolly"
RUN = [NEG, WILY, MIME]
# frame -> (jokers [(name, edition)], shop [(kind, name, price)]); None name = unidentifiable (negative)
VERIFIED = {
    "20261026-203104": ([], []),
    "20261026-204900": ([NEG, WILY], []),
    "20261026-210148": (RUN, []),
    "20261026-210506": (RUN, []),
    "20261026-210520": (RUN, [("planet", "Earth", 3), ("planet", "Saturn", 3),
                              ("booster_pack", "Mega Standard Pack", 8), ("booster_pack", "Jumbo Arcana Pack", 6)]),
    "20261026-210604": (RUN, []),
    "20261026-210710": (RUN, []),
    "20261026-210751": (RUN, []),
    "20261026-210822": (RUN, []),
    "20261026-210839": (RUN, []),
    "20261026-210445": (RUN, []),   # "Unstable connection" overlay hides most of two jokers
    "20261026-210731": ([], []),    # not the game
}


def check_row(got, want, frame):
    """Confident entries must be right; a fully confident row must be exactly right."""
    for i, g in enumerate(got):
        if g["conf"] >= MIN_CONF:
            assert i < len(want) and (g["name"], g["edition"]) == want[i], (frame, i, g, want)
    if got and min(g["conf"] for g in got) >= MIN_CONF:
        assert [(g["name"], g["edition"]) for g in got] == want, (frame, got, want)


def dataset():
    frames = sorted(DATASET.glob("*.png"))
    times = []
    for f in frames:
        label = json.loads(f.with_suffix(".json").read_text()) if f.with_suffix(".json").exists() else {}
        c = normalize(f)
        t = time.time()
        jk, co, sh = J.read_jokers(c), J.read_consumables(c), J.read_shop(c)
        times.append(time.time() - t)
        fmt = lambda xs: ", ".join(f"{x['name']}{'/' + x['edition'] if x.get('edition') else ''}"
                                   f"{' $' + str(x['price']) if x.get('price') is not None else ''} {x['conf']:.2f}"
                                   for x in xs)
        print(f"{f.stem} {times[-1] * 1000:4.0f}ms jokers [{fmt(jk)}] cons [{fmt(co)}] shop [{fmt(sh)}]")
        print(f"{'':16} label  jokers {[j.get('name') for j in label.get('jokers', [])]} "
              f"cons {label.get('consumables', [])} shop {[(s['name'], s.get('price')) for s in label.get('shop', [])]}")
        if f.stem in VERIFIED:
            want_j, want_s = VERIFIED[f.stem]
            assert len(jk) == len(want_j) or min((j["conf"] for j in jk), default=0) < MIN_CONF, (f.stem, jk)
            check_row(jk, want_j, f.stem)
            if want_j and want_j[0] == NEG and jk:
                assert jk[0]["edition"] == "negative", (f.stem, jk[0])
            assert all(c["conf"] < MIN_CONF for c in co), (f.stem, co)  # no consumables in these frames
            got_s = [(s["kind"], s["name"], s["price"]) for s in sh]
            assert got_s == want_s or all(s["conf"] < MIN_CONF for s in sh), (f.stem, sh)
            if want_s and J._library("booster_pack"):  # pack sprites: python -m balatro.localvision.jokers
                assert got_s == want_s, (f.stem, got_s)
    if times:
        print(f"{len(frames)} frames, {np.mean(times[1:] or times) * 1000:.0f} ms/frame warm, first {times[0]:.2f} s")
        assert np.median(times) < 0.5


def render(bg, names, rng):
    """Paste joker sprites into the empty joker tray the way Balatro spreads them (+ tilt, blur, scanlines)."""
    c = bg.copy()
    n, cw = len(names), 110
    step = min(237, (590 - cw) / max(1, n - 1))
    for x, name in zip(628 + (np.arange(n) - (n - 1) / 2) * step, names):
        sp = cv2.imread(str(ASSETS / "jokers" / f"{name}.png"), cv2.IMREAD_UNCHANGED)
        h = rng.uniform(140, 152)
        M = cv2.getRotationMatrix2D((sp.shape[1] / 2, sp.shape[0] / 2), rng.uniform(-4, 4), h / sp.shape[0])
        M[:, 2] += (x + rng.uniform(-3, 3) - sp.shape[1] / 2, 218 + rng.uniform(-4, 4) - sp.shape[0] / 2)
        w = cv2.warpAffine(sp, M, (c.shape[1], c.shape[0]), flags=cv2.INTER_AREA)
        a = w[..., 3:4] / 255.0
        c = (c * (1 - a) + w[..., :3] * a).astype(np.uint8)
    c = cv2.GaussianBlur(c, (0, 0), 0.7)
    c[::3] = (c[::3] * 0.9).astype(np.uint8)
    return cv2.imdecode(cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, 70])[1], cv2.IMREAD_COLOR)


def synthetic(frames=30):
    empty = DATASET / "20261026-203104.png"  # empty joker and consumable trays
    if not empty.exists():
        print("synthetic: background frame missing, skipped")
        return
    bg, rng = normalize(empty), random.Random(5)
    exact = trusted = 0
    for _ in range(frames):
        names = rng.sample(JOKER_NAMES, rng.choice([1, 2, 3, 4, 5, 5, 6]))
        got = J.read_jokers(render(bg, names, rng))
        check_row(got, [(n, None) for n in names], names)
        exact += [g["name"] for g in got] == names
        trusted += bool(got) and min(g["conf"] for g in got) >= MIN_CONF
    print(f"synthetic: {exact}/{frames} rows exact, {trusted}/{frames} fully trusted, none confidently wrong")
    assert exact >= 0.7 * frames


def main():
    if not J.assets_ok():
        print("local jokers: sprites missing (python -m balatro.localvision.assets), skipped")
        return
    dataset()
    synthetic()
    print("local jokers ok")


if __name__ == "__main__":
    main()
