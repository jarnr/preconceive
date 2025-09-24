import os
import random
from typing import List, Dict, Optional

import requests
from flask import Flask, Response, jsonify, render_template


ARCHIDEKT_API_URL = "https://archidekt.com/api/decks/v3/?ownerUsername=Archidekt_Precons"
ARCHIDEKT_DECK_URL = "https://archidekt.com/decks/{id}"


def fetch_all_decks(start_url: str) -> List[Dict]:
    """Fetch all decks from Archidekt, following pagination.

    The API is expected to return JSON with either a 'results' or 'decks' list
    and a 'next' URL for pagination.
    """
    decks: List[Dict] = []
    url: Optional[str] = start_url
    headers = {
        "User-Agent": "preconceive/1.0 (+https://archidekt.com/)",
        "Accept": "application/json",
    }

    while url:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        page_decks = []
        if isinstance(data, dict):
            if isinstance(data.get("results"), list):
                page_decks = data.get("results", [])
            elif isinstance(data.get("decks"), list):
                page_decks = data.get("decks", [])
        elif isinstance(data, list):
            page_decks = data

        if page_decks:
            decks.extend(page_decks)

        url = None
        if isinstance(data, dict):
            next_url = data.get("next")
            if isinstance(next_url, str) and next_url.strip():
                url = next_url

    return decks


def build_deck_url(deck: Dict) -> Optional[str]:
    deck_id = None
    # Support several possible id shapes just in case
    for key in ("id", "deckId", "deck_id"):
        if key in deck:
            deck_id = deck[key]
            break
    if deck_id is None:
        return None
    try:
        deck_id_int = int(deck_id)
    except (TypeError, ValueError):
        return None
    return ARCHIDEKT_DECK_URL.format(id=deck_id_int)


def extract_image_url(deck: Dict) -> Optional[str]:
    """Best-effort extraction of a featured/cover image URL from a deck object."""
    candidate_keys = (
        "featuredImage",
        "featured",
        "image",
        "thumbnail",
        "thumb",
        "cover",
        "coverImage",
        "images",
    )

    def from_value(value) -> Optional[str]:
        if isinstance(value, str) and value.startswith("http"):
            return value
        if isinstance(value, dict):
            for k in ("url", "src", "href"):  # common URL fields
                v = value.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    return v
        if isinstance(value, list) and value:
            first = value[0]
            return from_value(first)
        return None

    for key in candidate_keys:
        if key in deck:
            found = from_value(deck.get(key))
            if found:
                return found
    # Sometimes nested under media or similar
    for parent in ("media", "assets"):  # best guess fallbacks
        if isinstance(deck.get(parent), dict):
            found = from_value(deck[parent])
            if found:
                return found
    return None


def extract_colors_raw(deck: Dict) -> List[str]:
    """Extract raw color letters from common Archidekt fields.

    Returns a list of unique uppercase letters among W, U, B, R, G.
    """
    candidates = []
    for key in ("colors", "colorIdentity", "colourIdentity", "colour", "identity"):
        if key in deck:
            candidates.append(deck.get(key))

    colors: List[str] = []
    def add_letter(letter: str) -> None:
        upper = letter.upper()
        if upper in ("W", "U", "B", "R", "G") and upper not in colors:
            colors.append(upper)

    name_to_letter = {
        "white": "W", "w": "W", "wht": "W",
        "blue": "U", "u": "U", "blu": "U",
        "black": "B", "b": "B", "blk": "B",
        "red": "R", "r": "R",
        "green": "G", "g": "G", "grn": "G",
    }

    for c in candidates:
        if isinstance(c, str):
            # Split on non-letters to accommodate formats like "W,U" or "White,Blue"
            tokens = [t for t in __import__('re').split(r"[^A-Za-z]", c) if t]
            if not tokens:
                tokens = list(c)
            for tok in tokens:
                letter = name_to_letter.get(tok.lower())
                if letter:
                    add_letter(letter)
                else:
                    for ch in tok:
                        add_letter(ch)
        elif isinstance(c, list):
            for item in c:
                if isinstance(item, str):
                    letter = name_to_letter.get(item.lower())
                    if letter:
                        add_letter(letter)
                    else:
                        if len(item) == 1:
                            add_letter(item)
                        else:
                            for ch in item:
                                add_letter(ch)
                elif isinstance(item, dict):
                    # Common nested forms like { code: 'W' } or { symbol: 'W' }
                    for k in ("code", "symbol", "id"):
                        v = item.get(k)
                        if isinstance(v, str):
                            letter = name_to_letter.get(v.lower())
                            if letter:
                                add_letter(letter)
                            else:
                                for ch in v:
                                    add_letter(ch)
        elif isinstance(c, dict):
            # Expected format: { "W": 12, "U": 0, ... }
            for k, v in c.items():
                try:
                    count = int(v)
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    letter = name_to_letter.get(str(k).lower()) or str(k).upper()
                    add_letter(letter)
    return colors


def order_colors(colors: List[str]) -> List[str]:
    """Order colors per requested sequences for 1..5 colors.

    If a specific 2/3/4 ordering is provided, use that; otherwise fallback to W,U,B,R,G order filtered by presence.
    """
    base_order = ["W", "U", "B", "R", "G"]
    color_set = set(colors)
    if not color_set:
        return []

    # Predefined orders
    order2 = [
        "WU", "UB", "BR", "RG", "GW", "WB", "UR", "BG", "RW", "GU",
    ]
    order3 = [
        "WUB", "UBR", "BRG", "RGW", "GWU", "WBG", "URW", "BGU", "RWB", "GUR",
    ]
    order4 = [
        "WUBR", "UBRG", "BRGW", "RGWU", "GWUB",
    ]

    def pick_from_orders(target_set: set, sequences: List[str]) -> Optional[List[str]]:
        for seq in sequences:
            if set(seq) == target_set:
                return list(seq)
        return None

    if len(color_set) == 1:
        return [next(iter(color_set))]
    if len(color_set) == 2:
        picked = pick_from_orders(color_set, order2)
        if picked:
            return picked
    if len(color_set) == 3:
        picked = pick_from_orders(color_set, order3)
        if picked:
            return picked
    if len(color_set) == 4:
        picked = pick_from_orders(color_set, order4)
        if picked:
            return picked
    if len(color_set) == 5:
        return list("WUBRG")

    # Fallback: base WUBRG order filtered by presence
    return [c for c in base_order if c in color_set]


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def root() -> Response:
        return Response(render_template("index.html"), mimetype="text/html")

    @app.get("/generate")
    def generate() -> Response:
        try:
            all_decks = fetch_all_decks(ARCHIDEKT_API_URL)
        except requests.HTTPError as http_err:
            return Response(f"Upstream HTTP error: {http_err}", status=502, mimetype="text/plain")
        except requests.RequestException as req_err:
            return Response(f"Upstream request error: {req_err}", status=502, mimetype="text/plain")

        if not all_decks:
            return Response("No decks found", status=404, mimetype="text/plain")

        chosen = random.choice(all_decks)
        deck_url = build_deck_url(chosen)
        if not deck_url:
            return Response("Chosen deck missing id", status=500, mimetype="text/plain")

        # Try to derive a title: Archidekt API often includes 'name' or 'title'.
        deck_title = None
        for key in ("name", "title", "deckName"):
            val = chosen.get(key)
            if isinstance(val, str) and val.strip():
                deck_title = val.strip()
                break

        image_url = extract_image_url(chosen)
        colors_ordered = order_colors(extract_colors_raw(chosen))

        return jsonify({
            "url": deck_url,
            "title": deck_title,
            "image": image_url,
            "colors": colors_ordered,
        })

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)


