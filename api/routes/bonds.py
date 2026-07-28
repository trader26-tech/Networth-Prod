"""Bonds routes: CRUD on bond holdings + a computed summary with monthly
income, YTM, payout calendar and maturity ladder. See SUPABASE.md → "Bonds table"."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from ..bonds import store
from ..bonds import engine
from ..bonds import statement

router = APIRouter(prefix="/api/bonds", tags=["bonds"])


class ScheduleRow(BaseModel):
    date: str
    interest: float = 0.0
    principal: float = 0.0


class Bond(BaseModel):
    owner: str
    broker: str = ""
    issuer: str
    bond_type: str = "Other"
    isin: Optional[str] = ""
    rating: Optional[str] = ""
    tax_free: bool = False
    face_value: float = 1000.0
    quantity: float
    buy_price: float
    coupon_rate: float = 0.0
    coupon_freq: str = "annual"          # monthly|quarterly|half_yearly|annual|cumulative|zero
    repayment_type: str = "bullet"        # bullet|amortizing
    purchase_date: str
    first_payment_date: Optional[str] = None
    maturity_date: str
    redemption_value: Optional[float] = None
    ytm_input: Optional[float] = None     # YTM the user enters (seed for generation)
    schedule: Optional[list[ScheduleRow]] = None  # editable per-period cashflows (source of truth)
    note: Optional[str] = ""
    sellable_on: Optional[str] = None


class GenerateReq(BaseModel):
    invested: float
    face_total: float
    ytm: float
    first_payment_date: str
    maturity_date: str
    coupon_freq: str = "annual"
    repayment_type: str = "bullet"


class SipSplit(BaseModel):
    name: str
    amount: float = 0.0


class Sip(BaseModel):
    owner: str = ""
    total: float
    expected_date: str                    # when the SIP purchase completes → reminder date
    note: str = ""
    splits: list[SipSplit] = []           # how the total is spread across bonds


class SipPatch(BaseModel):
    owner: Optional[str] = None
    total: Optional[float] = None
    expected_date: Optional[str] = None
    note: Optional[str] = None
    splits: Optional[list[SipSplit]] = None
    status: Optional[str] = None          # 'pending' | 'logged'


def _require_ready() -> None:
    if not store.tables_ready():
        raise HTTPException(503, store.MIGRATION_HINT)


@router.get("/summary")
def summary():
    _require_ready()
    return engine.build_summary(store.list_bonds())


@router.post("/generate")
def generate(req: GenerateReq):
    """Seed an editable schedule from a target YTM (form 'Generate' button).
    Returns {schedule:[{date, interest, principal}], coupon_rate}."""
    return engine.generate_schedule(
        invested=req.invested, face_total=req.face_total, ytm=req.ytm,
        first_payment_date=req.first_payment_date, maturity_date=req.maturity_date,
        coupon_freq=req.coupon_freq, repayment_type=req.repayment_type,
    )


@router.post("/bonds")
def create(b: Bond):
    _require_ready()
    return store.add_bond(b.model_dump())


@router.put("/bonds/{bond_id}")
def update(bond_id: str, b: Bond):
    _require_ready()
    row = store.update_bond(bond_id, b.model_dump())
    if not row:
        raise HTTPException(404, "Bond not found.")
    return row


@router.delete("/bonds/{bond_id}")
def delete(bond_id: str):
    _require_ready()
    return {"deleted": store.delete_bond(bond_id)}


class PaymentStatusIn(BaseModel):
    bond_id: str
    date: str
    status: str = "pending"           # pending | received | not_received


# ── Bond SIPs — a planned purchase you log the real details of once it executes.
#    Each pending SIP raises a dashboard reminder on its expected date. ──────────────
@router.get("/sips")
def list_sips():
    return store.get_sips()


@router.post("/sips")
def create_sip(s: Sip):
    return store.add_sip(s.model_dump())


@router.put("/sips/{sip_id}")
def update_sip(sip_id: str, patch: SipPatch):
    body = {k: v for k, v in patch.model_dump().items() if v is not None}
    if body.get("status") == "logged" and "logged_at" not in body:
        from datetime import datetime
        body["logged_at"] = datetime.now().isoformat()
    row = store.update_sip(sip_id, body)
    if not row:
        raise HTTPException(404, "SIP not found.")
    return row


@router.delete("/sips/{sip_id}")
def delete_sip(sip_id: str):
    return {"deleted": store.delete_sip(sip_id)}


@router.get("/payment-status")
def list_payment_status():
    """Every payment the user has marked received / not-received, keyed by
    (bond_id, date). Anything absent is treated as pending (expected)."""
    return store.list_payment_status()


@router.put("/payment-status")
def set_payment_status(body: PaymentStatusIn):
    """Mark one bond payment received / pending / not-received."""
    return store.set_payment_status(body.bond_id, body.date, body.status)


# ── Bank-statement reconciliation ─────────────────────────────────────────────
# Upload a bank statement (Excel/CSV) → we parse the credits, match them to bond
# interest payments (issuer + amount ± TDS + date), and hand back the matches for
# the user to confirm. On confirm, the accepted payments are marked "received".
@router.post("/reconcile/preview")
async def reconcile_preview(file: UploadFile = File(...)):
    _require_ready()
    content = await file.read()
    if not content:
        raise HTTPException(400, "The uploaded file is empty.")
    try:
        credits = statement.parse_credits(content, file.filename or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"Could not read the statement: {e}")
    smap = {(s["bond_id"], s["date"]): s["status"] for s in store.list_payment_status()}
    return statement.reconcile(credits, store.list_bonds(), smap)


class ReconcileItem(BaseModel):
    bond_id: str
    date: str                          # the SCHEDULED payment date (calendar key)


class ReconcileConfirmIn(BaseModel):
    items: list[ReconcileItem]
    # optional statement metadata → recorded in the upload history
    filename: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    owners: Optional[list[str]] = None
    credits: Optional[int] = None
    amount: Optional[float] = None


@router.post("/reconcile/confirm")
def reconcile_confirm(body: ReconcileConfirmIn):
    """Mark each confirmed (bond_id, scheduled_date) payment as received, and log
    the statement upload (who, when, dates covered, how many marked)."""
    _require_ready()
    marked = 0
    for it in body.items:
        if it.bond_id and it.date:
            store.set_payment_status(it.bond_id, it.date[:10], "received")
            marked += 1

    upload = None
    try:
        from ..auth import config as authcfg
        who = authcfg.primary_email()
    except Exception:
        who = None
    try:
        upload = store.add_upload({
            "filename": body.filename, "uploaded_by": who,
            "date_from": body.date_from, "date_to": body.date_to,
            "owners": body.owners or [], "credits": body.credits,
            "marked": marked, "amount": body.amount,
        })
    except Exception:
        pass
    return {"ok": True, "marked": marked, "upload": upload}


@router.get("/reconcile/uploads")
def reconcile_uploads():
    """History of statement uploads (newest first) — filename, who, dates covered."""
    return store.list_uploads()


@router.delete("/reconcile/uploads/{upload_id}")
def reconcile_delete_upload(upload_id: str):
    return {"deleted": store.delete_upload(upload_id)}
