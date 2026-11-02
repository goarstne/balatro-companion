"""Autolearner: evaluate a logged autopilot run (live.log_move) and turn its mistakes into regression cases.

Telemetry reads the game's own state, so every play has ground truth: the score delta to the next frame.
A play whose engine prediction misses that delta is an engine bug (or missing joker vars) -> saved to
tests/learned/ as a case test_learned.py replays; fix the engine until those pass.

improve() then hands the run to Claude Opus (claude -p, subscription login) working on a throw-away copy of the
project: it reads the run, changes code, runs the tests and leaves a patch + report. A green patch is re-tested
and applied automatically, as approved by the user. These checks do not establish a win-rate improvement.
"""
import json
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .engine import score
from .models import State

_PROJECT = Path(__file__).resolve().parent.parent
CASES = (_PROJECT / "tests" / "learned" if (_PROJECT / "tests/test_engine.py").is_file()
         else Path.home() / ".local/share/balatro/regressions")
# EV-scored randomness: a single draw can't confirm the engine (strategy design: random effects = expected value)
RANDOM_JOKERS = {"Misprint", "Bloodstone", "Space Joker"}
# money won at random during the hand feeds money-scaled jokers: the real score drifts from the expectation
MONEY_JOKERS, RANDOM_MONEY = {"Bull", "Bootstraps"}, {"Business Card", "Reserved Parking"}


def game_over(s):
    """The patched hook reports 'game_over'. Older hooks show 'other' with no hands left and the blind not beaten -
    but so does the last hand's scoring animation, so callers must also see that nothing follows."""
    return s.get("phase") == "game_over" or s.get("phase") == "other" and s.get("hands_left") == 0 and \
        s.get("score", 0) < s.get("blind_chips", 0)


def played(run):
    """Did the autopilot play a hand in this run? (nothing to learn from a run that is only a game-over screen)"""
    return any(m["msg"] == "Played" for m in load(run)) if (Path(run) / "moves.jsonl").exists() else False


def load(run):
    return [json.loads(line) for line in (Path(run) / "moves.jsonl").read_text().splitlines() if line]


def _random(s, idx):
    names = {j.get("name") for j in s.get("jokers", [])}
    return bool(names & RANDOM_JOKERS) or bool(names & MONEY_JOKERS and names & RANDOM_MONEY) or \
        any(isinstance(c, dict) and c.get("enhancement") == "lucky" for i, c in enumerate(s.get("hand", [])) if i in idx)


def _misclicked(s, idx, t):
    """Cards not in idx stay in hand through the play; if one is missing next hand, the click hit another card."""
    key = lambda c: (c.get("rank"), c.get("suit"))
    if not s.get("hand") or not t.get("hand"):
        return False
    kept = Counter(key(c) for i, c in enumerate(s["hand"]) if i not in idx)
    return bool(kept - Counter(key(c) for c in t["hand"]))


def plays(moves):
    """-> [(move, actual score delta)] for plays whose result shows in a later frame of the same blind: the next
    hand (one hand fewer) or the cash-out screen. Scoring-animation frames ('other', old score) are skipped."""
    out = []
    for i, a in enumerate(moves):
        s = a["state"]
        if a["msg"] != "Played":  # msg, not action: the pilot may use a consumable first and play next frame
            continue
        for b in moves[i + 1:]:
            t = b["state"]
            if t.get("round") != s.get("round") or t.get("phase") in ("shop", "blind_select", "booster_pack"):
                break
            if t.get("phase") == "playing" and t.get("hands_left") != s.get("hands_left") - 1:
                break  # the click was lost, or no frame between two plays: delta isn't one hand
            if t.get("phase") == "playing" and _misclicked(s, a.get("idx") or [], t):
                break  # a held card left the hand: other cards were played than logged (run 074053 #424)
            if t.get("phase") in ("playing", "cash_out", "game_over"):
                out.append((a, t.get("score", 0) - s.get("score", 0)))
                break
    return out


def evaluate(moves):
    """-> dict report: outcome, score mismatches, failed actions, close/lost blinds."""
    last = moves[-1]["state"] if moves else {}
    lost = game_over(last)  # last logged frame: nothing followed it
    miss = []
    for m, actual in plays(moves):
        if _random(m["state"], m["idx"]):
            continue
        pred = score(State.from_dict(m["state"]), m["idx"]).score  # re-score: the engine may have been fixed since
        if abs(pred - actual) > max(1, 0.01 * actual):
            miss.append({"n": m["n"], "hand_type": m["hand_type"], "predicted": pred, "actual": actual})
    from .runs import failed_action
    fails = Counter(m["msg"].split(" – ")[0][:70] for m in moves if failed_action(m))
    # round -> [ante, blind, best score, chips needed, fewest hands left]; cash_out frames keep the score but
    # already count the next ante after a boss, so key by the (global) round counter
    blinds = {}
    for m in moves:
        s = m["state"]
        if s.get("phase") in ("playing", "cash_out", "other") and s.get("round"):
            b = blinds.setdefault(s["round"], [s.get("ante"), None, 0, 0, 99])
            b[1] = s.get("blind") or b[1]
            b[2], b[3] = max(b[2], s.get("score", 0)), max(b[3], s.get("blind_chips", 0))
            if s.get("phase") == "playing":
                b[4] = min(b[4], s.get("hands_left", 99))
    return {"moves": len(moves), "ante": last.get("ante"), "round": last.get("round"), "money": last.get("money"),
            "lost": lost, "last_blind": last.get("blind"), "plays": len(plays(moves)), "mismatches": miss,
            "failures": fails.most_common(), "blinds": blinds}


def save_cases(moves, report, run_name):
    """Mismatched plays -> tests/learned/<run>-<n>.json (state, played indices, the game's real score)."""
    CASES.mkdir(parents=True, exist_ok=True)
    byn = {m["n"]: m for m in moves}
    for x in report["mismatches"]:
        m = byn[x["n"]]
        (CASES / f"{run_name}-{x['n']:03d}.json").write_text(json.dumps(
            {"state": m["state"], "idx": m["idx"], "actual": x["actual"], "hand_type": x["hand_type"]},
            indent=1, ensure_ascii=False))
    return len(report["mismatches"])


def format_report(r):
    lines = [f"{r['moves']} moves, {r['plays']} scored hands – end: ante {r['ante']} round {r['round']}, ${r['money']}, "
             + (f"LOST to {r['last_blind']}" if r["lost"] else "not lost (running/won/aborted)")]
    for rnd, (ante, name, sc, need, hl) in r["blinds"].items():
        flag = "  LOST" if sc < need and hl == 0 else "  close" if need <= sc < 1.2 * need else ""
        lines.append(f"  Ante {ante} R{rnd} {name}: {sc}/{need}, min. {hl} hands left before the last hand{flag}")
    lines.append(f"Engine mismatches: {len(r['mismatches'])}")
    lines += [f"  #{x['n']} {x['hand_type']}: predicted {x['predicted']}, actual {x['actual']}" for x in r["mismatches"]]
    if r["failures"]:
        lines.append("Failed actions:")
        lines += [f"  {n}x {msg}" for msg, n in r["failures"]]
    return "\n".join(lines)


def latest_run():
    from .live import RUNS
    runs = sorted(p for p in RUNS.glob("*") if (p / "moves.jsonl").exists())
    return runs[-1] if runs else None


def run(path=None, save=True):
    path = Path(path) if path else latest_run()
    if path is None:
        return "No recorded run found."
    moves = load(path)
    r = evaluate(moves)
    text = f"Run {path.name}\n" + format_report(r)
    if save and r["mismatches"]:
        text += f"\n{save_cases(moves, r, path.name)} regression cases saved in {CASES} (tests/test_learned.py)."
    (path / "report.txt").write_text(text + "\n")
    return text


ROOT = Path(__file__).resolve().parent.parent
LEARNINGS = Path.home() / ".local/share/balatro/learnings.md"  # hypotheses carried from run to run
MODEL = "claude-opus-5-5"
from .checks import modules

TESTS = modules(core=True)
TOOLS = ["Read", "Grep", "Glob", "Edit", "Write"] + [f"Bash(.venv/bin/python -m tests.{t})" for t in TESTS]

PROMPT = """You are improving the Balatro autopilot in this directory (a throwaway copy of the project; your changes \
are re-tested and applied as a patch afterwards). Goal: win more runs, reach higher antes.

## Material (all under _learn/)
- report.txt – run summary: result, score per blind, close wins, engine mismatches, failed clicks.
- summary.txt – one move per line: no., phase, ante/round, blind, score/target, hands/discards, money, \
recommendation -> result; shop moves also list the jokers. Game states come from the in-game telemetry and are exact – \
except moves marked [LOCAL]: there the telemetry was missing (strip covered) and the state comes from the less reliable \
image recognition; treat bad decisions there as recognition errors first, not strategy errors.
- moves.jsonl – full state per move (hand, jokers with their values, shop offers, hand levels). Large: read \
selectively; line n = move n.
- learnings.md – hypotheses from earlier runs (may be empty).

## Approach
1. Read report.txt, summary.txt and learnings.md. Find the decisions that cost the run the most \
(shop/economy, joker choice, play vs. discard, packs, bosses). Back each with move numbers and figures from the log.
2. Read the responsible code (balatro/shop.py, strategy.py, optimizer.py, packs.py, autopilot.py) and explain \
WHICH rule produced the decision.
3. Change code only if
   - it is a clear bug (wrong rule, wrong arithmetic, contradictory condition, a text that is wrong), or
   - the same bad decision is already recorded in learnings.md from an earlier run (second piece of evidence).
   A pattern seen once that may just be bad luck goes ONLY into learnings.md as a hypothesis.
4. At most 3 changes, each small and targeted. Every behaviour change gets a test in the matching \
tests/test_*.py (follow the style of the existing tests, no new framework).
5. Then run all tests: {tests}. All must pass; if a change breaks a test, revert the change instead of \
weakening the test.

## Rules
- Do not touch: balatro/localvision/, pointer.py, live.py, vision.py, patch_balatro.bat, tests/learned/ \
(recognition and mouse control are out of scope here).
- Balatro rules: when in doubt, the decompiled original source (version 1.0.1o, card.lua / \
state_events.lua) wins over the wiki and your memory. Unsure? Then no rule change.
- Engine mismatches in the report (predicted != actual) have top priority: the cases are in \
tests/learned/ and replayed by tests/test_learned.py; fix the cause in the engine or the joker.
- Code style as in the surrounding code (short English comments, English output texts). No new dependencies.
- Better no change than a guessed one.

## Result
- Update _learn/learnings.md: confirmed findings, open hypotheses (with run name {run} and move numbers), \
remove resolved items. At most ~60 lines, condense older entries.
- Finish with an answer in English with exactly these sections:
  ## Run – one sentence on the result
  ## Key mistakes – each: move(s), what happened, what would have been better, responsible code
  ## Changes – each: file, what, why, which test covers it (or "none")
  ## Hypotheses for next runs
  ## Tests – result of the last test run
"""


def summarize(moves):
    """moves.jsonl -> one compact line per move (the prompt's main input)."""
    out = []
    for m in moves:
        s = m["state"]
        line = (f"{m['n']:3d} {s.get('phase', '?'):12s} A{s.get('ante')}/R{s.get('round')} {s.get('blind') or '-':14s} "
                f"{s.get('score', 0)}/{s.get('blind_chips', 0)} H{s.get('hands_left')} D{s.get('discards_left')} "
                f"${s.get('money')}  {'' if s.get('telemetry') else '[LOCAL] '}{m['advice'].splitlines()[0][:140]} "
                f"-> {m['msg'][:90]}")
        if s.get("phase") == "shop":
            line += "\n      Jokers: " + ", ".join(j["name"] + (f" {j['vars']}" if j.get("vars") else "")
                                           for j in s.get("jokers", []))
        out.append(line)
    return "\n".join(out)


def _workspace(run):
    """Copy of the project as it is now (tracked + untracked, not ignored) with a baseline commit -> path."""
    work = run / "work"
    if work.exists():
        shutil.rmtree(work)
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT, capture_output=True,
                           check=True).stdout.decode().split("\0")
    for f in filter(None, files):
        if (ROOT / f).is_file():
            (work / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / f, work / f)
    (work / ".venv").symlink_to(ROOT / ".venv")
    (work / ".gitignore").write_text(".venv\n__pycache__/\n")
    lr = work / "_learn"
    lr.mkdir()
    for name in ("report.txt", "moves.jsonl"):
        shutil.copy(run / name, lr / name)
    (lr / "summary.txt").write_text(summarize(load(run)) + "\n")
    (lr / "learnings.md").write_text(LEARNINGS.read_text() if LEARNINGS.exists() else "")
    git = ["git", "-c", "user.name=learn", "-c", "user.email=learn@localhost"]
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(git + ["commit", "-qm", "baseline"], cwd=work, check=True)
    return work


def improve(path=None):
    """Run -> Opus works on a copy -> run/opus.md (report) + run/opus.patch (tested diff). -> short status text."""
    if not TESTS:
        return "Autolearner requires the project checkout with its test suite."
    rd = Path(path) if path else latest_run()
    if rd is None:
        return "No recorded run found."
    if not (rd / "report.txt").exists():
        run(rd)
    work = _workspace(rd)
    prompt = PROMPT.format(tests=", ".join(f".venv/bin/python -m tests.{t}" for t in TESTS), run=rd.name)
    try:
        r = subprocess.run(["claude", "-p", "--model", MODEL, "--permission-mode", "dontAsk",
                            "--allowedTools", *TOOLS, "--strict-mcp-config", "--no-session-persistence"],
                           input=prompt, cwd=work, capture_output=True, text=True, timeout=3600)
        answer = r.stdout.strip() or f"(no answer, exit {r.returncode})\n{r.stderr[-2000:]}"
    except subprocess.TimeoutExpired:
        answer = "(aborted after 60 min)"
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)  # new files (tests) belong in the patch too
    patch = subprocess.run(["git", "diff", "--cached", "--no-ext-diff", "--src-prefix=a/", "--dst-prefix=b/", "--", ".",
                            ":(exclude)_learn"], cwd=work, capture_output=True,
                           text=True, check=True).stdout
    # don't trust Opus's own test claim: re-run the suite on its result
    failed = [t for t in TESTS if subprocess.run([".venv/bin/python", "-m", f"tests.{t}"], cwd=work,
                                                  capture_output=True, timeout=600).returncode]
    verdict = "Tests: all green" if not failed else "Tests RED: " + ", ".join(failed)
    (rd / "opus.md").write_text(f"{answer}\n\n---\nRe-check: {verdict}\n")
    (rd / "opus.patch").write_text(patch)
    learned = work / "_learn" / "learnings.md"
    if learned.exists() and learned.read_text().strip():
        LEARNINGS.write_text(learned.read_text())
    # work/ stays for inspection; the next improve() replaces it
    head = f"Opus report: {rd / 'opus.md'}\n{verdict}\n"
    if not patch:
        return head + "No code change proposed."
    # the user approved taking tested patches without asking (2026-10-31): apply() re-tests in the real tree
    return head + (apply(rd) if not failed else "No applicable patch.")


def apply(name, root=ROOT):
    """Apply a run's Opus patch to the real tree, re-run the suite there, and commit only the patched files plus a
    docs/CHANGELOG.md entry; a red suite rolls the patch back. name: run name or directory."""
    if not TESTS:
        return "Autolearner requires the project checkout with its test suite."
    from .live import RUNS
    rd = Path(name) if Path(name).is_dir() else RUNS / name
    patch = rd / "opus.patch"
    if not patch.exists() or not patch.read_text().strip():
        return f"No patch for {rd.name}."
    files = sorted({l[6:] for l in patch.read_text().splitlines() if l.startswith(("+++ b/", "--- a/"))})
    log = root / "docs" / "CHANGELOG.md"
    paths = sorted(set(files + [str(log.relative_to(root))]))
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=root,
                           capture_output=True, text=True, check=True).stdout
    if dirty:
        return "Patch not taken: affected files have local changes.\n" + dirty
    previous_log = log.read_bytes() if log.exists() else None
    r = subprocess.run(["git", "apply", str(patch)], cwd=root, capture_output=True, text=True)
    if r.returncode:
        return "Patch no longer applies (not taken):\n" + r.stderr.strip()
    try:
        failed = [t for t in TESTS if subprocess.run([sys.executable, "-m", f"tests.{t}"], cwd=root,
                                                      capture_output=True, timeout=180).returncode]
        if failed:
            raise RuntimeError("red in the project: " + ", ".join(failed))
        report = (rd / "opus.md").read_text() if (rd / "opus.md").exists() else ""
        changes = report.split("## Changes", 1)[1].split("\n## ", 1)[0].strip() if "## Changes" in report else ""
        entry = (f"\n## {datetime.now():%Y-%m-%d %H:%M} – autolearner, run {rd.name}\n\n"
                 f"Applied automatically (tests green). Files: {', '.join(files)}\n\n{changes}\n")
        log.parent.mkdir(exist_ok=True)
        log.write_text((previous_log.decode() if previous_log is not None else "# Changelog\n") + entry)
        subprocess.run(["git", "add", "--", *paths], cwd=root, check=True)
        msg = (f"Autolearner run {rd.name}: Opus patch, auto-applied (tests green)\n\n{changes[:3000]}\n\n"
               "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
        subprocess.run(["git", "commit", "-q", "-m", msg, "--", *paths], cwd=root,
                       capture_output=True, text=True, check=True)
    except BaseException as error:
        subprocess.run(["git", "apply", "-R", str(patch)], cwd=root, check=True)
        if previous_log is None:
            log.unlink(missing_ok=True)
        else:
            log.write_bytes(previous_log)
        subprocess.run(["git", "reset", "-q", "HEAD", "--", *paths], cwd=root, check=True)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return f"Patch rolled back, {error}"
    return f"Applied and committed ({', '.join(files)}); the autopilot reloads the code at the next run."
