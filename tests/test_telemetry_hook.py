"""Runs the Lua hook from patch_balatro.bat against mocked Balatro globals (needs luajit) and parses its output.
Run: .venv/bin/python -m tests.test_telemetry_hook"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from balatro.engine import most_played
from balatro.localvision.telemetry import parse_tsv
from balatro.models import State

ROOT = Path(__file__).resolve().parent.parent


def main():
    if not shutil.which("luajit"):
        print("telemetry hook skipped (no luajit)")
        return
    bat = (ROOT / "patch_balatro.bat").read_text(encoding="utf-8").replace("\r\n", "\n")
    hook = re.search(r"\$luaHook = @'\n(.*?)\n'@", bat, re.S).group(1)
    with tempfile.NamedTemporaryFile("w", suffix=".lua") as f:
        f.write(hook)
        f.flush()
        tsv = subprocess.run(["luajit", str(ROOT / "tests/fixtures/telemetry_mock.lua"), f.name],
                             capture_output=True, text=True, check=True).stdout
        ready = subprocess.run(["luajit", str(ROOT / "tests/fixtures/telemetry_mock.lua"), f.name, "cashout"],
                               capture_output=True, text=True, check=True).stdout
    d, _, _ = parse_tsv(tsv)
    assert d["blind"] == "The Ox" and d["money"] == 12 and len(d["hand"]) == 2, d
    assert d["last_tarot_planet"] == "Jupiter" and d["upcoming_boss"] == "The Ox"
    assert (d["small_tag"], d["big_tag"]) == ("Rare Tag", "D6 Tag")
    assert most_played(State.from_dict({**d, "played_counts": {"High Card": 9, "Pair": 8}})) == "Pair"
    j = d["jokers"][0]
    assert j["name"] == "Yorick" and j["rental"] and j["perish"] == 3 and j["vars"] == {"x": 2.0, "discarded": 12}, j
    s = State.from_dict(d)
    assert [str(c) for c in s.full_deck] == ["KS", "10H[glass,poly,red seal]", "AC", "Stone"], s.full_deck
    assert [str(c) for c in s.get_deck()] == ["AC", "Stone"] and s.jokers[0].rental
    assert s.last_tarot_planet == "Jupiter"
    assert d["telemetry_version"] == 3 and d["seed"] == "TESTSEED" and s.stake == 8
    assert s.round_hands == 6 and s.round_discards == 4 and s.discards_used == 2
    assert not d["cash_out_ready"] and parse_tsv(ready)[0]["cash_out_ready"]
    print("telemetry hook ok")


if __name__ == "__main__":
    main()
