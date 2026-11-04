"""Loopback-only replay and what-if editor. Uses the standard library; never writes edited states to runs."""
import contextlib
import json
import secrets
import threading
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .data import HAND_TYPES, JOKER_NAMES
from .engine import score
from .models import State
from .runs import RUNS, failed_action, paired, statistics


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError("State must be an object")
    json.dumps(raw, allow_nan=False)
    if not isinstance(raw.get("hand_levels", {}), dict):
        raise ValueError("Hand levels must be an object")
    for key in ("deck", "full_deck"):
        if raw.get(key) is not None and not isinstance(raw[key], list):
            raise ValueError(key + " must be an array")
    for joker in raw.get("jokers", []):
        if isinstance(joker, dict) and not isinstance(joker.get("vars", {}), dict):
            raise ValueError("Joker vars must be an object")
    if not isinstance(raw.get("hand", []), list) or len(raw.get("hand", [])) > 12:
        raise ValueError("The interactive editor supports up to 12 hand cards")
    if not isinstance(raw.get("jokers", []), list) or len(raw.get("jokers", [])) > 12:
        raise ValueError("The interactive editor supports up to 12 jokers")
    state = State.from_dict(raw)
    for card in state.hand + state.get_full_deck():
        if card.rank not in range(2, 15) or card.suit not in set("SHCD"):
            raise ValueError("Invalid rank or suit")
    if len(state.get_full_deck()) > 256 or not 1 <= state.hand_size <= 12:
        raise ValueError("Editor limit: 256 deck cards and hand size 1–12")
    if any(j.name not in JOKER_NAMES for j in state.jokers):
        raise ValueError("Unknown joker name")
    if not 0 <= state.hands_left <= 20 or not 0 <= state.discards_left <= 20:
        raise ValueError("Hands and discards must be between 0 and 20")
    if any(h not in HAND_TYPES or not isinstance(v, int) or not 1 <= v <= 100000 for h, v in state.hand_levels.items()):
        raise ValueError("Invalid hand level")
    return state


def analyze(raw, indices=None, selection_only=False):
    from .hud import model
    from .live import plan
    from .optimizer import format_recommendation, recommend
    state = validate(raw)
    selected = None
    if indices is not None:
        if (not isinstance(indices, list) or not 1 <= len(indices) <= 5 or
                any(type(i) is not int or not 0 <= i < len(state.hand) for i in indices) or len(set(indices)) != len(indices)):
            raise ValueError("Select 1–5 distinct cards from the hand")
        selected = asdict(score(state, indices, verbose=True))
    if selection_only:
        if selected is None:
            raise ValueError("Select at least one card")
        return {"selected": selected}
    details = {}
    if state.hand and raw.get("phase", "playing") == "playing":
        rec = recommend(state, samples=16)
        text = format_recommendation(rec)
    else:
        text, rec = plan(raw, details=details)
    out = model(raw, text, rec, details)
    if selected:
        out["selected"] = selected
    return out


def replay(path):
    from .learn import evaluate, load, plays
    moves = load(path)
    actual = {m["n"]: points for m, points in plays(moves)}
    mismatches = {m["n"] for m in evaluate(moves)["mismatches"]}
    return [{**m, "actual": actual.get(m["n"]), "mismatch": m["n"] in mismatches, "failure": failed_action(m),
             "image": next((f"{m['n']:03d}{ext}" for ext in (".jpg", ".png")
                            if (path / f"{m['n']:03d}{ext}").is_file()), None)} for m in moves]


def make_server(base=RUNS, port=0):
    base = Path(base).resolve()
    token = "/" + secrets.token_urlsafe(24) + "/"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, value, status=200, kind="application/json"):
            data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode() if kind == "application/json" else value
            self.send_response(status)
            self.send_header("Content-Type", kind + ("; charset=utf-8" if kind.startswith("text/") else ""))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def route(self):
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                raise ValueError("Unexpected host")
            path = unquote(urlsplit(self.path).path)
            if not path.startswith(token):
                raise FileNotFoundError("Unknown page")
            return path[len(token):]

        def run_path(self, name):
            path = base / name
            if name != path.name or path.is_symlink() or path.resolve().parent != base or not (path / "moves.jsonl").is_file():
                raise FileNotFoundError("Run not found")
            return path

        def do_GET(self):
            try:
                route = self.route()
                if route == "":
                    return self.send((Path(__file__).parent / "ui" / "inspect.html").read_bytes(), kind="text/html")
                if route == "favicon.ico":
                    return self.send(b"", kind="image/x-icon")
                if route == "api/runs":
                    return self.send(statistics(base))
                if route == "api/catalog":
                    return self.send({"jokers": JOKER_NAMES, "hands": list(HAND_TYPES)})
                if route.startswith("api/run/"):
                    return self.send(replay(self.run_path(route.removeprefix("api/run/"))))
                if route.startswith("frame/"):
                    _, run, image = route.split("/")
                    if not image.endswith((".jpg", ".png")) or not Path(image).stem.isdecimal():
                        raise ValueError("Invalid frame")
                    path = self.run_path(run) / image
                    if path.is_symlink():
                        raise ValueError("Invalid frame")
                    return self.send(path.read_bytes(), kind="image/jpeg" if image.endswith(".jpg") else "image/png")
                raise FileNotFoundError("Unknown page")
            except FileNotFoundError as error:
                self.send({"error": str(error)}, 404)
            except (ValueError, TypeError, KeyError, OSError) as error:
                self.send({"error": str(error)}, 400)

        def do_POST(self):
            try:
                route = self.route()
                origin = self.headers.get("Origin")
                if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                    raise ValueError("Cross-origin request refused")
                if self.headers.get("Content-Type") != "application/json":
                    raise ValueError("Expected application/json")
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 262144:
                    raise ValueError("Request must be at most 256 KiB")
                body = json.loads(self.rfile.read(length))
                if route in ("api/analyze", "api/score"):
                    return self.send(analyze(body["state"], body.get("indices"), route == "api/score"))
                if route in ("api/boss", "api/deck"):
                    state = validate(body["state"])
                    if route == "api/boss":
                        from .strategy import boss_forecast
                        return self.send(boss_forecast(state, int(body["state"].get("ante") or 1)))
                    from .deckplan import advice
                    return self.send(advice(state))
                if route == "api/control":
                    from .control import request
                    return self.send(request(body["command"]))
                if route == "api/pairs":
                    return self.send(paired(statistics(base)["runs"], body["before"], body["after"]))
                raise FileNotFoundError("Unknown endpoint")
            except FileNotFoundError as error:
                self.send({"error": str(error)}, 404)
            except (ValueError, TypeError, KeyError, RuntimeError, OSError, ArithmeticError, AttributeError) as error:
                self.send({"error": str(error)}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.url = f"http://127.0.0.1:{server.server_port}{token}"
    return server


def serve(base=RUNS, port=0, browser=True):
    with make_server(base, port) as server:
        print(f"Replay and what-if editor: {server.url}\nCtrl+C stops the local server.", flush=True)
        if browser:
            threading.Timer(0.3, lambda: webbrowser.open(server.url)).start()
        with contextlib.suppress(KeyboardInterrupt):
            server.serve_forever()
