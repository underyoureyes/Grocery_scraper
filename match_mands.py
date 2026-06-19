"""
Step 2 — For each parsed Tesco item, find candidate M&S equivalents.

Strategy:
  A) Query Trolley.co.uk's search AJAX endpoint — it returns product cards
     including M&S items with prices, without bot-blocking.
  B) Fall back to an M&S search URL if no Trolley results are found.

Usage:
    python match_mands.py <parsed_order.json> [--out matches.json] [--top 3]

Outputs a JSON file with this shape per item:
  {
    "id": 1,
    "tesco_item": "Whole Milk",
    "tesco_size": "2 litre",
    "tesco_qty": 2,
    "tesco_price": 1.45,
    "candidates": [
      {
        "name": "M&S Whole Milk 2L",
        "size": "",
        "price": 1.60,
        "url": "https://www.trolley.co.uk/product/m-s-whole-milk/HSG123",
        "score": 88.5,
        "source": "trolley"
      },
      ...
    ]
  }
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TROLLEY_SEARCH_URL = "https://www.trolley.co.uk/search/?q={query}&ajax=products"
MANDS_SEARCH_PAGE  = "https://www.marksandspencer.com/c/food-and-wine?q={query}&inView=FOOD"
REQUEST_DELAY   = 2.0
REQUEST_TIMEOUT = 10
TOP_N_DEFAULT   = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TROLLEY_HEADERS = {
    **HEADERS,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# ---------------------------------------------------------------------------
# Search term cleaning
# ---------------------------------------------------------------------------

_STRIP_PREFIXES = re.compile(
    r"^(tesco finest|tesco everyday value|tesco free from|tesco organic|tesco plant chef|tesco)\s+",
    re.IGNORECASE,
)

_STRIP_SIZES = re.compile(
    r"\s+\d+\s*x\s*\d+\s*(?:g|ml|l|kg)?|\s+\d+(?:\.\d+)?\s*(?:g|ml|l|kg|litre|liter|pint|pack|x)\b.*$",
    re.IGNORECASE,
)


def _clean_search_term(item_name: str) -> str:
    """Strip own-brand prefixes and size info to make a better search query."""
    name = item_name.strip()
    for _ in range(3):
        cleaned = _STRIP_PREFIXES.sub("", name).strip()
        if cleaned == name:
            break
        name = cleaned
    name = _STRIP_SIZES.sub("", name).strip()
    return name or item_name


def _build_mands_search_url(query: str) -> str:
    return MANDS_SEARCH_PAGE.format(query=urllib.parse.quote_plus(query))


# ---------------------------------------------------------------------------
# Trolley.co.uk scraping for M&S products
# ---------------------------------------------------------------------------

def _search_trolley_for_mands(query: str, session: requests.Session) -> list[dict]:
    """
    Query Trolley.co.uk AJAX search for M&S products matching *query*.
    Returns list of dicts: {name, size, price, url}
    """
    url = TROLLEY_SEARCH_URL.format(query=urllib.parse.quote_plus(query))
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, headers=TROLLEY_HEADERS)
    except requests.RequestException as exc:
        print(f"  [warn] Trolley HTTP error for '{query}': {exc}", file=sys.stderr)
        return []

    if resp.status_code != 200:
        print(f"  [warn] Trolley returned HTTP {resp.status_code} for '{query}'", file=sys.stderr)
        return []

    try:
        data = resp.json()
        html = data.get("results", "")
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"  [warn] Trolley JSON parse error for '{query}': {exc}", file=sys.stderr)
        return []

    if not html:
        return []

    soup = BeautifulSoup(html, "lxml")
    products = []

    for card in soup.select(".product-item"):
        # Find a link pointing to an M&S product (URL contains /m-s-)
        mands_link = None
        for a in card.select("a[href]"):
            if "/m-s-" in a.get("href", ""):
                mands_link = a
                break
        if not mands_link:
            continue

        href = mands_link.get("href", "")
        trolley_url = ("https://www.trolley.co.uk" + href) if href.startswith("/") else href

        # Name: prefer title attribute (full name), fall back to link text
        name = ""
        title_link = card.select_one("a[title]")
        if title_link:
            name = title_link.get("title", "").strip()
        if not name:
            name = mands_link.get_text(strip=True)
        if not name:
            continue

        # Price: look for a price element in the card
        price = None
        price_el = card.select_one(
            ".product-item__price, .price, [class*='price'], [class*='Price']"
        )
        if price_el:
            price_match = re.search(r"\d+\.\d{2}", price_el.get_text(strip=True))
            if price_match:
                price = float(price_match.group())

        products.append({"name": name, "size": "", "price": price, "url": trolley_url})

    return products


# ---------------------------------------------------------------------------
# Fuzzy scoring
# ---------------------------------------------------------------------------

def _score_candidate(tesco_name: str, tesco_size: str, candidate: dict) -> float:
    """
    Combined score (0-100) weighting name similarity more than size.
    Uses token_sort_ratio to handle word-order differences.
    """
    name_score = fuzz.token_sort_ratio(tesco_name.lower(), candidate["name"].lower())
    size_score = fuzz.token_sort_ratio(tesco_size.lower(), candidate.get("size", "").lower())
    return round(name_score * 0.75 + size_score * 0.25, 1)


def _fallback_candidate(query: str, tesco_name: str, tesco_size: str) -> dict:
    """Return a fallback pseudo-candidate with just an M&S search URL."""
    url = _build_mands_search_url(query)
    return {
        "name": f"[Search M&S for '{tesco_name}']",
        "size": tesco_size,
        "price": None,
        "url": url,
        "score": 0.0,
        "source": "fallback_url",
    }


# ---------------------------------------------------------------------------
# Per-item matching
# ---------------------------------------------------------------------------

def match_item(item: dict, session: requests.Session, top_n: int) -> dict:
    clean_name = _clean_search_term(item["item_name"])
    print(f"  {item['item_name']!r} -> {clean_name!r}")

    raw = _search_trolley_for_mands(clean_name, session)
    time.sleep(REQUEST_DELAY)

    if raw:
        scored = [
            {**c, "score": _score_candidate(item["item_name"], item["size"], c), "source": "trolley"}
            for c in raw
        ]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return {**_item_meta(item), "candidates": scored[:top_n]}

    print(f"  [warn] No Trolley results for '{clean_name}' — generating search URL.")
    return {
        **_item_meta(item),
        "candidates": [_fallback_candidate(clean_name, item["item_name"], item["size"])],
    }


def _item_meta(item: dict) -> dict:
    return {
        "id":          item["id"],
        "tesco_item":  item["item_name"],
        "tesco_size":  item["size"],
        "tesco_qty":   item["quantity"],
        "tesco_price": item["price"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Match Tesco items to M&S equivalents via Trolley.co.uk.")
    parser.add_argument("input", help="Path to parsed_order.json from parse_tesco.py")
    parser.add_argument("--out", default=None, help="Output path (JSON). Default: data/matches.json")
    parser.add_argument("--top", type=int, default=TOP_N_DEFAULT, help="Top N candidates per item")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Error: {in_path} not found.", file=sys.stderr)
        sys.exit(1)

    items = json.loads(in_path.read_text(encoding="utf-8"))
    out_path = Path(args.out) if args.out else Path("data/matches.json")

    session = requests.Session()
    session.headers.update(HEADERS)

    results = []
    matched = 0
    for i, item in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {item['item_name']}")
        result = match_item(item, session, args.top)
        results.append(result)
        if result["candidates"] and result["candidates"][0]["source"] == "trolley":
            matched += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nMatches saved -> {out_path}")
    print(f"M&S matched: {matched}/{len(items)} ({100*matched//len(items)}%)")
    print(f"\nNext step: python generate_review.py --matches {out_path} --html")


if __name__ == "__main__":
    main()
