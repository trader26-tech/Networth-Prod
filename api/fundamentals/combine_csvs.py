"""
Combine the two Tickertape export CSVs into a single universe file.

Stdlib-only — no pandas needed.

Drop both Tickertape exports into api/fundamentals/data/ as:
    tickertape_part1.csv
    tickertape_part2.csv

Then run:
    python3 api/fundamentals/combine_csvs.py

Output:
    api/fundamentals/data/tickertape_universe.csv  (deduped, full universe)
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
PART1 = DATA_DIR / "tickertape_part1.csv"
PART2 = DATA_DIR / "tickertape_part2.csv"
OUT = DATA_DIR / "tickertape_universe.csv"


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return headers, rows


def main() -> None:
    if not PART1.exists() or not PART2.exists():
        print(f"Missing input(s). Expected:")
        print(f"  {PART1}  -> exists: {PART1.exists()}")
        print(f"  {PART2}  -> exists: {PART2.exists()}")
        sys.exit(1)

    h1, rows1 = read_csv(PART1)
    h2, rows2 = read_csv(PART2)

    print(f"part1: {len(rows1):>6} rows x {len(h1):>3} cols")
    print(f"part2: {len(rows2):>6} rows x {len(h2):>3} cols")

    # Sanity check: same columns
    if h1 != h2:
        only_in_1 = set(h1) - set(h2)
        only_in_2 = set(h2) - set(h1)
        if only_in_1:
            print(f"  WARN: cols only in part1: {sorted(only_in_1)}")
        if only_in_2:
            print(f"  WARN: cols only in part2: {sorted(only_in_2)}")

    # Use union of headers, in order of first appearance
    seen = set()
    headers: list[str] = []
    for col in h1 + h2:
        if col not in seen:
            seen.add(col)
            headers.append(col)

    combined = rows1 + rows2

    # Dedupe — pick the first ticker-like column we recognize
    dedupe_col = None
    for candidate in ("Ticker", "Symbol", "NSE Code", "BSE Code", "Stock Name", "Name"):
        if candidate in headers:
            dedupe_col = candidate
            break

    if dedupe_col:
        before = len(combined)
        seen_keys: set[str] = set()
        deduped: list[dict] = []
        for r in combined:
            key = (r.get(dedupe_col) or "").strip().upper()
            if not key:
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped.append(r)
        combined = deduped
        print(f"deduped on {dedupe_col!r}: dropped {before - len(combined)} duplicate/empty rows")
    else:
        print(f"WARN: no ticker-like column found for dedup; keeping all rows")

    with OUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for r in combined:
            writer.writerow({col: r.get(col, "") for col in headers})

    print(f"\nwrote: {OUT}")
    print(f"  {len(combined)} rows x {len(headers)} columns")
    print(f"\nColumns:")
    for i, col in enumerate(headers, 1):
        print(f"  {i:>3}. {col}")


if __name__ == "__main__":
    main()
