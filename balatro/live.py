"""Live mode (Hyprland/Wayland): watches the game window and gives recommendations continuously.

Per tick: small screenshot (grim) -> greyscale difference to the previous frame.
Once the picture is still again and differs clearly from the last analysed one, a full
screenshot is recognised (vision.recognize), evaluated (optimizer.recommend) and shown
(and announced via notify-send).
"""
import json
import os
import re
import sys
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .models import State
from .optimizer import format_recommendation, recommend
from .pointer import hypr_json
from .runs import ROOT, RUNS
from .shop import advise
from .learn import game_over, improve, played, run as learn
from .vision import recognize


def find_window(pattern, details=False):
    """Visible part of the first matching window as a grim geometry, else None."""
    rx = re.compile(pattern, re.I)
    monitors = hypr_json("monitors")
    for c in hypr_json("clients"):
        if c["class"] == "org.omarchy.balatro-hud":
            continue
        # anchored: a terminal titled "omarchy: balatro" must not count as the game window
        if not (rx.match(c["class"]) or rx.match(c["title"])):
            continue
        for m in monitors:
            if c["workspace"]["id"] != m["activeWorkspace"]["id"]:
                continue
            mw, mh = m["width"] / m["scale"], m["height"] / m["scale"]
            x0, y0 = max(c["at"][0], m["x"]), max(c["at"][1], m["y"])
            x1 = min(c["at"][0] + c["size"][0], m["x"] + mw)
            y1 = min(c["at"][1] + c["size"][1], m["y"] + mh)
            # ponytail: only checks the monitor edges, not other windows covering it
            if (x1 - x0) * (y1 - y0) >= 0.9 * c["size"][0] * c["size"][1]:
                geom = f"{int(x0)},{int(y0)} {int(x1 - x0)}x{int(y1 - y0)}"
                return (geom, c) if details else geom
    return None


def grab(geom, path, scale=None, target=None):
    cmd = ["grim", *(["-T", target] if target else ["-g", geom]),
           *(["-s", str(scale)] if scale else []), str(path)]
    subprocess.run(cmd, check=True, capture_output=True, timeout=10)


def thumb(path):
    return Image.open(path).convert("L").resize((96, 64))


def diff(a, b):
    """Percent of pixels that changed strongly. The animated background only shifts pixels a little,
    so the mean difference stays ~5 even on a still screen; strong changes (cards, menus) don't."""
    if a is None or b is None or a.size != b.size:
        return 100.0
    strong = ImageChops.difference(a, b).point(lambda v: 255 if v > 48 else 0)
    return ImageStat.Stat(strong).mean[0] / 2.55


def notify(title, body):
    subprocess.run(["notify-send", "-a", "Balatro", title, body], check=False)


def plan(d, auto=False, details=None):
    """Recognised dict -> (recommendation text, Recommendation or None) for the current phase.
    auto: the autopilot plays it - plan with the joker order as it is (it can't rearrange jokers)."""
    state = State.from_dict(d)
    if d.get("phase") == "booster_pack" and d.get("booster"):
        from .autopilot import _pack_choice
        b, c = d["booster"], _pack_choice(state, d["booster"])
        if details is not None:
            details["pack_choice"] = asdict(c)
        offered = ", ".join(x.get("name") or x.get("rank", "") + x.get("suit", "") for x in b["cards"])
        pick = "Skip" if c.index is None else \
            f"Take {b['cards'][c.index].get('name') or b['cards'][c.index].get('rank', '')}"
        return f"RECOMMENDATION: {pick} – {c.why}\n{b['pack']}: {offered}", None
    if d.get("phase") in ("shop", "booster_pack") and d.get("shop"):
        return advise(state, d["shop"], d["phase"], d.get("reroll_cost"), ante=d.get("ante"), details=details, auto=auto), None
    if d.get("phase") == "cash_out":
        return "RECOMMENDATION: Cash Out", None
    if d.get("phase") == "blind_select":
        return blind_advice(d, state), None
    if not state.hand:
        return "No hand cards recognised (menu or animation).", None
    rec = recommend(state, reorder=not auto or bool(d.get("telemetry")))
    return format_recommendation(rec), rec


def blind_advice(d, state):
    """Select or Skip, from the blind's skip tag (telemetry)."""
    from .strategy import blind_decision
    _, _, choice, why = blind_decision(d, state)
    boss = f"\nUpcoming boss: {d['upcoming_boss']}" if d.get("upcoming_boss") else ""
    return f"RECOMMENDATION: {'Skip' if choice == 'skip' else 'Play the blind (Select)'} – {why}{boss}"


def advice(d):
    return plan(d)[0]


_ITEM = {"jokers": "joker", "hand": "card", "shop": "shop", "consumables": "consumable",
         "consumable_items": "consumable"}


def state_key(d, confs=None):
    """Everything a recommendation depends on. Card positions (selection, hover) and notes don't count,
    so selecting cards is no change, but any discard/play/purchase is - however few pixels it moves.
    Fields read with low confidence count as '?': they may flicker between identical frames."""
    from .localvision import MIN_CONF
    confs = confs or {}
    out = {}
    for k, v in d.items():
        if k == "notes":
            continue
        if k == "booster" and isinstance(v, dict):
            v = {**v, "cards": [{a: b for a, b in c.items() if a != "pos"} if isinstance(c, dict) else c
                                for c in v.get("cards", [])]}
        elif isinstance(v, list):
            v = ["?" if confs.get(f"{_ITEM.get(k)}{i}", 1) < MIN_CONF else
                 {a: b for a, b in x.items() if a != "pos"} if isinstance(x, dict) else x for i, x in enumerate(v)]
        elif confs.get(f"hud.{k}", 1) < MIN_CONF:
            v = "?"
        out[k] = v
    return json.dumps(out, sort_keys=True, default=str)


DATASET = Path.home() / ".local/share/balatro/dataset"
LOG = Path.home() / ".local/share/balatro/autopilot.log"
SAMPLES = Path.home() / ".local/share/balatro/samples"


def _head():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                          timeout=10).stdout.strip()


def resume_run(max_age=600):
    """A restart mid-run keeps logging into the run it interrupted (else the autolearner sees half a run)."""
    from .learn import latest_run, load
    last = latest_run(RUNS)  # RUNS looked up here, so tests can point it elsewhere
    if last is None or time.time() - (last / "moves.jsonl").stat().st_mtime > max_age:
        return None
    moves = load(last)
    return last if moves and not game_over(moves[-1]["state"]) else None


def log_move(run, path, state, text, rec, ok, msg, version=None, duration=None):
    """One autopilot move -> runs/<start>/NNN.jpg + a line in moves.jsonl (input for `python -m balatro learn`).
    JPEG q90: ~15x smaller than grim's PNG, the telemetry strip still decodes (tested down to q55)."""
    run.mkdir(parents=True, exist_ok=True)
    log = run / "moves.jsonl"
    n = 0
    if log.exists():
        with log.open(encoding="utf-8") as f:
            n = sum(1 for line in f if line.strip())
    Image.open(path).convert("RGB").save(run / f"{n:03d}.jpg", quality=90)
    play = rec.play if rec is not None and rec.action == "play" else None
    entry = {"n": n, "t": datetime.now().isoformat(timespec="seconds"), "state": state, "advice": text,
             "action": rec.action if rec is not None else None,
             "idx": list(rec.play_idx if rec.action == "play" else rec.discard.discard_idx) if rec is not None else None,
             "hand_type": play.hand_type if play else None, "predicted": play.score if play else None,
             "ok": ok, "msg": msg, "duration": duration}
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    from .runs import record_metadata
    record_metadata(run, state, version)


def analyze(path, model, mode="local", memory=None, pre=None, auto=False, details=None):
    d = recognize(path, model, mode, memory, pre)
    if any(n.startswith("source: Claude") for n in d.get("notes", [])):
        # keep every Claude-labelled frame: training/test data for the local recogniser
        DATASET.mkdir(parents=True, exist_ok=True)
        stem = DATASET / datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy(path, stem.with_suffix(".png"))
        stem.with_suffix(".json").write_text(json.dumps(d, indent=1, ensure_ascii=False))
    if d.get("phase") == "booster_pack" and not d.get("telemetry"):  # rare CV screens: keep for calibration
        SAMPLES.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, SAMPLES / f"{datetime.now():%Y%m%d-%H%M%S}-{(d.get('booster') or {}).get('pack')}.png")
    notes = d.pop("notes", [])
    return notes, d, *plan(d, auto, details)


class _Restart(Exception):
    """New code landed between runs: leave all context managers, then re-exec."""


def run(a):
    if getattr(a, "hud", False) and a.auto:
        raise RuntimeError("The widget is an advisor. --hud and --auto can't run together.")
    pilots = []  # the autopilot, created on first use; its Wayland pointer is closed on exit
    restart = False
    try:
        from .control import Control
        with tempfile.TemporaryDirectory(prefix="balatro-live-") as tmp, \
                Control(getattr(a, "paused", False)) if a.auto else nullcontext() as control:
            if a.auto and getattr(a, "retain_days", 30) > 0:
                from .runs import prune
                trimmed = prune(days=getattr(a, "retain_days", 30), apply=True)
                if trimmed["paths"]:
                    print(f"Retention: removed {len(trimmed['paths'])} old routine artifacts ({trimmed['bytes'] / 1048576:.1f} MiB); logs and evidence kept.")
            from .hud import Hud
            with Hud(Path(tmp)) if getattr(a, "hud", False) else nullcontext() as hud:
                _loop(a, Path(tmp), pilots, hud, control)
    except KeyboardInterrupt:
        print("\nStopped.")
    except _Restart:
        restart = True
    finally:
        for p in pilots:
            p.ptr.close()
    if restart:
        print("New code in the repo – restarting the autopilot …", flush=True)
        os.execv(sys.executable, [sys.executable, "-m", "balatro", *sys.argv[1:]])


def _loop(a, tmp, pilots, hud=None, control=None):
    prev = last_key = seen = None
    fails = {}  # state key -> failed autopilot attempts
    acted = None
    still = busy = unstable = 0
    moved = True
    status = None
    memory = {}  # resolved joker names, reused while the joker row is unchanged
    run_dir = resume_run() or RUNS / f"{datetime.now():%Y%m%d-%H%M%S}"
    over_at = None  # time the last logged frame looked like game over (scoring animations look the same)
    learned = None  # run_dir already evaluated
    learner, head = None, _head()  # Opus thread; commit the code was loaded from
    telemetry = False  # the patched game was seen: a frame without its strip is a stream artefact, not the state
    lost = 0  # consecutive frames without the strip while telemetry is on
    state = {}
    from .runs import code_version
    version = code_version()
    can_act = True
    LOG.parent.mkdir(parents=True, exist_ok=True)
    print(f"Watching window /{a.window}/ … (Ctrl+C stops)" + (" – autopilot ON" if a.auto else ""))
    while True:
        if control:
            allowed = control.poll()
            if allowed and not can_act:
                last_key, moved = None, True
            can_act = allowed
        if hud and not hud.running():
            break
        # telemetry needs no animation guard (checksummed game state): poll faster
        interval = min(a.interval, 0.2) if telemetry else a.interval
        found = find_window(a.window, details=bool(hud))
        if found is None:
            if hud:
                hud.status("hidden", "Game window not visible – showing the last state")
            if status != "hidden":
                print("Game window not (fully) visible – waiting …")
                status = "hidden"
            time.sleep(a.interval * 2)
            continue
        geom, client = found if hud else (found, None)
        status = "visible"
        capture = grab
        if hud:
            try:
                hud.select(client)
                capture = hud.grab
                capture(geom, tmp / "small.png", scale=0.15)
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                hud.target = None
                hud.status("error", f"window capture failed: {e}")
                time.sleep(1)
                continue
            hud.heartbeat()
        else:
            capture(geom, tmp / "small.png", scale=0.15)
        t = thumb(tmp / "small.png")
        d, prev = diff(t, prev), t
        if d < a.still:
            still += 1
        else:
            still, moved = 0, True
        busy = busy + 1 if moved else 0
        if a.debug:
            print(f"Δ tick {d:5.1f}%  still {still}  moving {busy}")
        if over_at and run_dir != learned and (state.get("phase") == "game_over" or time.time() - over_at > 30) \
                and played(run_dir):  # a restart in front of an old game-over screen logs a run without plays
            over_at, learned = None, run_dir  # autolearner, once per run: the game-over screen stays and is re-read
            print("\n=== AUTOLEARNER ===\n" + learn(run_dir), flush=True)
            if not a.no_improve:  # Opus takes minutes: don't block the loop (the next run may start meanwhile)
                print("Claude Opus is working on improvements in the background …", flush=True)
                learner = threading.Thread(target=lambda rd=run_dir: print("\n=== OPUS ===\n" + improve(rd), flush=True))
                learner.start()
        if a.auto and acted and time.time() - acted > 20:  # click lost (stream hiccup)? look again once
            acted, last_key, moved = None, None, True
        # Something moved and has settled (or keeps moving: constant animation). Pixel change alone can't tell
        # whether it matters (a discard of 2 cards moves few pixels, the background swirls), so read the state
        # locally and only re-analyse when the recognised state differs.
        if (moved and (still >= 2 or busy >= 8)) or (still > 0 and still % 4 == 0):
            moved, busy = False, 0
            try:
                path, local, confs, notes = best_read(geom, tmp, capture=capture)
            except (OSError, RuntimeError, subprocess.SubprocessError) as e:
                if hud is None:
                    raise
                hud.target = None
                hud.status("error", f"recognition failed: {e}")
                moved = True
                time.sleep(1)
                continue
            if local.get("telemetry"):
                telemetry, lost = True, 0
            elif telemetry and lost < 5:  # strip hidden in this frame (overlay, stream glitch): read again
                if hud:
                    hud.status("waiting", "Telemetry interrupted – waiting for a valid frame")
                lost, moved = lost + 1, True
                time.sleep(interval)
                continue
            else:
                telemetry = False  # 5 frames in a row without it: the game runs unpatched now
            key = state_key(local, confs)
            if hud:
                hud.observed(key == last_key)
            unstable = unstable + 1 if key != seen else 0
            # CV must read the same twice in a row (no decisions on animation frames) - but never wait forever:
            # after 3 differing reads of a still screen take the best one. Telemetry is exact at once.
            if key != seen and unstable < 3 and not local.get("telemetry"):
                seen, moved = key, True
            elif key != last_key:
                seen, unstable, acted = key, 0, None
                print(f"\n{datetime.now():%H:%M:%S} change detected – analysing …", flush=True)
                started = time.time()
                details = {}
                if hud:
                    hud.status("analyzing", "Computing recommendation …")
                try:
                    notes, new, text, rec = analyze(path, a.model, a.vision, memory, (local, confs, notes), a.auto,
                                                    details=details)
                except Exception as e:  # recognition is external; keep watching (and retry this state)
                    print("Error:", e)
                    if hud:
                        hud.status("error", f"analysis failed: {e}")
                    moved = True
                    time.sleep(interval)
                    continue
                last_key = key  # only now: a failed analysis must not block a retry of the same state
                # a new run: back to ante 1, or anything after a game-over screen (a loss in ante 1 keeps ante 1)
                if new.get("ante") == 1 and (new.get("round") or 0) <= 1 and (state.get("ante") or 1) > 1 \
                        or state.get("phase") == "game_over" and new.get("phase") != "game_over":
                    run_dir = RUNS / f"{datetime.now():%Y%m%d-%H%M%S}"  # a new run in the same session
                    fails = {}
                    if a.auto and learner and learner.is_alive():
                        # wait for the Opus patch: skipping the restart kept 2 patches unloaded for a day (11-01)
                        print("Waiting for the autolearner before the new run …", flush=True)
                        learner.join()
                    if a.auto and _head() != head:
                        # an auto-applied patch (or any commit) landed: restart on the new code between runs
                        raise _Restart
                state = new
                if hud:
                    hud.update(state, text, rec, details, notes, time.time() - started)
                print("\033[2J\033[H", end="")  # clear screen
                print(f"{datetime.now():%H:%M:%S}  (recognition+analysis {time.time() - started:.1f}s)\n")
                print(text)
                for n in notes:
                    print("Note:", n)
                if text.startswith("RECOMMENDATION") and not a.no_notify:
                    notify("Balatro", text.splitlines()[0])
                if control:
                    can_act = control.poll()
                if a.auto and can_act and (not control or not control.paused or state.get("phase") in
                                          ("playing", "shop", "booster_pack", "blind_select", "cash_out")):
                    if not pilots:
                        pilots.append(_pilot(a.window, tmp))
                        if control:
                            pilots[0].allowed = control.poll
                    try:
                        ok, msg = pilots[0].act(state, rec, details) if fails.get(key, 0) < 2 else (False, "waiting for a change")
                    except RuntimeError as e:
                        ok, msg = False, str(e)
                    print("Autopilot:", msg)
                    if control:
                        control.consume()
                    with open(LOG, "a", encoding="utf-8") as f:  # the screen is cleared per analysis: keep a history
                        f.write(f"{datetime.now():%H:%M:%S} {text.splitlines()[0]} -> {msg}\n")
                    log_move(run_dir, path, state, text, rec, ok, msg, version, time.time() - started)
                    over_at = time.time() if game_over(state) else None
                    if not ok and fails.get(key, 0) < 2:  # retry once from a fresh frame, then wait for a change
                        fails[key] = fails.get(key, 0) + 1
                        last_key = None
                        if fails[key] == 2:
                            print("\n>>> AUTOPILOT STUCK: the screen doesn't match the recognition – please make this move yourself, "
                                  "then it continues automatically.")
                            if not a.no_notify:
                                notify("Balatro autopilot", "Please make this move yourself – recognition uncertain")
                    moved, acted = True, time.time()  # the screen will change (or we retry): look again
        time.sleep(interval)


def best_read(geom, tmp, frames=3, capture=None):
    """The stream flickers (a card corner lost in one frame, a pip read as a corner in the next): read up to
    `frames` captures and keep the one with the fewest unsure fields. -> (path, state dict, confs, notes)."""
    from .localvision import MIN_CONF, recognize_local
    best = None
    for i in range(frames):
        path = tmp / f"full{i}.png"
        (capture or grab)(geom, path)
        local, confs, notes = recognize_local(path)
        bad = sum(c < MIN_CONF for c in confs.values())
        if best is None or bad < best[0]:
            best = bad, path, local, confs, notes
        if not bad:
            break
    return best[1:]


def _pilot(window, tmp):
    import cv2
    from .autopilot import Autopilot

    def grab_fn():
        geom = find_window(window)
        if geom is None:
            return None
        grab(geom, tmp / "pilot.png")
        return geom, cv2.imread(str(tmp / "pilot.png"))
    return Autopilot(grab_fn)
