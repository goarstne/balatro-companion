"""python -m balatro {analyze,score,joker,jokers} ..."""
import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from . import jokers as J
from .engine import score
from .models import State

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def load_state_dict(path, save=None):
    """JSON file -> dict; screenshot -> vision dict (printed so the user can check/correct it)."""
    if Path(path).suffix.lower() not in IMAGE_EXT:
        return json.loads(Path(path).read_text())
    from .vision import recognize
    d = recognize(path)
    for n in d.pop("notes", []):
        print("Note:", n, file=sys.stderr)
    text = json.dumps(d, indent=2, ensure_ascii=False)
    print("Recognised state (please check; fix with --save-state + editing):", file=sys.stderr)
    print(text, file=sys.stderr)
    if save:
        Path(save).write_text(text + "\n")
        print(f"Gespeichert: {save}", file=sys.stderr)
    return d


def cmd_analyze(a):
    from .optimizer import format_recommendation, recommend
    d = load_state_dict(a.state, a.save_state)
    if d.get("phase") in ("shop", "booster_pack") and not a.json:
        from .live import advice
        return print(advice(d))
    state = State.from_dict(d)
    kw = {"samples": a.samples} if a.samples else {}
    rec = recommend(state, **kw)
    if a.json:
        print(json.dumps(rec if isinstance(rec, dict) else asdict(rec), indent=2, ensure_ascii=False, default=str))
    else:
        print(format_recommendation(rec))


def cmd_score(a):
    state = State.from_dict(load_state_dict(a.state))
    for i in a.cards:
        if not 0 <= i < len(state.hand):
            sys.exit(f"card index {i} outside the hand (0..{len(state.hand) - 1})")
    r = score(state, a.cards, verbose=True)
    print("Played:", " ".join(str(state.hand[i]) for i in sorted(a.cards)))
    if r.reason:
        print("Invalid:", r.reason)
    print("\n".join(r.log))
    print(f"{r.hand_type}: {r.chips:g} x {r.mult:g} = {r.score}" + (f"  (+${r.money:g})" if r.money else ""))
    if state.blind_chips:
        print(f"Blind: {state.score + r.score} / {state.blind_chips}")


def cmd_joker(a):
    from .vision import _closest
    name = _closest(a.name, J.REGISTRY)
    if name is None:
        sys.exit(f"joker '{a.name}' not found")
    s = J.REGISTRY[name]
    print(f"{s.name}  [{s.rarity}, ${s.cost}]\n{s.desc}")
    if s.defaults:
        print("Standardwerte:", s.defaults)
    if not s.blueprint_compat:
        print("Not Blueprint-compatible")
    if s.notes:
        print("Hinweise:", s.notes)


def cmd_jokers(a):
    for s in J.REGISTRY.values():
        print(f"{s.rarity:<10} {s.name}")


def cmd_learn(a):
    from . import learn
    if a.apply:
        return print(learn.apply(a.apply))
    print(learn.run(a.run, not a.no_save))
    if a.improve:
        print("Claude Opus is working (may take 10–30 min) …", flush=True)
        print(learn.improve(a.run))


def cmd_stats(a):
    from .runs import paired, statistics
    result = statistics(a.runs)
    if a.compare:
        result = paired(result["runs"], *a.compare)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_benchmark(a):
    from .benchmark import compare
    result = json.dumps(compare(a.revision, a.paths, a.limit, a.samples), indent=2, ensure_ascii=False)
    if a.output:
        Path(a.output).write_text(result + "\n")
    print(result)


def cmd_forecast(a):
    state = State.from_dict(load_state_dict(a.state))
    if a.cmd == "boss":
        from .strategy import boss_forecast
        result = boss_forecast(state, a.ante, a.samples)
    else:
        from .deckplan import advice
        result = advice(state)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m balatro", description="Balatro score engine, advisor and autopilot")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("analyze", help="recommend the best move (state.json or screenshot)")
    s.add_argument("state")
    s.add_argument("--json", action="store_true", help="output as JSON")
    s.add_argument("--samples", type=int, help="number of simulations for draws")
    s.add_argument("--save-state", metavar="FILE", help="save the recognised state as JSON")
    s.set_defaults(fn=cmd_analyze)
    s = sub.add_parser("score", help="score specific cards (indices from 0, left to right)")
    s.add_argument("state")
    s.add_argument("cards", type=int, nargs="+")
    s.set_defaults(fn=cmd_score)
    s = sub.add_parser("joker", help="show joker data")
    s.add_argument("name")
    s.set_defaults(fn=cmd_joker)
    sub.add_parser("jokers", help="list all jokers").set_defaults(fn=cmd_jokers)
    s = sub.add_parser("learn", help="evaluate a recorded autopilot run, save engine mismatches as tests")
    s.add_argument("run", nargs="?", help="run folder (default: latest under ~/.local/share/balatro/runs)")
    s.add_argument("--no-save", action="store_true", help="don't write regression cases")
    s.add_argument("--improve", action="store_true", help="then let Claude Opus work out improvements (patch)")
    s.add_argument("--apply", metavar="RUN", help="apply this run's tested Opus patch")
    s.set_defaults(fn=cmd_learn)
    from .runs import RUNS
    s = sub.add_parser("inspect", help="local interactive replay and what-if editor")
    s.add_argument("--runs", type=Path, default=RUNS)
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=lambda a: __import__("balatro.inspect", fromlist=["serve"]).serve(a.runs, a.port, not a.no_browser))
    s = sub.add_parser("control", help="pause, resume, or single-step a running autopilot")
    s.add_argument("action", choices=["pause", "resume", "step", "status"])
    s.set_defaults(fn=lambda a: print(json.dumps(__import__("balatro.control", fromlist=["request"]).request(a.action))))
    s = sub.add_parser("stats", help="run outcomes, failures and version comparisons")
    s.add_argument("--runs", type=Path, default=RUNS)
    s.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="compare versions on matching seed/stake/deck")
    s.set_defaults(fn=cmd_stats)
    s = sub.add_parser("prune", help="preview old routine images/work copies eligible for cleanup")
    s.add_argument("--runs", type=Path, default=RUNS)
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--apply", action="store_true", help="delete the listed artifacts; keep logs and error evidence")
    s.set_defaults(fn=lambda a: print(json.dumps(__import__("balatro.runs", fromlist=["prune"]).prune(a.runs, a.days, a.apply), indent=2)))
    s = sub.add_parser("benchmark", help="compare this checkout with a Git revision on fixed snapshots")
    s.add_argument("revision")
    s.add_argument("paths", nargs="+", help="JSON snapshots or run directories/moves.jsonl")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--samples", type=int, default=8)
    s.add_argument("--output", help="save the comparison as JSON")
    s.set_defaults(fn=cmd_benchmark)
    s = sub.add_parser("boss", help="simulate the upcoming boss and show preparation advice")
    s.add_argument("state")
    s.add_argument("--ante", type=int, default=1)
    s.add_argument("--samples", type=int, default=12)
    s.set_defaults(fn=cmd_forecast)
    s = sub.add_parser("deck", help="rank removals/copies across the known full deck")
    s.add_argument("state")
    s.set_defaults(fn=cmd_forecast)
    s = sub.add_parser("live", help="watch the game window and give recommendations continuously (Hyprland)")
    s.add_argument("--window", default="Balatro|Shadow PC", help="regex matching the start of the window class or title")
    s.add_argument("--model", help="recognition model (default: sonnet via claude -p, or the API default)")
    s.add_argument("--interval", type=float, default=0.5, help="seconds between checks")
    s.add_argument("--still", type=float, default=1.0, help="stillness threshold: %% of strongly changed pixels")
    s.add_argument("--no-notify", action="store_true", help="no desktop notifications")
    s.add_argument("--vision", choices=["local", "auto", "claude"], default="local",
                   help="image recognition: local = local only (default), auto = ask Claude when unsure")
    s.add_argument("--debug", action="store_true", help="print difference values (for calibration)")
    s.add_argument("--auto", action="store_true", help="autopilot: carry out recommendations with the mouse")
    s.add_argument("--paused", action="store_true", help="start --auto paused; use 'control step' for one action")
    s.add_argument("--retain-days", type=int, default=30, help="trim old routine images/work copies on auto startup (default 30; 0 disables)")
    s.add_argument("--hud", action="store_true", help="movable Quickshell widget (advice only, no autopilot)")
    s.add_argument("--no-improve", action="store_true", help="don't start an Opus improvement after a lost run")
    s.set_defaults(fn=lambda a: __import__("balatro.live", fromlist=["run"]).run(a))
    a = p.parse_args(argv)
    if getattr(a, "samples", None) is not None and not 1 <= a.samples <= 10000:
        p.error("--samples must be between 1 and 10000")
    if getattr(a, "retain_days", 0) < 0:
        p.error("--retain-days must not be negative")
    try:
        a.fn(a)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as e:
        sys.exit(str(e))
