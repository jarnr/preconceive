import os
import random
from typing import List, Dict, Optional, Set

import requests
from flask import Flask, Response, jsonify, render_template, request
from time import time
from collections import defaultdict, deque

import logging

logger = logging.getLogger(__name__)


ARCHIDEKT_USER_URL = "https://archidekt.com/api/decks/v3/?ownerUsername={username}"
ARCHIDEKT_DECK_URL = "https://archidekt.com/decks/{id}"
ALLOWED_USERS = ["Archidekt_Precons", "jarnr", "pertrick", "Bowden1337", "jden007", "tolariancommunitycollege"]


def fetch_all_decks(start_url: str) -> List[Dict]:
    """Fetch all decks from Archidekt, following pagination.

    The API is expected to return JSON with either a 'results' or 'decks' list
    and a 'next' URL for pagination.
    """
    decks: List[Dict] = []
    url: Optional[str] = start_url
    headers = {
        "User-Agent": "preconceive/1.0 (https://github.com/jarnr/preconceive)",
        "Accept": "application/json",
    }

    i = 0
    while url:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        page_decks = data.get("results", [])

        if page_decks:
            decks.extend(page_decks)

        url = data.get("next", None)
        i += 1
        if i > 10:
            logger.warning(f"Too many pages: {i}")
            logger.warning(f"URL: {url}")
            break

    return [deck for deck in decks if deck['size'] == 100]


def build_deck_url(deck: Dict) -> Optional[str]:
    deck_id = deck.get("id")
    if deck_id is None:
        return None
    return ARCHIDEKT_DECK_URL.format(id=deck_id)


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
    logger.warning(f"Fallback: {color_set}")
    return [c for c in base_order if c in color_set]


def create_app() -> Flask:
    app = Flask(__name__)

    # Simple in-memory cache and rate limiters
    cache_by_user: Dict[str, Dict[str, object]] = {}
    CACHE_TTL_SECONDS = 300  # 5 minutes

    requests_by_ip: Dict[str, deque] = defaultdict(deque)
    RATE_LIMIT_WINDOW = 60  # seconds
    RATE_LIMIT_MAX = 30     # requests per window

    # Compute inline script hashes from index.html
    def _compute_inline_script_hashes() -> List[str]:
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            template_path = os.path.join(here, "templates", "index.html")
            with open(template_path, "rb") as f:
                content = f.read()
            # naive extraction of inline <script>...</script> blocks
            import re, hashlib, base64
            text = content.decode("utf-8", errors="ignore")
            scripts = re.findall(r"<script>([\s\S]*?)</script>", text, re.IGNORECASE)
            hashes: List[str] = []
            for s in scripts:
                # CSP hash over the exact script bytes
                digest = hashlib.sha256(s.encode("utf-8")).digest()
                b64 = base64.b64encode(digest).decode("ascii")
                hashes.append(f"'sha256-{b64}'")
            return hashes
        except Exception:
            logger.exception("Failed computing inline script hashes")
            return []

    INLINE_SCRIPT_HASHES = _compute_inline_script_hashes()

    @app.get("/")
    def root() -> Response:
        return Response(render_template("index.html"), mimetype="text/html")

    @app.get("/pick")
    def pick() -> Response:
        # Rate limiting per IP
        ip = request.headers.get('X-Forwarded-For', request.remote_addr) or 'unknown'
        now = time()
        q = requests_by_ip[ip]
        while q and (now - q[0]) > RATE_LIMIT_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT_MAX:
            return Response("Too many requests", status=429, mimetype="text/plain")
        q.append(now)

        username = request.args.get("username", "Archidekt_Precons").strip()
        if not username in ALLOWED_USERS:
            return Response("username is invalid", status=400, mimetype="text/plain")
        api_url = ARCHIDEKT_USER_URL.format(username=username)

        filter_type = request.args.get("filter_type", "subset").lower()
        if filter_type not in {"exact", "subset"}:
            return Response("filter_type is invalid", status=400, mimetype="text/plain")

        colors = set(request.args.get("colors", "WUBRG").upper())
        for c in colors:
            if c not in {"W","U","B","R","G"}:
                return Response("colors is invalid", status=400, mimetype="text/plain")
            
        try:
            # Cache decks by username
            cached = cache_by_user.get(username)
            if cached and (now - cached.get("ts", 0)) < CACHE_TTL_SECONDS:
                all_decks = cached["decks"]  # type: ignore[index]
            else:
                all_decks = fetch_all_decks(api_url)
                cache_by_user[username] = {"decks": all_decks, "ts": now}
        except requests.HTTPError as http_err:
            logger.exception("Upstream HTTP error")
            return Response("Upstream error", status=502, mimetype="text/plain")
        except requests.RequestException:
            logger.exception("Upstream request error")
            return Response("Upstream error", status=502, mimetype="text/plain")

        if not all_decks:
            return Response("No decks found", status=404, mimetype="text/plain")

        # filter by allowed colors: remove decks containing any unselected color
        def deck_colors(deck: Dict) -> List[str]:
            return order_colors(extract_colors_raw(deck))

        if filter_type == "exact":
            filtered = [d for d in all_decks if set(deck_colors(d)) == set(colors)]
        else:
            filtered = [d for d in all_decks if set(deck_colors(d)).issubset(colors)]
        decks_pool = filtered if filtered else all_decks

        chosen = random.choice(decks_pool)
        deck_url = build_deck_url(chosen)
        if not deck_url:
            return Response("Chosen deck missing id", status=500, mimetype="text/plain")

        deck_title = chosen.get("name", "").replace(" - jarcon", "").strip()
        if not deck_title:
            deck_title = "Deck Name Not Found"
        
        image_url = chosen.get("featured", None)
        colors_ordered = deck_colors(chosen)

        return jsonify({
            "url": deck_url,
            "title": deck_title,
            "image": image_url,
            "colors": colors_ordered,
        })

    @app.after_request
    def set_security_headers(resp: Response) -> Response:
        # Basic security headers and a restrictive CSP suitable for this app
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        # Allow self, images from https and data URIs (for featured images and favicon)
        script_src = ["'self'"] + INLINE_SCRIPT_HASHES
        csp = (
            "default-src 'self'; "
            f"script-src {' '.join(script_src)}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' https: data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "frame-ancestors 'none'"
        )
        resp.headers.setdefault("Content-Security-Policy", csp)
        return resp

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
