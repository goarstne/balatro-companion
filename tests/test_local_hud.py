"""Local HUD reader over all dataset frames: predictions vs labels, timing, exact checks on verified frames.

.venv/bin/python -m tests.test_local_hud
"""
import json
import time

from balatro.localvision.hud import read_hud
from balatro.localvision.layout import DATASET, normalize

FIELDS = ["phase", "blind", "blind_chips", "score", "hands_left", "discards_left", "money", "ante", "round",
          "reroll_cost"]
CONFIDENT = 0.8


def main():
    stats = {f: [0, 0, 0] for f in FIELDS}  # labelled & confident, correct among those, labelled total
    times = []
    for png in sorted(DATASET.glob("*.png")):
        label = json.loads(png.with_suffix(".json").read_text())
        t = time.perf_counter()
        got = read_hud(normalize(png))
        times.append(time.perf_counter() - t)
        line = []
        for f in FIELDS:
            want, have, conf = label.get(f), got[f], got["conf"][f]
            if want is not None:
                s = stats[f]
                s[2] += 1
                if conf >= CONFIDENT:
                    s[0] += 1
                    s[1] += have == want
            mark = "" if want is None or have == want else f"(label {want})"
            line.append(f"{f}={have}{mark}@{conf:.2f}")
        print(f"{png.stem} {times[-1] * 1000:.0f}ms", " ".join(line))

        if label.get("verified"):
            for f in [f for f in ["phase", "blind", "blind_chips", "score", "hands_left", "discards_left", "money",
                                  "ante"] if f in label]:  # a shop frame has no blind / target
                assert got[f] == label[f], (png.stem, f, got[f], label[f])
                assert got["conf"][f] >= CONFIDENT, (png.stem, f, got["conf"][f])
        if label.get("phase") == "other":
            assert got["phase"] in ("other", "cash_out"), (png.stem, got["phase"])  # Claude labels cash-out "other"
        # Confidently wrong is the worst outcome.
        for f in FIELDS:
            if label.get(f) is not None and got["conf"][f] >= CONFIDENT and f != "phase":
                assert got[f] == label[f] or not label.get("verified"), (png.stem, f, got[f], label[f])

    print("\nfield          confident/labelled  correct-when-confident")
    for f, (c, ok, n) in stats.items():
        print(f"{f:14} {c:3}/{n:<3}              {ok}/{c}")
    print(f"time: mean {sum(times) / len(times) * 1000:.0f} ms, max {max(times) * 1000:.0f} ms")
    assert max(times) < 1.0


if __name__ == "__main__":
    main()
