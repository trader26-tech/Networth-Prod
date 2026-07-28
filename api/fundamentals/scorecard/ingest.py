"""
Scorecard CSV ingestion.

The user uploads Tickertape exports with this exact column set (16 metrics
+ 4 anchors):

    Name | Ticker | Sub-Sector | Market Cap
    1Y Return | 5Y CAGR | Sharpe Ratio
    PE Ratio | PB Ratio | EV/EBITDA Ratio
    5Y Historical Revenue Growth | 5Y Historical EPS Growth | 1Y Forward EPS Growth
    ROCE | 5Y Avg Return on Equity | Net Profit Margin
    % Away From 52W High | RSI – 14D
    Dividend Yield | Payout Ratio

Each upload is merged into a master file. Multiple sectors can be uploaded
incrementally — each row is deduped on (Ticker) so re-uploading a sector
overwrites with the latest values.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "scorecard"
RAW_DIR  = DATA_DIR / "raw"
MASTER   = DATA_DIR / "master_scorecard.csv"

# The 16 metric columns used by the Scorecard scoring engine.
METRIC_COLS = [
    "1Y Return", "5Y CAGR", "Sharpe Ratio",
    "PE Ratio", "PB Ratio", "EV/EBITDA Ratio",
    "5Y Historical Revenue Growth", "5Y Historical EPS Growth", "1Y Forward EPS Growth",
    "ROCE", "5Y Avg Return on Equity", "Net Profit Margin",
    "% Away From 52W High", "RSI – 14D",
    "Dividend Yield", "Payout Ratio",
]
ANCHOR_COLS = ["Name", "Ticker", "Sub-Sector", "Market Cap"]

# Additional columns used by Exit Strategy (not scored — read as raw values).
# Each is a hard-flag/ownership input: e.g. pledged > 25% → exit signal.
EXIT_COLS = [
    "Return on Equity",            # latest ROE snapshot (vs. 5Y Avg ROE)
    "Debt to Equity",              # > 3 (ex-financials) = structural risk
    "Current Ratio",               # < 1 = short-term liquidity stress
    "Interest Coverage Ratio",     # < 1.5 = can't cover interest
    "Pledged Promoter Holdings",   # > 25% red, > 50% urgent
    "Promoter Holding",            # falling = smart money exiting
    "FII Holding Change – 6M",     # net institutional sentiment shift
    "3M Average Volume",           # liquidity for exit
]
MASTER_COLS = ANCHOR_COLS + METRIC_COLS + EXIT_COLS

# Tickertape sometimes writes columns slightly differently. Normalize.
HEADER_ALIASES: dict[str, list[str]] = {
    "RSI – 14D": ["rsi – 14d", "rsi - 14d", "rsi 14d", "rsi"],
    "% Away From 52W High": ["% away from 52w high", "% from 52w high", "52w high %"],
    "Sub-Sector":  ["sub-sector", "sub sector", "subsector"],
    "Market Cap":  ["market cap", "mcap"],
    # Exit-only columns — handle minor spelling/punctuation variants
    "Pledged Promoter Holdings": ["pledged promoter holdings", "pledged promoter holding",
                                  "promoter pledge", "pledged shares"],
    "Promoter Holding":          ["promoter holding", "promoter holdings", "promoter %"],
    "FII Holding Change – 6M":   ["fii holding change – 6m", "fii holding change - 6m",
                                  "fii change 6m", "fii holding change"],
    "3M Average Volume":         ["3m average volume", "3 month avg volume",
                                  "90d avg volume", "average volume"],
    "Debt to Equity":            ["debt to equity", "debt/equity", "d/e"],
    "Current Ratio":             ["current ratio"],
    "Interest Coverage Ratio":   ["interest coverage ratio", "interest coverage"],
    "Return on Equity":          ["return on equity", "roe"],
}


def _norm_header(h: str) -> str:
    return (h or "").strip().lower()


def _map_headers(headers: list[str]) -> dict[str, str]:
    """{canonical: original_header} — exact match first, alias fallback.
    Never maps the same source header to two canonicals — prevents a generic
    alias like "return on equity" from swallowing "5Y Avg Return on Equity"."""
    out: dict[str, str] = {}
    claimed: set[str] = set()
    # Direct match (case-insensitive)
    for col in MASTER_COLS:
        for h in headers:
            if h in claimed:
                continue
            if _norm_header(h) == _norm_header(col):
                out[col] = h
                claimed.add(h)
                break
    # Alias fallback — only consider headers not already claimed
    for col, aliases in HEADER_ALIASES.items():
        if col in out:
            continue
        for alias in aliases:
            for h in headers:
                if h in claimed:
                    continue
                if alias in _norm_header(h):
                    out[col] = h
                    claimed.add(h)
                    break
            if col in out:
                break
    return out


_NUM_CLEAN = re.compile(r"[,₹\s]")


def to_num(val) -> float | None:
    """Best-effort numeric parser. '' / '—' / 'NA' → None."""
    if val is None:
        return None
    s = str(val).strip()
    if not s or s in {"—", "-", "NA", "N/A", "null"}:
        return None
    s = _NUM_CLEAN.sub("", s).rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def _parse(content: bytes) -> tuple[list[str], list[dict], dict[str, str]]:
    """Parse a CSV → (headers, rows_with_canonical_keys, mapping)."""
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    headers = list(reader.fieldnames or [])
    mapping = _map_headers(headers)
    rows: list[dict] = []
    for raw in reader:
        if not any(raw.values()):
            continue
        row = {col: raw.get(mapping.get(col, ""), "") for col in MASTER_COLS}
        rows.append(row)
    return headers, rows, mapping


# ─── Preview (parse without saving) ───────────────────────────────────────────

def preview_upload(content: bytes, *, sample_size: int = 8) -> dict:
    """Diagnose the file before commit — same UX as smart-money preview."""
    if not content:
        return {"ok": False, "error": "Empty file"}
    try:
        headers, rows, mapping = _parse(content)
    except Exception as e:
        return {"ok": False, "error": f"Parse error: {e}"}

    if not headers:
        return {"ok": False, "error": "No header row found"}

    missing_essential = [c for c in ["Name", "Ticker", "Sub-Sector"] if c not in mapping]
    missing_metrics   = [c for c in METRIC_COLS if c not in mapping]
    missing_exit      = [c for c in EXIT_COLS   if c not in mapping]
    mapped_originals  = set(mapping.values())
    unmatched         = [h for h in headers if h not in mapped_originals]

    # Usable = has Ticker AND Sub-Sector AND at least 1 metric value
    usable = 0
    sub_sectors: dict[str, int] = {}
    for r in rows:
        if not r.get("Ticker") or not r.get("Sub-Sector"):
            continue
        has_any_metric = any(to_num(r.get(m)) is not None for m in METRIC_COLS)
        if has_any_metric:
            usable += 1
            ss = r.get("Sub-Sector") or "Other"
            sub_sectors[ss] = sub_sectors.get(ss, 0) + 1

    warnings: list[str] = []
    if missing_essential:
        warnings.append(
            f"Essential column(s) missing: {', '.join(missing_essential)}. "
            f"At minimum we need Name, Ticker, and Sub-Sector."
        )
    if len(missing_metrics) > 8:
        warnings.append(
            f"{len(missing_metrics)} of 16 metric columns not found. "
            f"The scorecard will be much weaker — re-export with all 16 metrics."
        )
    elif missing_metrics:
        warnings.append(
            f"Metric column(s) not detected: {', '.join(missing_metrics)}. "
            f"Those categories will score on partial data."
        )
    if usable == 0:
        warnings.append("No usable rows after parsing. Re-export with the expected columns.")
    if rows and usable < len(rows) * 0.5:
        warnings.append(
            f"Only {usable} of {len(rows)} rows are usable — most are missing "
            f"essential fields. Check the CSV format."
        )
    if len(missing_exit) >= len(EXIT_COLS):
        warnings.append(
            "No exit-strategy columns detected (Pledge, Promoter Holding, D/E, etc.). "
            "Scorecard will work, but Exit Triage will use limited signals. "
            "Re-export with all 27 columns to unlock the full exit engine."
        )
    elif missing_exit:
        warnings.append(
            f"Exit-strategy column(s) missing: {', '.join(missing_exit)}. "
            f"Those exit flags will be skipped for this batch."
        )

    sample_raw    = [{k: r.get(k, "") for k in headers} for r in rows[:sample_size]]
    sample_parsed = [
        {**{c: r.get(c, "") for c in ANCHOR_COLS},
         **{m: to_num(r.get(m)) for m in METRIC_COLS}}
        for r in rows[:sample_size]
    ]

    return {
        "ok":                True,
        "headers":           headers,
        "header_mapping":    mapping,
        "missing_essential": missing_essential,
        "missing_metrics":   missing_metrics,
        "missing_exit":      missing_exit,
        "unmatched_headers": unmatched,
        "total_rows":        len(rows),
        "usable_rows":       usable,
        "sub_sectors":       sub_sectors,
        "sample_raw":        sample_raw,
        "sample_parsed":     sample_parsed,
        "warnings":          warnings,
    }


# ─── Save + master rebuild ────────────────────────────────────────────────────

def save_upload(filename: str, content: bytes) -> dict:
    """Persist the uploaded CSV (as canonical-headers form) then merge into master."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    headers, rows, _ = _parse(content)

    base = Path(filename).stem or "upload"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", base)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RAW_DIR / f"{safe}_{stamp}.csv"

    # Write canonical-headers CSV so future rebuilds parse without aliases.
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in MASTER_COLS})

    stats = rebuild_master()
    stats["saved_as"] = out.name
    stats["rows_in_file"] = len(rows)
    return stats


def rebuild_master() -> dict:
    """Merge every CSV in raw/ → master_scorecard.csv. Dedupe on Ticker
    (last-upload wins — fresh data overwrites stale)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    by_ticker: dict[str, dict] = {}
    files_processed = 0
    rows_seen = 0
    # Sort by name so newer timestamps come last (overwrite older entries)
    for f in sorted(RAW_DIR.glob("*.csv")):
        files_processed += 1
        try:
            _, rows, _ = _parse(f.read_bytes())
        except Exception as e:
            print(f"⚠ scorecard: parse failed for {f.name}: {e}")
            continue
        for r in rows:
            t = (r.get("Ticker") or "").strip().upper()
            if not t:
                continue
            rows_seen += 1
            by_ticker[t] = r

    merged = sorted(by_ticker.values(),
                    key=lambda r: ((r.get("Sub-Sector") or ""), (r.get("Ticker") or "")))

    with MASTER.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MASTER_COLS)
        w.writeheader()
        for r in merged:
            w.writerow({c: r.get(c, "") for c in MASTER_COLS})

    return {
        "files_processed":    files_processed,
        "rows_seen":          rows_seen,
        "unique_rows":        len(merged),
        "duplicates_dropped": rows_seen - len(merged),
        "master_path":        str(MASTER),
        "rebuilt_at":         datetime.now().isoformat(),
    }


def load_master() -> list[dict]:
    """Return all rows from master_scorecard.csv (or []) — values stay as strings."""
    if not MASTER.exists():
        return []
    with MASTER.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def clear_all() -> dict:
    """Wipe raw/ + master. Useful when starting fresh."""
    removed = 0
    if RAW_DIR.exists():
        for f in RAW_DIR.glob("*.csv"):
            f.unlink()
            removed += 1
    if MASTER.exists():
        MASTER.unlink()
    return {"ok": True, "files_removed": removed}


def list_files() -> list[dict]:
    """List all raw uploads with metadata. Newest first."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for f in sorted(RAW_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = f.stat()
        try:
            _, rows, _ = _parse(f.read_bytes())
            sub_sectors = sorted({
                (r.get("Sub-Sector") or "").strip()
                for r in rows if (r.get("Sub-Sector") or "").strip()
            })
        except Exception:
            rows, sub_sectors = [], []
        out.append({
            "name":             f.name,
            "size_bytes":       stat.st_size,
            "uploaded_at":      datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "rows":             len(rows),
            "sub_sectors":      sub_sectors,
            "sub_sector_count": len(sub_sectors),
        })
    return out


def delete_file(name: str) -> dict:
    """Delete a single raw file by name, then rebuild master. Safe from path
    traversal — only the basename is honored."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    safe = Path(name).name
    target = RAW_DIR / safe
    if not target.exists():
        return {"ok": False, "error": "File not found"}
    target.unlink()
    stats = rebuild_master()
    stats["deleted"] = safe
    stats["ok"]      = True
    return stats
