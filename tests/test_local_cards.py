"""Local hand-card reader over all dataset frames: rank+suit vs labels, timing, exact checks on verified frames.

.venv/bin/python -m tests.test_local_cards
"""
import json
import time

from balatro.localvision.layout import ASSETS, DATASET, normalize

# hands checked by eye where the label file has no "verified" flag
VERIFIED = {"20261026-214702": ["KH", "KC", "10C", "10D", "9D", "5H", "4H", "3C"]}
CONFIDENT = 0.6  # = localvision.MIN_CONF: below it the integration falls back to Claude


def key(c):
    return c["rank"] + c["suit"]


def main():
    if not (ASSETS / "cards").is_dir() or not DATASET.is_dir():
        print("skipped (no ~/.local/share/balatro/assets/cards or dataset)")
        return
    from balatro.localvision.cards import read_hand

    t = time.perf_counter()
    read_hand(normalize(next(DATASET.glob("*.png"))))
    print(f"cold start {(time.perf_counter() - t) * 1000:.0f}ms")
    total = right = confident = confident_wrong = frames = 0
    times = []
    for png in sorted(DATASET.glob("*.png")):
        label = json.loads(png.with_suffix(".json").read_text())
        want = [key(c) for c in label.get("hand") or []]
        canvas = normalize(png)
        t = time.perf_counter()
        got = read_hand(canvas)
        times.append(time.perf_counter() - t)
        have = [key(c) for c in got]
        if not want:
            print(f"{png.stem} {times[-1] * 1000:.0f}ms no hand labelled ({label.get('phase')}), read {have}")
            continue
        frames += 1
        ok = sum(a == b for a, b in zip(have, want)) if len(have) == len(want) else 0
        total += len(want)
        right += ok
        for c, w in zip(got, want + [None] * len(got)):
            if c["conf"] >= CONFIDENT:
                confident += 1
                confident_wrong += len(have) != len(want) or key(c) != w
        print(f"{png.stem} {times[-1] * 1000:.0f}ms {ok}/{len(want)}", "OK" if have == want else f"label {want}",
              " ".join(f"{key(c)}@{c['conf']:.2f}" for c in got))
        if label.get("verified") or png.stem in VERIFIED:
            assert have == VERIFIED.get(png.stem, want), (png.stem, have, want)
        if png.stem in VERIFIED:  # fanned hand: outer cards used to drop below CONFIDENT
            assert min(c["conf"] for c in got) >= CONFIDENT, (png.stem, [(key(c), c["conf"]) for c in got])

        # a card without a readable corner (stone / face down / covered) must make the hand unsure
        if len(got) >= 3 and have == want:
            x, y = read_hand(canvas, debug=True)[len(got) // 2]["debug"]["corner"]
            blank = canvas.copy()
            blank[y:y + 50, x:x + 30] = 250  # paint the middle card's corner glyph white
            res = read_hand(blank)
            if len(res) == len(got) - 1:  # card lost: the whole hand must be unsure
                assert max(c["conf"] for c in res) < CONFIDENT, (png.stem, "missing card not flagged",
                                                                 [(key(c), c["conf"]) for c in res])
            else:  # a remnant of the glyph is still read: that card must be unsure
                assert len(res) == len(got), (png.stem, "blanked corner", [key(c) for c in res])
                assert res[len(got) // 2]["conf"] < CONFIDENT, (png.stem, "blanked card not flagged",
                                                                [(key(c), c["conf"]) for c in res])

    print(f"\n{right}/{total} cards agree with the labels over {frames} frames;"
          f" confident (conf >= {CONFIDENT}): {confident}, of those disagreeing: {confident_wrong}")
    print(f"per frame: mean {sum(times) / len(times) * 1000:.0f}ms, max {max(times) * 1000:.0f}ms")
    assert max(times) < 0.5


if __name__ == "__main__":
    main()
