"""
Step 4 — Output a clean M&S shopping list from your confirmed selections.

Primary source: data/confirmed.json  (exported from the review HTML page)
Fallback:       data/review.csv      (edit the 'confirmed' column to 'yes')

Usage:
    python generate_final_list.py [--confirmed data/confirmed.json] [--out data/final_list.csv]
"""

import argparse
import csv
import json
import sys
from pathlib import Path


def load_from_json(confirmed_path: Path) -> tuple[list[dict], list]:
    """Load confirmed items from the JSON exported by the HTML review page."""
    rows = json.loads(confirmed_path.read_text(encoding="utf-8"))
    # Deduplicate by item id (keep first if user somehow ticked two candidates)
    seen: set[int] = set()
    confirmed = []
    for row in rows:
        item_id = int(row["id"])
        if item_id not in seen:
            confirmed.append(row)
            seen.add(item_id)
        else:
            print(
                f"[warn] Item {item_id} ({row['tesco_item']!r}) confirmed twice — keeping first.",
                file=sys.stderr,
            )
    return confirmed, []


def load_from_csv(review_path: Path) -> tuple[list[dict], list]:
    """Load confirmed items from an edited review CSV (confirmed column = 'yes')."""
    confirmed = []
    seen_ids: set[int] = set()
    all_ids: set[int] = set()

    with open(review_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            item_id = int(row["id"])
            all_ids.add(item_id)
            if row.get("confirmed", "").strip().lower() == "yes":
                if item_id not in seen_ids:
                    confirmed.append(row)
                    seen_ids.add(item_id)
                else:
                    print(
                        f"[warn] Item {item_id} ({row['tesco_item']!r}) has multiple "
                        f"'yes' rows — using first.",
                        file=sys.stderr,
                    )

    unmatched = sorted(all_ids - seen_ids)
    if unmatched:
        print(f"[warn] {len(unmatched)} item(s) have no confirmed match.", file=sys.stderr)
    return confirmed, unmatched


def load_confirmed(confirmed_arg: str | None, review_arg: str | None) -> tuple[list[dict], list]:
    """Pick the right source: explicit arg > data/confirmed.json > Downloads > review.csv."""
    if confirmed_arg:
        p = Path(confirmed_arg)
        if not p.exists():
            print(f"Error: {p} not found.", file=sys.stderr)
            sys.exit(1)
        print(f"Reading confirmed items from {p}")
        return load_from_json(p)

    # Check project data/ folder
    data_json = Path("data/confirmed.json")
    if data_json.exists():
        print(f"Reading confirmed items from {data_json}")
        return load_from_json(data_json)

    # Check common Downloads locations (Windows / Mac / Linux)
    home = Path.home()
    for candidate in [
        home / "Downloads" / "confirmed.json",
        home / "Desktop" / "confirmed.json",
    ]:
        if candidate.exists():
            print(f"Found confirmed.json in {candidate.parent} — copying to data/confirmed.json")
            import shutil
            data_json.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, data_json)
            return load_from_json(data_json)

    review_path = Path(review_arg or "data/review.csv")
    if not review_path.exists():
        print(
            f"Error: confirmed.json not found in data/, Downloads, or Desktop.\n"
            "Open data/review.html, tick your matches, click 'Download confirmed list',\n"
            "then re-run this script.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Reading confirmed items from {review_path} (CSV fallback)")
    return load_from_csv(review_path)


FINAL_COLUMNS = [
    "id", "tesco_item", "tesco_size", "tesco_qty",
    "mands_name", "mands_size", "mands_price", "mands_url", "notes",
]


def write_final_csv(rows: list[dict], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FINAL_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def print_shopping_list(rows: list[dict]) -> None:
    print("\n" + "=" * 60)
    print("M&S SHOPPING LIST")
    print("=" * 60)
    for row in rows:
        qty = row.get("tesco_qty", 1)
        name = row["mands_name"]
        size = row.get("mands_size", "")
        price = row.get("mands_price", "")
        url = row.get("mands_url", "")
        price_str = f"  £{price}" if price else ""
        print(f"\n[{row['id']}] {name}")
        if size:
            print(f"     Size:     {size}")
        print(f"     Qty:      {qty}{price_str}")
        if url:
            print(f"     Link:     {url}")
        if row.get("notes"):
            print(f"     Notes:    {row['notes']}")
    print("\n" + "=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build final M&S shopping list from confirmed review.")
    parser.add_argument("--confirmed", default=None,               help="Path to confirmed.json from the HTML page")
    parser.add_argument("--review",    default="data/review.csv",  help="Fallback: path to edited review CSV")
    parser.add_argument("--out",       default="data/final_list.csv", help="Output CSV path")
    args = parser.parse_args()

    confirmed, unmatched = load_confirmed(args.confirmed, args.review)
    if not confirmed:
        print(
            "No confirmed items found.\n"
            "Open data/review.html, tick your matches, then click 'Download confirmed list'\n"
            "and save the file as data/confirmed.json.",
            file=sys.stderr,
        )
        sys.exit(1)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_final_csv(confirmed, out_path)
    print(f"Final list -> {out_path}  ({len(confirmed)} items confirmed)")

    if unmatched:
        print(f"Items without a confirmed match (IDs {unmatched}) — add them manually.")

    print_shopping_list(confirmed)


if __name__ == "__main__":
    main()
