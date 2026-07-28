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
* Holdings are read ROW-RELATIVE, with zero absolute coordinates (see
  `rows_from_words`). Each holdings row is anchored on its ISIN token; the
  numeric cells to its right are ordered by right edge and the last one is the
  market value. Because the money columns are right-aligned this survives any
  page width, margin, or font scale, and values of any magnitude — the failure
  modes that a fixed-pixel-column parser cannot handle.
* Instrument class is derived from the ISIN itself (`_classify_isin`), which
  reproduces the statement's own asset-class summary to the rupee, so rows are
  bucketed without depending on page position or section banners.
* Rows carrying a transaction date are transaction-ledger entries, not
  holdings, and are dropped — that is what keeps the ledger from being counted.
* Every parse is checked against the statement's own asset-class totals; any
  drift becomes a `warning` rather than a silently-wrong import.
* Hindi (Devanagari) duplicate lines are stripped — every label is printed
  twice, once per language.
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
# --- row-relative holdings extraction --------------------------------------
# Holdings rows are read with ZERO absolute coordinates. The only geometry used
# is (a) grouping tokens that share a baseline (same `top`) and (b) ordering the
# numeric cells within a row by their right edge. Because CAS money columns are
# right-aligned, the right-most numeric on an ISIN row is always the market
# value — no matter the page width, margin, font scale, or how many digits the
# value has. This is what makes the parse robust across CAS layouts; the old
# fixed-pixel approach broke on every one of those variations.

_ISIN_ANCHOR = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
_ROW_DATE = re.compile(r"^\d{2}[-/]\d{2}[-/]\d{4}$")
_FOLIO_NO = re.compile(r"^\d{3,}/\d+$")
# Indian-format number (lakh/crore grouping) or plain decimal.
_ROW_NUM = re.compile(r"^-?\d{1,3}(?:,\d{2,3})*(?:\.\d+)?$|^-?\d+(?:\.\d+)?$")

_ROW_BAND = 5.0        # cells within this many pts share the row's baseline
_NAME_LOOKUP = 16.0    # debt names wrap over ~3-4 lines around the data row


def _is_row_num(t: str) -> bool:
    return t != "--" and bool(_ROW_NUM.match(t))


def _extract_words_tight(page) -> list[dict]:
    """
    Extract words with a word-gap tolerance tuned to the page's own glyph size.

    Right-aligned columns in a CAS sit only ~3pt apart, so the default gap
    tolerance sometimes fuses a price and a value into one token
    ("114.770017,84,673.50"), silently dropping that holding's value. A smaller
    tolerance splits them — but a fixed value would be wrong on a differently
    scaled PDF, so it is derived from the median character width on the page
    (≈0.4× a char). Falls back to a safe small constant when the page is empty.
    """
    chars = page.chars or []
    if chars:
        widths = sorted(c["x1"] - c["x0"] for c in chars if c.get("x1", 0) > c.get("x0", 0))
        med = widths[len(widths) // 2] if widths else 5.0
        tol = max(1.0, min(2.5, med * 0.4))
    else:
        tol = 2.0
    return page.extract_words(keep_blank_chars=False, x_tolerance=tol)


def rows_from_words(words: list[dict]) -> list[dict[str, Any]]:
    """
    Extract holdings rows from one page's words, coordinate-free.

    For every baseline that starts with an ISIN token:
      * drop it if any token on the line is a date  -> transaction ledger, not a
        holding;
      * take the numeric tokens to the right of the ISIN, ordered by right edge;
      * the last numeric is the market value, the one before it the unit price,
        the one before that the balance quantity (folio rows are handled
        specially — their value is the 3rd-from-last numeric);
      * the security name is the non-numeric text between the ISIN and the first
        number, plus any name-column fragments on the lines just above/below
        (names wrap), clamped so two adjacent holdings can't swap fragments.

    Returns dicts: {isin, name, quantity, price, value, is_folio, top}.
    """
    by_top: dict[float, list[dict]] = {}
    for w in words:
        by_top.setdefault(round(w["top"], 1), []).append(w)

    # baselines that begin with an ISIN, in page order
    isin_tops = sorted(
        t for t, line in by_top.items()
        if any(_ISIN_ANCHOR.match(w["text"].strip()) for w in line)
    )

    rows: list[dict[str, Any]] = []
    for t in isin_tops:
        line = sorted(by_top[t], key=lambda w: w["x0"])
        iw = next(w for w in line if _ISIN_ANCHOR.match(w["text"].strip()))
        isin = iw["text"].strip()

        right = [w for w in line if w["x0"] > iw["x1"] - 1]

        # Transaction-ledger rows carry a date and no price column — skip.
        if any(_ROW_DATE.match(w["text"].strip()) for w in right):
            continue

        is_folio = any(_FOLIO_NO.match(w["text"].strip()) for w in right)

        nums = [w for w in right if _is_row_num(w["text"].strip())]
        nums.sort(key=lambda w: w["x1"])          # right-aligned: order by right edge
        if not nums:
            continue

        if is_folio:
            # Folio summary: units | NAV | invested | VALUE | gain | gain%
            # -> value is 3rd from last; needs at least 4 numerics to be real.
            if len(nums) < 4:
                continue
            value = _num(nums[-3]["text"])
            price = _num(nums[-4]["text"]) if len(nums) >= 4 else None
            qty = _num(nums[0]["text"])
        else:
            value = _num(nums[-1]["text"])
            price = _num(nums[-2]["text"]) if len(nums) >= 2 else None
            qty = _num(nums[-3]["text"]) if len(nums) >= 3 else None
        if value is None:
            continue

        name = _row_name(isin, iw, nums, by_top, isin_tops, t)

        rows.append({
            "isin": isin,
            "name": name,
            "quantity": qty,
            "price": price,
            "value": value,
            "is_folio": is_folio,
            "top": t,
        })
    return rows


def _row_name(
    isin: str,
    iw: dict,
    nums: list[dict],
    by_top: dict[float, list[dict]],
    isin_tops: list[float],
    t: float,
) -> str:
    """
    Reassemble a security name that wraps across the lines around its data row.
    Name tokens are the non-numeric words left of the first numeric column;
    they are collected from this baseline and the neighbouring text lines, but
    never past an adjacent ISIN row, so holdings can't steal each other's names.
    """
    first_num_x0 = nums[0]["x0"] if nums else 1e9

    def name_tokens(line: list[dict]) -> list[dict]:
        out = []
        for w in line:
            s = w["text"].strip()
            if not s or _DEVANAGARI.search(s):
                continue
            if _ISIN_ANCHOR.match(s) or _is_row_num(s) or s == "--":
                continue
            if w["x0"] > iw["x1"] - 1 and w["x1"] <= first_num_x0 + 1:
                out.append(w)
        return out

    # bounds: don't reach past the neighbouring ISIN rows
    prev_t = max((x for x in isin_tops if x < t - 1), default=None)
    next_t = min((x for x in isin_tops if x > t + 1), default=None)
    lo = t - _NAME_LOOKUP if prev_t is None else max(t - _NAME_LOOKUP, (prev_t + t) / 2)
    hi = t + _NAME_LOOKUP if next_t is None else min(t + _NAME_LOOKUP, (next_t + t) / 2)

    frags: list[tuple[float, float, str]] = []
    for top, line in by_top.items():
        if lo <= top <= hi:
            for w in name_tokens(line):
                frags.append((round(top, 1), w["x0"], w["text"].strip()))
    frags.sort(key=lambda f: (f[0], f[1]))
    return _clean_name(" ".join(f[2] for f in frags))


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
                pages_words.append(_extract_words_tight(page))
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
    account_seq = [a for a in _extract_accounts(pages_text)]
    acct_idx = 0
    stated_per_account: list[float] = []

    for pno, text in enumerate(pages_text, start=1):
        if not text:
            continue
        words = pages_words[pno - 1] if pno - 1 < len(pages_words) else []

        # Find the y-position of each unlabelled "Portfolio Value" line — each
        # one closes a demat account. A page can hold both an account's last
        # rows AND its closing line, so account assignment must respect vertical
        # order WITHIN the page: a row belongs to the account whose closing line
        # is the first one at or below it. Advancing a page-global pointer
        # before placing that page's rows is what mis-assigned every Zerodha
        # holding to the next account.
        by_line: dict[float, list[dict]] = {}
        for w in words:
            by_line.setdefault(round(w["top"], 1), []).append(w)

        close_ys: list[float] = []          # y of each account-closing line, top→down
        for y in sorted(by_line):
            ltxt = " ".join(
                x["text"] for x in sorted(by_line[y], key=lambda w: w["x0"])
            )
            if _DEVANAGARI.search(ltxt):
                continue
            # "Total Portfolio Value 52,23,899.44" is the grand-total on the
            # summary page — NOT an account close. Counting it as one shifts
            # every account by one and empties the first account.
            if re.search(r"Total Portfolio Value", ltxt, re.I):
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
                    stated_per_account.append(val)
                    section_values["equity"] = section_values.get("equity", 0.0) + val
                    close_ys.append(y)

        page_start_idx = acct_idx            # account index at the top of this page

        def _acct_for_y(row_y: float):
            # Each unlabelled "Portfolio Value" line ENDS an account, so every
            # such line ABOVE a row means that row is in a later account. The
            # offset from the page's starting account is exactly the number of
            # closes above the row. (Rows below the last close on the page —
            # e.g. the bonds sub-section, whose own close is *labelled* and thus
            # not counted — correctly stay in that later account.)
            crossed = sum(1 for cy in close_ys if cy < row_y)
            idx = page_start_idx + crossed
            return account_seq[idx] if idx < len(account_seq) else None

        # After the page is placed, advance the global pointer past every
        # account that closed on it.
        acct_idx += len(close_ys)

        # The bonds sub-section on a page follows that page's equity section and
        # belongs to the SAME demat account — but it renders below the account's
        # (unlabelled) equity close, so a plain "closes above me" count would
        # push it to the next account. Anchor bond rows to the account of the
        # last unlabelled close on the page (the account this page belongs to),
        # not the index after it.
        def _bond_acct():
            idx = page_start_idx + max(0, len(close_ys) - 1)
            return account_seq[idx] if idx < len(account_seq) else (
                account_seq[-1] if account_seq else None
            )

        for row in rows_from_words(words):
            y = row["top"]
            value = row.get("value")
            price = row.get("price")
            qty = row.get("quantity")
            if value is None:
                continue
            _kind_for_acct = _classify_isin(row["isin"], row["name"])
            cur_acct = _bond_acct() if _kind_for_acct == "debt" else _acct_for_y(y)
            # Folio-summary rows (units|NAV|cost|VALUE|gain|gain%) are read by
            # _extract_mutual_funds with their full detail; skip them here so
            # they are not double-counted in the demat bucket.
            if row.get("is_folio"):
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
            if kind == "debt":
                # Debt holding. The dedicated bonds table (page 28) carries
                # coupon/maturity/face columns embedded in the name; parse them
                # when present, else leave None for the ISIN lookup to fill.
                parsed = _parse_bond_row(
                    f"{row['isin']} {row['name']}", row["isin"]
                ) or {}
                bonds.append(
                    {
                        "isin": row["isin"],
                        "name": row["name"],
                        "quantity": qty or parsed.get("quantity"),
                        "face_value": parsed.get("face_value"),
                        "price": price,
                        "value": value,
                        "coupon_rate": parsed.get("coupon_rate"),
                        "coupon_freq": parsed.get("coupon_freq"),
                        "maturity_date": parsed.get("maturity_date"),
                        "account": cur_acct,
                    }
                )
            elif kind == "mf":
                demat_funds.append(rec)
            else:
                # equity, preference, REIT/InvIT ("others") — all ride in the
                # equities bucket; the class split is recomputed by ISIN later.
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
