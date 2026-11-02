"""Window lookup and frame diff. Run: .venv/bin/python -m tests.test_live"""
from PIL import Image

from balatro import live

MON = [{"x": 0, "y": 0, "width": 2560, "height": 1080, "scale": 1.25, "activeWorkspace": {"id": 2}}]


def win(x, ws=2, title="Shadow PC - Display"):
    return [{"class": "Shadow PC - Display", "title": title, "at": [x, 28], "size": [1000, 834], "workspace": {"id": ws}}]


def main():
    for clients, want in [(win(1006), "1006,28 1000x834"), (win(2009), None), (win(-961), None),
                          (win(1006, ws=3), None), (win(1006) + win(5, title="x"), "1006,28 1000x834"),
                          ([{**win(5)[0], "class": "foot", "title": "omarchy: balatro"}] + win(1006), "1006,28 1000x834")]:
        live.hypr_json = lambda what, c=clients: MON if what == "monitors" else c
        got = live.find_window("Balatro|Shadow PC")
        assert got == want, (clients[0]["at"], got, want)
    a = Image.new("L", (96, 64), 10)
    assert live.diff(a, a) == 0 and live.diff(None, a) == 100
    assert live.diff(a, Image.new("L", (96, 64), 40)) == 0     # soft shift (background swirl) ignored
    b = a.copy()
    b.paste(200, (0, 0, 48, 64))                                # half the frame changes hard
    assert abs(live.diff(a, b) - 50) < 0.5
    test_memory_and_merge()
    test_state_key()
    test_log_move()
    test_blind_advice()
    print("live ok")


def test_log_move():
    # run frames are kept as JPEG (grim PNGs filled ~200 MB per run) and the telemetry strip still decodes
    import tempfile
    from pathlib import Path

    import numpy as np
    from balatro.localvision.layout import normalize
    from balatro.localvision.telemetry import decode_strip, encode_strip
    img = np.full((1042, 1250, 3), 90, np.uint8)
    img[:53] = img[990:] = 0
    img[53:990] = encode_strip(img[53:990], "B\tshop\t\t0\t0\t4\t3\t8\t17\t2\t5\t5\t5\tRed Deck\t0\t1\t0")
    with tempfile.TemporaryDirectory() as t:
        png = Path(t) / "f.png"
        Image.fromarray(img[:, :, ::-1]).save(png)
        live.log_move(Path(t) / "run", png, {"phase": "shop"}, "x", None, True, "ok")
        live.log_move(Path(t) / "run", png, {"phase": "shop"}, "x", None, True, "ok")
        assert sorted(p.name for p in (Path(t) / "run").iterdir()) == ["000.jpg", "001.jpg", "moves.jsonl", "run.json"]
        assert decode_strip(normalize(Path(t) / "run" / "001.jpg"))[0]["money"] == 17


def test_blind_advice():
    d = {"phase": "blind_select", "blind_col": 0, "ante": 2, "money": 4, "hand": [], "jokers": [],
         "small_tag": "Negative Tag", "upcoming_boss": "The Ox"}
    text = live.plan(d)[0]
    assert text.startswith("RECOMMENDATION") and "Negative Tag" in text and "The Ox" in text, text
    assert "not recognised" in live.plan({**d, "small_tag": None})[0]



def test_memory_and_merge():
    from balatro.localvision import MIN_CONF, apply_memory, merge
    known = {"jokers": [{"name": "Joker", "edition": "negative"}, {"name": "Wily Joker"}, {"name": "Mime"}]}
    local = {"jokers": [{"name": "Odd Todd", "edition": "negative"}, {"name": "Wily Joker"}, {"name": "Mime"}]}
    confs = {"joker0": 0.05, "joker1": 1.0, "joker2": 1.0}
    apply_memory(local, confs, known)
    assert local["jokers"][0]["name"] == "Joker" and confs["joker0"] == MIN_CONF
    changed = {"jokers": [{"name": "Odd Todd", "edition": "negative"}, {"name": "Blueprint"}, {"name": "Mime"}]}
    c2 = {"joker0": 0.05, "joker1": 1.0, "joker2": 1.0}
    apply_memory(changed, c2, known)
    assert changed["jokers"][0]["name"] == "Odd Todd" and c2["joker0"] == 0.05  # row changed: no reuse
    # merge keeps confident local fields over Claude's
    claude = {"money": 9, "jokers": [{"name": "Joker"}, {"name": "Business Card"}], "hands_left": 4}
    loc = {"money": 4, "jokers": [{"name": "Odd Todd"}, {"name": "Wily Joker"}]}
    m = merge(loc, {"hud.money": 1.0, "joker0": 0.1, "joker1": 1.0}, claude)
    assert m["money"] == 4 and [j["name"] for j in m["jokers"]] == ["Joker", "Wily Joker"] and m["hands_left"] == 4
    m = merge({"jokers": [{"name": "Odd Todd", "edition": "negative"}]}, {"joker0": 0.1},
              {"jokers": [{"name": "Joker", "edition": "foil"}]})
    assert m["jokers"] == [{"name": "Joker", "edition": "negative"}]


def test_state_key():
    # regression: discarding 2 cards changed too few pixels and the live mode never re-analysed
    a = {"phase": "playing", "discards_left": 3, "notes": ["x"],
         "hand": [{"rank": "K", "suit": "H", "pos": [0.3, 0.65]}, {"rank": "4", "suit": "D", "pos": [0.7, 0.64]}]}
    b = {**a, "discards_left": 2, "hand": [a["hand"][0], {"rank": "9", "suit": "S", "pos": [0.7, 0.64]}]}
    assert live.state_key(a) != live.state_key(b)
    raised = {**a, "notes": [], "hand": [{**a["hand"][0], "pos": [0.3, 0.62]}, a["hand"][1]]}
    assert live.state_key(a) == live.state_key(raised)  # selecting a card is no new state
    flicker = {**a, "jokers": [{"name": "Luchador"}]}, {**a, "jokers": [{"name": "Odd Todd"}]}
    assert live.state_key(flicker[0], {"joker0": 0.1}) == live.state_key(flicker[1], {"joker0": 0.2})


if __name__ == "__main__":
    main()
