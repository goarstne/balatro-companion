"""Screenshot -> state dict (the State.from_dict format).

recognize() uses the Claude API when ANTHROPIC_API_KEY is set, otherwise the local
Claude Code CLI (`claude -p`, runs on the user's subscription).
"""
import base64
import difflib
import json
import os
import shutil
import subprocess
from pathlib import Path

from .data import BOSSES, HAND_TYPES, JOKER_NAMES

BLINDS = ["Small Blind", "Big Blind", *BOSSES]
RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}

SYSTEM = """You read screenshots of the card game Balatro (vanilla 1.0.1) and report the game state exactly.

Screen layout (1920x1080 reference):
- LEFT PANEL, top to bottom: blind name in a coloured banner ("Small Blind", "Big Blind" or a boss such as
  "The Flint", "The Eye", "The Goad"; the boss effect is written beneath it), "Score at least N" (= blind_chips),
  "Round score" (= score already made this round), the selected hand type with "lvl.N" and chips x mult,
  then "Hands" (blue number = hands_left) and "Discards" (red number = discards_left), "$N" (money),
  "Ante N/8" and "Round N".
- TOP ROW, left/centre: jokers, left to right; a counter like "4/5" below them = jokers held / joker_slots.
- TOP RIGHT: consumables (tarot, planet, spectral cards), counter like "0/2" below them.
- BOTTOM: the hand, left to right; a counter like "8/8" below = cards in hand / hand_size.
  Cards raised above the row are selected - still report them as part of the hand, in left-to-right order.
  Cards in the middle of the table are being played/scored right now - do not include them in the hand.
- BOTTOM RIGHT: draw pile with "remaining/total" (e.g. 41/52).

Playing cards:
- Rank in the corner (2-10, J, Q, K, A); suits: Spades black, Clubs dark green/black, Hearts red, Diamonds orange.
- Enhancements: Bonus = pale blue-tinted card (+30 chips); Mult = red-tinted card (+4 Mult);
  Wild = multicoloured suit-pattern card; Glass = translucent/glassy card with light streaks;
  Steel = grey brushed-metal card; Stone = grey stone slab, NO rank or suit visible (report rank "A", suit "S");
  Gold = gold-coloured card; Lucky = card with green clover/lucky pattern.
- Editions (on cards and jokers): Foil = shiny silver-blue sheen; Holographic = rainbow hologram stripes,
  reddish tint; Polychrome = strong full rainbow colouring; Negative = inverted dark colours.
- Seals: a small coloured circle/wax seal on the card: red, gold, blue or purple.
- Debuffed cards show a red X / are greyed out (boss blinds like The Goad debuff whole suits).
- Face-down cards show the card back: set face_down true and guess rank "A", suit "S".

Jokers: use the exact name from the allowed list. If you cannot identify a joker, pick the closest name and
say so in notes. Scaling values (e.g. Ride the Bus mult) are usually only visible in a tooltip - mention
any value you can read in notes.

Phase: "playing" = hand cards at the bottom and Play Hand / Discard buttons; "shop" = the shop panel
(jokers/cards for sale, a voucher, booster packs, "Reroll $N", "Next Round"); "booster_pack" = an opened
pack offering cards to choose ("choose 1 of 3"); "blind_select" = choosing Small/Big/Boss blind (Select/Skip).
shop: every item that can be taken - in the shop the cards for sale, the voucher and the booster packs with
their price tags; in an opened booster pack the offered cards with price null. kind joker -> exact joker name
from the allowed list; planet/tarot/spectral/voucher -> exact English card name; booster_pack -> pack name as
written (e.g. "Buffoon Pack", "Jumbo Celestial Pack"); playing_card -> e.g. "K of Hearts, Glass".
reroll_cost: the number on the Reroll button. ante: the "Ante N/8" number.

Hand levels: if a "lvl.N" for a hand type is visible, report it in hand_levels; otherwise leave the list empty.
Use null for anything that is not visible. Put every uncertainty in notes."""


def _null(schema):
    return {"anyOf": [schema, {"type": "null"}]}


def _enum(values):
    return {"type": "string", "enum": list(values)}


def _obj(**props):
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


CARD = _obj(rank=_enum(RANKS), suit=_enum("SHCD"),
            enhancement=_null(_enum(["bonus", "mult", "wild", "glass", "steel", "stone", "gold", "lucky"])),
            edition=_null(_enum(["foil", "holo", "poly", "negative"])),
            seal=_null(_enum(["red", "gold", "blue", "purple"])),
            debuff={"type": "boolean"}, face_down={"type": "boolean"})
JOKER = _obj(name=_enum(JOKER_NAMES), edition=_null(_enum(["foil", "holo", "poly", "negative"])),
             debuff={"type": "boolean"})
PHASES = ["playing", "shop", "booster_pack", "blind_select", "other"]
SHOP_KINDS = ["joker", "planet", "tarot", "spectral", "voucher", "booster_pack", "playing_card"]
SHOP_ITEM = _obj(kind=_enum(SHOP_KINDS), name={"type": "string"}, price=_null({"type": "integer"}),
                 edition=_null(_enum(["foil", "holo", "poly", "negative"])))
SCHEMA = _obj(
    hand={"type": "array", "items": CARD},
    jokers={"type": "array", "items": JOKER},
    joker_slots=_null({"type": "integer"}),
    consumables={"type": "array", "items": {"type": "string"}},
    blind=_null(_enum(BLINDS)),
    blind_chips=_null({"type": "integer"}),
    score=_null({"type": "integer"}),
    hands_left=_null({"type": "integer"}),
    discards_left=_null({"type": "integer"}),
    hand_size=_null({"type": "integer"}),
    money=_null({"type": "integer"}),
    hand_levels={"type": "array", "items": _obj(hand=_enum(HAND_TYPES), level={"type": "integer"})},
    phase=_enum(PHASES),
    ante=_null({"type": "integer"}),
    reroll_cost=_null({"type": "integer"}),
    shop={"type": "array", "items": SHOP_ITEM},
    notes={"type": "array", "items": {"type": "string"}},
)


def _closest(name, choices):
    """Exact (case-insensitive) or closest match; None if nothing is close."""
    low = {c.lower(): c for c in choices}
    if name.lower() in low:
        return low[name.lower()]
    m = difflib.get_close_matches(name.lower(), list(low), n=1, cutoff=0.5)
    return low[m[0]] if m else None


def normalize(raw):
    """Model output -> State.from_dict-compatible dict. Problems go to d['notes'] (ignored by State)."""
    notes = list(raw.get("notes") or [])
    d = {k: v for k, v in raw.items() if v is not None and k not in ("notes", "hand_levels")}
    d["hand_levels"] = {h["hand"]: h["level"] for h in raw.get("hand_levels") or []}
    d["hand"] = [{k: v for k, v in c.items() if v not in (None, False)} for c in raw.get("hand", [])]
    jokers = []
    for j in raw.get("jokers", []):
        name = _closest(j["name"], JOKER_NAMES)
        if name is None:
            notes.append(f"Unbekannter Joker '{j['name']}' verworfen")
            continue
        if name != j["name"]:
            notes.append(f"Joker '{j['name']}' als '{name}' interpretiert")
        jokers.append({k: v for k, v in {**j, "name": name}.items() if v not in (None, False)})
    d["jokers"] = jokers
    if d.get("blind"):
        b = _closest(d["blind"], BLINDS)
        if b != d["blind"]:
            notes.append(f"Blind '{d['blind']}' als '{b}' interpretiert")
        d["blind"] = b
    for item in d.get("shop", []):
        if item["kind"] == "joker":
            name = _closest(item["name"], JOKER_NAMES)
            if name and name != item["name"]:
                notes.append(f"Shop-Joker '{item['name']}' als '{name}' interpretiert")
            item["name"] = name or item["name"]
    d["notes"] = notes
    return d


def state_from_screenshot(path, model="claude-opus-5-5"):
    """Recognise the game state on a Balatro screenshot. Returns a State.from_dict dict (+ 'notes')."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set - screenshot recognition via the API is not possible.\n"
            "Alternativen:\n"
            "  1. Show the screenshot in Claude Code and ask 'what should I play?' (skill 'balatro'):\n"
            "     Claude reads the image itself and writes state.json.\n"
            "  2. write state.json by hand (see examples/) and\n"
            "     run 'python -m balatro analyze state.json'.\n"
            "  3. export ANTHROPIC_API_KEY=sk-ant-...")
    import anthropic

    p = Path(path)
    media = MEDIA.get(p.suffix.lower())
    if media is None:
        raise ValueError(f"unsupported image format: {p.suffix}")
    # Opus 5.5 rejects forced tool_choice, so structured outputs (output_config.format) enforce the schema.
    resp = anthropic.Anthropic().messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media,
                                         "data": base64.standard_b64encode(p.read_bytes()).decode()}},
            {"type": "text", "text": "Report the game state shown in this Balatro screenshot."},
        ]}],
    )
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Claude returned no complete state (stop_reason={resp.stop_reason})")
    text = next(b.text for b in resp.content if b.type == "text")
    return normalize(json.loads(text))


def state_via_claude_cli(path, model="sonnet", timeout=180):
    """Same as state_from_screenshot, but through headless Claude Code (no API key needed)."""
    if not shutil.which("claude"):
        raise RuntimeError("Neither ANTHROPIC_API_KEY is set nor 'claude' (Claude Code CLI) found.")
    p = Path(path).resolve()
    cmd = ["claude", "-p", f"Read the Balatro screenshot {p} with the Read tool and report the game state.",
           "--append-system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA), "--output-format", "json",
           "--allowedTools", "Read", "--model", model, "--no-session-persistence", "--strict-mcp-config"]
    out = subprocess.run(cmd, cwd=p.parent, capture_output=True, text=True, timeout=timeout)
    try:
        d = json.loads(out.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"claude -p failed (exit {out.returncode}): {(out.stderr or out.stdout)[-500:]}")
    if d.get("is_error") or not d.get("structured_output"):
        raise RuntimeError(f"claude -p returned no state: {str(d.get('result'))[:500]}")
    return normalize(d["structured_output"])


def recognize(path, model=None, mode="local", memory=None, pre=None):
    """mode local (default): template matching only, never Claude; auto: Claude only if something is uncertain;
    claude: always Claude (API if ANTHROPIC_API_KEY is set, else Claude Code CLI).
    memory: dict kept across calls (live mode) so resolved joker names are reused.
    pre: recognize_local(path) result the caller already has (live mode reads every frame anyway)."""
    memory = {} if memory is None else memory
    d = _recognize(path, model, mode, memory, pre)
    if d.get("jokers") is not None and d.get("phase") != "other":
        memory["jokers"] = d["jokers"]
    return d


def _recognize(path, model, mode, memory, pre=None):
    if mode != "claude":
        from .localvision import MIN_CONF, apply_memory, merge, recognize_local
        try:
            local, confs, notes = pre or recognize_local(path)
        except Exception as e:  # missing assets or a recogniser bug: fall back unless local-only
            if mode == "local":
                raise
            local, confs, notes = {}, {}, [f"local recognition failed: {e}"]
        apply_memory(local, confs, memory)
        if mode == "local" or (confs and min(confs.values()) >= MIN_CONF):
            local["notes"] = notes + ["source: local"]
            return local
        low = sorted((c, k) for k, c in confs.items() if c < MIN_CONF)
        print("Local recognition unsure, asking Claude:", ", ".join(f"{k} ({c:.2f})" for c, k in low[:4]))
        claude = _recognize_claude(path, model)
        d = merge(local, confs, claude)
        d["notes"] = claude.get("notes", []) + notes + ["source: Claude + local"]
        return d
    d = _recognize_claude(path, model)
    d["notes"] = d.get("notes", []) + ["source: Claude"]
    return d


def _recognize_claude(path, model):
    if os.environ.get("ANTHROPIC_API_KEY"):
        return state_from_screenshot(path, **({"model": model} if model else {}))
    return state_via_claude_cli(path, **({"model": model} if model else {}))
