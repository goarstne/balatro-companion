"""Run: .venv/bin/python -m tests.test_vision  (no network)."""
import json
from pathlib import Path

from balatro.models import State
from balatro.vision import SCHEMA, normalize

ROOT = Path(__file__).resolve().parent.parent


def test_normalize():
    raw = {
        "hand": [{"rank": "K", "suit": "H", "enhancement": None, "edition": "foil", "seal": "red",
                  "debuff": False, "face_down": False},
                 {"rank": "10", "suit": "S", "enhancement": "glass", "edition": None, "seal": None,
                  "debuff": True, "face_down": False}],
        "jokers": [{"name": "blue print", "edition": None, "debuff": False},
                   {"name": "Joker", "edition": "poly", "debuff": False},
                   {"name": "zzzzzzzz", "edition": None, "debuff": False}],
        "joker_slots": 5, "consumables": ["Pluto"], "blind": "the flint", "blind_chips": 3000,
        "score": None, "hands_left": 3, "discards_left": None, "hand_size": 8, "money": 7,
        "hand_levels": [{"hand": "Flush", "level": 3}],
        "notes": ["right joker half covered"],
    }
    d = normalize(raw)
    assert [j["name"] for j in d["jokers"]] == ["Blueprint", "Joker"], d["jokers"]
    assert d["jokers"][1]["edition"] == "poly"
    assert d["blind"] == "The Flint"
    assert d["hand_levels"] == {"Flush": 3}
    assert "score" not in d and "discards_left" not in d          # nulls -> State defaults
    assert d["notes"][0] == "right joker half covered"
    assert any("zzzzzzzz" in n for n in d["notes"]) and any("Blueprint" in n for n in d["notes"])
    s = State.from_dict(d)
    assert (s.hand[0].rank, s.hand[0].suit, s.hand[0].seal, s.hand[0].edition) == (13, "H", "red", "foil")
    assert s.hand[1].debuff and s.hand[1].enhancement == "glass" and s.hand[1].rank == 10
    assert s.discards_left == 3 and s.blind_chips == 3000 and s.level("Flush") == 3


def test_schema_strict():
    """Structured outputs need every object closed and fully required."""
    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object":
                assert n["additionalProperties"] is False and set(n["required"]) == set(n["properties"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(SCHEMA)


def test_examples_load():
    files = sorted((ROOT / "examples").glob("*.json"))
    assert len(files) >= 2
    for f in files:
        d = json.loads(f.read_text())
        s = State.from_dict(d)
        assert (len(s.hand) == 8 and s.jokers) or d.get("phase") == "shop", f


def test_request_offline():
    """Full SDK round trip against a mock transport: checks the request body the API would get."""
    import os

    import importlib.util
    if not importlib.util.find_spec("anthropic"):
        print("SDK test skipped (install .[claude])")
        return
    import anthropic
    import httpx2

    from balatro import vision
    sent = {}
    out = {"hand": [], "jokers": [{"name": "Mime", "edition": None, "debuff": False}], "joker_slots": None,
           "consumables": [], "blind": "Small Blind", "blind_chips": 300, "score": 0, "hands_left": 4,
           "discards_left": 3, "hand_size": 8, "money": 4, "hand_levels": [], "notes": []}

    def handler(req):
        sent.update(json.loads(req.content))
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": sent["model"],
            "content": [{"type": "text", "text": json.dumps(out)}], "stop_reason": "end_turn",
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})

    real = anthropic.Anthropic
    anthropic.Anthropic = lambda: real(api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    img = ROOT / "tests" / "_tiny.png"
    img.write_bytes(bytes.fromhex("89504e470d0a1a0a"))
    try:
        d = vision.state_from_screenshot(img)
    finally:
        anthropic.Anthropic = real
        img.unlink()
    assert sent["model"] == "claude-opus-5-5" and "tool_choice" not in sent
    assert sent["output_config"]["format"]["schema"] == vision.SCHEMA
    assert sent["messages"][0]["content"][0]["source"]["media_type"] == "image/png"
    assert d["jokers"] == [{"name": "Mime"}] and d["blind_chips"] == 300


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
