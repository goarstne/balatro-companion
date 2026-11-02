"""Compare recommendations against a Git revision on the same logged states and RNG seeds."""
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from statistics import median

from .runs import ROOT

WORKER = '''
import json, sys, time
from balatro import optimizer
from balatro.models import State
jobs = json.load(sys.stdin)
# Fixed sample counts make comparisons independent of machine load/time-budget calibration.
optimizer._budget_samples = lambda state, plays, samples, **kw: samples
out = []
for job in jobs:
    started = time.perf_counter()
    try:
        rec = optimizer.recommend(State.from_dict(job["state"]), samples=job["samples"], seed=0, reorder=False)
        out.append({"id": job["id"], "action": rec.action,
                    "idx": list(rec.play_idx if rec.action == "play" else rec.discard.discard_idx),
                    "score": rec.play.score, "p_win": rec.play_win, "seconds": time.perf_counter() - started})
    except Exception as error:
        out.append({"id": job["id"], "error": str(error)})
json.dump(out, sys.stdout)
'''


def corpus(paths, limit=20, samples=8):
    if limit < 1 or not 1 <= samples <= 128:
        raise ValueError("Limit must be positive; samples must be between 1 and 128")
    jobs = []
    for path in map(Path, paths):
        if path.is_dir():
            path = path / "moves.jsonl"
        if path.suffix == ".jsonl":
            entries = [json.loads(line) for line in path.read_text().splitlines() if line]
        else:
            raw = json.loads(path.read_text())
            entries = [{"n": 0, "state": raw.get("state", raw)}]
        for entry in entries:
            state = entry["state"]
            if state.get("phase", "playing") == "playing" and state.get("hand"):
                jobs.append({"id": f"{path.parent.name}/{path.name}:{entry['n']}", "state": state, "samples": samples})
    if not jobs:
        raise ValueError("No playing states found in the corpus")
    # Spread checks across the run instead of testing only its easy opening hands.
    if len(jobs) > limit:
        jobs = [jobs[i * (len(jobs) - 1) // max(1, limit - 1)] for i in range(limit)]
    return jobs


def worker(root, jobs):
    result = subprocess.run([sys.executable, "-c", WORKER], cwd=root, input=json.dumps(jobs),
                            capture_output=True, text=True, timeout=max(60, len(jobs) * 30), check=True)
    return json.loads(result.stdout)


def compare(revision, paths, limit=20, samples=8, root=ROOT):
    root = Path(root)
    ref = subprocess.run(["git", "rev-parse", "--verify", "--end-of-options", revision + "^{commit}"],
                         cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    jobs = corpus(paths, limit, samples)
    with tempfile.TemporaryDirectory(prefix="balatro-benchmark-") as directory:
        with tempfile.TemporaryFile() as archive:
            subprocess.run(["git", "archive", "--format=tar", ref], cwd=root, stdout=archive, check=True)
            archive.seek(0)
            with tarfile.open(fileobj=archive) as tar:
                tar.extractall(directory, filter="data")
        before = worker(directory, jobs)
    after = worker(root, jobs)
    changes = []
    for a, b in zip(before, after):
        if a.get("error") or b.get("error") or (a["action"], a["idx"]) != (b["action"], b["idx"]):
            changes.append({"id": a["id"], "before": a, "after": b})
    timings = lambda rows: median(r["seconds"] for r in rows if "seconds" in r) if any("seconds" in r for r in rows) else None
    return {"revision": ref, "states": len(jobs), "samples": samples, "rng_seed": 0,
            "changed_decisions": len(changes), "before_median_seconds": timings(before),
            "after_median_seconds": timings(after), "changes": changes,
            "note": "Decision and latency comparison only. Measure win-rate changes with paired seeded game runs."}
