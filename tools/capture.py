"""Screenshots and short GIFs of the game and the widget for the README (Hyprland: grim, wf-recorder, ffmpeg).

    .venv/bin/python -m tools.capture shot game|widget|both docs/media/<name>.png
    .venv/bin/python -m tools.capture gif  game|widget|both <seconds> docs/media/<name>.gif [--fps 12] [--width 960]

"both" is the bounding box of game and widget (place the widget next to the game, not on top of it).
"""
import argparse
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from balatro.live import find_window
from balatro.pointer import hypr_json

WIDGET = "org.omarchy.balatro-hud"


def geometry(what, window="Balatro|Shadow PC"):
    boxes = []
    if what in ("game", "both"):
        g = find_window(window)
        if g is None:
            raise SystemExit("game window not visible")
        pos, size = g.split()
        x, y = map(int, pos.split(","))
        w, h = map(int, size.split("x"))
        boxes.append((x, y, x + w, y + h))
    if what in ("widget", "both"):
        c = next((c for c in hypr_json("clients") if c["class"] == WIDGET), None)
        if c is None:
            raise SystemExit("widget not open (python -m balatro live --hud)")
        boxes.append((c["at"][0], c["at"][1], c["at"][0] + c["size"][0], c["at"][1] + c["size"][1]))
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return f"{x0},{y0} {x1 - x0}x{y1 - y0}"


def shot(what, out):
    subprocess.run(["grim", "-g", geometry(what), out], check=True)


def gif(what, seconds, out, fps=12, width=960):
    with tempfile.TemporaryDirectory() as tmp:
        video = Path(tmp) / "clip.mp4"
        rec = subprocess.Popen(["wf-recorder", "-g", geometry(what), "-f", str(video)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(seconds)
        rec.send_signal(signal.SIGINT)  # SIGINT lets wf-recorder finish the mp4 (a kill leaves no moov atom)
        rec.wait(timeout=10)
        # two-pass palette keeps the game's flat colours crisp at a small file size
        vf = f"fps={fps},scale={width}:-1:flags=lanczos"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf",
                        f"{vf},split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];"
                        "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle", out], check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("shot")
    s.add_argument("what", choices=["game", "widget", "both"])
    s.add_argument("out")
    g = sub.add_parser("gif")
    g.add_argument("what", choices=["game", "widget", "both"])
    g.add_argument("seconds", type=float)
    g.add_argument("out")
    g.add_argument("--fps", type=int, default=12)
    g.add_argument("--width", type=int, default=960)
    a = p.parse_args()
    if a.cmd == "shot":
        shot(a.what, a.out)
    else:
        gif(a.what, a.seconds, a.out, a.fps, a.width)
    print(a.out)


if __name__ == "__main__":
    main()
