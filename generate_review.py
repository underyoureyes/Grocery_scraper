"""
Step 3 — Generate a review CSV (and optional HTML) from matches.json.

The CSV has one row per candidate so you can delete rows you don't want
and mark your chosen match with 'confirmed=yes'.

Usage:
    python generate_review.py [--matches data/matches.json] [--out data/review.csv] [--html]
"""

import argparse
import json
from pathlib import Path


CSV_COLUMNS = [
    "id",
    "tesco_item",
    "tesco_size",
    "tesco_qty",
    "tesco_price",
    "rank",
    "mands_name",
    "mands_size",
    "mands_price",
    "mands_url",
    "score",
    "source",
    "confirmed",   # leave blank; fill in 'yes' for the match you want
    "notes",       # optional free-text
]


def matches_to_rows(matches: list[dict]) -> list[dict]:
    rows = []
    for item in matches:
        for rank, cand in enumerate(item.get("candidates", []), 1):
            rows.append({
                "id":           item["id"],
                "tesco_item":   item["tesco_item"],
                "tesco_size":   item["tesco_size"],
                "tesco_qty":    item["tesco_qty"],
                "tesco_price":  item["tesco_price"],
                "rank":         rank,
                "mands_name":   cand.get("name", ""),
                "mands_size":   cand.get("size", ""),
                "mands_price":  cand.get("price", ""),
                "mands_url":    cand.get("url", ""),
                "score":        cand.get("score", ""),
                "source":       cand.get("source", ""),
                "confirmed":    "",
                "notes":        "",
            })
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    import csv
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def write_html(rows: list[dict], path: Path) -> None:
    """Simple self-contained HTML table for browser review."""
    # Group by tesco item id
    groups: dict[int, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["id"], []).append(row)

    html_rows = []
    for item_id, candidates in groups.items():
        first = candidates[0]
        rowspan = len(candidates)
        for i, cand in enumerate(candidates):
            cells = []
            if i == 0:
                # Tesco columns span all candidate rows
                cells.append(f'<td rowspan="{rowspan}">{item_id}</td>')
                cells.append(f'<td rowspan="{rowspan}">{first["tesco_item"]}</td>')
                cells.append(f'<td rowspan="{rowspan}">{first["tesco_size"]}</td>')
                cells.append(f'<td rowspan="{rowspan}">{first["tesco_qty"]}</td>')
                cells.append(f'<td rowspan="{rowspan}">£{first["tesco_price"] or ""}</td>')
            cells.append(f'<td>{cand["rank"]}</td>')
            url = cand["mands_url"]
            link = f'<a href="{url}" target="_blank">{cand["mands_name"]}</a>' if url else cand["mands_name"]
            cells.append(f'<td>{link}</td>')
            cells.append(f'<td>{cand["mands_size"]}</td>')
            price = f'£{cand["mands_price"]}' if cand["mands_price"] else ""
            cells.append(f'<td>{price}</td>')
            score_pct = f'{cand["score"]}%' if cand["score"] != "" else ""
            cells.append(f'<td>{score_pct}</td>')
            cells.append(f'<td>{cand["source"]}</td>')
            cells.append('<td><input type="checkbox" class="confirm-cb"></td>')
            cells.append('<td><input type="text" class="notes-input" placeholder="notes..."></td>')
            html_rows.append(f'  <tr>{"".join(cells)}</tr>')

    header_cols = [
        "ID", "Tesco Item", "Size", "Qty", "Tesco £",
        "Rank", "M&amp;S Name", "M&amp;S Size", "M&amp;S £", "Score", "Source",
        "Confirm", "Notes",
    ]
    headers = "".join(f"<th>{h}</th>" for h in header_cols)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Tesco → M&amp;S Review</title>
<style>
  body {{ font-family: sans-serif; font-size: 13px; padding: 16px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 8px; vertical-align: top; }}
  th {{ background: #f0f0f0; text-align: left; }}
  tr:hover td {{ background: #fafafa; }}
  .confirm-cb {{ transform: scale(1.3); }}
  .notes-input {{ width: 140px; }}
  a {{ color: #006400; }}
</style>
</head>
<body>
<h2>Tesco → M&amp;S Match Review</h2>
<p>Tick the M&amp;S candidate you want for each Tesco item, add notes, then use
   <code>generate_final_list.py</code> to produce your shopping list.</p>
<table>
<thead><tr>{headers}</tr></thead>
<tbody>
{"".join(html_rows)}
</tbody>
</table>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a review CSV/HTML from matches.")
    parser.add_argument("--matches", default="data/matches.json", help="Path to matches.json")
    parser.add_argument("--out",     default="data/review.csv",   help="Output CSV path")
    parser.add_argument("--html",    action="store_true",         help="Also write an HTML review page")
    args = parser.parse_args()

    matches_path = Path(args.matches)
    if not matches_path.exists():
        import sys
        print(f"Error: {matches_path} not found. Run match_mands.py first.", file=sys.stderr)
        raise SystemExit(1)

    matches = json.loads(matches_path.read_text())
    rows = matches_to_rows(matches)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_csv(rows, out_path)
    print(f"Review CSV → {out_path}  ({len(matches)} items, {len(rows)} candidate rows)")

    if args.html:
        html_path = out_path.with_suffix(".html")
        write_html(rows, html_path)
        print(f"Review HTML → {html_path}")


if __name__ == "__main__":
    main()
