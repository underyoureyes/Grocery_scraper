"""
Step 1 — Parse a manually exported Tesco order CSV into a clean JSON/DataFrame.

Usage:
    python parse_tesco.py <input.csv> [--out parsed_order.json]

Expected CSV columns (case-insensitive, extra columns are kept):
    item_name, quantity, size, price
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# Column normalisation helpers
# ---------------------------------------------------------------------------

_COLUMN_ALIASES: dict[str, list[str]] = {
    "item_name": ["item_name", "item", "product", "description", "name"],
    "quantity":  ["quantity", "qty", "count", "amount"],
    "size":      ["size", "weight", "volume", "unit_size", "pack_size"],
    "price":     ["price", "unit_price", "cost"],
}


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns to canonical names using alias table; tolerate casing."""
    rename_map: dict[str, str] = {}
    lower_cols = {c.lower().strip(): c for c in df.columns}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_cols:
                rename_map[lower_cols[alias]] = canonical
                break
    return df.rename(columns=rename_map)


# ---------------------------------------------------------------------------
# Value cleaning helpers
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(r"[\£\$\€]?([\d]+\.?[\d]*)")
_QTY_RE   = re.compile(r"(\d+)")


def _clean_price(val) -> float | None:
    if pd.isna(val):
        return None
    m = _PRICE_RE.search(str(val))
    return float(m.group(1)) if m else None


def _clean_quantity(val) -> int:
    if pd.isna(val):
        return 1
    m = _QTY_RE.search(str(val))
    return int(m.group(1)) if m else 1


def _clean_size(val) -> str:
    if pd.isna(val):
        return ""
    # Normalise spacing around numbers and units: "2litre" -> "2 litre"
    text = str(val).strip()
    text = re.sub(r"(\d)\s*([a-zA-Z])", r"\1 \2", text)
    return text


def _clean_item_name(val) -> str:
    if pd.isna(val):
        return ""
    return str(val).strip()


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def parse_tesco_csv(path: str | Path) -> pd.DataFrame:
    """Read and clean a Tesco order CSV; returns a tidy DataFrame."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    df = pd.read_csv(path, dtype=str, skip_blank_lines=True)
    df.columns = df.columns.str.strip()
    df = _normalise_columns(df)

    required = {"item_name", "quantity", "size", "price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"CSV is missing required columns: {missing}\n"
            f"Found: {list(df.columns)}"
        )

    df["item_name"] = df["item_name"].map(_clean_item_name)
    df["quantity"]  = df["quantity"].map(_clean_quantity)
    df["size"]      = df["size"].map(_clean_size)
    df["price"]     = df["price"].map(_clean_price)

    # Drop entirely blank rows
    df = df[df["item_name"] != ""].reset_index(drop=True)

    # Add a stable row id for downstream cross-referencing
    df.insert(0, "id", range(1, len(df) + 1))

    return df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a Tesco order CSV.")
    parser.add_argument("input", help="Path to the Tesco order CSV")
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (JSON). Defaults to <input>_parsed.json",
    )
    parser.add_argument(
        "--print",
        dest="print_table",
        action="store_true",
        help="Print the parsed table to stdout",
    )
    args = parser.parse_args()

    try:
        df = parse_tesco_csv(args.input)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out) if args.out else Path(args.input).with_name(
        Path(args.input).stem + "_parsed.json"
    )
    records = df.to_dict(orient="records")
    out_path.write_text(json.dumps(records, indent=2))
    print(f"Parsed {len(df)} items -> {out_path}")

    if args.print_table:
        print(df.to_string(index=False))


if __name__ == "__main__":
    main()
