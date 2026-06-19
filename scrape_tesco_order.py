"""Fetch your last N Tesco orders and save a distinct item list as CSV.

How it works:
  1. Opens a Chromium browser (visible on first run).
  2. Loads data/tesco_session.json if it exists — skips manual login on
     subsequent runs (session persists cookies & local-storage).
  3. Otherwise: navigates to the Tesco login page — YOU log in manually.
  4. Saves the session so the next run is hands-free.
  5. Fetches the last N orders (default 5), extracts all items, deduplicates
     by item name, and tracks how many orders each item appeared in.
  6. Writes two output files:
       data/tesco_distinct_items.csv  — distinct list sorted by frequency
       data/tesco_latest_order.csv   — most-recent order only (unchanged)

Usage:
    python scrape_tesco_order.py [--out data/tesco_distinct_items.csv] [--orders 5]

First-time setup:
    pip install playwright
    playwright install chromium

To force a fresh login (e.g. after account change):
    rm data/tesco_session.json
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# URLs, selectors & paths
# ---------------------------------------------------------------------------

LOGIN_URL    = "https://www.tesco.com/account/login/en-GB"
ORDERS_URL   = "https://www.tesco.com/groceries/en-GB/orders"
SESSION_FILE = Path("data/tesco_session.json")

# Selector priority lists — Tesco changes their HTML; we try each in order.
ORDER_LINK_SELECTORS = [
    "a[href*='/groceries/en-GB/orders/']",
    "[data-testid='order-link']",
    ".order-list--item a",
    ".orders-list a",
]

ITEM_ROW_SELECTORS = [
    "[data-testid='product-item']",
    ".product-details--wrapper",
    ".order-product",
    "li.product-list--item",
    "li[class*='product']",
]

NAME_SELECTORS  = ["[data-testid='product-title']", ".product-details--name", "h3", ".product-name"]
QTY_SELECTORS   = ["[data-testid='product-quantity']", ".quantity", ".qty", "input[name='qty']"]
SIZE_SELECTORS  = ["[data-testid='product-info-weight']", ".product-details--weight",
                   ".weight", ".product-info-message", ".pack-size"]
PRICE_SELECTORS = ["[data-testid='product-price']", ".price", ".product-price",
                   ".value", "[class*='price']"]

OUTPUT_COLUMNS         = ["item_name", "quantity", "size", "price", "frequency"]
LATEST_ORDER_COLUMNS   = ["item_name", "quantity", "size", "price"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_text(el, selectors: list[str]) -> str:
    for sel in selectors:
        try:
            found = el.query_selector(sel)
            if found:
                txt = found.inner_text().strip()
                if txt:
                    return txt
        except Exception:
            continue
    return ""


def _parse_price(raw: str) -> str:
    m = re.search(r"[\d]+\.?[\d]*", raw)
    return m.group() if m else ""


def _parse_qty(raw: str) -> str:
    m = re.search(r"\d+", raw)
    return m.group() if m else "1"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _find_order_urls(page, count: int) -> list[str]:
    """Return URLs of the most recent `count` distinct orders."""
    for sel in ORDER_LINK_SELECTORS:
        try:
            links = page.query_selector_all(sel)
            if not links:
                continue
            urls: list[str] = []
            seen: set[str] = set()
            for link in links:
                href = link.get_attribute("href")
                if not href:
                    continue
                full = href if href.startswith("http") else "https://www.tesco.com" + href
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
                if len(urls) >= count:
                    break
            if urls:
                return urls
        except Exception:
            continue
    return []


def _extract_items(page) -> list[dict]:
    """Extract product rows from an open order-detail page."""
    for sel in ITEM_ROW_SELECTORS:
        try:
            elements = page.query_selector_all(sel)
            if not elements:
                continue
            rows = []
            for el in elements:
                name  = _first_text(el, NAME_SELECTORS)
                qty   = _parse_qty(_first_text(el, QTY_SELECTORS))
                size  = _first_text(el, SIZE_SELECTORS)
                price = _parse_price(_first_text(el, PRICE_SELECTORS))
                if name:
                    rows.append({"item_name": name, "quantity": qty,
                                 "size": size, "price": price})
            if rows:
                return rows
        except Exception:
            continue
    return []


def _deduplicate_items(all_items: list[dict]) -> list[dict]:
    """
    Return a distinct list of items across all orders.
    frequency = number of orders the item appeared in (not total quantity).
    Sorted by frequency descending so most-bought items appear first.
    """
    seen: dict[str, dict] = {}
    for item in all_items:
        key = item["item_name"].lower().strip()
        if key in seen:
            seen[key]["frequency"] += 1
        else:
            seen[key] = {**item, "frequency": 1}
    return sorted(seen.values(), key=lambda x: x["frequency"], reverse=True)


# ---------------------------------------------------------------------------
# Login handling
# ---------------------------------------------------------------------------

def _on_auth_page(page) -> bool:
    """Return True if the browser is currently on a login/auth page."""
    return any(x in page.url for x in ["/login", "/email", "/password"])


def _accept_cookies(page) -> None:
    try:
        page.click("button#onetrust-accept-btn-handler", timeout=5000)
    except Exception:
        pass


def _wait_for_login(page, timeout_s: int = 300) -> None:
    """Block until the user completes manual login in the browser window."""
    print("\nPlease log in to Tesco in the browser window that just opened.")
    print("The script continues automatically once you're logged in.")
    print(f"(Waiting up to {timeout_s // 60} minutes…)\n")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if not _on_auth_page(page):
                print(f"Login detected — continuing.")
                return
        except Exception:
            break
        time.sleep(1)
    print("Timed out waiting for login.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape(out_path: Path, order_count: int = 5) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed.\nRun: pip install playwright && playwright install chromium",
              file=sys.stderr)
        sys.exit(1)

    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    has_session = SESSION_FILE.exists()

    with sync_playwright() as pw:
        # Always visible so the user can handle an expired session.
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        context_kwargs = dict(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
            viewport={"width": 1280, "height": 900},
        )

        if has_session:
            print(f"Loading saved session from {SESSION_FILE} …")
            context = browser.new_context(storage_state=str(SESSION_FILE), **context_kwargs)
        else:
            context = browser.new_context(**context_kwargs)

        page = context.new_page()

        # --- Step 1: ensure we are logged in ---
        if has_session:
            page.goto(ORDERS_URL, wait_until="domcontentloaded")
            time.sleep(2)
            if _on_auth_page(page):
                print("Saved session has expired — please log in again.")
                page.goto(LOGIN_URL, wait_until="domcontentloaded")
                _accept_cookies(page)
                _wait_for_login(page)
        else:
            print(f"Opening {LOGIN_URL} …")
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            _accept_cookies(page)
            _wait_for_login(page)

        # Persist / refresh the session after successful login
        context.storage_state(path=str(SESSION_FILE))
        print(f"Session saved → {SESSION_FILE}")

        # --- Step 2: navigate to order history ---
        if ORDERS_URL not in page.url:
            print("Navigating to order history…")
            page.goto(ORDERS_URL, wait_until="domcontentloaded")
            time.sleep(2)

        order_urls = _find_order_urls(page, order_count)
        if not order_urls:
            debug_path = Path("data/debug_orders_page.html")
            debug_path.write_text(page.content(), encoding="utf-8")
            print(
                f"Could not find any order links on {ORDERS_URL}.\n"
                f"Saved page HTML to {debug_path} for selector debugging.",
                file=sys.stderr,
            )
            browser.close()
            sys.exit(1)

        print(f"Found {len(order_urls)} order(s) to scrape (requested {order_count}).")

        # --- Step 3: scrape each order ---
        all_items: list[dict] = []
        latest_items: list[dict] = []

        for idx, order_url in enumerate(order_urls, 1):
            print(f"  [{idx}/{len(order_urls)}] {order_url}")
            page.goto(order_url, wait_until="domcontentloaded")
            time.sleep(2)
            items = _extract_items(page)
            print(f"       → {len(items)} item(s)")
            if idx == 1:
                latest_items = items
            all_items.extend(items)

        browser.close()

    if not all_items:
        print(
            "Could not parse any items from the scraped orders.\n"
            f"Try deleting {SESSION_FILE} and re-running to refresh your login.",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- Step 4: deduplicate and write outputs ---
    distinct = _deduplicate_items(all_items)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(distinct)

    print(f"\nDistinct items across {len(order_urls)} order(s): {len(distinct)}")
    print(f"Saved → {out_path}")

    # Keep the latest-order-only file for backward compatibility
    if latest_items:
        latest_path = out_path.parent / "tesco_latest_order.csv"
        with open(latest_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LATEST_ORDER_COLUMNS)
            writer.writeheader()
            writer.writerows(latest_items)
        print(f"Latest order only  → {latest_path}")

    print(f"\nNext step:  python parse_tesco.py {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch your last N Tesco orders and produce a distinct item list."
    )
    parser.add_argument(
        "--out", default="data/tesco_distinct_items.csv",
        help="Output CSV path (default: data/tesco_distinct_items.csv)",
    )
    parser.add_argument(
        "--orders", type=int, default=5,
        help="Number of recent orders to scrape (default: 5)",
    )
    args = parser.parse_args()
    scrape(Path(args.out), args.orders)


if __name__ == "__main__":
    main()
