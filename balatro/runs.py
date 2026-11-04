"""Run statistics, paired-seed comparisons and conservative artifact retention."""
import hashlib
import json
import re
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
RUNS = Path.home() / ".local/share/balatro/runs"


def code_version():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "balatro").rglob("*")):
        if path.suffix in (".py", ".qml", ".html"):
            digest.update(str(path.relative_to(ROOT)).encode())
            digest.update(path.read_bytes())
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip() or "installed"
    return head + ":" + digest.hexdigest()[:12]


def metadata(path):
    try:
        return json.loads((Path(path) / "run.json").read_text())
    except FileNotFoundError:
        return {}


def failed_action(move):
    return not move.get("ok", True) and not any(text in move.get("msg", "").lower() for text in
        ("no automatic action", "keine automatische aktion", "waiting for a change", "wartet auf änderung"))


def record_metadata(path, state, version=None):
    data = metadata(path)
    if "version" not in data:
        data["version"] = version or code_version()
    versions = data.setdefault("versions", [data["version"]])
    if version and version not in versions:
        versions.append(version)
        data["version"] = "mixed"
    for key in ("seed", "stake", "deck_type"):
        if state.get(key) is not None:
            data[key] = state[key]
    temp = Path(path) / "run.tmp"
    temp.write_text(json.dumps(data, indent=2))
    temp.replace(Path(path) / "run.json")


def summary(path):
    from .learn import load
    moves = load(path)
    states = [m["state"] for m in moves]
    last = states[-1] if states else {}
    meta = metadata(path)
    won = any(s.get("won") or (s.get("ante") or 0) > 8 for s in states)
    failed = [m for m in moves if failed_action(m)]
    times = [m["duration"] for m in moves if m.get("duration") is not None]
    reroll_spend = sum(max(0, m["state"].get("money", 0) - after["state"].get("money", 0))
                       for m, after in zip(moves, moves[1:]) if m.get("msg", "").startswith("Reroll –"))
    return {"run": Path(path).name, "version": meta.get("version", "unknown"),
            "seed": meta.get("seed") or next((s["seed"] for s in states if s.get("seed")), None),
            "stake": meta.get("stake", last.get("stake")), "deck": meta.get("deck_type", last.get("deck_type")),
            "moves": len(moves), "won": won, "finished": last.get("phase") == "game_over" or won,
            "ante": max((s.get("ante") or 0 for s in states), default=0),
            "lost_to": last.get("blind") if last.get("phase") == "game_over" else None,
            "failures": len(failed), "failure_types": dict(Counter(m["msg"].split(" – ")[0] for m in failed)),
            "stuck_observations": sum(any(text in m.get("msg", "").lower() for text in
                                           ("waiting for a change", "wartet auf änderung")) for m in moves),
            "reroll_spend": reroll_spend, "median_seconds": median(times) if times else None}


def statistics(base=RUNS):
    rows, errors = [], []
    for path in sorted(Path(base).glob("*")):
        if (path / "moves.jsonl").is_file():
            try:
                rows.append(summary(path))
            except (ValueError, KeyError, OSError) as error:
                errors.append(f"{path.name}: {error}")
    versions = defaultdict(list)
    for row in rows:
        versions[row["version"]].append(row)
    grouped = []
    for version, runs in versions.items():
        complete = [r for r in runs if r["finished"]]
        grouped.append({"version": version, "runs": len(runs), "finished": len(complete),
                        "win_rate": sum(r["won"] for r in complete) / len(complete) if complete else None,
                        "median_ante": median(r["ante"] for r in complete) if complete else None,
                        "failures": sum(r["failures"] for r in runs),
                        "reroll_spend": sum(r["reroll_spend"] for r in runs)})
    return {"runs": rows, "versions": grouped,
            "loss_bosses": dict(Counter(r["lost_to"] for r in rows if r["lost_to"])),
            "failures": dict(sum((Counter(r["failure_types"]) for r in rows), Counter())), "errors": errors}


def paired(rows, before, after):
    """Match seed, stake and deck, keeping the latest completed run per version and seed."""
    if before == after:
        raise ValueError("Choose two different versions")
    groups = defaultdict(dict)
    for row in rows:
        if row["seed"] and row["finished"] and row["version"] not in ("mixed", "unknown") and row["version"] in (before, after):
            groups[(row["seed"], row["stake"], row["deck"])][row["version"]] = row
    pairs = [(r[before], r[after]) for r in groups.values() if before in r and after in r]
    return {"before": before, "after": after, "paired_seeds": len(pairs),
            "win_delta": sum(b["won"] - a["won"] for a, b in pairs) / len(pairs) if pairs else None,
            "ante_delta": median(b["ante"] - a["ante"] for a, b in pairs) if pairs else None,
            "failure_delta": sum(b["failures"] - a["failures"] for a, b in pairs),
            "pairs": [{"seed": a["seed"], "before": a["run"], "after": b["run"]} for a, b in pairs]}


def prune(base=RUNS, days=30, apply=False):
    """Keep logs, reports, patches and evidence; only old work copies and routine images are eligible."""
    from .learn import CASES, evaluate, load
    if days < 1:
        raise ValueError("Retention must be at least one day")
    cutoff = time.time() - days * 86400
    candidates = []
    for path in sorted(Path(base).glob("*")):
        log = path / "moves.jsonl"
        if not log.is_file() or path.is_symlink() or log.stat().st_mtime >= cutoff:
            continue
        try:
            moves = load(path)
            evidence = {m["n"] for m in moves if not m.get("ok", True) or m["state"].get("phase") == "game_over"}
            evidence.update(x["n"] for x in evaluate(moves)["mismatches"])
            for report in (path / "report.txt", path / "opus.md"):
                if report.is_file():
                    evidence.update(int(n) for n in re.findall(r"(?:#|\bZ|\bmoves?\s+)(\d+)", report.read_text(), re.I))
            evidence.update(int(p.stem.rsplit("-", 1)[-1]) for p in CASES.glob(path.name + "-*.json")
                            if p.stem.rsplit("-", 1)[-1].isdigit())
        except (ValueError, KeyError, OSError):
            continue  # preserve unparseable runs for inspection
        if moves:
            evidence.update((moves[0]["n"], moves[-1]["n"]))
        # Retain neighbours so an error still has its before/after context.
        evidence |= {n + delta for n in list(evidence) for delta in (-1, 1)}
        for item in path.iterdir():
            if item.is_symlink() or item.stat().st_mtime >= cutoff:
                continue
            if (item.name == "work" and item.is_dir() and (path / "opus.patch").is_file() and (path / "opus.md").is_file()) or (item.suffix.lower() in (".jpg", ".png")
                                                         and item.stem.isdecimal() and int(item.stem) not in evidence):
                candidates.append(item)
    size = sum(sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())
               if p.is_dir() else p.stat().st_size for p in candidates)
    if apply:
        for path in candidates:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
    return {"applied": apply, "bytes": size, "paths": [str(p) for p in candidates]}
