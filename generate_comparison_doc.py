"""
Generate a shareable HTML comparison document from confirmed.json.

Shows each Tesco item alongside its matched M&S equivalent, with
per-item prices, the price difference, and a total spend summary.

Usage:
    python generate_comparison_doc.py [--confirmed data/confirmed.json] [--out data/comparison.html]
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import date


def load_items(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_price(val) -> str:
    try:
        return f"£{float(val):.2f}"
    except (TypeError, ValueError):
        return "—"


def _price_float(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _diff_cell(tesco_p: float | None, mands_p: float | None) -> tuple[str, str]:
    """Return (formatted string, css class) for the price difference column."""
    if tesco_p is None or mands_p is None:
        return "—", ""
    diff = mands_p - tesco_p
    if abs(diff) < 0.005:
        return "same", "diff-same"
    sign = "+" if diff > 0 else ""
    cls = "diff-more" if diff > 0 else "diff-less"
    return f"{sign}£{diff:.2f}", cls


def write_html(items: list[dict], out_path: Path) -> None:
    tesco_total = 0.0
    mands_total = 0.0
    tesco_missing = 0
    mands_missing = 0

    rows_html = []
    for item in items:
        tp = _price_float(item.get("tesco_price"))
        mp = _price_float(item.get("mands_price"))

        tesco_name = item["tesco_item"]
        # Strip leading "Tesco " brand prefix for display
        import re
        display_tesco = re.sub(r"^Tesco\s+", "", tesco_name, flags=re.IGNORECASE).strip()

        mands_name = item.get("mands_name", "")
        mands_url  = item.get("mands_url", "")
        mands_link = (
            f'<a href="{mands_url}" target="_blank">{mands_name}</a>'
            if mands_url else mands_name
        )

        score = item.get("score", "")
        score_str = f"{score:.0f}%" if isinstance(score, (int, float)) else ""

        if tp is not None:
            tesco_total += tp
        else:
            tesco_missing += 1

        if mp is not None:
            mands_total += mp
        else:
            mands_missing += 1

        diff_str, diff_cls = _diff_cell(tp, mp)

        rows_html.append(f"""
    <tr>
      <td class="item-name">{display_tesco}</td>
      <td class="price">{_fmt_price(tp)}</td>
      <td class="item-name mands-col">{mands_link}</td>
      <td class="price">{_fmt_price(mp)}</td>
      <td class="diff {diff_cls}">{diff_str}</td>
      <td class="score">{score_str}</td>
    </tr>""")

    # Totals row
    total_diff = mands_total - tesco_total
    total_diff_str = ("+" if total_diff > 0 else "") + f"£{total_diff:.2f}"
    total_diff_cls = "diff-more" if total_diff > 0 else "diff-less"

    tesco_note = f" <span class='missing'>({tesco_missing} items without price)</span>" if tesco_missing else ""
    mands_note = f" <span class='missing'>({mands_missing} items without price)</span>" if mands_missing else ""

    today = date.today().strftime("%-d %B %Y") if sys.platform != "win32" else date.today().strftime("%d %B %Y").lstrip("0")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tesco vs M&amp;S — Price Comparison</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 13px;
    color: #222;
    background: #fff;
    padding: 28px 32px;
    max-width: 1100px;
    margin: 0 auto;
  }}
  h1 {{
    font-size: 22px;
    font-weight: 700;
    margin-bottom: 4px;
  }}
  .subtitle {{
    color: #666;
    font-size: 12px;
    margin-bottom: 20px;
  }}

  /* Summary cards */
  .summary {{
    display: flex;
    gap: 16px;
    margin-bottom: 24px;
    flex-wrap: wrap;
  }}
  .card {{
    flex: 1;
    min-width: 180px;
    border-radius: 8px;
    padding: 16px 20px;
    border: 1px solid #ddd;
  }}
  .card-tesco  {{ border-left: 4px solid #005EB8; }}
  .card-mands  {{ border-left: 4px solid #1B4F37; }}
  .card-diff   {{ border-left: 4px solid #999; }}
  .card-label  {{ font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: #777; }}
  .card-value  {{ font-size: 28px; font-weight: 700; margin-top: 4px; }}
  .card-note   {{ font-size: 11px; color: #999; margin-top: 2px; }}
  .tesco-val   {{ color: #005EB8; }}
  .mands-val   {{ color: #1B4F37; }}
  .more  {{ color: #c0392b; }}
  .less  {{ color: #27ae60; }}

  /* Table */
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  thead tr {{
    background: #f5f5f5;
    border-bottom: 2px solid #ccc;
  }}
  th {{
    padding: 9px 10px;
    text-align: left;
    font-weight: 600;
    white-space: nowrap;
  }}
  th.price, th.diff, th.score {{ text-align: right; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid #eee; vertical-align: top; }}
  td.price, td.diff, td.score {{ text-align: right; white-space: nowrap; }}
  td.item-name {{ max-width: 280px; }}
  a {{ color: #1B4F37; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  tr:hover td {{ background: #fafafa; }}

  /* Diff colours */
  .diff-more {{ color: #c0392b; font-weight: 600; }}
  .diff-less {{ color: #27ae60; font-weight: 600; }}
  .diff-same {{ color: #999; }}

  .score {{ color: #888; font-size: 12px; }}

  /* Tesco / M&S column headers */
  .tesco-hdr {{ color: #005EB8; }}
  .mands-hdr  {{ color: #1B4F37; }}

  /* Totals row */
  .totals-row td {{
    border-top: 2px solid #ccc;
    border-bottom: none;
    font-weight: 700;
    font-size: 14px;
    background: #f9f9f9;
    padding-top: 10px;
    padding-bottom: 10px;
  }}

  .missing {{ font-weight: 400; color: #aaa; font-size: 11px; }}
  .footer {{ margin-top: 20px; font-size: 11px; color: #aaa; }}

  @media print {{
    body {{ padding: 16px; }}
    a {{ color: inherit; text-decoration: none; }}
    .footer {{ display: none; }}
  }}
</style>
</head>
<body>

<h1>Tesco vs M&amp;S — Price Comparison</h1>
<p class="subtitle">Generated {today} &nbsp;·&nbsp; {len(items)} matched items</p>

<div class="summary">
  <div class="card card-tesco">
    <div class="card-label">Tesco total</div>
    <div class="card-value tesco-val">£{tesco_total:.2f}</div>
    <div class="card-note">{tesco_note if tesco_missing else f"{len(items)} items"}</div>
  </div>
  <div class="card card-mands">
    <div class="card-label">M&amp;S total</div>
    <div class="card-value mands-val">£{mands_total:.2f}</div>
    <div class="card-note">{mands_note if mands_missing else f"{len(items)} items"}</div>
  </div>
  <div class="card card-diff">
    <div class="card-label">Difference</div>
    <div class="card-value {'more' if total_diff > 0 else 'less'}">
      {"+" if total_diff > 0 else ""}£{abs(total_diff):.2f}
    </div>
    <div class="card-note">
      {"M&S costs more" if total_diff > 0 else "M&S costs less" if total_diff < 0 else "Same price"}
    </div>
  </div>
</div>

<table>
  <thead>
    <tr>
      <th class="tesco-hdr">Tesco Item</th>
      <th class="price tesco-hdr">Tesco £</th>
      <th class="mands-hdr">M&amp;S Equivalent</th>
      <th class="price mands-hdr">M&amp;S £</th>
      <th class="diff">+/−</th>
      <th class="score">Match</th>
    </tr>
  </thead>
  <tbody>
{"".join(rows_html)}
    <tr class="totals-row">
      <td>Total ({len(items)} items)</td>
      <td class="price">£{tesco_total:.2f}{tesco_note}</td>
      <td></td>
      <td class="price">£{mands_total:.2f}{mands_note}</td>
      <td class="diff {total_diff_cls}">{total_diff_str}</td>
      <td></td>
    </tr>
  </tbody>
</table>

<p class="footer">
  M&amp;S prices from trolley.co.uk &nbsp;·&nbsp;
  Match scores show how closely the item name was matched (fuzzy string similarity).
  Prices may vary; verify before shopping.
</p>

</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a shareable Tesco vs M&S comparison doc.")
    parser.add_argument("--confirmed", default="data/confirmed.json", help="Path to confirmed.json")
    parser.add_argument("--out",       default="data/comparison.html", help="Output HTML path")
    args = parser.parse_args()

    confirmed_path = Path(args.confirmed)
    if not confirmed_path.exists():
        print(f"Error: {confirmed_path} not found. Run generate_final_list.py first.", file=sys.stderr)
        sys.exit(1)

    items = load_items(confirmed_path)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_html(items, out_path)

    tesco_tot = sum(float(i["tesco_price"]) for i in items if i.get("tesco_price") is not None)
    mands_tot = sum(float(i["mands_price"]) for i in items if i.get("mands_price") is not None)
    print(f"Comparison doc -> {out_path}")
    print(f"Tesco total:  £{tesco_tot:.2f}")
    print(f"M&S total:    £{mands_tot:.2f}")
    diff = mands_tot - tesco_tot
    print(f"Difference:   {'+'if diff>0 else ''}£{diff:.2f} ({'M&S costs more' if diff>0 else 'M&S costs less'})")


if __name__ == "__main__":
    main()
