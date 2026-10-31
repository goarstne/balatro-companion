"""Shop/pack actions with a fake pointer and telemetry frames (no Wayland). Run: .venv/bin/python -m tests.test_autopilot"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from balatro import autopilot
from balatro.autopilot import Autopilot
from balatro.localvision.telemetry import encode_strip

HDR = "B\tshop\t\t0\t0\t4\t3\t8\t{money}\t2\t5\t5\t5\tRed Deck\t0\t1\t0"


def frame(money, shop):
    """Window capture whose telemetry strip says: shop phase, `money`, offers [(kind, name, x)]."""
    tsv = "\n".join([HDR.format(money=money)] + [f"S\t{k}\t{n}\t5\t\t{x}\t0.45" for k, n, x in shop])
    img = np.zeros((1042, 1250, 3), np.uint8)
    img[53:990] = 90
    img[53:990] = encode_strip(img[53:990], tsv)
    return img


def pilot(frames):
    """Autopilot whose screen shows frames[0], frames[1], ... (the last one repeats); clicks are recorded."""
    ap = Autopilot.__new__(Autopilot)
    ap.settle, ap.last_mismatch, ap.pack_names, ap.shop_fails = 0, None, {}, {}
    ap.clicks, seq = [], list(frames)
    ap.grab = lambda: ("1046,28 1000x834", seq.pop(0) if len(seq) > 1 else seq[0])
    ap.ptr = SimpleNamespace(click=lambda x, y: ap.clicks.append((x, y)), nudge=lambda: None)
    ap.park, ap.focus = (lambda scr: None), (lambda x, y: False)
    ap._confirm = lambda done, timeout=0, telemetry=True: Autopilot._confirm(ap, done, 0, telemetry)
    return ap


def main():
    d = {"phase": "shop", "telemetry": True, "money": 10, "jokers": [], "hand": [],
         "shop": [{"kind": "joker", "name": "Misprint", "price": 5, "pos": [0.4, 0.45]}]}
    autopilot._shop_plan = lambda state, d: SimpleNamespace(kind="buy", index=0, why="test")

    # 1. another offer now sits where Misprint was: nothing is clicked
    ap = pilot([frame(10, [("joker", "Scary Face", 0.4)])])
    ok, msg = ap.shop(d)
    assert not ok and not ap.clicks and "no longer in its place" in msg, msg

    # 2. button pressed but the game shows no effect (click lost): reported as failure, not "Bought"
    ap = pilot([frame(10, [("joker", "Misprint", 0.4)])])
    ap._press = lambda *a, **k: True
    ok, msg = ap.shop(d)
    assert not ok and "no visible effect" in msg, msg
    # ... and the money went down: success
    before = frame(10, [("joker", "Misprint", 0.4)])
    ap = pilot([before, before, frame(5, [])])  # shop() and the buy each look once before clicking
    ap._press = lambda *a, **k: True
    ok, msg = ap.shop(d)
    assert ok and msg.startswith("Bought: Misprint"), msg

    # 3. the same action failing twice is not tried a third time (whatever else flickers in the state)
    ap = pilot([frame(10, [("joker", "Scary Face", 0.4)])])
    for _ in range(2):
        assert not ap.shop(d)[0]
    ap._shop_action = lambda *a: (_ for _ in ()).throw(AssertionError("tried again"))
    ok, msg = ap.shop({**d, "round": 7})
    assert not ok and "failed twice" in msg, msg

    # 4. one frame without the strip (stream artefact) is no confirmation; the next real frame decides
    blank = np.zeros((1042, 1250, 3), np.uint8)
    blank[53:990] = 90
    ap = pilot([blank, frame(5, [])])
    assert Autopilot._confirm(ap, lambda t: t["money"] == 5, 1.0) is True
    ap = pilot([blank])
    assert Autopilot._confirm(ap, lambda t: True, 0.2) is False  # telemetry run: strip gone = no proof
    assert Autopilot._confirm(ap, lambda t: True, 0.2, telemetry=False) is None  # CV run: can't tell

    # 5. tooltip check: the click selected the overlapping neighbour, not the joker to sell
    js = "J\tSplash\t\t0\t1\t0\t{}\t0.4000\t0.2200\t0\t0\t1\t-1\t\t\t\t0\nJ\tMime\t\t0\t2\t0\t{}\t0.4600\t0.2200\t0\t0\t1\t-1\t\t\t\t0"
    img = blank.copy()
    img[53:990] = encode_strip(img[53:990], HDR.format(money=5) + "\n" + js.format(0, 1))
    assert not autopilot.tooltip_is(autopilot.normalize(img), (0.40, 0.22), "Splash")
    img = blank.copy()
    img[53:990] = encode_strip(img[53:990], HDR.format(money=5) + "\n" + js.format(1, 0))
    assert autopilot.tooltip_is(autopilot.normalize(img), (0.40, 0.22), "Splash")

    # 6. target selection clicks only the difference: card 0 is already selected (retry, Cerulean Bell)
    def hand_frame(hi):
        rows = [f"H\t{r}\tS\t\t\t\t0\t0\t0\t0\t{int(i in hi)}\t{0.35 + 0.07 * i:.4f}\t0.6400"
                for i, r in enumerate("2345")]
        img = blank.copy()
        img[53:990] = encode_strip(img[53:990], HDR.replace("shop", "playing").format(money=5) + "\n" + "\n".join(rows))
        return img
    ap = pilot([hand_frame({0}), hand_frame({0, 2})])
    sel = ap._select({0, 2}, autopilot.read_hand, [("2", "S"), ("3", "S"), ("4", "S"), ("5", "S")])
    assert sel is not None and len(ap.clicks) == 1, ap.clicks  # only card 2 clicked, card 0 left alone
    # 7. a green joker (Oops! All 6s) whose green SELL button sits right at its edge (live frame, run
    # 20261030-220724 move 135): the old search merged card art + button into one blob and found nothing
    import cv2
    band = cv2.imread(str(Path(__file__).parent / "fixtures" / "sell_green_joker.jpg"))  # top 40 % of the canvas
    img = np.zeros((960, 1280, 3), np.uint8)
    img[:band.shape[0]] = band
    pos = (0.4898, 0.2297)
    want = (pos[0] + autopilot.SELL_D[0], pos[1] + autopilot.SELL_D[1])
    assert autopilot.button_near(img, want, ("green",), (0.06, 0.055)) is None
    hit = autopilot.button_near(img, want, ("green",), autopilot.SELL_REACH)
    assert hit is not None and abs(hit[0] - 0.5587) < 0.01, hit
    # 8. run 20261031-074053 move 89: the crop cut Smiley Face's orange art to button size, so the unselected
    # card looked selected, the art was clicked instead of BUY - twice, then the autopilot waited
    want = (0.5668, 0.4907 + autopilot.CARD_BUY_DY)
    fx = Path(__file__).parent / "fixtures"
    assert autopilot.button_near(cv2.imread(str(fx / "shop_smiley_unselected.jpg")), want, ("orange",)) is None
    hit = autopilot.button_near(cv2.imread(str(fx / "shop_smiley_selected.jpg")), want, ("orange",))
    assert hit is not None and abs(hit[1] - 0.5576) < 0.01, hit
    # 9. run 20261031-074053: won at ante 8 - the win overlay keeps phase cash_out, Cash Out did nothing
    hit = autopilot.endless_button(cv2.imread(str(fx / "win_screen.jpg")))
    assert hit is not None and abs(hit[0] - 0.645) < 0.01 and abs(hit[1] - 0.74) < 0.01, hit
    assert autopilot.endless_button(cv2.imread(str(fx / "shop_smiley_selected.jpg"))) is None
    # 10. Skip was recommended in 4 runs, the autopilot always selected: red Skip Blind under the active column
    from balatro.localvision.hud import SELECT_COLS
    from balatro.localvision.layout import REF_W, normalize
    sel = normalize(str(fx / "blind_select_small.jpg"))  # run 20261031-091942 move 32, small blind active
    hit = autopilot.button_near(sel, (SELECT_COLS[0] / REF_W + autopilot.SKIP_D[0], autopilot.SKIP_D[1]), ("red",))
    assert hit is not None and abs(hit[0] - 0.3738) < 0.01 and abs(hit[1] - 0.72) < 0.01, hit
    assert autopilot.button_near(sel, (SELECT_COLS[2] / REF_W + autopilot.SKIP_D[0], autopilot.SKIP_D[1]),
                                 ("red",)) is None  # the boss has no Skip
    print("autopilot actions ok")


if __name__ == "__main__":
    main()
