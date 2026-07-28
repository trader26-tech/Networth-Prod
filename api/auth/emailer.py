"""
Sends the login OTP via Resend (https://resend.com).

Reads RESEND_API_KEY + OTP_FROM from the environment. If no API key is set
(local dev), the code is printed to the server log instead of emailed, so the
flow is testable without credentials. In that case send_otp returns False and
the route surfaces "check the server log" to the client.
"""
from __future__ import annotations

import os


def _read_env(key: str, default: str = "") -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
        if os.path.isfile(env_path):
            v = (dotenv_values(env_path).get(key) or "").strip()
            if v:
                return v
    except Exception:
        pass
    return default


def is_configured() -> bool:
    return bool(_read_env("RESEND_API_KEY"))


def _html(code: str) -> str:
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;max-width:440px;margin:0 auto;padding:32px 28px;background:#0f1117;border-radius:16px;color:#e7ebf3">
  <div style="font-size:20px;font-weight:700;color:#fff;margin-bottom:4px">networth.io</div>
  <div style="font-size:13px;color:#8a93a6;margin-bottom:24px">Secure sign-in code</div>
  <div style="font-size:40px;font-weight:800;letter-spacing:10px;color:#fff;background:#1a1e29;border-radius:12px;padding:18px 0;text-align:center">{code}</div>
  <div style="font-size:13px;color:#8a93a6;margin-top:22px;line-height:1.6">
    Enter this code to sign in. It expires in <b style="color:#c7cedd">5 minutes</b>.<br>
    If you didn't request this, you can safely ignore this email — no one can access your account without this code.
  </div>
</div>"""


def send_otp(to_email: str, code: str) -> bool:
    """Email the code. Returns True if it was actually sent via Resend."""
    api_key = _read_env("RESEND_API_KEY")
    sender = _read_env("OTP_FROM", "onboarding@resend.dev")

    if not api_key:
        print(f"\n{'='*52}\n  [networth.io OTP]  code for {to_email}: {code}\n  (RESEND_API_KEY not set — printed to log for dev)\n{'='*52}\n", flush=True)
        return False

    try:
        import httpx
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": f"networth.io <{sender}>",
                "to": [to_email],
                "subject": f"{code} is your networth.io sign-in code",
                "html": _html(code),
            },
            timeout=15,
        )
        if r.status_code >= 300:
            print(f"⚠ Resend error {r.status_code}: {r.text[:300]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"⚠ Resend send failed: {e}", flush=True)
        return False
