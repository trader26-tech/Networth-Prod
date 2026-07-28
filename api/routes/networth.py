"""Net-Worth dashboard routes: Excel import (guided + cached), assets,
people/risk profiles, and computed summary.
"""
from __future__ import annotations

from typing import Optional, Any

from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel

from ..networth import excel_parser as xls
from ..networth import store

router = APIRouter(prefix="/api/networth", tags=["networth"])

VALUE_FIELDS = {"current_value", "invested_value", "monthly_income"}
ALL_FIELDS = VALUE_FIELDS | {"name", "cagr", "purchase_date", "owner", "risk", "asset_class", "ignore"}


# ==========================================================================
# Reference / meta
# ==========================================================================
@router.get("/meta")
def meta():
    return {
        "asset_classes": [
            {"key": k, "label": v["label"], "default_risk": v["risk"], "liability": v["liability"]}
            for k, v in store.ASSET_CLASSES.items()
        ],
        "risk_levels": list(store.RISK_LEVELS),
        "import_fields": [
            {"key": "name", "label": "Name / Description"},
            {"key": "current_value", "label": "Current Value"},
            {"key": "invested_value", "label": "Invested / Cost"},
            {"key": "monthly_income", "label": "Monthly Income"},
            {"key": "cagr", "label": "CAGR (% or decimal)"},
            {"key": "purchase_date", "label": "Purchase / Start Date"},
            {"key": "owner", "label": "Owner (person)"},
            {"key": "risk", "label": "Risk"},
            {"key": "asset_class", "label": "Asset Class"},
            {"key": "ignore", "label": "— Ignore —"},
        ],
    }


# ==========================================================================
# Upload + sheet inspection (cached)
# ==========================================================================
@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Please upload an .xlsx file (Numbers: File ▸ Export To ▸ Excel).")
    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file.")

    # write to a temp path so the parser can open it, then cache via store
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    try:
        tmp.write(content)
        tmp.close()
        try:
            sheets = xls.list_sheets(tmp.name)
        except Exception as e:
            raise HTTPException(400, f"Could not read workbook: {e}")
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass

    meta = store.save_upload(file.filename or "upload.xlsx", content, sheets)
    return {"upload_id": meta["id"], "filename": meta["filename"],
            "uploaded_at": meta["uploaded_at"], "sheet_count": len(sheets), "sheets": sheets}


@router.get("/uploads")
def uploads():
    return [{k: u[k] for k in ("id", "filename", "uploaded_at", "size", "sheet_count")}
            for u in store.get_uploads()]


@router.get("/upload/{upload_id}/sheet")
def sheet_grid(upload_id: str, name: str, max_rows: int = 300, max_cols: int = 60, col_offset: int = 0):
    path = store.upload_path(upload_id)
    if not path:
        raise HTTPException(404, "Upload not found (cache may have been cleared).")
    try:
        return xls.get_grid(path, name, max_rows=max_rows, max_cols=max_cols, col_offset=col_offset)
    except KeyError:
        raise HTTPException(404, f"Sheet {name!r} not found.")
    except Exception as e:
        raise HTTPException(400, f"Could not read sheet: {e}")


@router.delete("/upload/{upload_id}")
def remove_upload(upload_id: str):
    if not store.delete_upload(upload_id):
        raise HTTPException(404, "Upload not found.")
    return {"ok": True}


# ==========================================================================
# Import (map columns -> assets)
# ==========================================================================
class ColumnMap(BaseModel):
    index: int
    field: str


class OwnerSplit(BaseModel):
    person: str
    pct: float


class ImportDefaults(BaseModel):
    asset_class: str = "other"
    risk: Optional[str] = None
    owners: list[OwnerSplit] = []


class ImportRequest(BaseModel):
    upload_id: str
    sheet: str
    columns: list[ColumnMap]
    header_row: Optional[int] = None
    data_start_row: int = 1
    data_end_row: Optional[int] = None
    col_offset: int = 0
    max_cols: int = 60
    value_scale: float = 1.0
    defaults: ImportDefaults = ImportDefaults()


def _match_person(text: Any, names: list[str]) -> Optional[str]:
    if not isinstance(text, str):
        return None
    t = text.strip().lower()
    if not t:
        return None
    # prefer the longest person-name token that appears in the cell
    best = None
    for n in sorted(names, key=len, reverse=True):
        if n.lower() in t:
            return n
        if t in n.lower():
            best = best or n
    return best


def _match_class(text: Any) -> Optional[str]:
    if not isinstance(text, str):
        return None
    t = text.strip().lower()
    if not t:
        return None
    if t in store.ASSET_CLASSES:
        return t
    keys = {
        "real_estate": ["land", "real estate", "flat", "plot", "property", "house", "apartment"],
        "equity": ["share", "stock", "equity", "mutual", "mf", "demat"],
        "gold": ["gold", "jewel", "silver", "precious"],
        "fixed_deposit": ["fd", "fixed deposit", "deposit", "rd"],
        "ulip": ["ulip"],
        "lic": ["lic", "insurance", "policy"],
        "post_office": ["post office", "p.o", "po ", "nsc", "kvp", "ppf"],
        "provident_fund": ["provident", "epf", "pf", "gratuity"],
        "cash": ["cash", "savings", "bank balance", "liquid"],
        "loan": ["loan", "liability", "liabilit", "mortgage", "borrow"],
    }
    for cls, words in keys.items():
        if any(w in t for w in words):
            return cls
    return None


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").replace("₹", "").replace("%", "").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


@router.post("/import")
def import_sheet(req: ImportRequest):
    path = store.upload_path(req.upload_id)
    if not path:
        raise HTTPException(404, "Upload not found (cache may have been cleared).")
    try:
        grid = xls.get_grid(path, req.sheet, max_rows=2000, max_cols=req.max_cols, col_offset=req.col_offset)
    except Exception as e:
        raise HTTPException(400, f"Could not read sheet: {e}")

    rows = grid["rows"]
    names = [p["name"] for p in store.get_people()]
    field_by_col = {c.index: c.field for c in req.columns if c.field in ALL_FIELDS and c.field != "ignore"}
    if "name" not in field_by_col.values() and "current_value" not in field_by_col.values():
        raise HTTPException(400, "Map at least a Name or a Current Value column.")

    default_owners = [o.model_dump() for o in req.defaults.owners]
    scale = req.value_scale or 1.0
    start = max(0, req.data_start_row)
    end = req.data_end_row if req.data_end_row is not None else len(rows)

    staged: list[dict] = []
    for r in rows[start:end]:
        rec: dict = {
            "asset_class": req.defaults.asset_class,
            "risk": req.defaults.risk,
            "owners": list(default_owners),
            "source": {"type": "import", "upload_id": req.upload_id, "sheet": req.sheet},
        }
        row_owner = None
        for col, field in field_by_col.items():
            val = r[col] if col < len(r) else None
            if field == "name":
                rec["name"] = str(val).strip() if val not in (None, "") else rec.get("name")
            elif field in VALUE_FIELDS:
                f = _to_float(val)
                rec[field] = f * scale if f is not None else None
            elif field == "cagr":
                f = _to_float(val)
                if f is not None:
                    rec["cagr"] = f / 100.0 if abs(f) > 1.5 else f
            elif field == "purchase_date":
                rec["purchase_date"] = val if val else None
            elif field == "owner":
                m = _match_person(val, names)
                if m:
                    row_owner = m
            elif field == "risk":
                rv = str(val).strip().lower() if val else ""
                if rv in store.RISK_LEVELS:
                    rec["risk"] = rv
            elif field == "asset_class":
                m = _match_class(val)
                if m:
                    rec["asset_class"] = m

        if row_owner:
            rec["owners"] = [{"person": row_owner, "pct": 100.0}]
        rec.setdefault("name", None)

        # skip empty rows (no name and no value)
        if (not rec.get("name")) and not rec.get("current_value"):
            continue
        if not rec.get("name"):
            rec["name"] = f"{store.ASSET_CLASSES.get(rec['asset_class'], {}).get('label', 'Asset')}"
        staged.append(rec)

    if not staged:
        raise HTTPException(400, "No data rows matched. Check the start row and column mapping.")

    created = store.add_assets(staged)
    return {"imported": len(created), "assets": created}


# ==========================================================================
# Assets CRUD
# ==========================================================================
@router.get("/assets")
def list_assets():
    return store.get_assets()


class AssetIn(BaseModel):
    name: str
    asset_class: str = "other"
    current_value: float = 0
    invested_value: Optional[float] = None
    monthly_income: Optional[float] = None
    cagr: Optional[float] = None
    purchase_date: Optional[str] = None
    risk: Optional[str] = None
    owners: list[OwnerSplit] = []
    notes: Optional[str] = None


@router.post("/assets")
def create_asset(a: AssetIn):
    payload = a.model_dump()
    payload["owners"] = [o for o in payload.get("owners", [])]
    return store.add_asset(payload)


@router.put("/assets/{asset_id}")
def edit_asset(asset_id: str, patch: dict):
    updated = store.update_asset(asset_id, patch)
    if not updated:
        raise HTTPException(404, "Asset not found.")
    return updated


@router.delete("/assets/{asset_id}")
def remove_asset(asset_id: str):
    if not store.delete_asset(asset_id):
        raise HTTPException(404, "Asset not found.")
    return {"ok": True}


class BulkDeleteRequest(BaseModel):
    ids: Optional[list[str]] = None
    asset_class: Optional[str] = None
    all: bool = False


@router.post("/assets/bulk-delete")
def bulk_delete(req: BulkDeleteRequest):
    """Delete many assets at once — by id list, by asset_class, or all of them."""
    if req.all:
        return {"deleted": store.delete_all_assets()}
    if req.asset_class:
        return {"deleted": store.delete_assets_by_class(req.asset_class)}
    if req.ids:
        return {"deleted": store.delete_assets_by_ids(req.ids)}
    raise HTTPException(400, "Provide ids, asset_class, or all=true.")


# ==========================================================================
# People / risk profiles
# ==========================================================================
@router.get("/people")
def people():
    return store.get_people()


@router.put("/people")
def set_people(items: list[dict]):
    return store.save_people(items)


# ==========================================================================
# Summary
# ==========================================================================
@router.get("/summary")
def summary():
    return store.compute_summary()
