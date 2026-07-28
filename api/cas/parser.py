"""
CDSL / NSDL Consolidated Account Statement (CAS) parser.
========================================================

Turns a password-protected eCAS PDF into structured holdings:

    parse_cas(pdf_bytes, pan) -> {
        "investor":  {...},          # name, PAN, CAS id, period
        "accounts":  [...],          # one per demat account / MF folio
        "equities":  [...],          # ISIN, name, qty, price, value
        "bonds":     [...],          # + coupon, frequency, maturity, face value
        "mutual_funds": [...],       # AMC, scheme, ISIN, folio, units, NAV, value
        "totals":    {...},
        "warnings":  [...],
    }

The PDF password for a CAS is the holder's PAN. Nothing here is persisted —
callers get plain dicts and decide what to write.

Design notes
------------
* Text is extracted per page and parsed with anchored regexes rather than
  positional table extraction: CDSL's generated tables have merged/rotated
  header cells that defeat pdfplumber's table finder, but the *data* rows are
  reliably single-line and ISIN-anchored.
* Hindi (Devanagari) duplicate lines are stripped — every label in a CAS is
  printed twice, once per language.
* Section state is tracked via "HOLDING STATEMENT OF <X>" banners so the same
  ISIN row shape can be attributed to equities vs bonds vs G-secs.
* Unknown/garbled rows are collected into `warnings` instead of raising, so one
  malformed line never loses the other 300 holdings.
"""

from __future__ import annotations

import io
import re
from typing import Any

# Devanagari block — CAS prints every header twice (English + Hindi).
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# ISIN: 2 letters (country) + 9 alphanumerics + 1 check digit.
_ISIN = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")

_PAN_RE = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b")

# A number like 1,23,456.78 or 15.000 or 363.8500
_NUM = r"(?:\d{1,3}(?:,\d{2,3})*|\d+)(?:\.\d+)?"
_NUM_RE = re.compile(_NUM)

_DATE_RE = re.compile(r"\b(\d{2}-\d{2}-\d{4})\b")
_DDMMYYYY = re.compile(r"\b(\d{2})-(\d{2})-(\d{4})\b")

# "HOLDING STATEMENT OF BONDS AS ON 30-06-2026"
_SECTION_RE = re.compile(r"HOLDING STATEMENT OF ([A-Z0-9 /&,\-\.]+?)\s+AS ON", re.I)

# "Portfolio Value for Bond ` 4,91,500.00 as on 30-06-2026"
_PORTFOLIO_VAL_RE = re.compile(
    r"Portfolio Value(?:\s+for\s+([A-Za-z ]+?))?\s*[`₹]?\s*(" + _NUM + r")",
    re.I,
)

_SUMMARY_ROW_RE = re.compile(
    r"(CDSL Demat Accounts|NSDL Demat Accounts|Mutual Fund Folios|Total Portfolio Value)"
    r"\s*[`₹]?\s*(" + _NUM + r")",
    re.I,
)

# Coupon column in the bonds table, e.g. "11.65 17052031" or "7.5"
_COUPON_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,4})?)\b")

_SECTION_KINDS = {
    "EQUITIES": "equity",
    "EQUITY SHARES": "equity",
    "BONDS": "bond",
    "DEBENTURES": "bond",
    "CORPORATE BONDS": "bond",
    "GOVERNMENT SECURITIES": "gsec",
    "GOVT SECURITIES": "gsec",
    "MUTUAL FUND UNITS": "mf",
    "MUTUAL FUNDS": "mf",
    "PREFERENCE SHARES": "preference",
    "MONEY MARKET INSTRUMENTS": "mmi",
    "SECURITISED INSTRUMENTS": "other",
    "POST OFFICE SCHEMES": "other",
}


class CasPasswordError(Exception):
    """Wrong or missing PDF password (for a CAS this is the PAN)."""


class CasParseError(Exception):
    """The PDF opened but does not look like a CAS."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _num(raw: str | None) -> float | None:
    """'1,23,456.78' -> 123456.78 ; '--' / '' / None -> None."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "").replace("₹", "").replace("`", "")
    if s in ("", "--", "-", "N.A.", "NA", "nil", "Nil"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _iso_date(raw: str | None) -> str | None:
    """'17-05-2031' -> '2031-05-17'. Also accepts '17052031'."""
    if not raw:
        return None
    s = str(raw).strip()
    m = _DDMMYYYY.search(s)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{mo}-{d}"
    if re.fullmatch(r"\d{8}", s):  # DDMMYYYY, as used inside bond names
        d, mo, y = s[0:2], s[2:4], s[4:8]
        if 1 <= int(mo) <= 12 and 1 <= int(d) <= 31:
            return f"{y}-{mo}-{d}"
    return None


def _strip_hindi(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln.strip() and not _DEVANAGARI.search(ln)]


def _clean_name(raw: str) -> str:
    """Collapse whitespace and trim CAS's '#' separators."""
    s = re.sub(r"\s+", " ", raw or "").strip()
    s = s.strip("#").strip()
    return re.sub(r"\s*#\s*", " ", s).strip()


# Security-type code lives at ISIN[7:9] (e.g. INE101Q07CA7 -> "07").
# Verified against this CAS's own asset-class summary, which these buckets
# reproduce to the rupee.
_ISIN_TYPE_MAP = {
    "01": "equity",
    "04": "preference",
    "07": "debt",
    "08": "debt",
    "25": "other",  # REITs / InvITs — the statement's "Others" class
}


def _classify_isin(isin: str, name: str = "") -> str:
    """
    Classify a holding from its ISIN, which encodes the instrument type:

      INF...      -> mutual fund / ETF unit (AMC-issued)
      ISIN[7:9]   -> security-type code: 01 equity, 04 preference,
                     07/08 debt, 25 REIT/InvIT ("Others")

    This reproduces the CAS's own asset-class summary, so imported rows land
    on the right page. Name-based hints are only a fallback for ISINs whose
    type code is unrecognised.
    """
    s = (isin or "").upper()
    if s.startswith("INF"):
        return "mf"
    if len(s) >= 9:
        kind = _ISIN_TYPE_MAP.get(s[7:9])
        if kind:
            return kind
    if re.search(r"\b(NCD|BOND|DEBENTURE|SLR|G-?SEC)\b", name or "", re.I):
        return "debt"
    if re.search(r"\b(ETF|MUTUAL FUND|MF-|LIQUID|NIFTY|BEES)\b", name or "", re.I):
        return "mf"
    return "equity"


def _coupon_freq(raw: str) -> str | None:
    """Map CAS frequency wording onto the app's coupon_freq vocabulary."""
    s = (raw or "").lower()
    if "month" in s:
        return "monthly"
    if "quarter" in s:
        return "quarterly"
    if "half" in s or "semi" in s:
        return "half_yearly"
    if "annual" in s or "yearly" in s:
        return "annual"
    if "cumul" in s:
        return "cumulative"
    if "zero" in s:
        return "zero"
    return None


# ---------------------------------------------------------------------------
# PDF opening
# ---------------------------------------------------------------------------
# --- coordinate-based row assembly -----------------------------------------
# CAS holdings tables are strictly columnar. Reading them from word positions
# instead of text lines is what makes the parse reliable: the security name
# wraps onto its own rows in the name column, and pdfplumber's line-joining
# otherwise interleaves a holding's name with its neighbour's.
_NAME_COL_X0 = 90.0    # names start ~101; ISIN sits at ~21
_NAME_COL_X1 = 265.0   # first numeric column starts ~265
_ROW_BAND = 5.0        # cells within this many pts share the row's baseline
# Debt descriptions wrap over 3-4 text lines (~4.5pt apart), so the name search
# reaches further than the row band — but is always clamped to the midpoint
# between neighbouring rows so names can't bleed between holdings.
_NAME_LOOKUP = 16.0

# A *holdings* row carries a unit-price cell (470 ≤ x0 < 528) AND a market-value
# cell (x0 ≥ 528), and no date. A *transaction* row carries a date around
# x0≈301 and no price cell — its trailing numbers are balance columns. This
# structural difference, not the page number, separates the holding statement
# from the movement ledger, and it holds across all sections and row shapes
# (equity, ETF, NSDL debt, and the bond table).
_PRICE_COL_MIN = 470.0
_PRICE_COL_MAX = 528.0

# The market-value column is RIGHT-aligned (right edge ~x1≈580), so its x0
# slides with digit count: 12 digits → 531.0, 1 digit → 573.3. Nothing else
# numeric ever appears at x0 ≥ 528 on a holdings row, so a lower bound is a
# safe way to target the value regardless of magnitude.
_VALUE_COL_MIN = 528.0

# pdfplumber sometimes emits the unit price and the market value as a single
# word ("114.770017,84,673.50" = ₹114.7700 + ₹17,84,673.50). Left unsplit the
# token is not a number and the whole holding is silently dropped, so this
# split is mandatory, not cosmetic.
_GLUED_PRICE_VALUE = re.compile(r"^(\d+\.\d{4})([\d,]+\.\d\d)$")

# The constants above are measured from CDSL's current A4 layout. Rather than
# trust them, `calibrate_layout()` re-derives the column boundaries from each
# PDF's own geometry, so a different page size, margin or font scale still
# parses. The constants remain only as a fallback when calibration can't find
# enough structure to measure.
_LAYOUT_DEFAULT = {
    "name_x0": _NAME_COL_X0,
    "name_x1": _NAME_COL_X1,
    "price_min": _PRICE_COL_MIN,
    "price_max": _PRICE_COL_MAX,
    "value_min": _VALUE_COL_MIN,
}


def calibrate_layout(pages_words: list[list[dict]]) -> dict[str, float]:
    """
    Measure the holdings-table column boundaries from the document itself.

    Anchor: rows whose first token is an ISIN. On such a row the right-most
    numeric cell is the market value and the one before it is the unit price, so
    the gap between those two columns gives a real split point — whatever the
    page width. Everything is expressed relative to observed positions, so a
    rescaled or re-margined CAS calibrates to its own numbers.

    Falls back to the measured-from-A4 defaults if too few rows are found to
    measure confidently.
    """
    isin_x: list[float] = []
    numeric_x: list[float] = []
    rightmost: list[float] = []

    for words in pages_words:
        if not words:
            continue
        by_y: dict[float, list[dict]] = {}
        for w in words:
            by_y.setdefault(round(w["top"], 1), []).append(w)
        for y, row in by_y.items():
            row = sorted(row, key=lambda w: w["x0"])
            first = row[0]
            if not _ISIN.fullmatch(first["text"].strip()):
                continue
            nums = [
                w for w in row[1:]
                if _num(w["text"]) is not None and _NUM_RE.fullmatch(
                    w["text"].replace(",", "")
                )
            ]
            if len(nums) < 3:
                continue
            isin_x.append(first["x0"])
            rightmost.append(nums[-1]["x0"])
            numeric_x.extend(w["x0"] for w in nums)

    if len(rightmost) < 20:
        return dict(_LAYOUT_DEFAULT)

    rightmost.sort()
    numeric_x.sort()

    # Value column: right-aligned, so take a little below its smallest observed
    # x0 (long numbers start furthest left).
    value_min = rightmost[0] - 3.0

    # Price column: the widest gap among numeric starts that sits left of the
    # value column marks the price/value boundary.
    left_of_value = [x for x in numeric_x if x < value_min - 1]
    if not left_of_value:
        return dict(_LAYOUT_DEFAULT)
    price_max = value_min
    price_min = max(left_of_value[0], min(left_of_value[-1] - 60.0, value_min - 60.0))

    # Name column sits between the ISIN token and the first numeric column.
    first_num = left_of_value[0] if left_of_value else _NAME_COL_X1
    name_x0 = (min(isin_x) + first_num) / 2 if isin_x else _NAME_COL_X0
    name_x0 = min(name_x0, first_num - 20.0)
    name_x1 = first_num - 1.0

    return {
        "name_x0": name_x0,
        "name_x1": name_x1,
        "price_min": price_min,
        "price_max": price_max,
        "value_min": value_min,
    }


def _rows_from_words(
    words: list[dict], layout: dict[str, float] | None = None
) -> list[dict[str, Any]]:
    """
    Group a page's words into holdings rows keyed on the ISIN token, pulling
    the security name from the name column within the row's vertical band.
    """
    lay = layout or _LAYOUT_DEFAULT
    name_x0, name_x1 = lay["name_x0"], lay["name_x1"]
    price_min, price_max = lay["price_min"], lay["price_max"]
    value_min = lay["value_min"]

    isin_words = [
        w for w in words if w["x0"] < name_x0 and _ISIN.fullmatch(w["text"].strip())
    ]
    if not isin_words:
        return []
    isin_words.sort(key=lambda w: w["top"])

    name_words = [
        w for w in words if name_x0 <= w["x0"] < name_x1
    ]

    rows: list[dict[str, Any]] = []
    for iw in isin_words:
        y = iw["top"]
        # numeric/data cells on the same baseline
        band = [w for w in words if abs(w["top"] - y) < _ROW_BAND and w["x0"] >= name_x1]
        band.sort(key=lambda w: w["x0"])
        # CAS sometimes emits an adjacent "--" glued to the next figure
        # ("--3540.000"); split those so column counting stays correct.
        # Normalise cells into (x0, text) pairs, splitting the two glue cases:
        #   "--3540.000"           -> "--", "3540.000"
        #   "114.770017,84,673.50" -> price, value  (value keeps the value x0)
        cells_xy: list[tuple[float, str]] = []
        for w in band:
            txt = w["text"].strip()
            g = _GLUED_PRICE_VALUE.match(txt)
            if g:
                cells_xy.append((w["x0"], g.group(1)))
                # the value half belongs in the value column
                cells_xy.append((max(value_min, w["x0"]), g.group(2)))
                continue
            for part in re.split(r"(?<=--)(?=\d)|(?<=\d)(?=--)", txt):
                part = part.strip()
                if part:
                    cells_xy.append((w["x0"], part))
        cells = [t for _, t in cells_xy]

        # Reject movement-ledger rows: they date-stamp the transaction and have
        # no unit-price column.
        # Test dates against the RAW words, never the split cells — splitting
        # can break a date apart and let a ledger row through.
        has_date = any(_DATE_RE.search(w["text"]) for w in band)
        has_price = any(
            price_min <= x < price_max and _num(t) is not None
            for x, t in cells_xy
        )
        value = next(
            (
                _num(t)
                for x, t in sorted(cells_xy, key=lambda c: -c[0])
                if x >= value_min and _num(t) is not None
            ),
            None,
        )
        if has_date or not has_price or value is None:
            continue

        # Name fragments sit in the name column across several text lines
        # around the data row (debt descriptions run 3-4 lines). Claim the
        # fragments nearest this row but never past the neighbouring rows, so
        # two adjacent holdings don't swap name pieces.
        prev_y = max((w["top"] for w in isin_words if w["top"] < y - 1), default=None)
        next_y = min((w["top"] for w in isin_words if w["top"] > y + 1), default=None)
        lo = y - _NAME_LOOKUP if prev_y is None else max(y - _NAME_LOOKUP, (prev_y + y) / 2)
        hi = y + _NAME_LOOKUP if next_y is None else min(y + _NAME_LOOKUP, (next_y + y) / 2)

        frags = [w for w in name_words if lo <= w["top"] <= hi]
        frags.sort(key=lambda w: (round(w["top"], 1), w["x0"]))
        name = _clean_name(" ".join(w["text"] for w in frags))

        price = next(
            (
                _num(t)
                for x, t in sorted(cells_xy, key=lambda c: -c[0])
                if price_min <= x < price_max and _num(t) is not None
            ),
            None,
        )
        rows.append(
            {
                "isin": iw["text"].strip(),
                "name": name,
                "cells": cells,
                "value": value,
                "price": price,
                "top": y,
            }
        )
    return rows


def _row_quantity(row: dict[str, Any]) -> float | None:
    """
    Balance quantity for a holdings row: the last numeric cell before the
    price column. Row shapes differ by instrument (equity rows carry
    current/frozen/pledge/market-pledge/balance; NSDL debt rows carry only
    quantity), so counting backwards from the price is what works for both.
    """
    price, value = row.get("price"), row.get("value")
    nums = [_num(c) for c in row.get("cells") or []]
    nums = [v for v in nums if v is not None]
    # drop the trailing price/value we already resolved by column
    while nums and nums[-1] in (value, price):
        nums.pop()
    return nums[-1] if nums else None


def _open_pdf(pdf_bytes: bytes, password: str):
    """Open with pdfplumber, trying sensible PAN case variants."""
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover
        raise CasParseError(
            "pdfplumber is not installed — add pdfplumber to requirements.txt"
        ) from e

    pw = (password or "").strip()
    # CAS passwords are the PAN in caps; try caps first, then as-typed/lower.
    variants = [pw.upper(), pw, pw.lower()]
    seen: set[str] = set()
    last: Exception | None = None
    for cand in variants:
        if cand in seen:
            continue
        seen.add(cand)
        try:
            return pdfplumber.open(io.BytesIO(pdf_bytes), password=cand)
        except Exception as e:  # pdfminer raises a wrapped PDFPasswordIncorrect
            last = e
    raise CasPasswordError(
        "Could not open the PDF with that PAN. For a CDSL/NSDL CAS the password "
        "is the holder's 10-character PAN (e.g. ABCDE1234F)."
    ) from last


# ---------------------------------------------------------------------------
# row parsers
# ---------------------------------------------------------------------------
# A holdings row's numeric tail, e.g. "5.000 -- -- -- 5.000 182.1000 910.50":
#   current | frozen | pledge | market-pledge | balance | price | value
# The four middle slots are usually '--' but can carry numbers, so we accept
# either and key off the *shape*: >=3 numbers ending in a 2-decimal money value.
_HOLDING_TAIL_RE = re.compile(
    r"(?P<qty>" + _NUM + r")\s+"
    r"(?P<mid>(?:(?:--|-|" + _NUM + r")\s+){2,5})"
    r"(?P<bal>" + _NUM + r")\s+"
    r"(?P<price>" + _NUM + r")\s+"
    r"(?P<value>" + _NUM + r")\s*$"
)


def _parse_security_row(line: str, isin: str) -> dict[str, Any] | None:
    """
    Equity / preference holding row. The ISIN and the numeric tail sit on one
    line; the security name may be split across this line and its neighbours:

        BALMER LAWRIE & COMPANY                 <- name (line above)
        INE164A01016 5.000 -- -- -- 5.000 182.1000 910.50
        LIMITED EQUITY SHARES                   <- name cont. (line below)

    Returns the row with whatever name fragment is on *this* line; the caller
    stitches in the neighbouring fragments.
    """
    after = line.split(isin, 1)[1] if isin in line else line

    m = _HOLDING_TAIL_RE.search(after)
    if not m:
        return None

    value = _num(m.group("value"))
    price = _num(m.group("price"))
    qty = _num(m.group("bal"))
    if value is None or price is None or qty is None:
        return None

    # Reject transaction-ledger rows: those carry a date and zero balances.
    if _DATE_RE.search(after[: m.start()]):
        return None

    inline_name = _clean_name(after[: m.start()])

    return {
        "isin": isin,
        "name": inline_name,
        "quantity": qty,
        "price": price,
        "value": value,
    }


def _is_name_fragment(line: str) -> bool:
    """
    True if `line` looks like a wrapped piece of a security name rather than
    another row, a header, or page furniture.
    """
    s = (line or "").strip()
    if not s or len(s) <= 1:
        return False
    if _ISIN.search(s):
        return False
    if _DATE_RE.search(s):
        return False
    if re.match(r"^Page \d+ of \d+", s, re.I):
        return False
    if _PORTFOLIO_VAL_RE.search(s) or _SECTION_RE.search(s):
        return False
    # Rotated single-character header cells ("I", "S", "N", "al", "ty", ...)
    letters = re.sub(r"[^A-Za-z]", "", s)
    if len(letters) < 3:
        return False
    # Mostly-numeric lines are data, not names
    digits = sum(c.isdigit() for c in s)
    if digits > len(s) * 0.4:
        return False
    # Known header/footer vocabulary
    if re.search(
        r"\b(ISIN|Security|Balance|Description|Lockin|Pledge|Frozen|Current|Market|"
        r"Face Value|Coupon|Maturity|Quantity|Value|Stamp|Transaction|Note)\b",
        s,
        re.I,
    ):
        return False
    return True


def _stitch_name(
    lines: list[str], idx: int, inline: str, row_lines: set[int]
) -> str:
    """
    Rebuild a security name split across the lines above/below its data row:

        BALMER LAWRIE & COMPANY          <- above
        INE164A01016 5.000 ... 910.50    <- data row (idx), maybe inline name
        LIMITED EQUITY SHARES            <- below

    CAS wraps names both ways. `row_lines` holds the indices of every data row
    on the page, so a fragment is only claimed if no *other* data row sits
    between it and this one — otherwise adjacent holdings steal each other's
    names.
    """
    below: list[str] = []
    j = idx + 1
    while j < len(lines) and j not in row_lines and _is_name_fragment(lines[j]):
        below.append(lines[j].strip())
        j += 1

    # Look upward only as far as the previous data row, and skip fragments that
    # the previous row will already have claimed as its own trailing wrap.
    prev_row = max((r for r in row_lines if r < idx), default=-1)
    above: list[str] = []
    i = idx - 1
    while i > prev_row and _is_name_fragment(lines[i]):
        above.append(lines[i].strip())
        i -= 1
    above.reverse()

    if prev_row >= 0 and above:
        # fragments directly under the previous row belong to it
        k = prev_row + 1
        claimed = 0
        while k < idx and _is_name_fragment(lines[k]):
            claimed += 1
            k += 1
        above = above[claimed:]

    parts = above + ([inline] if inline else []) + below
    return _clean_name(" ".join(p for p in parts if p))


def _parse_bond_row(line: str, isin: str) -> dict[str, Any] | None:
    """
    Bond holding row:
      INE583D08131 UCL 11.65 17052031 11.65 17052031 5.00 1,00,000.00 98,300.00 4,91,500.00
                   ^name/desc          coupon maturity qty face        mkt/bond  value

    Layout: ISIN | ISIN Name | Coupon/Rate/Frequency | Maturity Date |
            Quantity | Face Value per Bond | Market Value per Bond | Value
    The name often embeds the coupon and DDMMYYYY maturity, so the columns
    repeat; we read the *trailing* four numbers as qty/face/mkt/value and pull
    coupon + maturity from the remaining middle text.
    """
    after = line.split(isin, 1)[1] if isin in line else line
    tokens = after.split()

    nums: list[tuple[int, float]] = []
    for idx, tok in enumerate(tokens):
        v = _num(tok)
        if v is not None and _NUM_RE.fullmatch(tok.replace(",", "")):
            nums.append((idx, v))
    if len(nums) < 4:
        return None

    value = nums[-1][1]
    mkt_per_bond = nums[-2][1]
    face_value = nums[-3][1]
    qty = nums[-4][1]

    head_tokens = tokens[: nums[-4][0]]
    head = " ".join(head_tokens)

    # maturity: a DD-MM-YYYY or a bare DDMMYYYY inside the description
    maturity = None
    m = _DATE_RE.search(head)
    if m:
        maturity = _iso_date(m.group(1))
    else:
        for tok in head_tokens:
            if re.fullmatch(r"\d{8}", tok):
                maturity = _iso_date(tok)
                if maturity:
                    break

    # coupon: first small decimal in the description (e.g. 11.65)
    coupon = None
    for tok in head_tokens:
        cm = _COUPON_RE.match(tok)
        if cm and re.fullmatch(r"\d{1,2}(?:\.\d{1,4})?", tok):
            cand = float(cm.group(1))
            if 0 < cand <= 30:  # plausible coupon %
                coupon = cand
                break

    name = _clean_name(head)

    return {
        "isin": isin,
        "name": name,
        "quantity": qty,
        "face_value": face_value,
        "price": mkt_per_bond,
        "value": value,
        "coupon_rate": coupon,
        "coupon_freq": _coupon_freq(head),
        "maturity_date": maturity,
    }


# ---------------------------------------------------------------------------
# account / investor extraction
# ---------------------------------------------------------------------------
_DP_RE = re.compile(r"DP\s*Id\s*:?\s*([A-Z0-9]+)\s*Client\s*Id\s*:?\s*(\d+)", re.I)
_ACCT_TYPE_RE = re.compile(r"(CDSL|NSDL)\s+Demat Account", re.I)


def _extract_accounts(pages_text: list[str]) -> list[dict[str, Any]]:
    """Pull demat account identities from the summary pages."""
    accounts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text in pages_text[:6]:
        lines = _strip_hindi(text)
        for i, line in enumerate(lines):
            m = _DP_RE.search(line)
            if not m:
                continue
            dp_id, client_id = m.group(1), m.group(2)
            key = f"{dp_id}:{client_id}"
            if key in seen:
                continue
            seen.add(key)

            window = " ".join(lines[max(0, i - 4) : i + 2])
            dep = _ACCT_TYPE_RE.search(window)
            # broker name: the nearest preceding line that is mostly caps words
            broker = None
            for back in range(i - 1, max(-1, i - 6), -1):
                cand = lines[back].strip()
                if _DP_RE.search(cand) or _ACCT_TYPE_RE.search(cand):
                    continue
                letters = re.sub(r"[^A-Za-z]", "", cand)
                if len(letters) >= 6 and cand.upper() == cand:
                    broker = _clean_name(cand)
                    break
            accounts.append(
                {
                    "depository": (dep.group(1).upper() if dep else None),
                    "dp_id": dp_id,
                    "client_id": client_id,
                    "broker": broker,
                    "account_label": f"{broker or dep.group(1) if dep else 'Demat'} "
                    f"{client_id}".strip(),
                }
            )
    return accounts


def _extract_investor(pages_text: list[str]) -> dict[str, Any]:
    joined = "\n".join(_strip_hindi("\n".join(pages_text[:5])))
    pan = None
    pm = _PAN_RE.search(joined)
    if pm:
        pan = pm.group(1)

    cas_id = None
    cm = re.search(r"CAS ID\s*:?\s*([A-Z0-9]+)", joined, re.I)
    if cm:
        cas_id = cm.group(1)

    period_from = period_to = None
    pmatch = re.search(
        r"PERIOD FROM\s+(\d{2}-\d{2}-\d{4})\s+TO\s+(\d{2}-\d{2}-\d{4})", joined, re.I
    )
    if pmatch:
        period_from = _iso_date(pmatch.group(1))
        period_to = _iso_date(pmatch.group(2))

    name = None
    if pm:
        # the holder name usually sits just before "( PAN :XXXXX )"
        before = joined[: pm.start()].strip().split("\n")
        for cand in reversed(before[-6:]):
            c = re.sub(r"\(\s*PAN.*$", "", cand).strip()
            letters = re.sub(r"[^A-Za-z]", "", c)
            if len(letters) >= 5 and c.upper() == c and "CONSOLIDATED" not in c:
                name = _clean_name(c)
                break

    return {
        "name": name,
        "pan": pan,
        "cas_id": cas_id,
        "period_from": period_from,
        "period_to": period_to,
    }


_CLASS_ROW_RE = re.compile(
    r"(Mutual Funds Held in Demat Form|Equity|Debts?|Preference Shares|"
    r"Government Securities|Mutual Fund Folios|Others?)\s+(" + _NUM + r")",
    re.I,
)


def _extract_class_summary(pages_text: list[str]) -> dict[str, float]:
    """
    The CAS carries an asset-class summary ("Equity 6,53,072.54 12.50",
    "Mutual Funds Held in Demat Form 27,71,905.54 53.06", "Debts ..."). It is
    the authority for what each class *should* total, so it drives the
    reconciliation warnings.
    """
    out: dict[str, float] = {}
    for text in pages_text[:8]:
        for line in _strip_hindi(text):
            m = _CLASS_ROW_RE.search(line)
            if not m:
                continue
            label = m.group(1).lower()
            val = _num(m.group(2))
            if val is None:
                continue
            if "demat form" in label:
                out.setdefault("mf", val)
            elif label.startswith("debt"):
                out.setdefault("debt", val)
            elif label.startswith("equity"):
                out.setdefault("equity", val)
            elif "preference" in label:
                out.setdefault("preference", val)
            elif "government" in label:
                out.setdefault("gsec", val)
            elif "folios" in label:
                out.setdefault("mf_folios", val)
            elif label.startswith("other"):
                # "Others" = REITs / InvITs (ISIN type code 25)
                out.setdefault("other", val)
    return out


def _extract_totals(pages_text: list[str]) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    head = "\n".join(_strip_hindi("\n".join(pages_text[:4])))
    for m in _SUMMARY_ROW_RE.finditer(head):
        label = m.group(1).lower()
        val = _num(m.group(2))
        if "cdsl" in label:
            totals["cdsl_demat"] = val
        elif "nsdl" in label:
            totals["nsdl_demat"] = val
        elif "folios" in label:
            totals["mutual_funds"] = val
        elif "total" in label:
            totals["total_portfolio"] = val
    return totals


# ---------------------------------------------------------------------------
# mutual funds
# ---------------------------------------------------------------------------
# The MF section of a CAS has TWO tables per folio:
#   1. a transaction ledger (dates, redemptions, NAV per transaction)
#   2. a holdings SUMMARY row — the one that actually states your position:
#        Scheme Name | ISIN | Folio No. | Units | NAV | Invested | Valuation | P/L | P/L%
# Only (2) describes what you hold, so that is what we read. Scraping (1) yields
# one bogus "scheme" per redemption line, which is exactly the bug this replaces.
#
# Summary-row column positions (x0), stable across CAS output:
_MF_COL = {
    "isin":     (100.0, 175.0),
    "folio":    (175.0, 255.0),
    "units":    (255.0, 295.0),
    "nav":      (295.0, 360.0),
    "invested": (360.0, 430.0),
    "value":    (430.0, 500.0),
    "pnl":      (500.0, 545.0),
    "pnl_pct":  (545.0, 600.0),
}
_MF_NAME_X1 = 100.0          # scheme name occupies x0 < 100
_MF_ROW_BAND = 6.0
_MF_NAME_LOOKUP = 14.0       # scheme names wrap over ~3 lines

_MF_AMC_RE = re.compile(r"(Mutual Fund|Asset Management|AMC)\s*$", re.I)
_MF_SCHEME_RE = re.compile(r"^(\d{2,6})\s*-\s*(.+)$")


def _mf_cell(words: list[dict], lo: float, hi: float) -> str | None:
    """First word whose x0 falls in [lo, hi)."""
    for w in sorted(words, key=lambda x: x["x0"]):
        if lo <= w["x0"] < hi:
            return w["text"].strip()
    return None


def _extract_mutual_funds(
    pages_text: list[str], pages_words: list[list[dict]] | None = None
) -> list[dict[str, Any]]:
    """
    Read each folio's holdings-summary row: units, NAV, invested cost, current
    value and P&L.

    Note this is richer than the demat side of a CAS: for MF folios the
    statement DOES report invested cost, so real gain/loss is available without
    a tradebook.
    """
    funds: list[dict[str, Any]] = []
    if not pages_words:
        return funds

    for pno, words in enumerate(pages_words):
        if not words:
            continue

        # An MF summary row = an ISIN in the ISIN column plus a numeric value
        # in the valuation column.
        for w in words:
            if not (_MF_COL["isin"][0] <= w["x0"] < _MF_COL["isin"][1]):
                continue
            isin = w["text"].strip().upper()
            if not _ISIN.fullmatch(isin):
                continue

            y = w["top"]
            band = [x for x in words if abs(x["top"] - y) < _MF_ROW_BAND]
            value = _num(_mf_cell(band, *_MF_COL["value"]))
            units = _num(_mf_cell(band, *_MF_COL["units"]))
            if value is None or units is None:
                continue  # not the summary row (e.g. the "ISIN : ..." header)

            # scheme name: the name column around this row (wraps over lines)
            frags = [
                x for x in words
                if x["x0"] < _MF_NAME_X1 and abs(x["top"] - y) <= _MF_NAME_LOOKUP
            ]
            frags.sort(key=lambda x: (round(x["top"], 1), x["x0"]))
            scheme = _clean_name(" ".join(x["text"] for x in frags))

            # scheme code + AMC come from the ledger header above this table
            code = None
            cm = _MF_SCHEME_RE.match(scheme)
            if cm:
                code, scheme = cm.group(1), _clean_name(cm.group(2))

            amc = None
            for line in _strip_hindi(pages_text[pno] if pno < len(pages_text) else ""):
                s = line.strip()
                if _MF_AMC_RE.search(s) and not _MF_SCHEME_RE.match(s):
                    amc = _clean_name(s)
                    break
                m2 = _MF_SCHEME_RE.match(s)
                if m2 and not scheme:
                    code = code or m2.group(1)

            invested = _num(_mf_cell(band, *_MF_COL["invested"]))
            funds.append({
                "amc": amc,
                "scheme_code": code,
                "scheme": scheme or None,
                "isin": isin,
                "folio": _mf_cell(band, *_MF_COL["folio"]),
                "units": units,
                "nav": _num(_mf_cell(band, *_MF_COL["nav"])),
                "invested": invested,
                "value": value,
                "pnl": _num(_mf_cell(band, *_MF_COL["pnl"])),
                "pnl_pct": _num(_mf_cell(band, *_MF_COL["pnl_pct"])),
                "page": pno + 1,
            })

    # one row per (isin, folio)
    merged: dict[tuple, dict[str, Any]] = {}
    for f in funds:
        merged.setdefault((f.get("isin"), f.get("folio")), f)
    return list(merged.values())


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------
def parse_cas(pdf_bytes: bytes, pan: str) -> dict[str, Any]:
    """
    Parse a CDSL/NSDL eCAS PDF. `pan` is the PDF password.

    Raises CasPasswordError on a bad password, CasParseError if the document
    isn't a CAS. Never raises for individual malformed rows — those land in
    the returned `warnings` list.
    """
    if not pdf_bytes:
        raise CasParseError("Empty file.")

    warnings: list[str] = []

    with _open_pdf(pdf_bytes, pan) as pdf:
        pages_text: list[str] = []
        pages_words: list[list[dict]] = []
        for pno, page in enumerate(pdf.pages, start=1):
            try:
                pages_text.append(page.extract_text() or "")
            except Exception as e:
                pages_text.append("")
                warnings.append(f"page {pno}: text extraction failed ({e})")
            try:
                pages_words.append(page.extract_words(keep_blank_chars=False))
            except Exception as e:
                pages_words.append([])
                warnings.append(f"page {pno}: word extraction failed ({e})")

    joined_upper = "\n".join(pages_text).upper()
    if "CONSOLIDATED ACCOUNT STATEMENT" not in joined_upper and "CAS ID" not in joined_upper:
        raise CasParseError(
            "This PDF does not look like a CDSL/NSDL Consolidated Account "
            "Statement (CAS). A bank account statement will not work here."
        )

    equities: list[dict[str, Any]] = []
    bonds: list[dict[str, Any]] = []
    gsecs: list[dict[str, Any]] = []
    demat_funds: list[dict[str, Any]] = []  # ETFs / MF units held in demat
    section_values: dict[str, float] = {}

    # Holdings rows are recognised by SHAPE (ISIN + "qty -- -- -- bal price
    # value"), not by a section banner: in real CAS output the equity holdings
    # table has no "HOLDING STATEMENT OF ..." header at all — only bonds and
    # G-secs do. Banners, when present, still switch the active section so a
    # bond row gets its coupon/maturity columns read.
    # Each demat account's holdings end with a bare "Portfolio Value ` X" line.
    # Walk pages in order, attributing rows to the current account, and close
    # the account when its valuation line appears.
    # Measure this document's own column geometry before reading any row, so a
    # different page size / margin / font scale parses without code changes.
    layout = calibrate_layout(pages_words)
    # Only mention calibration when the document's geometry is materially
    # different from the common CDSL layout — otherwise it is just noise.
    if abs(layout["value_min"] - _LAYOUT_DEFAULT["value_min"]) > 25:
        warnings.append(
            "This CAS uses a different page layout; columns were auto-calibrated "
            f"(value column at x≥{layout['value_min']:.0f}). Totals were still "
            "reconciled against the statement."
        )

    account_seq = [a for a in _extract_accounts(pages_text)]
    acct_idx = 0
    stated_per_account: list[float] = []

    for pno, text in enumerate(pages_text, start=1):
        if not text:
            continue
        words = pages_words[pno - 1] if pno - 1 < len(pages_words) else []
        cur_acct = account_seq[acct_idx] if acct_idx < len(account_seq) else None

        # Vertical positions of this page's section banners / valuation lines,
        # so a row can be classified by where it sits on the page.
        bond_bands: list[float] = []
        gsec_bands: list[float] = []

        # Derive banner + valuation y-positions from word groups
        by_line: dict[float, list[dict]] = {}
        for w in words:
            by_line.setdefault(round(w["top"], 1), []).append(w)
        for y in sorted(by_line):
            ltxt = " ".join(
                x["text"] for x in sorted(by_line[y], key=lambda w: w["x0"])
            )
            if _DEVANAGARI.search(ltxt):
                continue
            sm = _SECTION_RE.search(ltxt)
            if sm:
                raw = re.sub(r"\s+", " ", sm.group(1).strip().upper())
                kind = _SECTION_KINDS.get(raw)
                if kind is None:
                    for key, val in _SECTION_KINDS.items():
                        if key in raw:
                            kind = val
                            break
                if kind == "bond":
                    bond_bands.append(y)
                elif kind == "gsec":
                    gsec_bands.append(y)
                continue

            pv = _PORTFOLIO_VAL_RE.search(ltxt)
            if pv:
                label = (pv.group(1) or "").strip().lower()
                val = _num(pv.group(2))
                if val is None:
                    continue
                if "bond" in label:
                    section_values["bond"] = section_values.get("bond", 0.0) + val
                elif "govern" in label or "gsec" in label:
                    section_values["gsec"] = section_values.get("gsec", 0.0) + val
                else:
                    # An unlabelled "Portfolio Value" closes one demat account.
                    stated_per_account.append(val)
                    section_values["equity"] = section_values.get("equity", 0.0) + val
                    acct_idx += 1

        for row in _rows_from_words(words, layout):
            y = row["top"]
            in_bond = any(y > b for b in bond_bands)
            in_gsec = any(y > b for b in gsec_bands)

            if in_bond or in_gsec:
                line = f"{row['isin']} {row['name']} " + " ".join(row["cells"])
                parsed = _parse_bond_row(line, row["isin"])
                if parsed:
                    parsed["account"] = cur_acct
                    (bonds if in_bond else gsecs).append(parsed)
                continue

            value = row.get("value")
            price = row.get("price")
            qty = _row_quantity(row)
            if value is None:
                continue
            kind = _classify_isin(row["isin"], row["name"])
            rec = {
                "isin": row["isin"],
                "name": row["name"],
                "quantity": qty,
                "price": price,
                "value": value,
                "category": kind,
                "account": cur_acct,
                "page": pno,
            }
            if kind in ("mf", "preference", "other"):
                # Preference shares and REITs/InvITs are tiny here but must
                # still be counted somewhere; they ride along with the demat
                # securities bucket rather than being silently discarded.
                (demat_funds if kind == "mf" else equities).append(rec)
            elif kind == "debt":
                # A debt holding in the equity-shaped table: no coupon columns
                # here, so record what we have and let the bonds section (which
                # does carry coupon/maturity) win on merge.
                bonds.append(
                    {
                        "isin": row["isin"],
                        "name": row["name"],
                        "quantity": qty,
                        "face_value": None,
                        "price": price,
                        "value": value,
                        "coupon_rate": None,
                        "coupon_freq": None,
                        "maturity_date": None,
                        "account": cur_acct,
                    }
                )
            else:
                equities.append(rec)

    # De-dupe within a single (ISIN, account) — the same holding must not be
    # counted twice if a row is picked up on two pages. Holdings of the same
    # ISIN in *different* demat accounts are genuinely separate and kept apart,
    # which is also what stock_holdings.account_id requires.
    # Key on (page, ISIN): 27 ISINs in this statement appear in more than one
    # section/DP, and those are separate real holdings. Keying on ISIN alone
    # would silently drop value; keying per section keeps them distinct while
    # still collapsing a row read twice on one page.
    by_key: dict[tuple, dict[str, Any]] = {}
    for row in equities:
        key = (row.get("page"), row["isin"], row.get("value"))
        by_key.setdefault(key, row)
    equities = list(by_key.values())

    # Merge bond rows: the same ISIN can appear both in the equity-shaped table
    # (value only) and in the bonds table (with coupon/maturity). Prefer the
    # richer record and never double-count the value.
    # Merge only rows that are the SAME holding seen twice — the dedicated bond
    # table (which carries coupon/maturity) and the securities table (value
    # only) both list it. Match on (ISIN, value) so a genuine second holding of
    # the same ISIN at a different value stays a separate bond.
    bond_by_key: dict[tuple, dict[str, Any]] = {}
    for row in bonds:
        key = (row["isin"], row.get("value"))
        prev = bond_by_key.get(key)
        if prev is None:
            bond_by_key[key] = row
            continue
        for field in ("coupon_rate", "coupon_freq", "maturity_date", "face_value"):
            if prev.get(field) in (None, "") and row.get(field) not in (None, ""):
                prev[field] = row[field]
        if not prev.get("quantity"):
            prev["quantity"] = row.get("quantity")
        if not prev.get("price"):
            prev["price"] = row.get("price")

    # Fold in coupon/maturity from the bond table onto same-ISIN rows that
    # lacked them, without merging their values.
    enrich: dict[str, dict[str, Any]] = {}
    for row in bond_by_key.values():
        if row.get("coupon_rate") or row.get("maturity_date"):
            enrich.setdefault(row["isin"], row)
    for row in bond_by_key.values():
        src = enrich.get(row["isin"])
        if src is None or src is row:
            continue
        for field in ("coupon_rate", "coupon_freq", "maturity_date", "face_value"):
            if row.get(field) in (None, "") and src.get(field) not in (None, ""):
                row[field] = src[field]
    bonds = list(bond_by_key.values())

    investor = _extract_investor(pages_text)
    accounts = _extract_accounts(pages_text)
    totals = _extract_totals(pages_text)
    mutual_funds = _extract_mutual_funds(pages_text, pages_words)

    totals["equity_value"] = round(sum(e.get("value") or 0 for e in equities), 2)
    totals["bond_value"] = round(sum(b.get("value") or 0 for b in bonds), 2)
    totals["gsec_value"] = round(sum(g.get("value") or 0 for g in gsecs), 2)
    totals["demat_fund_value"] = round(sum(f.get("value") or 0 for f in demat_funds), 2)
    totals["equity_count"] = len(equities)
    totals["bond_count"] = len(bonds)
    totals["demat_fund_count"] = len(demat_funds)
    totals["mf_count"] = len(mutual_funds)
    if section_values:
        totals["stated_section_values"] = section_values

    # Reconciliation check against the CAS's own asset-class summary. This is
    # the parser's self-audit: if a class drifts, the import UI shows it rather
    # than silently importing a wrong portfolio.
    stated_classes = _extract_class_summary(pages_text)
    if stated_classes:
        totals["stated_class_values"] = stated_classes

        # Compare per asset class, re-deriving each row's class from its ISIN so
        # the comparison matches how the statement groups them. `equities` is a
        # mixed bucket (equity + preference + REIT/InvIT), so summing it whole
        # against the statement's "Equity" line would be wrong.
        parsed_classes: dict[str, float] = {}
        for rec in equities + demat_funds + bonds + gsecs:
            k = _classify_isin(rec.get("isin") or "", rec.get("name") or "")
            parsed_classes[k] = parsed_classes.get(k, 0.0) + (rec.get("value") or 0.0)
        totals["parsed_class_values"] = {
            k: round(v, 2) for k, v in parsed_classes.items()
        }

        labels = {
            "equity": "Equity",
            "mf": "Mutual funds held in demat",
            "debt": "Debt",
            "other": "Others",
            "preference": "Preference shares",
        }
        drift_total = 0.0
        for key, label in labels.items():
            stated = stated_classes.get(key)
            got = parsed_classes.get(key)
            if not stated or not got:
                continue
            drift = abs(stated - got)
            drift_total += drift
            if drift > max(1.0, stated * 0.02):
                warnings.append(
                    f"{label}: parsed ₹{got:,.2f} vs statement ₹{stated:,.2f} "
                    f"— review before importing."
                )

        stated_sum = sum(
            v for k, v in stated_classes.items() if k in labels
        )
        parsed_sum = sum(parsed_classes.get(k, 0.0) for k in labels)
        totals["reconciled"] = bool(stated_sum) and abs(stated_sum - parsed_sum) < 1.0
        totals["reconciliation_drift"] = round(parsed_sum - stated_sum, 2)

    return {
        "investor": investor,
        "accounts": accounts,
        "equities": equities,
        "bonds": bonds,
        "gsecs": gsecs,
        "demat_funds": demat_funds,
        "mutual_funds": mutual_funds,
        "totals": totals,
        "warnings": warnings,
    }
