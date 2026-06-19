"""
Step 2 — For each parsed Tesco item, find candidate M&S equivalents.

Strategy (in order of preference):
  A) Scrape M&S public search results (respects robots.txt, 2 s delay).
  B) Fall back to generating a search URL if scraping is blocked/fails.

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
        "name": "M&S Semi-Skimmed Milk",
        "size": "2L",
        "price": 1.60,
        "url": "https://www.marksandspencer.com/...",
        "score": 88.5,
        "source": "scraped"   // or "fallback_url"
      },
      ...
    ]
  }
"""

import argparse
import json
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

MANDS_SEARCH_URL = "https://www.marksandspencer.com/webapp/wcs/stores/servlet/MSAjaxSearchResultsView"
MANDS_SEARCH_PAGE = "https://www.marksandspencer.com/c/food-and-wine?q={query}&inView=FOOD"
REQUEST_DELAY = 2.0          # seconds between HTTP requests
REQUEST_TIMEOUT = 10         # seconds
TOP_N_DEFAULT = 3
USER_AGENT = (
    "Mozilla/5.0 (compatible; personal-grocery-helper/1.0; "
    "single-user local tool; not a bot)"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-GB,en;q=0.9",
}

# ---------------------------------------------------------------------------
# Robots.txt check
# ---------------------------------------------------------------------------

_robots_cache: dict[str, bool] = {}


def _robots_allows(base_url: str, path: str) -> bool:
    """Very lightweight robots.txt check for the paths we use."""
    cache_key = base_url
    if cache_key not in _robots_cache:
        try:
            from urllib.robotparser import RobotFileParser
            rp = RobotFileParser()
            rp.set_url(f"{base_url}/robots.txt")
            rp.read()
            _robots_cache[cache_key] = rp
        except Exception:
            _robots_cache[cache_key] = None
    rp = _robots_cache[cache_key]
    if rp is None:
        return True  # can't check → assume allowed
    return rp.can_fetch(USER_AGENT, f"{base_url}{path}")


# ---------------------------------------------------------------------------
# M&S search scraping
# ---------------------------------------------------------------------------

def _build_search_url(query: str) -> str:
    return MANDS_SEARCH_PAGE.format(query=urllib.parse.quote_plus(query))


def _scrape_mands_results(query: str, session: requests.Session) -> list[dict]:
    """
    Attempt to scrape M&S search results for *query*.
    Returns list of dicts: {name, size, price, url}
    Returns empty list if blocked or parsing fails (caller uses fallback).
    """
    url = _build_search_url(query)
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
    except requests.RequestException as exc:
        print(f"  [warn] HTTP error for '{query}': {exc}", file=sys.stderr)
        return []

    if resp.status_code != 200:
        print(f"  [warn] M&S returned HTTP {resp.status_code} for '{query}'", file=sys.stderr)
        return []

    soup = BeautifulSoup(resp.text, "lxml")
    products = []

    # M&S product cards — selectors may need updating if their HTML changes.
    # We look for common patterns and gracefully return [] if nothing found.
    for card in soup.select("li.product-item, div[data-testid='product-card'], article.product-tile"):
        try:
            name_el  = card.select_one("[data-testid='product-title'], .product-name, h3")
            price_el = card.select_one("[data-testid='product-price'], .price, .product-price")
            size_el  = card.select_one("[data-testid='product-description'], .product-size, .pack-size")
            link_el  = card.select_one("a[href]")

            name  = name_el.get_text(strip=True)  if name_el  else ""
            price_text = price_el.get_text(strip=True) if price_el else ""
            size  = size_el.get_text(strip=True)  if size_el  else ""
            href  = link_el["href"]               if link_el  else ""

            # Resolve relative URLs
            if href and not href.startswith("http"):
                href = "https://www.marksandspencer.com" + href

            # Parse price to float
            import re
            price_match = re.search(r"[\d]+\.?[\d]*", price_text)
            price = float(price_match.group()) if price_match else None

            if name:
                products.append({"name": name, "size": size, "price": price, "url": href})
        except Exception:
            continue

    return products


# ---------------------------------------------------------------------------
# Fuzzy scoring
# ---------------------------------------------------------------------------

def _score_candidate(tesco_name: str, tesco_size: str, candidate: dict) -> float:
    """
    Combined score (0–100) weighting name similarity more than size.
    Uses token_sort_ratio to handle word-order differences.
    """
    name_score = fuzz.token_sort_ratio(tesco_name.lower(), candidate["name"].lower())
    size_score = fuzz.token_sort_ratio(tesco_size.lower(), candidate.get("size", "").lower())
    return round(name_score * 0.75 + size_score * 0.25, 1)


def _fallback_candidate(query: str, tesco_name: str, tesco_size: str) -> dict:
    """Return a 'fallback' pseudo-candidate with just a search URL."""
    url = _build_search_url(query)
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
    query = f"{item['item_name']} {item['size']}".strip()
    print(f"  Searching: {query!r}")

    base = "https://www.marksandspencer.com"
    search_path = _build_search_url(query).replace(base, "")

    if not _robots_allows(base, search_path):
        print(f"  [robots.txt] Scraping disallowed for this path — using fallback URL.")
        return {
            **_item_meta(item),
            "candidates": [_fallback_candidate(query, item["item_name"], item["size"])],
        }

    raw = _scrape_mands_results(query, session)
    time.sleep(REQUEST_DELAY)

    if raw:
        scored = [
            {**c, "score": _score_candidate(item["item_name"], item["size"], c), "source": "scraped"}
            for c in raw
        ]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return {**_item_meta(item), "candidates": scored[:top_n]}

    # Scraping returned nothing — one fallback URL
    print(f"  [warn] No results scraped for '{query}' — generating search URL.")
    return {
        **_item_meta(item),
        "candidates": [_fallback_candidate(query, item["item_name"], item["size"])],
    }


def _item_meta(item: dict) -> dict:
    return {
        "id":           item["id"],
        "tesco_item":   item["item_name"],
        "tesco_size":   item["size"],
        "tesco_qty":    item["quantity"],
        "tesco_price":  item["price"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Match Tesco items to M&S equivalents.")
    parser.add_argument("input", help="Path to parsed_order.json from parse_tesco.py")
    parser.add_argument("--out", default=None, help="Output path (JSON). Default: matches.json")
    parser.add_argument("--top", type=int, default=TOP_N_DEFAULT, help="Top N candidates per item")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Error: {in_path} not found.", file=sys.stderr)
        sys.exit(1)

    items = json.loads(in_path.read_text())
    out_path = Path(args.out) if args.out else Path("data/matches.json")

    session = requests.Session()
    session.headers.update(HEADERS)

    results = []
    for i, item in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {item['item_name']}")
        results.append(match_item(item, session, args.top))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nMatches saved → {out_path}")


if __name__ == "__main__":
    main()
