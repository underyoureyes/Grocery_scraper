"""
Fetch your most recent Tesco order and save it as a CSV ready for parse_tesco.py.

How it works:
  1. Opens a VISIBLE browser window (Chromium).
  2. Navigates to the Tesco login page — YOU log in and handle any 2FA.
  3. Once you're logged in the script detects that and continues automatically.
  4. It navigates to your order history, picks the most recent order,
     and extracts item name / quantity / size / price.
  5. Saves to data/tesco_latest_order.csv (overrides each run).

Usage:
    python scrape_tesco_order.py [--out data/tesco_latest_order.csv]

First-time setup:
    pip install playwright
    playwright install chromium
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# URLs & selectors
# ---------------------------------------------------------------------------

LOGIN_URL  = "https://www.tesco.com/account/login/en-GB"
ORDERS_URL = "https://www.tesco.com/groceries/en-GB/orders"

# Selector patterns — Tesco changes their HTML; we try a prioritised list.
ORDER_LINK_SELECTORS = [
    "a[href*='/groceries/en-GB/orders/']",   # any link into an order detail page
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

NAME_SELECTORS    = ["[data-testid='product-title']", ".product-details--name", "h3", ".product-name"]
QTY_SELECTORS     = ["[data-testid='product-quantity']", ".quantity", ".qty", "input[name='qty']"]
SIZE_SELECTORS    = ["[data-testid='product-info-weight']", ".product-details--weight",
                     ".weight", ".product-info-message", ".pack-size"]
PRICE_SELECTORS   = ["[data-testid='product-price']", ".price", ".product-price",
                     ".value", "[class*='price']"]

OUTPUT_COLUMNS = ["item_name", "quantity", "size", "price"]

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
# Core extraction
# ---------------------------------------------------------------------------

def _find_order_url(page) -> str | None:
    """Return the URL of the most recent order on the orders list page."""
    for sel in ORDER_LINK_SELECTORS:
        try:
            links = page.query_selector_all(sel)
            if links:
                href = links[0].get_attribute("href")
                if href:
                    return href if href.startswith("http") else "https://www.tesco.com" + href
        except Exception:
            continue
    return None


def _extract_items(page) -> list[dict]:
    """Extract product rows from an open order-detail page."""
    rows = []
    for sel in ITEM_ROW_SELECTORS:
        try:
            items = page.query_selector_all(sel)
            if items:
                for item in items:
                    name  = _first_text(item, NAME_SELECTORS)
                    qty   = _parse_qty(_first_text(item, QTY_SELECTORS))
                    size  = _first_text(item, SIZE_SELECTORS)
                    price = _parse_price(_first_text(item, PRICE_SELECTORS))
                    if name:
                        rows.append({"item_name": name, "quantity": qty,
                                     "size": size, "price": price})
                if rows:
                    return rows
        except Exception:
            continue
    return rows


def _wait_for_login(page, timeout_s: int = 300) -> None:
    """
    Block until the user has completed login.
    We detect success by watching for the URL to leave the login/email pages.
    """
    print("\nPlease log in to Tesco in the browser window that just opened.")
    print("The script will continue automatically once you're logged in.")
    print(f"(Waiting up to {timeout_s // 60} minutes…)\n")

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            current = page.url
        except Exception:
            break
        if not any(x in current for x in ["/login", "/email", "/password"]):
            print(f"Login detected (now at {current})")
            return
        time.sleep(1)
    print("Timed out waiting for login.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scrape(out_path: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed.\nRun: pip install playwright && playwright install chromium",
              file=sys.stderr)
        sys.exit(1)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-GB",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        # --- Step 1: open login page ---
        print(f"Opening {LOGIN_URL} …")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        # Accept cookies banner if present
        try:
            page.click("button#onetrust-accept-btn-handler", timeout=5000)
        except Exception:
            pass

        # --- Step 2: wait for you to log in ---
        _wait_for_login(page)

        # --- Step 3: navigate to order history ---
        print(f"Navigating to order history…")
        page.goto(ORDERS_URL, wait_until="domcontentloaded")
        time.sleep(2)  # let dynamic content settle

        order_url = _find_order_url(page)
        if not order_url:
            # Dump page source for debugging
            debug_path = Path("data/debug_orders_page.html")
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(page.content(), encoding="utf-8")
            print(
                f"Could not find any order links on {ORDERS_URL}.\n"
                f"Saved page HTML to {debug_path} so you can inspect the selectors.\n"
                "Please open a GitHub issue or share that file to get the selectors updated.",
                file=sys.stderr,
            )
            browser.close()
            sys.exit(1)

        # --- Step 4: open the most recent order ---
        print(f"Opening most recent order: {order_url}")
        page.goto(order_url, wait_until="domcontentloaded")
        time.sleep(2)

        items = _extract_items(page)

        if not items:
            debug_path = Path("data/debug_order_detail_page.html")
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(page.content(), encoding="utf-8")
            print(
                f"Found the order page but could not parse any items.\n"
                f"Saved page HTML to {debug_path} for selector debugging.",
                file=sys.stderr,
            )
            browser.close()
            sys.exit(1)

        browser.close()

    # --- Step 5: write CSV ---
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(items)

    print(f"\nSaved {len(items)} items → {out_path}")
    print("Next step:  python parse_tesco.py", out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch your latest Tesco order via visible browser.")
    parser.add_argument("--out", default="data/tesco_latest_order.csv",
                        help="Output CSV path (default: data/tesco_latest_order.csv)")
    args = parser.parse_args()
    scrape(Path(args.out))


if __name__ == "__main__":
    main()
