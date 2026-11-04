"""Autopilot: carries out recommendations with the mouse in the game (Hyprland, virtual pointer).

Positions are fractions of the game canvas (as in localvision); Screen converts them to logical
screen coordinates. Before every Play/Discard the selection is checked on a fresh screenshot, so
the wrong cards are never played.
"""
import time
import json
from dataclasses import asdict

import cv2
import numpy as np

from .localvision.booster import read_pack_tooltip
from .localvision.cards import read_hand, read_pack_hand
from .localvision.hud import SELECT_COLS, blind_select_column
from .localvision.jokers import read_jokers, read_shop
from .localvision.layout import REF_W, canvas_box, normalize
from .localvision.telemetry import decode_strip
from .data import SUIT_DEBUFF_BOSSES
from .models import Card, Joker, State
from .pointer import Pointer, focus_window_at, move_cursor
from .packs import choose_from_pack, use_consumable
from .shop import shop_plan
from .strategy import blind_decision

BUTTONS = {"play": (0.43, 0.792), "discard": (0.662, 0.792), "next_round": (0.3576, 0.4365),
           "cash_out": (0.5456, 0.4312), "reroll": (0.3576, 0.538), "skip_pack": (0.706, 0.8133)}
SELECT_Y = 0.3735     # "Select" button of the active blind column
SKIP_D = (0.0193, 0.7199)  # red "Skip Blind" under the active small/big column: dx from Select, y (measured)
# A clicked shop item rises and shows its button this far below its resting centre (measured per kind)
BUY_DY = {"booster_pack": 0.0907, "voucher": 0.062}  # OPEN / REDEEM (both green); cards (BUY, orange): 0.069
CARD_BUY_DY = 0.069
PICK_DY = 0.065       # under a clicked card of an opened pack: SELECT (green; jokers, playing cards) or USE (red)
SELL_D = (0.0744, -0.021)  # SELL button beside a clicked joker in the tray
# search only right of the joker (its edge: +0.044): green card art (Oops! All 6s) merged with the green SELL
# button into one blob the size check rejects - run 20261030-220724 move 135 hung on it
SELL_REACH = (0.028, 0.055)
USE_D = (0.064, 0.019)     # USE button beside a clicked consumable (SELL sits above it)
PARK = (0.13, 0.57)   # the hand-score box of the left panel: no tooltip, far from the remote taskbar edge
RAISE = 0.018         # selected cards sit ~0.03 canvas heights above their place in the fan
FAN = (0.545, 0.24)   # the hand is a fan: y drops by FAN[1] * (x - FAN[0])^2 towards the edges (measured)


class Screen:
    """Canvas fractions of a full window capture -> logical screen coordinates."""

    def __init__(self, geom, img):
        xy, wh = geom.split(" ")
        self.x, self.y = map(int, xy.split(","))
        self.k = img.shape[1] / int(wh.split("x")[0])  # capture pixels per logical pixel
        self.box = canvas_box(img)

    def point(self, fx, fy):
        r0, r1, c0, c1 = self.box
        return self.x + (c0 + fx * (c1 - c0)) / self.k, self.y + (r0 + fy * (r1 - r0)) / self.k


BUTTON_COLOURS = {
    "orange": lambda b, g, r: (r > 200) & (g > 120) & (g < 190) & (b < 90),       # BUY
    "green": lambda b, g, r: (g > 150) & (g - r > 40) & (g - b > 15),            # OPEN, SELECT, SELL
    "red": lambda b, g, r: (r > 190) & (r - g > 90) & (r - b > 80),              # USE
}


def button_near(canvas, want, colours=("orange", "green", "red"), reach=(0.06, 0.035)):
    """Centre (fractions) of the button of one of `colours` closest to `want` within +-reach, or None.
    A button is a solid rectangle of button size; card art (Raised Fist's orange rays) is not."""
    H, W = canvas.shape[:2]
    x0, x1 = max(0, int((want[0] - reach[0]) * W)), int((want[0] + reach[0]) * W)
    pad = 0.03  # look a bit beyond reach so a button near its edge isn't cut by the crop; its centre must be in reach
    y0, y1 = max(0, int((want[1] - reach[1] - pad) * H)), int((want[1] + reach[1] + pad) * H)
    b, g, r = (canvas[y0:y1, x0:x1, i].astype(np.int16) for i in range(3))
    if b.size == 0:  # empty capture (stream hiccup): no button, the caller looks again
        return None
    m = np.zeros(b.shape, bool)
    for c in colours:
        m |= BUTTON_COLOURS[c](b, g, r)
    m = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))  # close the label text
    n, lab, st, cen = cv2.connectedComponentsWithStats(m)
    st, cen = st.copy(), cen.copy()
    for i in range(1, n):  # green art below the button (Green Joker's hat) makes the blob too tall: keep the
        bx, by, bw, bh, a = st[i]  # longest run of solidly filled rows - the button; art rows are ragged
        if bh > 80:
            full = (lab[by:by + bh, bx:bx + bw] == i).mean(axis=1) >= 0.8
            runs = np.flatnonzero(np.diff(np.r_[0, full.astype(np.int8), 0]))
            if len(runs):
                s, e = max(zip(runs[::2], runs[1::2]), key=lambda r: r[1] - r[0])
                st[i] = bx, by + s, bw, e - s, (lab[by + s:by + e, bx:bx + bw] == i).sum()
                cen[i] = bx + bw / 2, by + (s + e) / 2
    h = m.shape[0]
    # a blob touching the crop's top/bottom was cut to size by it (Smiley Face's orange art above the BUY
    # spot), not a button; left/right stay allowed: the SELL crop starts right at the button's edge
    hits = [((cx + x0) / W, (cy + y0) / H) for (bx, by, bw, bh, a), (cx, cy) in zip(st[1:], cen[1:])
            if 35 <= bw <= 120 and 15 <= bh <= 80 and bw >= 0.75 * bh and a > 0.6 * bw * bh  # SELL $2: 63x64
            and by > 0 and by + bh < h and abs((cy + y0) / H - want[1]) <= reach[1]]
    return min(hits, key=lambda c: (c[0] - want[0]) ** 2 + (c[1] - want[1]) ** 2, default=None)


def endless_button(canvas):
    """Centre of the wide blue Endless Mode button of the win screen (measured 375x68 px at 0.645/0.74), or None.
    Telemetry has no win flag: the phase stays cash_out under the win overlay."""
    b, g, r = (canvas[..., i].astype(np.int16) for i in range(3))
    m = ((b > 200) & (b - r > 120) & (b - g > 40)).astype(np.uint8)
    n, _, st, cen = cv2.connectedComponentsWithStats(m)
    H, W = m.shape
    return next(((cx / W, cy / H) for (bx, by, bw, bh, a), (cx, cy) in zip(st[1:], cen[1:])
                 if bw > 0.2 * W and 0.04 * H < bh < 0.1 * H and a > 0.7 * bw * bh), None)


def cash_out_button(canvas):
    """The wide orange button appears only after the round payout animation finishes."""
    b, g, r = (canvas[..., i].astype(np.int16) for i in range(3))
    mask = cv2.morphologyEx(BUTTON_COLOURS["orange"](b, g, r).astype(np.uint8),
                           cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    _, _, stats, centers = cv2.connectedComponentsWithStats(mask)
    H, W = mask.shape
    return next(((x / W, y / H) for (_, _, w, h, area), (x, y) in zip(stats[1:], centers[1:])
                 if 0.2 * W < w < 0.5 * W and 0.035 * H < h < 0.12 * H and area > 0.6 * w * h
                 and 0.3 < x / W < 0.8 and 0.15 < y / H < 0.7), None)


def tooltip_is(canvas, pos, name):
    """Is exactly the tray joker `name` at `pos` selected? Telemetry: its highlighted flag (a click on the
    overlapping edge of a neighbour selects the neighbour); else OCR of the tooltip title."""
    tel = decode_strip(canvas)
    if tel is not None:
        hi = [j for j in tel[0].get("jokers", []) if j.get("highlighted")]
        return len(hi) == 1 and hi[0]["name"] == name and abs(hi[0]["pos"][0] - pos[0]) < 0.05
    import difflib
    from .localvision.booster import ocr
    H, W = canvas.shape[:2]
    x, y = pos[0] * W, pos[1] * H
    box = canvas[int(y + 0.06 * H):int(y + 0.16 * H), max(0, int(x - 0.10 * W)):int(x + 0.10 * W)]
    lines = [" ".join(line.split()) for line in ocr(box).splitlines()]
    return max((difflib.SequenceMatcher(None, line, name).ratio() for line in lines), default=0) >= 0.55


def raised(hand):
    if any("highlighted" in c for c in hand):
        return {i for i, c in enumerate(hand) if c.get("highlighted")}
    r = [c["pos"][1] - FAN[1] * (c["pos"][0] - FAN[0]) ** 2 for c in hand]  # height above the fan curve
    return {i for i, v in enumerate(r) if v < max(r) - RAISE}


def _shop_plan(state, d):
    """First action of the shop plan (cheap on a repeat: the advice already scored the same hands)."""
    return shop_plan(state, d["shop"], d.get("ante"), d.get("reroll_cost"), auto=True)[0]


def _pack_choice(state, b):
    return choose_from_pack(state, b["pack"] or "", b["cards"], b.get("choose", 1))


class Autopilot:
    def __init__(self, grab_fn, settle=1.0):
        """grab_fn() -> (geom, BGR image) of the game window, or None."""
        self.grab, self.settle, self.ptr, self.focus = grab_fn, settle, Pointer(), focus_window_at
        self.focused = False  # window focus checked during this act() (3 hyprctl queries per check)
        self.last_mismatch = None
        self.pack_names = {}  # shop pack position -> name read from its tooltip
        self.shop_fails = {}  # (action, item) -> failed attempts this shop visit: no endless click loops

    def _screen(self):
        g = self.grab()
        if g is None:
            raise RuntimeError("game window not visible")
        geom, img = g
        return Screen(geom, img), normalize(img)

    def click(self, scr, fx, fy):
        if not getattr(self, "allowed", lambda: True)():
            raise RuntimeError("Autopilot paused before click")
        if not getattr(self, "focused", False):  # once per act(): the window doesn't change between clicks
            if self.focus(*scr.point(fx, fy)):  # unfocused: the click would be swallowed; focusing may shift it
                time.sleep(0.4)
                scr = self._screen()[0]
            self.focused = True
        if not getattr(self, "allowed", lambda: True)():
            raise RuntimeError("Autopilot paused before click")
        self.ptr.click(*scr.point(fx, fy))

    def park(self, scr):
        move_cursor(*scr.point(*PARK))
        time.sleep(0.05)
        self.ptr.nudge()  # without a motion event the game keeps the last card's hover tooltip

    def act(self, d, rec, details=None):
        """Carry out the recommendation for recognised state d. -> (done, short German status)."""
        phase = d.get("phase")
        self.focused = False
        if phase != "shop":
            self.pack_names, self.shop_fails = {}, {}
        if phase == "playing" and d.get("blind") == "Verdant Leaf" and not d.get("boss_disabled"):
            state = State.from_dict(d)
            if not any(j.name == "Chicot" and not j.debuff for j in state.jokers):
                from .shop import ShopAction
                from .strategy import leaf_sale
                sale = leaf_sale(state, d.get("ante") or 8)
                if sale is None or not d.get("telemetry"):
                    return False, "Verdant Leaf: sell a joker manually to remove the debuff"
                return self._shop_action({**d, "shop": []}, state,
                                         ShopAction("sell", sale, "disable Verdant Leaf"))
        if phase in ("shop", "playing", "blind_select") and d.get("consumable_items"):
            done = self.consumables(d, phase)
            if done:
                return done
        tel = bool(d.get("telemetry"))
        if phase == "playing" and rec is not None:
            if tel and rec.joker_order:
                desired = [asdict(j) for j in rec.joker_order[0]]
                return self.reorder(d, "jokers", desired)
            if tel and rec.card_order:
                desired = [d["hand"][i] for i in rec.card_order[0]]
                return self.reorder(d, "hand", desired)
            idx = rec.play_idx if rec.action == "play" else rec.discard.discard_idx
            ok = self.cards(rec.action, idx, [(c["rank"], c["suit"]) for c in d["hand"]],
                            SUIT_DEBUFF_BOSSES.get(d.get("blind")))
            if not ok:
                return False, f"hand on screen doesn't match the recognition – nothing pressed {self.last_mismatch}"
            left = (d.get("hands_left"), d.get("discards_left"))
            if self._confirm(lambda t: (t.get("hands_left"), t.get("discards_left")) != left
                             or t.get("phase") != "playing", 3.0, tel) is False:
                return False, "Play/Discard had no visible effect (click lost?)"
            return True, "Played" if rec.action == "play" else "Discarded"
        if phase == "blind_select":
            scr, canvas = self._screen()
            col = blind_select_column(canvas)
            if col is None:
                return False, "Select button not found"
            blind, tag, choice, _ = blind_decision(d, State.from_dict(d))
            skip = choice == "skip" and button_near(canvas, (SELECT_COLS[col] / REF_W + SKIP_D[0], SKIP_D[1]),
                                                    ("red",))
            if skip:  # a tag may open a pack right away (Charm, Buffoon …): any change of column or phase counts
                self.click(scr, *skip)
                self.park(scr)
                if self._confirm(lambda t: t.get("phase") != "blind_select" or t.get("blind_col") != col,
                                 3.0, tel) is False:
                    return False, "Skip had no visible effect"
                return True, f"{blind} skipped for {tag}"
            self.click(scr, SELECT_COLS[col] / REF_W, SELECT_Y)
            self.park(scr)
            if self._confirm(lambda t: t.get("phase") != "blind_select", 3.0, tel) is False:
                return False, "Select had no visible effect"
            return True, "Blind selected (Select)"
        if phase == "cash_out":
            scr, canvas = self._screen()
            endless = endless_button(canvas)
            if endless:  # won: keep playing
                self.click(scr, *endless)
                self.park(scr)
                time.sleep(self.settle)
                if endless_button(self._screen()[1]):
                    return False, "Endless Mode had no visible effect"
                return True, "Won – continuing in Endless Mode"
            end = time.monotonic() + 12
            while True:
                tel_state = decode_strip(canvas)
                observed = tel_state[0] if tel_state else {}
                ready = observed.get("cash_out_ready")
                if ready:
                    pos = cash_out_button(canvas)
                    if pos:
                        break
                # Old hooks: wait until the actual orange Cash Out button is visible.
                pos = cash_out_button(canvas) if ready is None else None
                if pos:
                    break
                if time.monotonic() >= end:
                    return False, "Cash Out is not ready yet"
                time.sleep(0.2)
                scr, canvas = self._screen()
                if endless_button(canvas):
                    return True, "Win screen ready; re-read before continuing"
            self.click(scr, *pos)
            self.park(scr)
            if self._confirm(lambda t: t.get("phase") != "cash_out", 3.0, tel) is False:
                return False, "Cash Out had no visible effect"
            return True, "Cash Out"
        if phase == "shop" and d.get("shop") is not None:
            return self.shop(d, (details or {}).get("shop_actions"))
        if phase == "booster_pack" and d.get("booster"):
            return self.booster(d)
        return False, f"phase {phase!r}: no automatic action"

    def _press(self, scr, pos, dy, dx=0.0, colour="green", check=None, reach=(0.06, 0.035)):
        """Click an item, wait for its button, click the button: found by colour near the expected spot (item
        sizes differ, e.g. Jumbo packs sit higher). check(canvas) -> False aborts (item deselected).
        -> True if the button was clicked, False if no button showed, None if check() refused."""
        want = (pos[0] + dx, pos[1] + dy)
        scr, canvas = self._screen()
        hit = button_near(canvas, want, (colour,), reach)
        if hit is None:  # not selected yet (clicking a selected item deselects)
            self.click(scr, *pos)
            deadline = time.monotonic() + 3 * self.settle  # stream lag: measured 0.8 s until the button shows
            while True:
                time.sleep(0.15)
                scr, canvas = self._screen()
                hit = button_near(canvas, want, (colour,), reach)
                if hit is not None or time.monotonic() > deadline:
                    break
        refused = hit is not None and check is not None and not check(canvas)
        if hit is None or refused:
            if hit is not None:
                self.click(scr, *pos)  # deselect, press nothing
            self.park(scr)
            return None if refused else False
        self.click(scr, *hit)
        self.park(scr)
        return True

    def _confirm(self, done, timeout=4.0, telemetry=True):
        """After a click: poll fresh frames until done(telemetry state) holds. -> True, False (no visible effect
        within timeout: click lost in the stream?) or None (no telemetry: can't tell). With telemetry=True a
        frame without the strip (stream artefact, notification over it) is skipped, never taken as success."""
        end = time.monotonic() + timeout
        while True:
            tel = decode_strip(self._screen()[1])
            if tel is None and not telemetry:
                return None
            if tel is not None and done(tel[0]):
                return True
            if time.monotonic() > end:
                return False
            time.sleep(0.15)

    def _select(self, want, read, expect=None):
        """Make exactly the hand cards `want` selected: click only the difference (a click on a selected card
        deselects it - Cerulean Bell's forced card, a retry), then poll until the screen shows it.
        read(canvas) -> hand; expect = [(rank, suit)] the hand must match. -> (scr, hand) or None."""
        scr, canvas = self._screen()
        for _ in range(4):
            hand = read(canvas)
            if expect is not None and not self._matches(hand, expect):
                time.sleep(self.settle)  # cards still moving (raise animation, stream lag): look again
                scr, canvas = self._screen()
                continue
            have = raised(hand)
            if have == want:
                return scr, hand
            for i in sorted(have ^ want):
                self.click(scr, *hand[i]["pos"])
                time.sleep(0.05)
            self.park(scr)
            end = time.monotonic() + 1.5  # stream lag spikes: measured up to ~1.3 s
            while time.monotonic() < end:
                time.sleep(0.15)
                scr, canvas = self._screen()
                if raised(read(canvas)) == want:
                    break
        return None

    def _matches(self, hand, expect):
        boss_suit = getattr(self, "boss_suit", None)
        for c in hand:  # same correction as recognize_local: the boss fixes a debuffed card's suit
            if boss_suit and c.get("debuff") and "highlighted" not in c:  # telemetry cards: exact suit
                c["suit"] = boss_suit
        same = sum((c["rank"], c["suit"]) == e for c, e in zip(hand, expect))
        required = len(expect) if any("highlighted" in c for c in hand) else 0.75 * len(expect)
        if len(hand) != len(expect) or same < required:
            self.last_mismatch = [c["rank"] + c["suit"] for c in hand], [r + u for r, u in expect]
            return False
        return True

    def reorder(self, d, area, desired):
        """One drag per action; confirm the entire intermediate order on fresh telemetry."""
        parse = Joker.parse if area == "jokers" else Card.parse
        key = lambda item: json.dumps(asdict(parse(item)), sort_keys=True)
        original = [key(c) for c in d.get(area, [])]
        target = [key(c) for c in desired]
        if sorted(original) != sorted(target):
            return False, "Reorder rejected: card identities changed"
        scr, canvas = self._screen()
        tel = decode_strip(canvas)
        if tel is None or tel[0].get("phase") != d.get("phase"):
            return False, "Reorder requires fresh telemetry in the same phase"
        cards = tel[0].get(area, [])
        if [key(c) for c in cards] != original:
            return False, "Reorder rejected: row changed"
        for destination, wanted in enumerate(target):
            if original[destination] == wanted:
                continue
            source = original.index(wanted, destination + 1)
            if self.focus(*scr.point(*cards[source]["pos"])):
                return False, "Window focused; re-read before dragging"
            self.focused = True
            intermediate = original[:]
            intermediate.insert(destination, intermediate.pop(source))
            if not getattr(self, "allowed", lambda: True)():
                return False, "Autopilot paused before drag"
            self.ptr.drag(scr.point(*cards[source]["pos"]), scr.point(*cards[destination]["pos"]))
            self.park(scr)
            if self._confirm(lambda t, want=intermediate: t.get("phase") == d.get("phase") and
                             [key(c) for c in t.get(area, [])] == want, timeout=5) is not True:
                return False, "Drag order was not confirmed; nothing played"
            original = intermediate
            _, canvas = self._screen()
            tel = decode_strip(canvas)
            if tel is None or [key(c) for c in tel[0].get(area, [])] != original:
                return False, "Row changed after drag; nothing played"
            cards = tel[0][area]
        return True, f"Reordered {area}"

    def _death_order(self, d, choice, name):
        if name != "Death" or len(choice.targets) != 2 or choice.targets[0] < choice.targets[1]:
            return None
        if not d.get("telemetry"):
            return False, "Death needs a confirmed hand reorder; telemetry unavailable"
        weak, strong = choice.targets
        desired = list(d["hand"])
        desired[weak], desired[strong] = desired[strong], desired[weak]
        return self.reorder(d, "hand", desired)

    def shop(self, d, actions=None):
        """Carry out the first action of the shop plan (the shop re-lays out after every action, the live loop
        re-reads it and calls again): buy (joker/card BUY, pack OPEN, voucher REDEEM), sell, reroll, leave."""
        scr, canvas = self._screen()
        self._name_packs(scr, d)
        state = State.from_dict(d)
        from .shop import ShopAction
        action = ShopAction(**actions[0]) if actions and d.get("telemetry") else _shop_plan(state, d)
        target = (action.kind, d["shop"][action.index]["name"] if action.kind == "buy" else
                  state.jokers[action.index].name if action.kind == "sell" else "")
        if self.shop_fails.get(target, 0) >= 2:  # keyed by the action, so flickering reads can't reset it
            return False, f"{action.kind} {target[1]} failed twice – please do it yourself"
        ok, msg = self._shop_action(d, state, action)
        if not ok:
            self.shop_fails[target] = self.shop_fails.get(target, 0) + 1
        return ok, msg

    def _shop_action(self, d, state, action):
        from .localvision import MIN_CONF
        scr, canvas = self._screen()
        money, n_shop, n_jokers = d.get("money", 0), len(d["shop"]), len(state.jokers)
        tel = bool(d.get("telemetry"))
        if action.kind == "buy":
            it = d["shop"][action.index]
            # the item on the fresh frame: same kind and name near the recognised spot (a tooltip can hide
            # others; a re-laid-out shop puts another offer there). Vision-read pack names are unreliable.
            near = [i for i in read_shop(canvas) if i["kind"] == it["kind"] and it.get("pos")
                    and abs(i["pos"][0] - it["pos"][0]) < 0.04 and abs(i["pos"][1] - it["pos"][1]) < 0.06
                    and (i["name"] == it["name"] or i.get("conf", 0) < MIN_CONF
                         or it["kind"] == "booster_pack" and not d.get("telemetry"))]
            if not near:
                return False, f"{it['name']} no longer in its place – nothing bought"
            colour = "orange" if it["kind"] in ("joker", "planet", "tarot", "spectral", "playing_card") \
                else "green"
            if not self._press(scr, near[0]["pos"], BUY_DY.get(it["kind"], CARD_BUY_DY), colour=colour):
                return False, f"buy button for {it['name']} not found – nothing bought"
            if self._confirm(lambda t: t.get("money", 0) != money or len(t.get("shop", [])) != n_shop
                             or t.get("phase") != "shop", telemetry=tel) is False:
                return False, f"buying {it['name']} had no visible effect"
            return True, f"Bought: {it['name']} – {action.why}"
        if action.kind == "sell":
            js = read_jokers(canvas)
            if len(js) != len(state.jokers):
                return False, "joker row doesn't match the recognition – nothing sold"
            name = state.jokers[action.index].name
            pos = js[action.index]["pos"]
            pressed = self._press(scr, pos, SELL_D[1], SELL_D[0], "green", check=lambda c: tooltip_is(c, pos, name),
                                  reach=SELL_REACH)
            if pressed is None:
                return False, f"{name} not reliably selected (tooltip) – nothing sold"
            if not pressed:
                return False, f"SELL button for {name} not found – nothing sold"
            if self._confirm(lambda t: len(t.get("jokers", [])) < n_jokers, telemetry=tel) is False:
                return False, f"selling {name} had no visible effect"
            return True, f"Sold: {name} – {action.why}"
        if action.kind == "reroll":
            self.click(scr, *BUTTONS["reroll"])
            self.park(scr)
            if self._confirm(lambda t: t.get("money", 0) != money, telemetry=tel) is False:
                return False, "Reroll had no visible effect"
            return True, f"Reroll – {action.why}"
        self.click(scr, *BUTTONS["next_round"])
        self.park(scr)
        if self._confirm(lambda t: t.get("phase") != "shop", telemetry=tel) is False:
            return False, "Next Round had no visible effect"
        return True, "Left the shop (Next Round)" + (f" – {action.why}" if action.why else "")

    def consumables(self, d, phase):
        """Use the first held planet/tarot packs.use_consumable wants used now (tarot targets need the hand,
        so those only while playing). -> (done, status) or None if nothing to use."""
        state = State.from_dict(d)
        for c in d["consumable_items"]:
            choice = use_consumable(state, c["name"], c["kind"])
            if choice.index != 0 or (choice.targets and phase != "playing"):
                continue
            rearrange = self._death_order(d, choice, c["name"])
            if rearrange:
                return rearrange
            scr, canvas = self._screen()
            if choice.targets:
                sel = self._select(set(choice.targets), read_hand, [(h["rank"], h["suit"]) for h in d["hand"]])
                if sel is None:
                    return False, "hand doesn't match the recognition – consumable not used"
                scr = sel[0]
            if not self._press(scr, c["pos"], USE_D[1], USE_D[0], "red"):
                return False, f"USE button for {c['name']} not found"
            n = len(d["consumable_items"])
            if self._confirm(lambda t, n=n: len(t.get("consumables", [])) < n, telemetry=bool(d.get("telemetry"))) is False:
                return False, f"using {c['name']} had no visible effect"
            return True, f"Used: {c['name']} – {choice.why}"
        return None

    def _name_packs(self, scr, d):
        """Pack wrappers are animated art, often misread: name each shop pack from its tooltip (click, OCR,
        click again to deselect). Cached per position while the shop lasts."""
        if d.get("telemetry"):
            return
        for it in d["shop"]:
            if it["kind"] != "booster_pack" or not it.get("pos"):
                continue
            key = tuple(round(v, 2) for v in it["pos"])
            if not self.pack_names.get(key):  # unread or failed (None): try again on this fresh frame
                want = (it["pos"][0], it["pos"][1] + BUY_DY["booster_pack"])
                selected = button_near(self._screen()[1], want, ("green",)) is not None  # don't deselect it
                if not selected:
                    self.click(scr, *it["pos"])
                    time.sleep(self.settle)
                self.pack_names[key] = read_pack_tooltip(self._screen()[1], it["pos"])
                if not selected:
                    self.click(scr, *it["pos"])
                    time.sleep(0.3)
                self.park(scr)
            if self.pack_names[key]:
                it["name"] = self.pack_names[key]

    def booster(self, d):
        """Opened pack: pick the card packs.choose_from_pack wants (tarot targets: hand cards first), else Skip."""
        b, state = d["booster"], State.from_dict(d)
        choice = _pack_choice(state, b)
        scr, canvas = self._screen()
        if choice.index is None or choice.index >= len(b["cards"]):
            self.click(scr, *BUTTONS["skip_pack"])
            self.park(scr)
            return True, f"Pack skipped – {choice.why}"
        card = b["cards"][choice.index]
        rearrange = self._death_order(d, choice, card.get("name"))
        if rearrange:
            return rearrange
        if choice.targets:  # Arcana/Spectral: the hand is dealt below the offered cards
            sel = self._select(set(choice.targets), read_pack_hand, [(h["rank"], h["suit"]) for h in d["hand"]])
            if sel is None:
                return False, "hand cards in the pack don't match the recognition – nothing picked"
            scr = sel[0]
        use = card.get("kind") in ("planet", "tarot", "spectral")  # used at once: red USE, else green SELECT
        if not self._press(scr, card["pos"], PICK_DY, colour="red" if use else "green"):
            return False, ("USE button (red) not found – card may not be usable" if use
                           else "SELECT button in the pack not found")
        n = len(b["cards"])
        if self._confirm(lambda t: t.get("phase") != "booster_pack"
                         or len((t.get("booster") or {}).get("cards", [])) < n, timeout=8.0 if use else 4.0,
                         telemetry=bool(d.get("telemetry"))) is False:
            return False, "pack pick had no visible effect"
        return True, f"From the pack: {card.get('name') or card.get('rank', '') + card.get('suit', '')} – {choice.why}"

    def cards(self, action, idx, expect, boss_suit=None):
        """Select exactly the cards idx (hand order, left to right) and press Play/Discard.
        expect = [(rank, suit)] of the recognised hand. -> True if pressed; False if the hand on screen
        doesn't match (then nothing is pressed)."""
        self.boss_suit = boss_suit
        sel = self._select(set(idx), read_hand, expect)
        if sel is None:
            return False
        self.click(sel[0], *BUTTONS[action])
        self.park(sel[0])
        return True


if __name__ == "__main__":
    img = np.zeros((1042, 1250, 3), np.uint8)
    img[53:990] = 90
    s = Screen("1046,28 1000x834", img)
    x, y = s.point(0.43, 0.792)
    assert abs(x - (1046 + 537 / 1.25)) < 1.5 and abs(y - (28 + 795 / 1.25)) < 1.5, (x, y)
    hand = [{"pos": [0.3, 0.654]}, {"pos": [0.5, 0.643 - 0.035]}, {"pos": [0.6, 0.640]}, {"pos": [0.79, 0.617]}]
    assert raised(hand) == {1, 3}
    # measured frame: A♠ (left edge) raised; the fan curve alone made it look unraised
    ys = [0.629, 0.62, 0.648, 0.646, 0.646, 0.613, 0.612, 0.649]
    xs = [0.3058, 0.3737, 0.4448, 0.5144, 0.5831, 0.6527, 0.723, 0.7902]
    assert raised([{"pos": p} for p in zip(xs, ys)]) == {0, 1, 5, 6}
    ys = [0.6548, 0.6184, 0.6496, 0.6413, 0.6382, 0.609, 0.6142, 0.6475]
    assert raised([{"pos": p} for p in zip(xs, ys)]) == {1, 5, 6}
    from .localvision.layout import DATASET
    # measured: a Jumbo pack's OPEN sat 0.01 below the fixed offset (clicks missed); the colour search finds it
    shop = normalize(DATASET / "20261027-161400-shop-grey-reroll.png")
    assert button_near(shop, (0.5, 0.95)) is None
    # measured: a selected planet in a pack shows a small red USE right under it (not the tall box the Lua
    # UI definition suggests); red Mars art next to it must not count as a button
    from .localvision.layout import Path as _P
    samples = _P.home() / ".local/share/balatro/samples"
    mars = (0.5459, 0.6652)
    assert button_near(normalize(samples / "20261027-201920-celestial-open.png"), (mars[0], mars[1] + PICK_DY),
                       ("red",)) is None
    mercury = (0.5459, 0.6675)
    hit = button_near(normalize(samples / "20261027-204500-celestial-use-selected.png"),
                      (mercury[0], mercury[1] + PICK_DY), ("red",))
    assert hit and abs(hit[0] - mercury[0]) < 0.01 and abs(hit[1] - mercury[1] - 0.0686) < 0.01, hit
    # measured (run 20261030-194631, move 43): the two-line SELL $2 button is 63x64 px and was dropped by a
    # 60 px height limit; the joker's pre-click (unraised) position must still find it
    sold = _P.home() / ".local/share/balatro/runs/20261030-194631/043.png"
    if sold.exists():
        hit = button_near(normalize(sold), (0.5811 + SELL_D[0], 0.2295 + SELL_D[1]), ("green",), (0.06, 0.055))
        assert hit and abs(hit[0] - 0.649) < 0.01, hit
    print("autopilot ok")
