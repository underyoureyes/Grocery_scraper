"""
Step 4 — Read your confirmed review CSV and output a clean M&S shopping list.

After editing data/review.csv (mark 'yes' in the 'confirmed' column for your
chosen match per item), run this to get a final CSV and plain-text list.

Usage:
    python generate_final_list.py [--review data/review.csv] [--out data/final_list.csv]
"""

import argparse
import csv
import sys
from pathlib import Path


def load_confirmed(review_path: Path) -> list[dict]:
    confirmed = []
    unmatched = []
    seen_ids: set[int] = set()

    with open(review_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item_id = int(row["id"])
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

    # Collect IDs that had no confirmed row
    all_ids: set[int] = set()
    with open(review_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            all_ids.add(int(row["id"]))
    unmatched = sorted(all_ids - seen_ids)

    if unmatched:
        print(f"[warn] {len(unmatched)} item(s) have no confirmed match: IDs {unmatched}", file=sys.stderr)

    return confirmed, unmatched


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
    parser.add_argument("--review", default="data/review.csv",     help="Path to your edited review CSV")
    parser.add_argument("--out",    default="data/final_list.csv", help="Output CSV path")
    args = parser.parse_args()

    review_path = Path(args.review)
    if not review_path.exists():
        print(f"Error: {review_path} not found.", file=sys.stderr)
        sys.exit(1)

    confirmed, unmatched = load_confirmed(review_path)
    if not confirmed:
        print("No rows have 'confirmed=yes'. Edit the review CSV first.", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_final_csv(confirmed, out_path)
    print(f"Final list → {out_path}  ({len(confirmed)} items confirmed)")

    if unmatched:
        print(f"Items without a confirmed match (IDs {unmatched}) — add them manually.")

    print_shopping_list(confirmed)


if __name__ == "__main__":
    main()
