"""
Portfolio Analyzer — upload an Excel of holdings, fetch live NSE prices via Kite,
and return enriched analytics + 10 actionable insights + chart-ready datasets.

Excel is expected to have the columns shown in the user's sheet:
  SHARE | Total Qty | Avg CMP | Total Purchase Value | Current Value
  | Total Profit | P/L In % | Price per share | Weight

We only require SHARE and Total Qty + (Avg CMP OR Total Purchase Value);
everything else is recomputed from live prices.
"""
import io
import math
import re
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from api import state

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


# ── Sector mapping ─────────────────────────────────────────────────────────────

NSE_SECTORS: dict[str, str] = {
    # Financials / Banks / NBFCs / Insurance
    "HDFCBANK": "Financials", "ICICIBANK": "Financials", "SBIN": "Financials",
    "AXISBANK": "Financials", "KOTAKBANK": "Financials", "INDUSINDBK": "Financials",
    "BANDHANBNK": "Financials", "FEDERALBNK": "Financials", "IDFCFIRSTB": "Financials",
    "YESBANK": "Financials", "AUBANK": "Financials", "RBLBANK": "Financials",
    "CANBK": "Financials", "PNB": "Financials", "BANKBARODA": "Financials",
    "UNIONBANK": "Financials", "IDBI": "Financials", "HDFCAMC": "Financials",
    "ICICIGI": "Financials", "ICICIPRULI": "Financials", "SBILIFE": "Financials",
    "SBICARD": "Financials", "BAJFINANCE": "Financials", "BAJAJFINSV": "Financials",
    "CHOLAFIN": "Financials", "MUTHOOTFIN": "Financials", "LICHSGFIN": "Financials",
    "PNBHOUSING": "Financials", "LTFH": "Financials", "MANAPPURAM": "Financials",
    "JMFINANCL": "Financials", "MOTILALOFS": "Financials", "IIFL": "Financials",
    "HDFC": "Financials", "NIACL": "Financials", "LICI": "Financials",
    # IT / Technology
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "TECHM": "IT", "LTIM": "IT", "MPHASIS": "IT", "PERSISTENT": "IT",
    "COFORGE": "IT", "HEXAWARE": "IT", "KPITTECH": "IT",
    "TATAELXSI": "IT", "CYIENT": "IT", "MASTEK": "IT", "NIIT": "IT",
    "ROUTE": "IT", "TANLA": "IT", "BIRLASOFT": "IT",
    # Pharma / Healthcare
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "LUPIN": "Pharma", "TORNTPHARM": "Pharma",
    "ALKEM": "Pharma", "AUROPHARMA": "Pharma", "BIOCON": "Pharma",
    "ZYDUSLIFE": "Pharma", "ABBOTINDIA": "Pharma", "IPCALAB": "Pharma",
    "GRANULES": "Pharma", "GLAND": "Pharma", "LAURUSLABS": "Pharma",
    "APOLLOHOSP": "Healthcare", "FORTIS": "Healthcare", "MAXHEALTH": "Healthcare",
    "METROPOLIS": "Healthcare", "LALPATHLAB": "Healthcare", "THYROCARE": "Healthcare",
    # Auto / Ancillaries
    "TATAMOTORS": "Auto", "MARUTI": "Auto", "M&M": "Auto", "BAJAJ-AUTO": "Auto",
    "HEROMOTOCO": "Auto", "EICHERMOT": "Auto", "TVSMOTOR": "Auto",
    "ASHOKLEY": "Auto", "ESCORTS": "Auto", "BALKRISIND": "Auto",
    "MOTHERSON": "Auto", "BOSCHLTD": "Auto", "EXIDEIND": "Auto",
    "MRF": "Auto", "AMARAJABAT": "Auto", "SUPRAJIT": "Auto",
    # FMCG / Consumer Staples
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "DABUR": "FMCG", "MARICO": "FMCG",
    "COLPAL": "FMCG", "GODREJCP": "FMCG", "EMAMILTD": "FMCG",
    "PGHH": "FMCG", "TATACONSUM": "FMCG", "VBL": "FMCG",
    # Metals & Mining
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals",
    "VEDL": "Metals", "SAIL": "Metals", "NATIONALUM": "Metals",
    "NMDC": "Metals", "HINDCOPPER": "Metals", "RATNAMANI": "Metals",
    "APLAPOLLO": "Metals", "WELSPUNLTD": "Metals",
    # Oil & Gas
    "RELIANCE": "Oil & Gas", "ONGC": "Oil & Gas", "BPCL": "Oil & Gas",
    "IOC": "Oil & Gas", "HINDPETRO": "Oil & Gas", "GAIL": "Oil & Gas",
    "MGL": "Oil & Gas", "IGL": "Oil & Gas", "PETRONET": "Oil & Gas",
    "CASTROLIND": "Oil & Gas", "ATGL": "Oil & Gas",
    # Power & Utilities
    "NTPC": "Power", "POWERGRID": "Power", "TATAPOWER": "Power",
    "ADANIGREEN": "Power", "CESC": "Power", "TORNTPOWER": "Power",
    "NHPC": "Power", "SJVN": "Power", "JSWENERGY": "Power",
    # Telecom
    "BHARTIARTL": "Telecom", "IDEA": "Telecom", "TATACOMM": "Telecom",
    # Cement
    "ULTRACEMCO": "Cement", "AMBUJACEM": "Cement", "ACC": "Cement",
    "SHREECEM": "Cement", "JKCEMENT": "Cement", "RAMCOCEM": "Cement",
    "HEIDELBERG": "Cement", "BIRLACORPN": "Cement",
    # Infra & Engineering
    "LT": "Infra & Engg", "SIEMENS": "Infra & Engg", "ABB": "Infra & Engg",
    "BEL": "Infra & Engg", "HAL": "Infra & Engg", "BHEL": "Infra & Engg",
    "ADANIENT": "Infra & Engg", "IRB": "Infra & Engg", "KEC": "Infra & Engg",
    "POLYCAB": "Infra & Engg", "HAVELLS": "Infra & Engg",
    # Consumer Discretionary
    "TITAN": "Consumer", "TRENT": "Consumer", "DMART": "Consumer",
    "NYKAA": "Consumer", "ZOMATO": "Consumer", "INDIGO": "Consumer",
    "IRCTC": "Consumer", "JUBLFOOD": "Consumer", "DEVYANI": "Consumer",
    "MANYAVAR": "Consumer", "SHOPERSTOP": "Consumer",
    # Chemicals
    "PIIND": "Chemicals", "DEEPAKNTR": "Chemicals", "SRF": "Chemicals",
    "ALKYLAMINE": "Chemicals", "NAVINFLUOR": "Chemicals", "ATUL": "Chemicals",
    "CLEAN": "Chemicals", "FINEORG": "Chemicals", "VINATI": "Chemicals",
    # Realty
    "DLF": "Realty", "GODREJPROP": "Realty", "PRESTIGE": "Realty",
    "SOBHA": "Realty", "OBEROIRLTY": "Realty", "BRIGADE": "Realty",
    "PHOENIXLTD": "Realty",
    # Media
    "ZEEL": "Media", "SUNTV": "Media", "PVRINOX": "Media",
    # Capital Goods
    "CUMMINSIND": "Capital Goods", "THERMAX": "Capital Goods",
    "AIAENG": "Capital Goods", "GRINDWELL": "Capital Goods",
    "KAYNES": "Capital Goods", "DIXON": "Capital Goods",
    # ETFs
    "NIFTYBEES": "ETF", "JUNIORBEES": "ETF", "BANKBEES": "ETF",
    "SETFNIF50": "ETF", "MAFSETF": "ETF",
}

_SECTOR_KW: list[tuple[str, str]] = [
    ("bank", "Financials"), ("financ", "Financials"), ("insur", "Financials"),
    ("bees", "ETF"), ("etf", "ETF"), ("sensex", "ETF"),
    ("pharma", "Pharma"), ("lab", "Pharma"), ("drug", "Pharma"), ("medic", "Healthcare"),
    ("tech", "IT"), ("software", "IT"), ("infosy", "IT"),
    ("auto", "Auto"), ("motor", "Auto"), ("wheel", "Auto"), ("tyre", "Auto"),
    ("steel", "Metals"), ("metal", "Metals"), ("copper", "Metals"), ("alumin", "Metals"),
    ("cement", "Cement"),
    ("power", "Power"), ("energy", "Power"), ("solar", "Power"),
    ("oil", "Oil & Gas"), ("gas", "Oil & Gas"), ("petro", "Oil & Gas"),
    ("telec", "Telecom"), ("airtel", "Telecom"),
    ("hotel", "Consumer"), ("resort", "Consumer"), ("retail", "Consumer"),
    ("chem", "Chemicals"),
    ("real", "Realty"), ("realty", "Realty"), ("prop", "Realty"),
    ("infra", "Infra & Engg"),
]


def _get_sector(symbol: str, name: str = "") -> str:
    sym = (symbol or "").upper().strip()
    if sym in NSE_SECTORS:
        return NSE_SECTORS[sym]
    combined = ((symbol or "") + " " + (name or "")).lower()
    for kw, sec in _SECTOR_KW:
        if kw in combined:
            return sec
    return "Other"


# ── Kite NSE instruments cache ─────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _nse_instruments() -> list[dict]:
    """Pull the full NSE equity instrument dump from Kite once per process."""
    kite = state.get_kite()
    if not kite:
        return []
    try:
        return [i for i in kite.instruments("NSE")
                if i.get("instrument_type") == "EQ"]
    except Exception:
        return []


def _normalise(s: str) -> str:
    """Strip suffixes, punctuation, casing for matching company names."""
    if not s:
        return ""
    s = s.upper()
    s = re.sub(r"\b(LTD|LIMITED|LTD\.|CORP|CORPORATION|INC|COMPANY|CO|"
               r"INDIA|INDUSTRIES|HOLDINGS|HOLDING|GROUP|ENTERPRISES?)\b", "", s)
    s = re.sub(r"[^A-Z0-9]", "", s)
    return s


def _build_name_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    for inst in _nse_instruments():
        name_n = _normalise(inst.get("name", ""))
        sym_n  = _normalise(inst.get("tradingsymbol", ""))
        if name_n and name_n not in index:
            index[name_n] = inst
        if sym_n and sym_n not in index:
            index[sym_n] = inst
    return index


def _resolve_symbol(share: str) -> Optional[dict]:
    """Match a user-supplied SHARE name to a Kite NSE instrument."""
    if not share:
        return None
    idx = _build_name_index()
    needle = _normalise(share)
    if not needle:
        return None
    if needle in idx:
        return idx[needle]
    for k, v in idx.items():
        if needle in k or k in needle:
            return v
    return None


def _ltp_batch(symbols: list[str]) -> dict[str, float]:
    """Fetch LTPs for a batch of NSE symbols. Returns {symbol: price}."""
    kite = state.get_kite()
    if not kite or not symbols:
        return {}
    keys = [f"NSE:{s}" for s in symbols]
    out: dict[str, float] = {}
    for start in range(0, len(keys), 500):
        chunk = keys[start: start + 500]
        try:
            data = kite.ltp(chunk)
            for k, v in data.items():
                sym = k.split(":", 1)[1]
                price = v.get("last_price") or 0
                if price:
                    out[sym] = float(price)
        except Exception:
            pass
    return out


# ── Technical indicators ───────────────────────────────────────────────────────

def _rsi(closes: list[float], period: int = 14) -> float:
    """Wilder's RSI. Returns 50 if insufficient data."""
    if len(closes) <= period:
        return 50.0
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    # Seed with SMA
    gains  = [max(0.0, c) for c in changes]
    losses = [max(0.0, -c) for c in changes]
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return round(100 - (100 / (1 + avg_g / avg_l)), 1)


def _ma(closes: list[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 2)


# ── Insights & charts ──────────────────────────────────────────────────────────

def _safe_pct(num: float, den: float) -> float:
    return (num / den * 100) if den else 0.0


def _build_insights(holdings: list[dict], stats: dict) -> list[dict]:
    """Return a list of actionable insight cards."""
    n_holdings   = len(holdings)
    winners      = [h for h in holdings if h["pnl"] > 0]
    losers       = [h for h in holdings if h["pnl"] < 0]
    flat         = [h for h in holdings if h["pnl"] == 0]

    by_ret_desc  = sorted(holdings, key=lambda h: h["pnl_pct"], reverse=True)
    by_ret_asc   = sorted(holdings, key=lambda h: h["pnl_pct"])
    by_pnl_desc  = sorted(holdings, key=lambda h: h["pnl"], reverse=True)
    by_pnl_asc   = sorted(holdings, key=lambda h: h["pnl"])
    by_weight    = sorted(holdings, key=lambda h: h["weight_pct"], reverse=True)

    big_winners  = [h for h in holdings if h["pnl_pct"] >= 50]
    big_losers   = [h for h in holdings if h["pnl_pct"] <= -20]

    hhi = sum(h["weight_pct"] ** 2 for h in holdings)
    top10_weight = sum(h["weight_pct"] for h in by_weight[:10])

    insights: list[dict] = []

    insights.append({
        "id": "total_pnl",
        "icon": "💰",
        "tone": "good" if stats["total_pnl"] >= 0 else "bad",
        "label": "Total Unrealised P&L",
        "value": stats["total_pnl"],
        "format": "rupees",
        "sub": f"{stats['total_pnl_pct']:+.2f}% on ₹{stats['total_invested']:,.0f} invested",
    })
    insights.append({
        "id": "current_value",
        "icon": "📊",
        "tone": "neutral",
        "label": "Portfolio Value",
        "value": stats["total_value"],
        "format": "rupees",
        "sub": f"{n_holdings} holdings · live priced {stats['priced_count']}/{n_holdings}",
    })
    insights.append({
        "id": "win_rate",
        "icon": "🎯",
        "tone": "good" if len(winners) >= len(losers) else "warn",
        "label": "Win Rate",
        "value": _safe_pct(len(winners), n_holdings),
        "format": "percent",
        "sub": f"{len(winners)} winners · {len(losers)} losers · {len(flat)} flat",
    })
    if by_ret_desc:
        bw = by_ret_desc[0]
        insights.append({
            "id": "best_performer",
            "icon": "🚀",
            "tone": "good",
            "label": "Best % return",
            "value": bw["pnl_pct"],
            "format": "percent",
            "sub": f"{bw['share']} · {bw['pnl']:+,.0f} on ₹{bw['invested']:,.0f}",
        })
    if by_ret_asc and by_ret_asc[0]["pnl_pct"] < 0:
        wp = by_ret_asc[0]
        insights.append({
            "id": "worst_performer",
            "icon": "🚨",
            "tone": "bad",
            "label": "Worst % return",
            "value": wp["pnl_pct"],
            "format": "percent",
            "sub": f"{wp['share']} · {wp['pnl']:+,.0f} on ₹{wp['invested']:,.0f}",
        })
    if big_winners:
        amt = sum(h["pnl"] for h in big_winners)
        insights.append({
            "id": "book_partial",
            "icon": "✂️",
            "tone": "good",
            "label": "Consider booking partial profit",
            "value": len(big_winners),
            "format": "count_holdings",
            "sub": f"{len(big_winners)} holdings up ≥50% · combined gain ₹{amt:,.0f}",
        })
    if big_losers:
        amt = sum(h["pnl"] for h in big_losers)
        insights.append({
            "id": "review_thesis",
            "icon": "🔍",
            "tone": "bad",
            "label": "Review thesis on losers",
            "value": len(big_losers),
            "format": "count_holdings",
            "sub": f"{len(big_losers)} holdings down ≥20% · combined loss ₹{amt:,.0f}",
        })
    if by_weight:
        top = by_weight[0]
        insights.append({
            "id": "top_weight",
            "icon": "⚖️",
            "tone": "warn" if top["weight_pct"] > 15 else "neutral",
            "label": "Largest position",
            "value": top["weight_pct"],
            "format": "percent",
            "sub": f"{top['share']} · ₹{top['current_value']:,.0f} ({top['pnl_pct']:+.1f}%)",
        })
    insights.append({
        "id": "concentration_top10",
        "icon": "📉" if top10_weight > 70 else "🟢",
        "tone": "warn" if top10_weight > 70 else "good",
        "label": "Top-10 concentration",
        "value": top10_weight,
        "format": "percent",
        "sub": "High concentration risk" if top10_weight > 70 else "Reasonably diversified",
    })
    insights.append({
        "id": "hhi",
        "icon": "🧮",
        "tone": "warn" if hhi > 1500 else "neutral",
        "label": "HHI Concentration Index",
        "value": hhi,
        "format": "raw",
        "sub": (
            "Highly concentrated (>1500)" if hhi > 1500 else
            "Moderately concentrated (1000-1500)" if hhi > 1000 else
            "Well diversified (<1000)"
        ),
    })
    if by_pnl_desc:
        top_pnl = by_pnl_desc[0]
        insights.append({
            "id": "top_contributor",
            "icon": "🏆",
            "tone": "good",
            "label": "Biggest ₹ contributor",
            "value": top_pnl["pnl"],
            "format": "rupees",
            "sub": f"{top_pnl['share']} · {top_pnl['pnl_pct']:+.1f}%",
        })
    if by_pnl_asc and by_pnl_asc[0]["pnl"] < 0:
        bot_pnl = by_pnl_asc[0]
        insights.append({
            "id": "biggest_drag",
            "icon": "⚓",
            "tone": "bad",
            "label": "Biggest ₹ drag",
            "value": bot_pnl["pnl"],
            "format": "rupees",
            "sub": f"{bot_pnl['share']} · {bot_pnl['pnl_pct']:+.1f}%",
        })

    # Sector concentration risk among losers
    if len(losers) >= 3:
        sector_counts: dict[str, int] = {}
        for h in losers:
            s = h.get("sector", "Other")
            sector_counts[s] = sector_counts.get(s, 0) + 1
        top_sec = max(sector_counts, key=lambda x: sector_counts[x])
        if sector_counts[top_sec] >= 2 and top_sec != "Other":
            sec_loss = sum(h["pnl"] for h in losers if h.get("sector") == top_sec)
            insights.append({
                "id": "sector_risk",
                "icon": "🏭",
                "tone": "warn",
                "label": "Sector concentration in losers",
                "value": sector_counts[top_sec],
                "format": "count_holdings",
                "sub": f"{sector_counts[top_sec]} losers in {top_sec} · ₹{abs(sec_loss):,.0f} loss",
            })

    return insights[:12]


def _build_charts(holdings: list[dict]) -> dict:
    by_value     = sorted(holdings, key=lambda h: h["current_value"], reverse=True)
    by_ret_desc  = sorted(holdings, key=lambda h: h["pnl_pct"], reverse=True)
    by_ret_asc   = sorted(holdings, key=lambda h: h["pnl_pct"])
    by_pnl_abs   = sorted(holdings, key=lambda h: abs(h["pnl"]), reverse=True)

    top_n        = by_value[:10]
    others_value = sum(h["current_value"] for h in by_value[10:])
    others_count = len(by_value) - len(top_n)
    weight_data  = [{"share": h["share"], "value": h["current_value"], "pct": h["weight_pct"]} for h in top_n]
    if others_value > 0:
        weight_data.append({"share": f"Others ({others_count})", "value": others_value,
                            "pct": _safe_pct(others_value, sum(h['current_value'] for h in holdings))})

    winners_pct = [{"share": h["share"], "pnl_pct": h["pnl_pct"], "pnl": h["pnl"]}
                   for h in by_ret_desc if h["pnl_pct"] > 0][:10]
    losers_pct  = [{"share": h["share"], "pnl_pct": h["pnl_pct"], "pnl": h["pnl"]}
                   for h in by_ret_asc if h["pnl_pct"] < 0][:10]

    contributors = [{"share": h["share"], "pnl": h["pnl"], "pnl_pct": h["pnl_pct"]}
                    for h in by_pnl_abs[:10]]

    buckets: dict[str, int] = {}
    edges = [-100, -50, -30, -20, -10, 0, 10, 20, 30, 50, 100, float("inf")]
    labels = ["<-50%", "-50→-30", "-30→-20", "-20→-10", "-10→0", "0→10",
              "10→20", "20→30", "30→50", "50→100", ">100%"]
    for lbl in labels:
        buckets[lbl] = 0
    for h in holdings:
        r = h["pnl_pct"]
        for i in range(len(edges) - 1):
            if edges[i] <= r < edges[i + 1]:
                buckets[labels[i]] += 1
                break
    distribution = [{"bucket": k, "count": v} for k, v in buckets.items()]

    # Sector breakdown
    sector_map: dict[str, dict] = {}
    for h in holdings:
        sec = h.get("sector", "Other")
        if sec not in sector_map:
            sector_map[sec] = {"sector": sec, "value": 0.0, "pnl": 0.0, "count": 0}
        sector_map[sec]["value"] += h["current_value"]
        sector_map[sec]["pnl"]   += h["pnl"]
        sector_map[sec]["count"] += 1
    sector_breakdown = sorted(sector_map.values(), key=lambda x: -x["value"])

    return {
        "weight":           weight_data,
        "winners":          winners_pct,
        "losers":           losers_pct,
        "contributors":     contributors,
        "distribution":     distribution,
        "sector_breakdown": sector_breakdown,
    }


def _safe_float(v: Any) -> float:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v: Any) -> int:
    try:
        return int(_safe_float(v))
    except (TypeError, ValueError):
        return 0


# ── Main endpoint ──────────────────────────────────────────────────────────────

def _read_grid(raw: bytes, filename: str):
    import pandas as pd
    if filename.lower().endswith(".csv"):
        return pd.read_csv(io.BytesIO(raw), header=None, dtype=object, keep_default_na=False)
    return pd.read_excel(io.BytesIO(raw), engine="openpyxl", header=None, dtype=object)


def _to_jsonable(v):
    import math as _math
    if v is None:
        return ""
    try:
        if isinstance(v, float) and _math.isnan(v):
            return ""
    except Exception:
        pass
    s = str(v).strip()
    if s.lower() in ("nan", "nat"):
        return ""
    return s


def _apply_format_overrides(df, skip_rows: int, drop_first_cols: int, drop_last_cols: int):
    if drop_first_cols < 0: drop_first_cols = 0
    if drop_last_cols  < 0: drop_last_cols  = 0
    if skip_rows       < 0: skip_rows       = 0

    n_cols = len(df.columns)
    end = n_cols - drop_last_cols if drop_last_cols > 0 else n_cols
    if drop_first_cols > 0 or drop_last_cols > 0:
        if end <= drop_first_cols:
            raise HTTPException(400, "Column-trim removes every column. Reduce drop-first / drop-last.")
        df = df.iloc[:, drop_first_cols:end]

    if skip_rows > 0:
        if skip_rows >= len(df):
            raise HTTPException(400, "Skip-rows removes every row. Reduce skip-first.")
        df = df.iloc[skip_rows:]

    df = df.reset_index(drop=True)
    if df.empty:
        raise HTTPException(400, "Sheet is empty after trimming.")

    new_cols = [str(c).strip() if c is not None else "" for c in df.iloc[0].tolist()]
    df = df.iloc[1:].copy()
    df.columns = new_cols
    df = df.reset_index(drop=True)
    return df


# ── Cross-device saved portfolio ──────────────────────────────────────────────
# So uploading on phone A becomes visible on phone B/C/the laptop without
# re-uploading. See api/portfolio_persist.py for the storage layer.

class SavePortfolioBody(BaseModel):
    result:    dict
    file_name: str = ""


@router.get("/saved")
def get_saved_portfolio():
    """Return the most recently saved analyzed portfolio (or {available: false})."""
    from api.portfolio_persist import load_snapshot
    snap = load_snapshot()
    if not snap or not snap.get("result"):
        return {"available": False}
    return {"available": True, **snap}


@router.post("/saved")
def save_saved_portfolio(body: SavePortfolioBody):
    """Persist the analyzed result so other devices can fetch it on load."""
    from api.portfolio_persist import save_snapshot
    return save_snapshot(body.result, file_name=body.file_name)


@router.delete("/saved")
def delete_saved_portfolio():
    """Wipe the saved snapshot (both Supabase and file copies)."""
    from api.portfolio_persist import clear_snapshot
    return clear_snapshot()


@router.post("/preview")
async def preview_portfolio(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(400, "Upload an .xlsx, .xls or .csv file")
    raw = await file.read()
    try:
        df = _read_grid(raw, file.filename)
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")
    rows = [[_to_jsonable(v) for v in r] for r in df.values.tolist()]
    return {
        "filename":   file.filename,
        "total_rows": len(rows),
        "total_cols": len(df.columns),
        "rows":       rows[:60],
    }


@router.post("/analyze")
async def analyze_portfolio(
    file:            UploadFile = File(...),
    skip_rows:       int        = Form(0),
    drop_first_cols: int        = Form(0),
    drop_last_cols:  int        = Form(0),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(400, "Upload an .xlsx, .xls or .csv file")

    raw = await file.read()
    import pandas as pd
    try:
        df = _read_grid(raw, file.filename)
    except Exception as e:
        raise HTTPException(400, f"Could not parse file: {e}")

    df = _apply_format_overrides(df, skip_rows, drop_first_cols, drop_last_cols)

    cols_lower = {c: re.sub(r"\s+", "", str(c)).lower() for c in df.columns}
    def _find(*candidates) -> Optional[str]:
        for cand in candidates:
            cand_n = re.sub(r"\s+", "", cand).lower()
            for orig, low in cols_lower.items():
                if low == cand_n:
                    return orig
        return None

    col_share   = _find("SHARE", "Symbol", "Stock", "Name")
    col_qty     = _find("Total Qty", "Qty", "Quantity")
    col_avg     = _find("Avg CMP", "Avg Price", "AvgCost", "Avg")
    col_invested= _find("Total Purchase Value", "Invested", "Cost", "Buy Value")
    col_price   = _find("Price per share", "CMP", "Current Price", "LTP")

    if not col_share or not col_qty:
        raise HTTPException(400, "Sheet must contain 'SHARE' and 'Total Qty' columns")

    holdings: list[dict] = []
    skipped_rows: list[dict] = []
    AGG_KEYWORDS = ("total", "grand", "summary", "aggregate", "subtotal", "sum")

    for idx, row in df.iterrows():
        share_raw = row[col_share]
        share = str(share_raw).strip() if share_raw is not None else ""
        share_norm = share.lower()

        skip_reason = None
        if not share or share_norm in ("nan", "none", "#n/a", "n/a", "na"):
            skip_reason = "blank/n-a"
        elif share.startswith("#"):
            skip_reason = "starts with #"
        elif any(k in share_norm for k in AGG_KEYWORDS):
            skip_reason = "looks like aggregate row"

        if skip_reason:
            skipped_rows.append({"row": int(idx) + 2, "share": share or "(blank)", "reason": skip_reason})
            continue

        qty = _safe_int(row[col_qty])
        if qty <= 0:
            skipped_rows.append({"row": int(idx) + 2, "share": share, "reason": "qty <= 0"})
            continue

        avg = _safe_float(row[col_avg]) if col_avg else 0.0
        invested = _safe_float(row[col_invested]) if col_invested else (avg * qty)
        if avg == 0 and qty > 0 and invested > 0:
            avg = invested / qty
        sheet_price = _safe_float(row[col_price]) if col_price else avg
        holdings.append({
            "share":       share,
            "qty":         qty,
            "avg_cost":    avg,
            "invested":    invested if invested > 0 else avg * qty,
            "sheet_price": sheet_price,
        })

    if not holdings:
        raise HTTPException(400, "No valid holdings rows found")

    # Resolve NSE symbols + fetch live LTPs
    for h in holdings:
        inst = _resolve_symbol(h["share"])
        if inst:
            h["matched_symbol"]    = inst.get("tradingsymbol")
            h["matched_name"]      = inst.get("name")
            h["instrument_token"]  = inst.get("instrument_token")
        else:
            h["matched_symbol"]   = None
            h["matched_name"]     = None
            h["instrument_token"] = None

    syms_to_fetch = [h["matched_symbol"] for h in holdings if h["matched_symbol"]]
    ltps = _ltp_batch(syms_to_fetch)
    priced_count = 0
    for h in holdings:
        sym = h["matched_symbol"]
        live = ltps.get(sym) if sym else None
        if live and live > 0:
            h["live_price"]   = live
            h["price_source"] = "live"
            priced_count += 1
        elif h["sheet_price"] > 0:
            h["live_price"]   = h["sheet_price"]
            h["price_source"] = "sheet"
        else:
            h["live_price"]   = h["avg_cost"]
            h["price_source"] = "avg"
        h["current_value"] = round(h["qty"] * h["live_price"], 2)
        h["pnl"]           = round(h["current_value"] - h["invested"], 2)
        h["pnl_pct"]       = _safe_pct(h["pnl"], h["invested"])

    # Sector classification
    for h in holdings:
        h["sector"] = _get_sector(h.get("matched_symbol", "") or "", h.get("matched_name", "") or "")

    total_invested = sum(h["invested"] for h in holdings)
    total_value    = sum(h["current_value"] for h in holdings)
    total_pnl      = total_value - total_invested
    for h in holdings:
        h["weight_pct"] = _safe_pct(h["current_value"], total_value)

    holdings.sort(key=lambda h: h["current_value"], reverse=True)

    stats = {
        "total_invested":    round(total_invested, 2),
        "total_value":       round(total_value, 2),
        "total_pnl":         round(total_pnl, 2),
        "total_pnl_pct":     round(_safe_pct(total_pnl, total_invested), 2),
        "n_holdings":        len(holdings),
        "winners":           sum(1 for h in holdings if h["pnl"] > 0),
        "losers":            sum(1 for h in holdings if h["pnl"] < 0),
        "flat":              sum(1 for h in holdings if h["pnl"] == 0),
        "priced_count":      priced_count,
        "unmatched":         sum(1 for h in holdings if not h["matched_symbol"]),
        "live_data":         priced_count > 0,
        "skipped_rows":      len(skipped_rows),
    }

    return {
        "stats":        stats,
        "holdings":     holdings,
        "insights":     _build_insights(holdings, stats),
        "charts":       _build_charts(holdings),
        "skipped_rows": skipped_rows[:50],
    }


# ── Historical P&L time-series ─────────────────────────────────────────────────

class HoldingRef(BaseModel):
    instrument_token: int
    qty: int
    avg_cost: float


class HistoryRequest(BaseModel):
    holdings: list[HoldingRef]
    days: int = 84


@router.post("/history")
async def portfolio_history(body: HistoryRequest):
    kite = state.get_kite()
    if not kite:
        raise HTTPException(503, "Kite not connected — enable API in settings")

    days = max(7, min(body.days, 365))
    today = date.today()
    from_date = today - timedelta(days=days + 14)   # buffer for weekends/holidays

    total_invested = sum(h.avg_cost * h.qty for h in body.holdings)

    # date_str → cumulative portfolio value
    date_values: dict[str, float] = {}

    # Cap to top 25 holdings to avoid timeout
    top_holdings = body.holdings[:25]

    for h in top_holdings:
        try:
            bars = kite.historical_data(
                h.instrument_token,
                from_date.strftime("%Y-%m-%d"),
                today.strftime("%Y-%m-%d"),
                "day",
            )
            for bar in bars:
                d = bar["date"]
                d_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
                date_values[d_str] = date_values.get(d_str, 0.0) + bar["close"] * h.qty
        except Exception:
            pass

    if not date_values:
        raise HTTPException(503, "No historical data returned — markets may be closed or API unavailable")

    series = sorted(
        [{"date": d, "portfolio_value": round(v, 0), "pnl": round(v - total_invested, 0)}
         for d, v in date_values.items()],
        key=lambda x: x["date"],
    )
    # Trim to requested window
    series = series[-days:]

    return {
        "series":          series,
        "total_invested":  round(total_invested, 0),
        "days":            days,
    }


# ── Buy / Sell signals ─────────────────────────────────────────────────────────

class SignalHolding(BaseModel):
    instrument_token: int
    symbol: str
    qty: int
    avg_cost: float
    invested: float
    current_value: float
    pnl_pct: float


class SignalsRequest(BaseModel):
    holdings: list[SignalHolding]
    total_value: float


@router.post("/signals")
async def portfolio_signals(body: SignalsRequest):
    kite = state.get_kite()
    if not kite:
        raise HTTPException(503, "Kite not connected — enable API in settings")

    today = date.today()
    from_date = today - timedelta(days=70)   # enough for RSI(14) + MA(50)

    scored: list[dict] = []

    for h in body.holdings[:30]:
        try:
            bars = kite.historical_data(
                h.instrument_token,
                from_date.strftime("%Y-%m-%d"),
                today.strftime("%Y-%m-%d"),
                "day",
            )
            closes = [b["close"] for b in bars if "close" in b]
            if len(closes) < 5:
                continue

            cur   = closes[-1]
            rsi   = _rsi(closes)
            ma20  = _ma(closes, 20)
            ma50  = _ma(closes, 50)

            scored.append({
                "symbol":        h.symbol,
                "qty":           h.qty,
                "avg_cost":      h.avg_cost,
                "current_value": h.current_value,
                "invested":      h.invested,
                "pnl_pct":       h.pnl_pct,
                "cur":           cur,
                "rsi":           rsi,
                "ma20":          ma20,
                "ma50":          ma50,
            })
        except Exception:
            pass

    total_value = max(body.total_value, 1.0)
    buys:  list[dict] = []
    sells: list[dict] = []

    for r in scored:
        rsi, cur, ma20, ma50 = r["rsi"], r["cur"], r["ma20"], r["ma50"]
        pnl_pct = r["pnl_pct"]

        # ── Buy scoring ──
        buy_score = 0
        buy_reasons: list[str] = []
        if rsi < 35:
            buy_score += 3
            buy_reasons.append(f"RSI {rsi:.0f} — oversold")
        elif rsi < 45:
            buy_score += 1
            buy_reasons.append(f"RSI {rsi:.0f} — weakening")
        if ma50 and cur > ma50:
            buy_score += 2
            buy_reasons.append("price above MA50")
        if ma20 and ma50 and ma20 > ma50:
            buy_score += 1
            buy_reasons.append("golden cross (MA20>MA50)")
        if -10 < pnl_pct <= 0:
            buy_score += 1
            buy_reasons.append("slight dip from cost — averaging opportunity")

        # ── Sell scoring ──
        sell_score = 0
        sell_reasons: list[str] = []
        if rsi > 70:
            sell_score += 3
            sell_reasons.append(f"RSI {rsi:.0f} — overbought")
        elif rsi > 62:
            sell_score += 1
            sell_reasons.append(f"RSI {rsi:.0f} — elevated")
        if pnl_pct >= 60:
            sell_score += 3
            sell_reasons.append(f"+{pnl_pct:.0f}% gain — book partial profit")
        elif pnl_pct >= 40:
            sell_score += 2
            sell_reasons.append(f"+{pnl_pct:.0f}% gain")
        if pnl_pct <= -25 and rsi < 50:
            sell_score += 2
            sell_reasons.append(f"{pnl_pct:.0f}% loss with weak momentum — cut losses")
        if ma50 and cur < ma50 * 0.95 and rsi > 50:
            sell_score += 1
            sell_reasons.append("price broken below MA50")

        # Prices & quantities
        buy_price  = round(cur * 0.99, 2)
        buy_qty    = max(1, int((total_value * 0.05) / cur)) if cur > 0 else 1
        sell_price = round(cur * 1.01, 2)
        sell_qty   = max(1, int(r["qty"] * 0.25))

        holding_value  = r["qty"] * r["avg_cost"]
        buy_investment = round(buy_qty * buy_price, 0)

        if buy_score >= 3:
            buys.append({
                "symbol":         r["symbol"],
                "score":          buy_score,
                "max_score":      6,
                "reason":         " · ".join(buy_reasons),
                "price":          buy_price,
                "qty":            buy_qty,
                "investment":     buy_investment,
                "rsi":            rsi,
                "pnl_pct":        pnl_pct,
                "cur":            cur,
                "ma20":           ma20,
                "ma50":           ma50,
                "your_qty":       r["qty"],
                "your_avg_cost":  round(r["avg_cost"], 2),
                "your_invested":  round(r["invested"], 0),
                "your_cur_val":   round(r["current_value"], 0),
            })
        sell_investment = round(sell_qty * sell_price, 0)
        if sell_score >= 3:
            sells.append({
                "symbol":         r["symbol"],
                "score":          sell_score,
                "max_score":      6,
                "reason":         " · ".join(sell_reasons),
                "price":          sell_price,
                "qty":            sell_qty,
                "investment":     sell_investment,
                "rsi":            rsi,
                "pnl_pct":        pnl_pct,
                "cur":            cur,
                "ma20":           ma20,
                "ma50":           ma50,
                "your_qty":       r["qty"],
                "your_avg_cost":  round(r["avg_cost"], 2),
                "your_invested":  round(r["invested"], 0),
                "your_cur_val":   round(r["current_value"], 0),
            })

    buys.sort(key=lambda x: -x["score"])
    sells.sort(key=lambda x: -x["score"])

    return {"buys": buys[:5], "sells": sells[:5], "analyzed": len(scored)}
