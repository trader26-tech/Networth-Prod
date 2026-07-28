"""File-based store + summary math for the Net-Worth dashboard.

Mirrors the simple JSON-on-disk pattern used elsewhere in the API
(e.g. smart_money). Three files under ``api/networth_data/``:
  - assets.json   normalised asset records
  - people.json   the (default 4) owners + risk profiles
  - uploads.json  metadata for cached uploaded workbooks
Cached workbooks live in ``api/networth_data/cache/<upload_id>.xlsx``.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "networth_data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

ASSETS_FILE = os.path.join(DATA_DIR, "assets.json")
PEOPLE_FILE = os.path.join(DATA_DIR, "people.json")
UPLOADS_FILE = os.path.join(DATA_DIR, "uploads.json")

# --------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------
# Canonical asset classes -> display label, default risk, whether it's a liability
ASSET_CLASSES: dict[str, dict] = {
    "real_estate":    {"label": "Real Estate / Land", "risk": "medium", "liability": False},
    "equity":         {"label": "Stocks & Mutual Funds", "risk": "high", "liability": False},
    "gold":           {"label": "Gold & Precious Metals", "risk": "medium", "liability": False},
    "fixed_deposit":  {"label": "Fixed Deposits", "risk": "low", "liability": False},
    "ulip":           {"label": "ULIP", "risk": "medium", "liability": False},
    "lic":            {"label": "LIC / Insurance", "risk": "low", "liability": False},
    "post_office":    {"label": "Post Office Schemes", "risk": "low", "liability": False},
    "provident_fund": {"label": "Provident Fund / EPF", "risk": "low", "liability": False},
    "cash":           {"label": "Cash & Liquid", "risk": "low", "liability": False},
    "other":          {"label": "Other Assets", "risk": "medium", "liability": False},
    "loan":           {"label": "Loans / Liabilities", "risk": "low", "liability": True},
}

# The household's four canonical members (single source of truth lives in
# api/people/store.py — kept in sync here for this module's offline default).
DEFAULT_PEOPLE = [
    {"name": "Ranjeev", "risk_profile": "balanced", "color": "#5b8def"},
    {"name": "Sanjeev", "risk_profile": "balanced", "color": "#9b6dd6"},
    {"name": "Ramprasad", "risk_profile": "aggressive", "color": "#f0883e"},
    {"name": "Maha", "risk_profile": "conservative", "color": "#20c4a8"},
]

RISK_LEVELS = ("low", "medium", "high")


# --------------------------------------------------------------------------
# Low-level IO
# --------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# People
# --------------------------------------------------------------------------
def get_people() -> list[dict]:
    people = _read(PEOPLE_FILE, None)
    if not people:
        _write(PEOPLE_FILE, DEFAULT_PEOPLE)
        return list(DEFAULT_PEOPLE)
    return people


def save_people(people: list[dict]) -> list[dict]:
    _write(PEOPLE_FILE, people)
    return people


def _person_names() -> list[str]:
    return [p["name"] for p in get_people()]


# --------------------------------------------------------------------------
# Assets
# --------------------------------------------------------------------------
def get_assets() -> list[dict]:
    return _read(ASSETS_FILE, [])


def _normalise_owners(owners, default_name: str | None = None) -> list[dict]:
    """Ensure owners is a list of {person, pct} summing ~100."""
    names = _person_names()
    cleaned: list[dict] = []
    for o in owners or []:
        name = (o.get("person") or "").strip()
        if not name:
            continue
        try:
            pct = float(o.get("pct", 0) or 0)
        except (TypeError, ValueError):
            pct = 0.0
        cleaned.append({"person": name, "pct": pct})
    if not cleaned:
        owner = default_name or (names[0] if names else "Unassigned")
        cleaned = [{"person": owner, "pct": 100.0}]
    total = sum(o["pct"] for o in cleaned)
    if total <= 0:
        even = round(100.0 / len(cleaned), 4)
        for o in cleaned:
            o["pct"] = even
    return cleaned


def _clean_asset(a: dict) -> dict:
    cls = a.get("asset_class") if a.get("asset_class") in ASSET_CLASSES else "other"
    risk = a.get("risk") if a.get("risk") in RISK_LEVELS else ASSET_CLASSES[cls]["risk"]

    def _f(key):
        v = a.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "id": a.get("id") or uuid.uuid4().hex[:12],
        "name": (a.get("name") or "Untitled").strip(),
        "asset_class": cls,
        "current_value": _f("current_value") or 0.0,
        "invested_value": _f("invested_value"),
        "monthly_income": _f("monthly_income"),
        "cagr": _f("cagr"),
        "purchase_date": a.get("purchase_date") or None,
        "risk": risk,
        "owners": _normalise_owners(a.get("owners"), a.get("default_owner")),
        "source": a.get("source") or {"type": "manual"},
        "documents": a.get("documents") or [],
        "notes": a.get("notes") or None,
        "created_at": a.get("created_at") or _now(),
        "updated_at": _now(),
    }


def add_asset(a: dict) -> dict:
    assets = get_assets()
    rec = _clean_asset(a)
    assets.append(rec)
    _write(ASSETS_FILE, assets)
    return rec


def add_assets(items: list[dict]) -> list[dict]:
    assets = get_assets()
    created = [_clean_asset(a) for a in items]
    assets.extend(created)
    _write(ASSETS_FILE, assets)
    return created


def update_asset(asset_id: str, patch: dict) -> dict | None:
    assets = get_assets()
    for i, a in enumerate(assets):
        if a.get("id") == asset_id:
            merged = {**a, **patch, "id": asset_id, "created_at": a.get("created_at")}
            assets[i] = _clean_asset(merged)
            _write(ASSETS_FILE, assets)
            return assets[i]
    return None


def delete_asset(asset_id: str) -> bool:
    assets = get_assets()
    new = [a for a in assets if a.get("id") != asset_id]
    if len(new) == len(assets):
        return False
    _write(ASSETS_FILE, new)
    return True


def delete_assets_by_ids(ids: list[str]) -> int:
    """Delete many assets by id. Returns how many were removed."""
    idset = set(ids or [])
    assets = get_assets()
    new = [a for a in assets if a.get("id") not in idset]
    removed = len(assets) - len(new)
    if removed:
        _write(ASSETS_FILE, new)
    return removed


def delete_assets_by_class(asset_class: str) -> int:
    """Delete every asset in one class (e.g. all real_estate). Returns count removed."""
    assets = get_assets()
    new = [a for a in assets if a.get("asset_class") != asset_class]
    removed = len(assets) - len(new)
    if removed:
        _write(ASSETS_FILE, new)
    return removed


def delete_all_assets() -> int:
    """Wipe every asset. Returns count removed."""
    assets = get_assets()
    n = len(assets)
    if n:
        _write(ASSETS_FILE, [])
    return n


# --------------------------------------------------------------------------
# Uploads (cached workbooks)
# --------------------------------------------------------------------------
def get_uploads() -> list[dict]:
    return _read(UPLOADS_FILE, [])


def save_upload(filename: str, content: bytes, sheets: list[dict]) -> dict:
    upload_id = uuid.uuid4().hex[:12]
    path = os.path.join(CACHE_DIR, f"{upload_id}.xlsx")
    with open(path, "wb") as f:
        f.write(content)
    meta = {
        "id": upload_id,
        "filename": filename,
        "uploaded_at": _now(),
        "size": len(content),
        "sheet_count": len(sheets),
        "path": path,
    }
    uploads = get_uploads()
    uploads.insert(0, meta)
    _write(UPLOADS_FILE, uploads)
    return meta


def upload_path(upload_id: str) -> str | None:
    for u in get_uploads():
        if u["id"] == upload_id:
            return u.get("path")
    return None


def delete_upload(upload_id: str) -> bool:
    uploads = get_uploads()
    new = [u for u in uploads if u["id"] != upload_id]
    if len(new) == len(uploads):
        return False
    p = os.path.join(CACHE_DIR, f"{upload_id}.xlsx")
    if os.path.exists(p):
        try:
            os.remove(p)
        except OSError:
            pass
    _write(UPLOADS_FILE, new)
    return True


# --------------------------------------------------------------------------
# Summary math
# --------------------------------------------------------------------------
def _weighted_cagr(items: list[tuple[float, float]]) -> float | None:
    """items = list of (value, cagr). Value-weighted average over those w/ cagr."""
    num = den = 0.0
    for value, cagr in items:
        if cagr is None:
            continue
        num += value * cagr
        den += value
    return (num / den) if den > 0 else None


def compute_summary() -> dict:
    assets = get_assets()
    people = get_people()
    person_names = [p["name"] for p in people]

    total_assets = 0.0
    total_liabilities = 0.0
    monthly_income = 0.0
    cagr_pairs: list[tuple[float, float]] = []

    by_class: dict[str, dict] = {}
    by_person: dict[str, dict] = {n: {"person": n, "value": 0.0, "monthly_income": 0.0,
                                      "asset_count": 0} for n in person_names}

    for a in assets:
        cls = a["asset_class"]
        is_liab = ASSET_CLASSES.get(cls, {}).get("liability", False)
        val = a.get("current_value") or 0.0
        inc = a.get("monthly_income") or 0.0

        if is_liab:
            total_liabilities += val
        else:
            total_assets += val
            monthly_income += inc
            if a.get("cagr") is not None:
                cagr_pairs.append((val, a["cagr"]))

        # by class
        c = by_class.setdefault(cls, {
            "asset_class": cls,
            "label": ASSET_CLASSES.get(cls, {}).get("label", cls),
            "liability": is_liab,
            "value": 0.0, "monthly_income": 0.0, "asset_count": 0,
            "cagr_pairs": [],
        })
        c["value"] += val
        c["monthly_income"] += inc
        c["asset_count"] += 1
        if a.get("cagr") is not None and not is_liab:
            c["cagr_pairs"].append((val, a["cagr"]))

        # by person (split)
        for o in a.get("owners", []):
            name = o["person"]
            share = (o.get("pct", 0) or 0) / 100.0
            bp = by_person.setdefault(name, {"person": name, "value": 0.0,
                                             "monthly_income": 0.0, "asset_count": 0})
            signed = -val if is_liab else val
            bp["value"] += signed * share
            if not is_liab:
                bp["monthly_income"] += inc * share
            bp["asset_count"] += 1

    net_worth = total_assets - total_liabilities

    class_list = []
    for c in by_class.values():
        c["cagr"] = _weighted_cagr(c.pop("cagr_pairs"))
        c["pct"] = round(c["value"] / total_assets * 100, 2) if total_assets and not c["liability"] else 0
        class_list.append(c)
    class_list.sort(key=lambda x: x["value"], reverse=True)

    person_list = sorted(by_person.values(), key=lambda x: x["value"], reverse=True)
    for p in person_list:
        p["pct"] = round(p["value"] / net_worth * 100, 2) if net_worth else 0

    return {
        "net_worth": round(net_worth, 2),
        "total_assets": round(total_assets, 2),
        "total_liabilities": round(total_liabilities, 2),
        "monthly_income": round(monthly_income, 2),
        "annual_income": round(monthly_income * 12, 2),
        "weighted_cagr": _weighted_cagr(cagr_pairs),
        "asset_count": len([a for a in assets if not ASSET_CLASSES.get(a["asset_class"], {}).get("liability")]),
        "by_class": class_list,
        "by_person": person_list,
        "people": people,
    }
