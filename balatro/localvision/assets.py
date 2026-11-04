"""Download reference sprites from the Balatro wikis (MediaWiki API: balatrowiki.org, fandom fallback).

python -m balatro.localvision.assets  -> ~/.local/share/balatro/assets/<category>/<name>.png
Images stay local (game art, not committed). Only valid PNG/JPEG images are kept.
"""
import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

from ..data import JOKER_NAMES, PLANETS
from ..shop import VOUCHER_POINTS

APIS = ["https://balatrowiki.org/api.php", "https://balatrogame.fandom.com/api.php"]
UA = {"User-Agent": "balatro-analyzer/0.1 (personal, non-commercial)"}
ASSETS = Path.home() / ".local/share/balatro/assets"

RANK_WORDS = {"J": "Jack", "Q": "Queen", "K": "King", "A": "Ace"}
SUIT_WORDS = {"S": "Spades", "H": "Hearts", "C": "Clubs", "D": "Diamonds"}
TAROTS = ["The Fool", "The Magician", "The High Priestess", "The Empress", "The Emperor", "The Hierophant",
          "The Lovers", "The Chariot", "Justice", "The Hermit", "The Wheel of Fortune", "Strength",
          "The Hanged Man", "Death", "Temperance", "The Devil", "The Tower", "The Star", "The Moon", "The Sun",
          "Judgement", "The World"]
SPECTRALS = ["Familiar", "Grim", "Incantation", "Talisman", "Aura", "Wraith", "Sigil", "Ouija", "Ectoplasm",
             "Immolate", "Ankh", "Deja Vu", "Hex", "Trance", "Medium", "Cryptid", "The Soul", "Black Hole"]


def card_key(rank, suit):
    return f"{rank}{suit}"


def wanted():
    """category -> {local name: [candidate wiki file titles]}"""
    cards = {}
    for s, sw in SUIT_WORDS.items():
        for r in ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]:
            cards[card_key(r, s)] = [f"{RANK_WORDS.get(r, r)} of {sw}"]
    plain = lambda names: {n: [n, n.replace("é", "e"), n.replace("!", ""), n.replace(".", "")] for n in names}
    return {
        "jokers": plain(JOKER_NAMES),
        "cards": cards,
        "planets": plain(PLANETS),
        "jokers_negative": {n: [c + " (Negative)" for c in cs] for n, cs in plain(JOKER_NAMES).items()},
        "tarots": {n: [n, n + " (Tarot)", n + " (Tarot Card)"] for n in TAROTS},
        "spectrals": {n: [n, n + " (Spectral)", n + " (Spectral Card)"] for n in SPECTRALS},
        "vouchers": plain(VOUCHER_POINTS),
    }


def _get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code not in (403, 429, 503) or i == tries - 1:
                raise
            time.sleep(5 * (i + 1))  # CDN rate limit


def image_urls(titles):
    """wiki file titles (without 'File:') -> {title: url}; first wiki that has a file wins."""
    out = {}
    for api in reversed(APIS):
        out |= _image_urls(api, titles)
    return out


def _image_urls(api, titles):
    out = {}
    titles = list(dict.fromkeys(titles))
    for i in range(0, len(titles), 50):
        q = urllib.parse.urlencode({"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url",
                                    "titles": "|".join("File:" + t + ".png" for t in titles[i:i + 50])})
        data = json.loads(_get(f"{api}?{q}"))
        norm = {n["to"]: n["from"] for n in data["query"].get("normalized", [])}
        for p in data["query"]["pages"].values():
            if "imageinfo" in p:
                title = norm.get(p["title"], p["title"])
                out[title.removeprefix("File:").removesuffix(".png")] = p["imageinfo"][0]["url"]
        time.sleep(0.5)
    return out


def download(force=False):
    missing = []
    for cat, items in wanted().items():
        d = ASSETS / cat
        d.mkdir(parents=True, exist_ok=True)
        todo = {n: c for n, c in items.items() if force or not (d / f"{n}.png").exists()}
        urls = image_urls([t for c in todo.values() for t in c])
        for name, cands in todo.items():
            url = next((urls[c] for c in cands if c in urls), None)
            if url is None:
                missing.append(f"{cat}/{name}")
                continue
            try:
                img = Image.open(io.BytesIO(_get(url)))
                img.load()
                img.convert("RGBA").save(d / f"{name}.png")
            except Exception as e:  # untrusted download: skip anything that isn't a real image
                missing.append(f"{cat}/{name} ({e})")
            time.sleep(0.2)
        print(f"{cat}: {len(list(d.glob('*.png')))}/{len(items)}")
    return missing


if __name__ == "__main__":
    miss = download(force="--force" in sys.argv)
    if miss:
        print("Missing:", ", ".join(miss))
