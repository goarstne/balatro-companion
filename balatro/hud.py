"""Presentation model and lifetime of the movable, read-only Quickshell companion."""
import fcntl
import json
import os
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

from .data import BOSSES, HAND_TYPES
from .models import State
from .optimizer import card_name
from .shop import consumable_slots
from .strategy import economy_note, focus_hand, has_clear_focus, interest_of, joker_role, sell_candidates


def model(d, text, rec=None, details=None, notes=(), duration=0):
    """Build display data without re-running any search or simulation."""
    details = details or {}
    state = State.from_dict(d)
    known = bool(state.hand or state.jokers or state.hand_levels)
    focus = focus_hand(state) if known else None
    clear = known and has_clear_focus(state)
    ante = d.get("ante")
    warnings = [n for n in notes if n.startswith("uncertain:") or "failed" in n]
    if d.get("deck") is None:
        warnings.append("Deck left unknown · assuming full deck minus hand" if d.get("full_deck") is not None
                        else "Deck unknown · assuming standard deck minus hand")
    if not d.get("telemetry"):
        warnings.append("Image recognition: hidden joker values and hand levels may be missing")
    warnings.append("Random effects in scores are expected values; win chances are simulations")
    strategy = []
    if focus:
        planet = HAND_TYPES[focus][4]
        strategy.append({"title": "DECK BUILDING", "text":
                         f"Focus {focus} (level {state.level(focus)}): prioritise {planet} and matching jokers."
                         if clear else "No focus yet: build reliable scoring first; don't commit to one hand."})
    if "money" in d:
        strategy.append({"title": "ECONOMY", "text": economy_note(state.money, ante or 1, state)})
    if state.jokers and ante is not None:
        roles = Counter(joker_role(j.name) for j in state.jokers if not j.debuff)
        growth = ("Early: build immediate chips/mult and income." if ante <= 2 else
                  "Mid: strengthen joker scaling and the focus hand." if ante <= 4 else
                  "Late: prioritise strong multipliers and matching synergies.")
        if ante >= 3 and not roles["xmult"]:
            growth += " No xMult joker yet."
        strategy.append({"title": "JOKER PLAN", "text": growth})
        candidates = sell_candidates(state, ante)
        if candidates:
            i, why = candidates[0]
            strategy.append({"title": "CONSIDER REPLACING", "text": f"{state.jokers[i].name}: {why}. Only sell once a replacement is worth it."})
    boss = d.get("upcoming_boss") or d.get("blind")
    if boss in BOSSES:
        strategy.append({"title": "BOSS · " + boss, "text": BOSSES[boss]})
    lines = text.splitlines()
    title, _, explanation = (lines[0].removeprefix("RECOMMENDATION: ") if lines else "Waiting for the game …").partition(" – ")
    recommendation = {"title": title, "reason": "\n".join(filter(None, [explanation, *lines[1:]])), "cards": [], "score": None,
                      "chips": None, "mult": None, "hand_type": None, "need": None,
                      "projected": None, "play_win": None, "discard_win": None, "samples": 0,
                      "joker_order": [], "card_order": [], "reordered_score": None, "log": []}
    alternatives = []
    discards = []
    if rec is not None:
        idx = rec.discard.discard_idx if rec.action == "discard" else rec.play_idx
        recommendation.update(title="DISCARD" if rec.action == "discard" else "PLAY",
                              reason=rec.reason, cards=[card_name(state.hand[i]) for i in idx],
                              score=rec.play.score, chips=rec.play.chips, mult=rec.play.mult,
                              hand_type=rec.play.hand_type, need=max(0, rec.need),
                              projected=state.score + rec.play.score if rec.action == "play" else None,
                              play_win=rec.play_win, discard_win=rec.discard.p_win if rec.discard else None,
                              samples=rec.samples, log=rec.play.log)
        if rec.joker_order:
            recommendation.update(joker_order=[str(j) for j in rec.joker_order[0]],
                                  reordered_score=rec.joker_order[1].score)
        if rec.card_order:
            recommendation["card_order"] = [card_name(state.hand[i]) for i in rec.card_order[0]]
        alternatives = [{"hand": r.hand_type, "score": r.score, "cards": " · ".join(card_name(state.hand[i]) for i in idx)}
                        for idx, r in rec.alternatives[:3]]
        discards = [{"cards": " · ".join(card_name(c) for c in o.discarded), "win": o.p_win,
                     "expected": o.expected, "samples": o.samples} for o in rec.discard_options[:3]]
    pack = d.get("booster") or {}
    choice = details.get("pack_choice")
    pack_items = []
    for i, item in enumerate(pack.get("cards", [])):
        pack_items.append({"name": item.get("name") or str(item.get("rank", "")) + item.get("suit", ""),
                           "chosen": choice is not None and choice["index"] == i})
    # State defaults are useful for the engine, but absent observed values must remain unknown in the UI.
    stats = {k: d.get(k) for k in ("ante", "round", "hands_left", "discards_left", "money", "blind", "blind_chips",
                                  "score", "deck_type", "joker_slots")}
    stats.update(focus=focus, focus_level=state.level(focus) if focus and (d.get("telemetry") or focus in state.hand_levels) else None,
                 interest=interest_of(state.money, state) if "money" in d else None,
                 joker_count=len(state.jokers) if "jokers" in d else None,
                 consumable_count=len(state.consumables) if "consumables" in d else None,
                 consumable_slots=consumable_slots(state) if d.get("telemetry") else None,
                 deck_count=len(state.deck) if state.deck is not None else None,
                 full_deck_count=len(state.full_deck) if state.full_deck is not None else None)
    return {"phase": d.get("phase", "other"), "source": "TELEMETRY" if d.get("telemetry") else "IMAGE RECOGNITION",
            "duration": duration, "stats": stats, "recommendation": recommendation,
            "strategy": strategy, "alternatives": alternatives, "discards": discards,
            "hand": [card_name(c) for c in state.hand], "jokers": [str(j) for j in state.jokers],
            "consumables": state.consumables, "offers": details.get("offers", []),
            "shop_actions": details.get("shop_actions", []), "pack": pack.get("pack"), "pack_items": pack_items,
            "warnings": warnings, "notes": list(notes)}


class Hud:
    def __init__(self, directory):
        self.path = directory / "hud.json"
        self.data = {"schema": 1, "status": "waiting", "message": "Waiting for the game window …", "history": []}
        self.process = self.lock = self.log = None
        self.target = None
        self.round_key = None
        self.last_write = 0

    def __enter__(self):
        if not shutil.which("quickshell"):
            raise RuntimeError("Quickshell is missing (Omarchy installs it with its shell)")
        self.lock = open(Path(os.environ["XDG_RUNTIME_DIR"]) / "balatro-hud.lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            raise RuntimeError("The Balatro widget is already open") from None
        try:
            self.write()
            self.log = open(self.path.with_suffix(".log"), "w+")
            self.process = subprocess.Popen(["quickshell", "-p", str(Path(__file__).parent / "ui" / "shell.qml")],
                                            env={**os.environ, "BALATRO_HUD_FILE": str(self.path)},
                                            stdout=self.log, stderr=self.log)
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.log:
            self.log.close()
        if self.lock:
            self.lock.close()

    def running(self):
        code = self.process.poll()
        if code is not None and code != 0:
            self.log.seek(0)
            raise RuntimeError("Widget beendet: " + self.log.read()[-3000:])
        return code is None

    def select(self, client):
        # Hyprland's stableId is the ext-foreign-toplevel identifier consumed by grim -T.
        self.target = client.get("stableId")
        if not self.target:
            raise RuntimeError("Hyprland provides no stableId for window capture (grim -T)")

    def grab(self, geom, path, scale=None):
        from .live import grab
        if not self.target:
            raise RuntimeError("No safe window capture available")
        grab(geom, path, scale, target=self.target)

    def write(self):
        self.data["heartbeat"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        tmp.replace(self.path)
        self.last_write = time.monotonic()

    def status(self, status, message):
        changed = self.data.get("status") != status or self.data.get("message") != message
        self.data.update(status=status, message=message)
        if changed or time.monotonic() - self.last_write >= 1:
            self.write()

    def heartbeat(self):
        if time.monotonic() - self.last_write >= 1:
            self.write()

    def observed(self, unchanged):
        if unchanged and "stats" in self.data:
            self.data["observed_at"] = time.time()
            if self.data.get("phase") == "other":
                self.status("waiting", "No game screen detected – open Balatro")
            else:
                self.status("ready", "Advice only · no automatic clicks")
        else:
            self.status("waiting", "Game state changed – waiting for a stable frame")

    def update(self, state, text, rec, details, notes, duration):
        key = (state.get("ante"), state.get("round"), state.get("blind"))
        if key != self.round_key:
            self.data["history"] = []
            self.round_key = key
        value = state.get("score")
        history = self.data["history"]
        if value is not None and (not history or history[-1] != value):
            history.append(value)
        self.data.update(model(state, text, rec, details, notes, duration), history=history[-24:], observed_at=time.time())
        if state.get("phase") == "other":
            self.status("waiting", "No game screen detected – open Balatro")
        else:
            self.status("ready", "Advice only · no automatic clicks")
        self.write()
