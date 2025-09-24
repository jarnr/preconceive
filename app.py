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
    """Extract the featured image URL from the deck object.

    `deck["featured"]` is expected to be a non-empty string URL.
    """
    value = deck.get("featured")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def extract_colors_raw(deck: Dict) -> List[str]:
    """Extract raw color letters; optimized for deck['colors'] dict shape.

    Returns a list of unique uppercase letters among W, U, B, R, G.
    """
    colors_dict = deck.get("colors")
    if isinstance(colors_dict, dict):
        ordered_keys = ["W", "U", "B", "R", "G"]
        result = []
        for key in ordered_keys:
            try:
                count = int(colors_dict.get(key, 0))
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                result.append(key)
        if result:
            return result
    return []

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


