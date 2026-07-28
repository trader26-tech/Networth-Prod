"""
Parse a bank account statement (.xls/.xlsx/.csv) into spend/earn transactions for
the Expenses tab: every WITHDRAWAL is a candidate expense, every DEPOSIT is income.

Each row is auto-tagged with a best-guess category from the narration (UPI merchant,
NACH mandate, ATM, fuel, food …) — the user re-categorises inline before importing.
"""
from __future__ import annotations

import io
import re
from datetime import datetime

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{2,4}$")
_AMT_RE = re.compile(r"^-?[\d,]+\.?\d*$")


def _num(v) -> float | None:
    s = str(v).strip().replace(",", "")
    if not s or s.lower() == "nan" or not _AMT_RE.match(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _iso(dcell: str) -> str | None:
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(dcell.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ── category guessing ────────────────────────────────────────────────────────────
# narration keyword → category. First hit wins; order matters (specific first).
# The category names MUST come from api/expenses/store.CATEGORIES — the same list
# the picker, the treemap and the Claude/MCP inbox use — so a spend lands in one
# category however it was entered.
_CATEGORY_RULES: list[tuple[str, str]] = [
    (r"swiggy|zomato|eatfit|eat\.fit|dominos|mcdonald|burger|kfc|restaurant|cafe|coffee|hotel|bakery|pizza|biryani|food", "Dining out"),
    (r"petrol|fuel|hpcl|iocl|bpcl|indian ?oil|bharat ?petro|shell|pump|uber|ola|rapido|metro|toll|fastag|parking", "Transport & Fuel"),
    (r"irctc|makemytrip|ixigo|redbus|railway|indigo|vistara|air ?india|goibibo|cleartrip|oyo|airbnb", "Travel"),
    (r"bigbasket|blinkit|zepto|dmart|d-?mart|grofers|instamart|supermarket|kirana|provision", "Groceries"),
    (r"amazon|flipkart|myntra|ajio|meesho|nykaa|reliance ?retail|shopping|store|mart", "Shopping"),
    (r"jio|airtel|vodafone|vi |bsnl|broadband|wifi|recharge|dth|tata ?play", "Internet & Phone"),
    (r"netflix|prime|hotstar|spotify|youtube|subscription|membership|gym|cult", "Subscriptions"),
    (r"electricity|tneb|bescom|power|water|gas|lpg|indane|hp ?gas|bill ?desk|bbps|utility", "Utilities"),
    (r"pharmacy|apollo|medplus|hospital|clinic|medical|diagnostic|lab|health|doctor|1mg|pharmeasy", "Healthcare"),
    (r"school|college|tuition|course|udemy|coursera|byju|fees|education|exam", "Education"),
    (r"lic|insurance|premium|policy|hdfc ?life|icici ?pru|max ?life|star ?health", "Insurance"),
    (r"sip|mutual ?fund|zerodha|groww|coin|kuvera|nps|ppf|investment|broking", "Investments"),
    (r"atm|cash ?wdl|cash withdrawal|nwd|eaw", "Cash / ATM"),
    (r"salary|sal ?cr|neft cr|imps.*cr", "Miscellaneous"),
    (r"emi|loan|finance|credit ?card|cred |billpay", "EMI / Loan"),
    (r"rent|maintenance|society|apartment", "Housing / Rent"),
    (r"salon|spa|barber|beauty|grooming", "Personal care"),
    (r"donation|temple|charity|trust|ngo", "Donations"),
    (r"maid|driver|cook|househelp|domestic", "Domestic help"),
    (r"movie|pvr|inox|bookmyshow|game|concert", "Entertainment"),
    (r"upi-|imps-|neft|paytm|phonepe|gpay|google ?pay|@ok|@ybl|@paytm|@axl", "Transfers / UPI"),
]
_CATEGORY_RES = [(re.compile(pat, re.I), cat) for pat, cat in _CATEGORY_RULES]


# income (credit) sub-categories — narration keyword → category.
_INCOME_RULES: list[tuple[str, str]] = [
    (r"salary|sal ?cr|payroll|wages", "Salary"),
    (r"dividend|\bdiv\b|div ?\d|fnl ?div|final ?div|interim ?div", "Dividend"),
    (r"interest|int\.?cr|int ?credit|fd ?int|monthly interest", "Interest"),
    (r"rent|lease", "Rent"),
    (r"refund|reversal|revers|cashback|chargeback|failed", "Refund"),
    (r"ncd|debent|coupon|maturity|redemption", "Bond / NCD"),
    (r"neft|imps|upi|rtgs|transfer|from ", "Transfer in"),
]
_INCOME_RES = [(re.compile(pat, re.I), cat) for pat, cat in _INCOME_RULES]
INCOME_CATEGORIES = ["Salary", "Dividend", "Interest", "Rent", "Bond / NCD",
                     "Refund", "Transfer in", "Business", "Gift", "Other income"]


def guess_category(narration: str, direction: str) -> str:
    s = narration or ""
    if direction == "credit":
        for rx, cat in _INCOME_RES:
            if rx.search(s):
                return cat
        return "Other income"
    for rx, cat in _CATEGORY_RES:
        if rx.search(s):
            return cat
    return "Miscellaneous"


def _merchant(narration: str) -> str:
    """A short, human name pulled from the narration (best-effort), for the row label."""
    s = re.sub(r"\s+", " ", (narration or "").strip())
    # UPI-<MERCHANT>-... → the merchant token
    m = re.match(r"(?:UPI|IMPS|NEFT|ACH ?C?-?|POS|MMT)[-/ ]+([A-Za-z0-9&.' ]{3,40})", s, re.I)
    if m:
        name = m.group(1).strip(" -.")
        # drop trailing handle fragments
        name = re.split(r"[-@]", name)[0].strip()
        if len(name) >= 3:
            return name.title()[:40]
    return (s[:40] or "Transaction")


# ── parse ────────────────────────────────────────────────────────────────────────
def parse_transactions(content: bytes, filename: str) -> dict:
    """Return {transactions:[{date, amount, direction, narration, name, category, ref}],
    total_spent, total_earned, date_from, date_to, count}."""
    import pandas as pd

    name = (filename or "").lower()
    bio = io.BytesIO(content)
    if name.endswith(".csv"):
        df = pd.read_csv(bio, header=None, dtype=str, on_bad_lines="skip")
    else:
        engine = "xlrd" if name.endswith(".xls") else "openpyxl"
        try:
            df = pd.read_excel(bio, header=None, dtype=str, engine=engine)
        except Exception:
            bio.seek(0)
            df = pd.read_excel(bio, header=None, dtype=str)

    hdr = None
    header: list[str] = []
    for i in range(min(80, len(df))):
        cells = [str(x).strip().lower() for x in df.iloc[i].tolist()]
        if any(c == "date" for c in cells) and any("withdrawal" in c for c in cells) \
           and any("deposit" in c for c in cells):
            hdr, header = i, cells
            break
    if hdr is None:
        raise ValueError("Couldn't find the statement's transaction table (need Date / Narration / "
                         "Withdrawal / Deposit columns). Export the account statement as Excel/CSV and retry.")

    di = header.index("date")
    ni = next((j for j, c in enumerate(header) if "narration" in c or "description" in c or "particular" in c), di + 1)
    wi = next(j for j, c in enumerate(header) if "withdrawal" in c)
    pi = next(j for j, c in enumerate(header) if "deposit" in c)

    txns: list[dict] = []
    spent = earned = 0.0
    dates: list[str] = []
    for i in range(hdr + 1, len(df)):
        row = df.iloc[i].tolist()
        dcell = str(row[di]).strip() if di < len(row) else ""
        if not _DATE_RE.match(dcell):
            continue
        iso = _iso(dcell)
        if not iso:
            continue
        wd = _num(row[wi]) if wi < len(row) else None
        dep = _num(row[pi]) if pi < len(row) else None
        narr = re.sub(r"\s+", " ", str(row[ni]).strip()) if ni < len(row) else ""
        if wd and wd > 0:
            direction, amount = "debit", round(wd, 2)
            spent += amount
        elif dep and dep > 0:
            direction, amount = "credit", round(dep, 2)
            earned += amount
        else:
            continue
        dates.append(iso)
        txns.append({
            "date": iso, "amount": amount, "direction": direction, "narration": narr,
            "name": _merchant(narr), "category": guess_category(narr, direction),
            "ref": f"{iso}|{amount}|{narr[:60]}",       # stable signature for de-dup
        })

    dates.sort()
    return {
        "transactions": txns,
        "total_spent": round(spent, 2), "total_earned": round(earned, 2),
        "date_from": dates[0] if dates else None, "date_to": dates[-1] if dates else None,
        "count": len(txns), "debit_count": sum(1 for t in txns if t["direction"] == "debit"),
        "credit_count": sum(1 for t in txns if t["direction"] == "credit"),
        "income_categories": INCOME_CATEGORIES,
    }
