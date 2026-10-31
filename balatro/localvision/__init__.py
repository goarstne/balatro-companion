"""Local (no AI) screenshot recognition: template matching against wiki sprites.

recognize_local(path) -> (state dict like vision.normalize, {field: confidence}, notes).
vision.recognize() uses it first; if any field is unsure it asks Claude and merge() keeps
every field the local recogniser was sure about (Claude misreads jokers more often).
"""
import time

from ..data import SUIT_DEBUFF_BOSSES
from .layout import normalize

MIN_CONF = 0.6  # ponytail: one global threshold; tune per field once the dataset is larger


def recognize_local(path):
    from .cards import read_hand, read_pack_hand
    from .hud import read_hud
    from .jokers import read_consumables, read_jokers, read_shop
    from .telemetry import decode_strip

    t = time.time()
    canvas = normalize(path)
    tel = decode_strip(canvas)
    if tel is not None:
        d, confs, notes = tel
        d["hand"] = [{k: v for k, v in c.items() if k not in ("conf", "highlighted")} for c in d.get("hand", [])]
        d["jokers"] = [{k: v for k, v in j.items() if k not in ("conf", "highlighted")} for j in d.get("jokers", [])]
        d["shop"] = [{k: v for k, v in s.items() if k != "conf"} for s in d.get("shop", [])]
        return d, confs, notes
    hud = read_hud(canvas)
    hud_conf = hud.pop("conf", {})
    d = {k: v for k, v in hud.items() if v is not None}
    # round/ante don't change any recommendation: an unsure reading there must not cost a Claude call
    confs = {f"hud.{k}": c for k, c in hud_conf.items()
             if (hud.get(k) is not None or k == "phase") and k not in ("round", "ante")}
    phase = d.get("phase", "other")
    if phase == "other":
        return d, {"hud.phase": confs.get("hud.phase", 0)}, ["No game screen detected"]

    jokers = read_jokers(canvas)
    d["jokers"] = [{k: v for k, v in j.items() if k != "conf" and v not in (None, False)} for j in jokers]
    confs |= {f"joker{i}": j["conf"] for i, j in enumerate(jokers)}
    cons = read_consumables(canvas)
    d["consumables"] = [c["name"] for c in cons]
    d["consumable_items"] = [{k: c[k] for k in ("name", "kind", "pos")} for c in cons]  # for the autopilot
    confs |= {f"consumable{i}": c["conf"] for i, c in enumerate(cons)}
    if phase == "playing":
        hand = read_hand(canvas)
        boss_suit = SUIT_DEBUFF_BOSSES.get(d.get("blind"))
        for c in hand:  # the boss fixes the suit of every card it debuffs; the red tint misleads the colour
            if boss_suit and c["debuff"]:
                c["suit"] = boss_suit
        d["hand"] = [{k: v for k, v in c.items() if k != "conf" and v not in (None, False)} for c in hand]
        confs |= {f"card{i}": c["conf"] for i, c in enumerate(hand)} or {"hand": 0.0}  # playing but no cards read
    if phase == "booster_pack":
        from .booster import read_booster
        b = read_booster(canvas)
        d["booster"] = b
        confs["booster"] = 0.95 if b and b["pack"] else 0.0
        confs |= {f"pack{i}": c.get("conf", 0) for i, c in enumerate(b["cards"] if b else [])}
        if b and any(k in (b.get("pack") or "") for k in ("Arcana", "Spectral")):  # targets for the pack's cards
            hand = read_pack_hand(canvas)
            d["hand"] = [{k: v for k, v in c.items() if k != "conf" and v not in (None, False)} for c in hand]
            confs |= {f"card{i}": c["conf"] for i, c in enumerate(hand)}
    if phase == "shop":
        shop = read_shop(canvas)
        d["shop"] = [{k: v for k, v in s.items() if k != "conf"} for s in shop]
        confs |= {f"shop{i}": s["conf"] for i, s in enumerate(shop)}
    low = sorted((c, k) for k, c in confs.items() if c < MIN_CONF)
    notes = [f"local {time.time() - t:.2f}s"] + [f"uncertain: {k} ({c:.2f})" for c, k in low]
    return d, confs, notes


def apply_memory(local, confs, memory):
    """Reuse joker names from earlier frames for jokers that can't be told apart locally
    (Negative jokers sharing the jester art), as long as the rest of the joker row is unchanged."""
    known, js = memory.get("jokers"), local.get("jokers", [])
    if not known or len(known) != len(js):
        return
    if any(confs.get(f"joker{i}", 1) >= MIN_CONF and j["name"] != known[i]["name"] for i, j in enumerate(js)):
        return  # joker row changed
    for i, j in enumerate(js):
        if confs.get(f"joker{i}", 1) < MIN_CONF and j.get("edition") == known[i].get("edition"):
            js[i] = dict(known[i])
            confs[f"joker{i}"] = MIN_CONF


_LISTS = {"joker": "jokers", "card": "hand", "shop": "shop"}


def merge(local, confs, claude):
    """Claude's dict with every confidently-read local field put back on top."""
    out = dict(claude)
    for k in ("ante", "round"):  # not gated (see recognize_local), but still better than nothing
        if out.get(k) is None and local.get(k) is not None:
            out[k] = local[k]
    # a Negative joker's edition is read reliably even when its name isn't (Claude calls it foil)
    if len(local.get("jokers", [])) == len(claude.get("jokers", [])):
        out["jokers"] = [{**cj, "edition": "negative"} if lj.get("edition") == "negative" else cj
                         for lj, cj in zip(local["jokers"], claude["jokers"])]
    for key, c in confs.items():
        if c < MIN_CONF:
            continue
        if key.startswith("hud."):
            field = key[4:]
            if field in local:
                out[field] = local[field]
            continue
        name = key.rstrip("0123456789")
        lst = _LISTS.get(name)
        # only per-position overlay when both saw the same number of items
        if lst and len(local.get(lst, [])) == len(claude.get(lst, [])):
            i = int(key[len(name):])
            out[lst] = list(out[lst])
            out[lst][i] = local[lst][i]
    return out
