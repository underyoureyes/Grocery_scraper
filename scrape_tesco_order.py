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
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# URLs, selectors & paths
# ---------------------------------------------------------------------------

LOGIN_URL    = "https://www.tesco.com/account/login/en-GB"
ORDERS_URL   = "https://www.tesco.com/shop/en-GB/orders/recent"
SESSION_FILE = Path("data/tesco_session.json")

# Selector priority lists — Tesco changes their HTML; we try each in order.
ORDER_LINK_SELECTORS = [
    "a[href*='/shop/en-GB/orders/']",
    "a[href*='/groceries/en-GB/orders/']",
    "[data-testid='order-link']",
    ".order-list--item a",
    ".orders-list a",
]

ORDER_EXCLUDE_PATHS = {"/shop/en-GB/orders/upcoming", "/shop/en-GB/orders/recent", "/shop/en-GB/orders/returns"}

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
                if any(href.endswith(p) for p in ORDER_EXCLUDE_PATHS):
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
    """Extract product rows from an open order-detail page (receipt or amend view)."""
    rows = _extract_receipt_items(page)
    if not rows:
        rows = _extract_amend_items(page)
    return rows


def _extract_receipt_items(page) -> list[dict]:
    """Receipt page: uses data-testid='product-title'."""
    rows = []
    try:
        titles = page.query_selector_all("[data-testid='product-title']")
        for title_el in titles:
            name_el = title_el.query_selector("a")
            name = name_el.inner_text().strip() if name_el else title_el.inner_text().strip()
            if not name:
                continue

            wrapper = title_el.evaluate_handle("el => el.closest('.flexWrapper') || el.parentElement.parentElement")

            price = ""
            try:
                price_el = wrapper.query_selector("[data-testid='receipt-total-price'], [data-testid='receipt-item-price']")
                if price_el:
                    price = _parse_price(price_el.inner_text())
            except Exception:
                pass

            qty = "1"
            try:
                qty_el = wrapper.query_selector("[data-testid='product-quantity']")
                if qty_el:
                    qty = _parse_qty(qty_el.inner_text())
                else:
                    # Fallback: look for "Quantity : N" text in wrapper
                    full_text = wrapper.inner_text()
                    m = re.search(r"Quantity\s*:?\s*(\d+)", full_text, re.IGNORECASE)
                    if m:
                        qty = m.group(1)
            except Exception:
                pass

            rows.append({"item_name": name, "quantity": qty, "size": "", "price": price})
    except Exception as e:
        print(f"Warning: receipt extraction error: {e}", file=sys.stderr)
    return rows


def _extract_amend_items(page) -> list[dict]:
    """Amend/upcoming order page: uses data-testid^='imageContainer_'."""
    rows = []
    try:
        anchors = page.query_selector_all("a[data-testid^='imageContainer_']")
        for anchor in anchors:
            li = anchor.evaluate_handle("el => el.closest('li')")
            if not li:
                continue

            name_el = li.query_selector("h3 a")
            name = name_el.inner_text().strip() if name_el else ""
            if not name:
                continue

            qty = "1"
            try:
                for p in li.query_selector_all("p"):
                    txt = p.inner_text().strip()
                    m = re.match(r"^(\d+)\s+items?$", txt, re.IGNORECASE)
                    if m:
                        qty = m.group(1)
                        break
            except Exception:
                pass

            price = ""
            try:
                for p in li.query_selector_all("p"):
                    txt = p.inner_text().strip()
                    if re.search(r"\d+\.\d{2}", txt) and len(txt) < 20:
                        price = _parse_price(txt)
                        break
            except Exception:
                pass

            rows.append({"item_name": name, "quantity": qty, "size": "", "price": price})
    except Exception as e:
        print(f"Warning: amend extraction error: {e}", file=sys.stderr)
    return rows


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


def _auto_login(page) -> bool:
    """Try to fill in credentials from .env. Returns True if attempted."""
    email = os.getenv("TESCO_EMAIL", "").strip()
    password = os.getenv("TESCO_PASSWORD", "").strip()
    if not email or not password:
        return False
    try:
        page.fill("input[name='email']", email, timeout=5000)
        page.fill("input[name='password']", password, timeout=5000)
        page.click("button[type='submit']", timeout=5000)
        print("Credentials submitted automatically.")
        return True
    except Exception as e:
        print(f"Auto-login failed ({e}), please log in manually.")
        return False


def _wait_for_login(page, timeout_s: int = 300) -> None:
    """Auto-fill credentials if available, then wait for login to complete."""
    _auto_login(page)
    print("\nPlease log in to Tesco in the browser window that just opened.")
    print("The script continues automatically once you're logged in.")
    print(f"(Waiting up to {timeout_s // 60} minutes…)\n")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if not _on_auth_page(page):
                print("Login detected — continuing.")
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

    CHROME_PROFILE = Path("data/chrome_profile")

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(CHROME_PROFILE),
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            locale="en-GB",
            viewport={"width": 1280, "height": 900},
        )
        from playwright_stealth import Stealth
        page = context.new_page()
        Stealth().apply_stealth_sync(page)

        # --- Step 1: ensure we are logged in ---
        page.goto(ORDERS_URL, wait_until="domcontentloaded")
        time.sleep(2)
        if _on_auth_page(page):
            print("Not logged in — please log in to Tesco in the browser window.")
            _accept_cookies(page)
            _wait_for_login(page)

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
            context.close()
            sys.exit(1)

        print(f"Found {len(order_urls)} order(s) to scrape (requested {order_count}).")

        # --- Step 3: scrape each order ---
        all_items: list[dict] = []
        latest_items: list[dict] = []

        for idx, order_url in enumerate(order_urls, 1):
            print(f"  [{idx}/{len(order_urls)}] {order_url}")
            page.goto(order_url, wait_until="domcontentloaded")
            time.sleep(3)
            if idx == 1:
                Path("data/debug_order_detail.html").write_text(page.content(), encoding="utf-8")
            items = _extract_items(page)
            print(f"       -> {len(items)} item(s)")
            if idx == 1:
                latest_items = items
            all_items.extend(items)

        context.close()

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
    print(f"Saved -> {out_path}")

    # Keep the latest-order-only file for backward compatibility
    if latest_items:
        latest_path = out_path.parent / "tesco_latest_order.csv"
        with open(latest_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LATEST_ORDER_COLUMNS)
            writer.writeheader()
            writer.writerows(latest_items)
        print(f"Latest order only  -> {latest_path}")

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
