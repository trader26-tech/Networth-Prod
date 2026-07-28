"""
Smart-money trade ingestion.

User downloads bulk-deal / block-deal / SAST disclosure CSVs from Tickertape
(or similar) up to 5,000 rows at a time. We accumulate them into a single
deduped master file so analytics can run on the whole history without
re-uploading every time.

Expected raw CSV columns (case-insensitive, flexible naming):
    Stock | Date | Party | Category | Txn. type
    Avg. trade price (₹) | Value traded (₹)
    Holdings change | Quantity

Storage:
    api/fundamentals/data/smart_money/
        raw/                 — every uploaded CSV, untouched
        master_trades.csv    — merged, deduped, normalized
"""
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "smart_money"
RAW_DIR  = DATA_DIR / "raw"
MASTER   = DATA_DIR / "master_trades.csv"

# Canonical column names we write to master.
MASTER_COLS = [
    "date", "stock", "party", "category", "txn_type",
    "price", "value", "holdings_change", "quantity", "source_file",
]

# Map common header variants → canonical name. Case-insensitive substring match.
# Canonical names appear in their own alias list so files we save with
# canonical headers (after a manual mapping override) auto-detect on re-read.
HEADER_ALIASES = {
    "date":            ["date"],
    "stock":           ["stock", "company", "scrip", "symbol"],
    "party":           ["party", "client", "buyer", "seller", "investor", "name"],
    "category":        ["category", "type"],
    "txn_type":        ["txn_type", "txn. type", "txn type", "transaction type", "buy/sell", "side"],
    "price":           ["price", "avg. trade price", "avg trade price"],
    "value":           ["value", "value traded", "trade value", "amount"],
    "holdings_change": ["holdings_change", "holdings change", "change in holding"],
    "quantity":        ["quantity", "qty", "shares"],
}


def _norm_header(h: str) -> str:
    return (h or "").strip().lower().replace("(₹)", "").replace("(rs)", "").strip()


def _map_headers(headers: list[str]) -> dict[str, str]:
    """Return {canonical: original_header} based on substring matching."""
    out: dict[str, str] = {}
    norm = [(_norm_header(h), h) for h in headers]
    for canonical, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            for nh, original in norm:
                if alias in nh:
                    out[canonical] = original
                    break
            if canonical in out:
                break
    return out


def _to_iso_date(s: str) -> str:
    """Parse the various date formats Indian disclosures use into ISO YYYY-MM-DD."""
    if not s:
        return ""
    s = s.strip()
    fmts = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d-%b-%Y", "%b %d, %Y"]
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    # Last-resort: keep as-is so the row still loads
    return s


_NUM_CLEAN = re.compile(r"[,₹\s]")


def _to_num(val) -> float | None:
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in {"—", "-", "NA", "N/A"}:
        return None
    s = _NUM_CLEAN.sub("", s).rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def _txn_norm(val) -> str:
    """Normalize transaction type to 'buy' | 'sell' (lowercased, anything else preserved)."""
    if not val:
        return ""
    s = str(val).strip().lower()
    if s in {"buy", "b", "purchase", "bought"}:
        return "buy"
    if s in {"sell", "s", "sale", "sold"}:
        return "sell"
    return s


def _parse_csv(path: Path) -> list[dict]:
    """Parse a single raw CSV → list of normalized rows."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return []
        mapping = _map_headers(reader.fieldnames)
        out: list[dict] = []
        for raw in reader:
            row = {
                "date":            _to_iso_date(raw.get(mapping.get("date", ""), "")),
                "stock":           (raw.get(mapping.get("stock", ""), "") or "").strip(),
                "party":           (raw.get(mapping.get("party", ""), "") or "").strip(),
                "category":        (raw.get(mapping.get("category", ""), "") or "").strip(),
                "txn_type":        _txn_norm(raw.get(mapping.get("txn_type", ""), "")),
                "price":           _to_num(raw.get(mapping.get("price", ""), "")),
                "value":           _to_num(raw.get(mapping.get("value", ""), "")),
                "holdings_change": _to_num(raw.get(mapping.get("holdings_change", ""), "")),
                "quantity":        _to_num(raw.get(mapping.get("quantity", ""), "")),
                "source_file":     path.name,
            }
            # Drop totally blank rows.
            if not row["date"] and not row["stock"] and not row["party"]:
                continue
            out.append(row)
        return out


def _row_key(r: dict) -> tuple:
    """Dedup key — same (date, stock, party, qty) almost certainly the same trade."""
    return (
        r.get("date") or "",
        (r.get("stock") or "").upper().strip(),
        (r.get("party") or "").upper().strip(),
        round(float(r.get("quantity") or 0)),
        round(float(r.get("price") or 0), 2),
    )


def rebuild_master() -> dict:
    """Scan raw/, merge all files, dedupe, write master_trades.csv. Returns stats."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    seen: dict[tuple, dict] = {}
    files_processed = 0
    rows_seen = 0
    for f in sorted(RAW_DIR.glob("*.csv")):
        files_processed += 1
        for r in _parse_csv(f):
            rows_seen += 1
            key = _row_key(r)
            # First file wins for a given key. Source-file metadata kept from first.
            if key not in seen:
                seen[key] = r

    merged = list(seen.values())
    merged.sort(key=lambda r: (r.get("date") or "", r.get("stock") or ""))

    with MASTER.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_COLS)
        w.writeheader()
        for r in merged:
            w.writerow({c: r.get(c, "") for c in MASTER_COLS})

    return {
        "files_processed":   files_processed,
        "rows_seen":         rows_seen,
        "unique_rows":       len(merged),
        "duplicates_dropped": rows_seen - len(merged),
        "master_path":       str(MASTER),
        "rebuilt_at":        datetime.now(timezone.utc).isoformat(),
    }


def save_upload(filename: str, content: bytes, mapping_override: dict | None = None) -> dict:
    """Save a newly-uploaded CSV into raw/ then rebuild the master.

    If `mapping_override` is provided (e.g. {"stock": "Symbol", "value": "Total"}),
    we rewrite the file with canonical headers before saving — so future
    rebuilds work without needing the override every time.
    """
    import io

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    base = Path(filename).stem or "upload"
    ext = Path(filename).suffix or ".csv"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", base)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RAW_DIR / f"{safe}_{stamp}{ext}"

    if mapping_override:
        # Parse with override, write a canonical-headers file.
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        headers = list(reader.fieldnames or [])
        auto = _map_headers(headers)
        # Override wins where the original column exists in the file.
        for canonical, original in (mapping_override or {}).items():
            if original and original in headers:
                auto[canonical] = original

        canonical_cols = ["date", "stock", "party", "category", "txn_type",
                          "price", "value", "holdings_change", "quantity"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=canonical_cols)
        w.writeheader()
        for raw in reader:
            w.writerow({c: raw.get(auto.get(c, ""), "") for c in canonical_cols})
        out.write_text(buf.getvalue(), encoding="utf-8")
    else:
        out.write_bytes(content)

    stats = rebuild_master()
    stats["saved_as"] = out.name
    return stats


# ─── Preview (parse without saving) ──────────────────────────────────────────

def preview_upload(
    content: bytes,
    *,
    sample_size: int = 8,
    mapping_override: dict | None = None,
) -> dict:
    """Parse an uploaded CSV in memory and return a diagnostic preview so the
    user can confirm before we commit the data to the master file.

    Returns:
        headers:           the column names the file actually has
        header_mapping:    {canonical_field: matched_header_in_your_file}
        missing_canonical: canonical fields we couldn't find (essential ones flagged)
        unmatched_headers: file columns we couldn't map (data we'll ignore)
        sample_raw:        first N rows EXACTLY as they appear in the file
        sample_parsed:     first N rows AFTER we normalize them
        warnings:          list of human-readable issues (bad dates, unknown side)
        total_rows:        total rows in the file
        usable_rows:       rows that have at least date+stock+party after parsing
    """
    import io

    # Try utf-8-sig first (handles Excel BOM); fall back to utf-8.
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = content.decode("utf-8", errors="replace")
        except Exception:
            return {"ok": False, "error": "Could not decode file as UTF-8."}

    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    if not headers:
        return {"ok": False, "error": "No header row found in CSV.",
                "headers": [], "total_rows": 0}

    mapping = _map_headers(headers)
    # User-provided overrides win (only where the named column actually exists).
    if mapping_override:
        for canonical, original in mapping_override.items():
            if original and original in headers:
                mapping[canonical] = original
            elif canonical in mapping and not original:
                # Explicit "" override means user wants the auto-detection cleared
                del mapping[canonical]
    mapped_originals = set(mapping.values())
    unmatched = [h for h in headers if h not in mapped_originals]

    # Which canonical fields are essential vs nice-to-have
    ESSENTIAL = {"date", "stock", "party", "txn_type", "value"}
    NICE     = {"price", "category", "holdings_change", "quantity"}
    missing_canonical = {
        "essential": sorted(ESSENTIAL - mapping.keys()),
        "nice_to_have": sorted(NICE - mapping.keys()),
    }

    sample_raw: list[dict] = []
    sample_parsed: list[dict] = []
    warnings: list[str] = []
    txn_seen: dict[str, int] = {}
    date_unparsed: int = 0
    value_unparsed: int = 0
    total = 0
    usable = 0

    for raw in reader:
        total += 1
        # Build canonical row exactly like _parse_csv does
        date_str = raw.get(mapping.get("date", ""), "")
        iso_date = _to_iso_date(date_str)
        if date_str and (not iso_date or iso_date == date_str):
            # Date couldn't be re-parsed into ISO — looks unrecognised
            if not iso_date or "-" not in iso_date[:10]:
                date_unparsed += 1

        value_raw = raw.get(mapping.get("value", ""), "")
        value_num = _to_num(value_raw)
        if value_raw and value_num is None:
            value_unparsed += 1

        txn_raw = raw.get(mapping.get("txn_type", ""), "")
        txn = _txn_norm(txn_raw)
        if txn_raw:
            txn_seen[txn or "<empty>"] = txn_seen.get(txn or "<empty>", 0) + 1

        row = {
            "date":            iso_date,
            "stock":           (raw.get(mapping.get("stock", ""), "") or "").strip(),
            "party":           (raw.get(mapping.get("party", ""), "") or "").strip(),
            "category":        (raw.get(mapping.get("category", ""), "") or "").strip(),
            "txn_type":        txn,
            "price":           _to_num(raw.get(mapping.get("price", ""), "")),
            "value":           value_num,
            "holdings_change": _to_num(raw.get(mapping.get("holdings_change", ""), "")),
            "quantity":        _to_num(raw.get(mapping.get("quantity", ""), "")),
        }
        if row["date"] and row["stock"] and row["party"]:
            usable += 1

        if len(sample_parsed) < sample_size:
            sample_raw.append({k: raw.get(k, "") for k in headers})
            sample_parsed.append(row)

    # ─── Build warnings ─────────────────────────────────────────────────────
    if missing_canonical["essential"]:
        warnings.append(
            f"Essential column(s) not detected: "
            f"{', '.join(missing_canonical['essential'])}. The dashboard needs these."
        )
    if date_unparsed:
        warnings.append(
            f"{date_unparsed} row(s) have a date format we couldn't recognise. "
            f"Supported: YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY, '15 Mar 2026'."
        )
    if value_unparsed:
        warnings.append(
            f"{value_unparsed} row(s) have a 'Value traded' that couldn't be parsed as a number. "
            f"Strip any non-numeric characters (other than ₹ and commas, which are fine)."
        )
    # Anything that's not buy/sell will fall out of the analytics
    unrecognised_sides = {k: v for k, v in txn_seen.items()
                          if k not in {"buy", "sell"}}
    if unrecognised_sides:
        warnings.append(
            "Transaction-type values that aren't 'buy' or 'sell' will be ignored: "
            + ", ".join(f"{k} ({v})" for k, v in sorted(unrecognised_sides.items()))
        )
    if usable == 0 and total > 0:
        warnings.append(
            "No rows have all three of date + stock + party after parsing. "
            "Double-check the column names in your CSV match the format described."
        )

    return {
        "ok":                 True,
        "headers":            headers,
        "header_mapping":     mapping,
        "missing_canonical":  missing_canonical,
        "unmatched_headers":  unmatched,
        "sample_raw":         sample_raw,
        "sample_parsed":      sample_parsed,
        "warnings":           warnings,
        "total_rows":         total,
        "usable_rows":        usable,
        "txn_type_breakdown": txn_seen,
    }


def load_master() -> list[dict]:
    """Return all rows from the master CSV (or [] if it doesn't exist yet)."""
    if not MASTER.exists():
        return []
    with MASTER.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
