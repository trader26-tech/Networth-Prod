"""
Daily reminders digest → email via Resend.

Builds one tidy email of everything pending that needs attention (overdue +
due in the next 7 days) and sends it to the configured recipient. Reuses the
same Resend setup as the login OTP (RESEND_API_KEY / OTP_FROM). If no API key
is set the digest is printed to the server log instead, so the flow is testable
locally without credentials.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from . import aggregate

# Where the digest goes. Override with REMINDERS_TO in the environment.
DEFAULT_TO = "ranjeev2003@gmail.com"


def _read_env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(root, ".env")
        if os.path.isfile(env_path):
            v = (dotenv_values(env_path).get(key) or "").strip()
            if v:
                return v
    except Exception:
        pass
    return default


def _inr(x: float) -> str:
    return "₹" + f"{round(float(x or 0)):,}"


def digest_items(today=None, days_ahead: int = 7) -> dict:
    """Overdue + next-`days_ahead`-days pending reminders, split in/out — limited
    to the categories the user has opted into (the "Notify me about" settings)."""
    from . import store as ov_store
    today = today or date.today()
    feed = aggregate.build_feed(today)
    horizon = (today + timedelta(days=days_ahead)).isoformat()
    today_iso = today.isoformat()
    muted = set(ov_store.get_muted_items())
    due = [i for i in feed["items"]
           if i["status"] == "pending" and i["date"] <= horizon
           and ov_store.category_enabled(i.get("category", ""))
           and f"{i.get('source','')}:{i.get('source_id','')}" not in muted]
    due.sort(key=lambda x: (x["date"], 0 if x["direction"] == "in" else 1))
    inflow = [i for i in due if i["direction"] == "in"]
    outflow = [i for i in due if i["direction"] == "out"]
    return {
        "today": today_iso, "due": due, "inflow": inflow, "outflow": outflow,
        "in_total": round(sum(i["amount"] for i in inflow), 2),
        "out_total": round(sum(i["amount"] for i in outflow), 2),
        "overdue": [i for i in due if i["date"] < today_iso],
    }


def _fmt_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%a %d %b")
    except Exception:
        return iso


def _row_html(it: dict, today_iso: str) -> str:
    overdue = it["date"] < today_iso
    color = "#e53935" if it["direction"] == "out" else "#16a085"
    sign = "−" if it["direction"] == "out" else "+"
    date_col = ("<span style='color:#e53935;font-weight:700'>" + _fmt_date(it["date"]) + " · overdue</span>") if overdue else _fmt_date(it["date"])
    return (
        "<tr>"
        f"<td style='padding:8px 10px;font-size:13px;color:#5b6270;white-space:nowrap'>{date_col}</td>"
        f"<td style='padding:8px 10px;font-size:13px;color:#1a1c2e'><b>{it['label']}</b><br>"
        f"<span style='font-size:11px;color:#8a93a6'>{it['kind']} · {it['sub']}</span></td>"
        f"<td style='padding:8px 10px;font-size:14px;font-weight:800;color:{color};text-align:right;white-space:nowrap'>{sign}{_inr(it['amount'])}</td>"
        "</tr>"
    )


def build_html(d: dict) -> str:
    today_iso = d["today"]
    rows = "".join(_row_html(it, today_iso) for it in d["due"])
    when = _fmt_date(today_iso)
    n_over = len(d["overdue"])
    over_line = (f"<div style='background:#fdecea;color:#c23a2b;border-radius:10px;padding:10px 14px;font-size:13px;font-weight:700;margin-bottom:16px'>⚠ {n_over} payment(s) overdue</div>"
                 if n_over else "")
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:560px;margin:0 auto;padding:28px 24px;background:#f5f7fb;border-radius:16px;color:#1a1c2e">
  <div style="font-size:20px;font-weight:800;color:#1a1c2e">networth.io</div>
  <div style="font-size:13px;color:#6b7190;margin-bottom:18px">Reminders · {when}</div>
  {over_line}
  <div style="display:flex;gap:12px;margin-bottom:16px">
    <div style="flex:1;background:#fff;border:1px solid #e0e4f0;border-radius:12px;padding:12px 14px">
      <div style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#8a93a6;font-weight:700">Coming in (7d)</div>
      <div style="font-size:20px;font-weight:800;color:#16a085">+{_inr(d['in_total'])}</div>
    </div>
    <div style="flex:1;background:#fff;border:1px solid #e0e4f0;border-radius:12px;padding:12px 14px">
      <div style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;color:#8a93a6;font-weight:700">Going out (7d)</div>
      <div style="font-size:20px;font-weight:800;color:#e53935">−{_inr(d['out_total'])}</div>
    </div>
  </div>
  <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e0e4f0;border-radius:12px;overflow:hidden">
    {rows if rows else "<tr><td style='padding:18px;text-align:center;color:#8a93a6;font-size:13px'>Nothing due in the next 7 days 🎉</td></tr>"}
  </table>
  <div style="font-size:12px;color:#8a93a6;margin-top:18px;line-height:1.6">
    You're getting this because reminders are on. Open the app to mark items received/paid or move a date.
  </div>
</div>"""


def send_digest(to_email: str | None = None, today=None) -> dict:
    """Build + send the daily digest. Returns {sent, reason, count}. Sending is
    skipped (not an error) when there's nothing due, so a quiet day is quiet."""
    to_email = (to_email or _read_env("REMINDERS_TO", DEFAULT_TO)).strip()
    d = digest_items(today)
    if not d["due"]:
        return {"sent": False, "reason": "nothing_due", "count": 0}

    api_key = _read_env("RESEND_API_KEY")
    sender = _read_env("OTP_FROM", "onboarding@resend.dev")
    subject = f"Reminders · {len(d['due'])} due" + (f" · {len(d['overdue'])} overdue" if d["overdue"] else "")
    html = build_html(d)

    if not api_key:
        print(f"\n{'='*56}\n  [reminders digest → {to_email}]  {subject}\n  (RESEND_API_KEY not set — printed to log for dev)\n{'='*56}\n", flush=True)
        return {"sent": False, "reason": "no_api_key", "count": len(d["due"])}

    try:
        import httpx
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"from": sender, "to": [to_email], "subject": subject, "html": html},
            timeout=15.0,
        )
        ok = r.status_code < 300
        return {"sent": ok, "reason": "ok" if ok else f"resend_{r.status_code}", "count": len(d["due"])}
    except Exception as e:
        return {"sent": False, "reason": f"error:{type(e).__name__}", "count": len(d["due"])}
