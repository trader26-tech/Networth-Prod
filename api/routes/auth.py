"""Auth / settings routes.

Two concerns live here:
  • App sign-in — email OTP → trusted device (httpOnly cookie) → short PIN,
    issuing the HMAC access tokens the rest of the API requires.
  • Kite / Supabase configuration (legacy, unchanged below).
"""
import secrets
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from api.models import SaveKeysRequest, ConnectTokenRequest, DirectTokenRequest
from api import state
from api.core.chain import invalidate_nfo_cache
from api.auth import config as authcfg, store as authstore, tokens as authtok, emailer

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/status")
def auth_status():
    from dotenv import dotenv_values
    vals       = dotenv_values(state.ENV_PATH)
    api_key    = vals.get("KITE_API_KEY", "").strip()
    has_secret = bool(vals.get("KITE_API_SECRET", "").strip().replace("YOUR_API_SECRET_HERE", ""))
    has_token  = bool(vals.get("KITE_ACCESS_TOKEN", "").strip().replace("YOUR_ACCESS_TOKEN_HERE", ""))
    has_key    = bool(api_key.replace("YOUR_API_KEY_HERE", ""))

    profile = None
    kite    = state.get_kite()
    if kite:
        try:
            profile = kite.profile()
        except Exception:
            pass

    return {
        "connected":      kite is not None,
        "has_api_key":    has_key,
        "has_api_secret": has_secret,
        "has_access_token": has_token,
        "api_key_hint":   (api_key[:4] + "****") if has_key else None,
        "profile":        profile,
    }


@router.get("/login-url")
def login_url():
    """One-click daily login: build the Kite login URL from the api_key already
    in .env, so you never re-type credentials. (Kite still requires the daily
    interactive login + 2FA — the access token expires every morning.)"""
    from dotenv import dotenv_values
    vals       = dotenv_values(state.ENV_PATH)
    api_key    = vals.get("KITE_API_KEY", "").strip().replace("YOUR_API_KEY_HERE", "")
    api_secret = vals.get("KITE_API_SECRET", "").strip().replace("YOUR_API_SECRET_HERE", "")
    if not api_key:
        raise HTTPException(400, "No KITE_API_KEY in .env — save your API key first.")
    if not api_secret:
        raise HTTPException(400, "No KITE_API_SECRET in .env — needed to exchange the token after login.")
    from kiteconnect import KiteConnect
    return {"login_url": KiteConnect(api_key=api_key).login_url(), "api_key_hint": api_key[:4] + "****"}


@router.post("/save-keys")
def save_keys(req: SaveKeysRequest):
    if not req.api_key or len(req.api_key) < 4:
        raise HTTPException(400, "Invalid API key")
    state.write_env(KITE_API_KEY=req.api_key, KITE_API_SECRET=req.api_secret)
    from kiteconnect import KiteConnect
    kite      = KiteConnect(api_key=req.api_key)
    login_url = kite.login_url()
    return {"login_url": login_url, "message": "Keys saved. Open login_url to authenticate."}


@router.post("/connect")
def connect_token(req: ConnectTokenRequest):
    from dotenv import dotenv_values
    vals       = dotenv_values(state.ENV_PATH)
    api_key    = vals.get("KITE_API_KEY", "").strip()
    api_secret = vals.get("KITE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        raise HTTPException(400, "API key and secret must be saved first")

    try:
        from kiteconnect import KiteConnect
        k       = KiteConnect(api_key=api_key)
        session = k.generate_session(req.request_token, api_secret=api_secret)
        token   = session["access_token"]
        k.set_access_token(token)
        profile = k.profile()
        state.write_env(KITE_ACCESS_TOKEN=token)
        state.set_kite(k)
        invalidate_nfo_cache()   # fresh session → discard any stale instrument list
        return {
            "status":    "connected",
            "user_name": profile.get("user_name"),
            "user_id":   profile.get("user_id"),
        }
    except Exception as e:
        msg = str(e).lower()
        if "checksum" in msg:
            raise HTTPException(400, (
                f"Checksum mismatch — the API Secret saved in .env does not match "
                f"the secret registered in the Kite developer console for API key "
                f"'{api_key[:4]}…'. "
                f"Go to developers.kite.trade → your app → copy the API Secret exactly "
                f"and enter it in Step 1 again."
            ))
        if "token" in msg and ("invalid" in msg or "expired" in msg):
            raise HTTPException(400, (
                "Request token is invalid or expired (tokens last only 5 minutes). "
                "Please click 'Open Kite Login' and complete the login again quickly."
            ))
        raise HTTPException(400, f"Authentication failed: {e}")


@router.post("/connect-direct")
def connect_direct(req: DirectTokenRequest):
    try:
        from kiteconnect import KiteConnect
        k = KiteConnect(api_key=req.api_key)
        k.set_access_token(req.access_token)
        profile = k.profile()
        state.write_env(KITE_API_KEY=req.api_key, KITE_ACCESS_TOKEN=req.access_token)
        state.set_kite(k)
        invalidate_nfo_cache()   # fresh session → discard any stale instrument list
        return {
            "status":    "connected",
            "user_name": profile.get("user_name"),
            "user_id":   profile.get("user_id"),
        }
    except Exception as e:
        raise HTTPException(400, f"Authentication failed: {e}")


@router.post("/disconnect")
def disconnect():
    state.set_kite(None)
    return {"status": "disconnected"}


# ==========================================================================
# Kite OAuth callback — one-click auto-login (Stocks + F&O)
# ==========================================================================
#
# This is the URL registered as the app's "Redirect URL" in the Kite developer
# console. After the user signs in on Kite, the browser lands here with
# ?request_token=…&status=success (&state=<nonce> echoed via redirect_params).
# We exchange the token server-side, store the session on every matching account
# (see api/auth/kite_oauth.py), then hand a tiny page back that pings the opener
# window so the app's login modal closes itself — no manual paste.
#
# Public (in _AUTH_PUBLIC): a top-level browser redirect from Kite can't carry
# the Bearer token. Safe — the exchange still needs the server-only api_secret,
# the state nonce is single-use, and the opener message carries no secrets.

def _callback_page(ok: bool, user_name: str = "", error: str = "") -> Response:
    """Self-closing HTML that reports the result to window.opener and closes."""
    import json as _json
    payload = _json.dumps({"source": "kite-login", "ok": bool(ok),
                           "user_name": user_name or "", "error": error or ""})
    heading = "✓ Signed in" if ok else "Login failed"
    sub = (f"Connected as {user_name}. You can close this window." if ok
           else (error or "Something went wrong.") + " You can close this window and try again.")
    color = "#16a34a" if ok else "#dc2626"
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Kite login</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  html,body{{height:100%;margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
    background:#0b0f17;color:#e5e7eb;display:flex;align-items:center;justify-content:center}}
  .card{{text-align:center;padding:2rem 2.5rem;max-width:420px}}
  h1{{font-size:1.35rem;margin:0 0 .5rem;color:{color}}}
  p{{margin:0 0 1.25rem;color:#9ca3af;line-height:1.5}}
  button{{background:#1f2937;color:#e5e7eb;border:1px solid #374151;border-radius:8px;
    padding:.6rem 1.2rem;font-size:.95rem;cursor:pointer}}
  button:hover{{background:#374151}}
</style></head><body>
  <div class="card">
    <h1>{heading}</h1>
    <p>{sub}</p>
    <button onclick="window.close()">Close window</button>
  </div>
<script>
  (function() {{
    var msg = {payload};
    try {{ if (window.opener) window.opener.postMessage(msg, window.location.origin); }} catch (e) {{}}
    // Auto-close shortly after a successful login; leave failures on screen.
    if (msg.ok) setTimeout(function() {{ try {{ window.close(); }} catch (e) {{}} }}, 900);
  }})();
</script>
</body></html>"""
    return Response(content=html, media_type="text/html")


@router.get("/kite/callback")
def kite_callback(request: Request):
    from api.auth import kite_oauth
    qp = request.query_params
    status = (qp.get("status") or "").lower()
    if status == "error":
        return _callback_page(False, error=qp.get("message") or "You cancelled the Kite login.")
    request_token = qp.get("request_token") or ""
    try:
        res = kite_oauth.complete_login(
            request_token,
            source=qp.get("src") or None,
            acc_id=qp.get("acc") or None,
            state_nonce=qp.get("st") or qp.get("state") or "",
        )
    except ValueError as e:
        return _callback_page(False, error=str(e))
    except Exception as e:
        return _callback_page(False, error=f"Unexpected error: {e}")
    return _callback_page(True, user_name=res.get("user_name") or "")


def _reset_all_supabase_clients():
    """Re-read env vars in every Supabase-backed store after the config changes."""
    for mod_path in (
        "api.cc_supabase_store",
        "api.hedges_supabase_store",
        "api.land.store",
    ):
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            mod.reset_client_cache()
        except Exception:
            pass


# ==========================================================================
# App sign-in: email OTP → trusted device (cookie) → PIN → access tokens
# ==========================================================================
#
# Token model (see api/auth/tokens.py for the why):
#   • Device token  — long-lived (30d), httpOnly cookie, proves "trusted device".
#   • Access token  — short-lived (= the device's auto-lock window), bearer on
#                     every API call, slides forward while active, dies on idle.
#
# Flow: request-otp → verify-otp (mints device cookie + first access token) →
# set-pin. Later visits: unlock with PIN (uses the cookie, no email). Active use:
# refresh slides the access token; idle past the window → next call 401 → PIN.


class _EmailBody(BaseModel):
    email: str = ""        # optional — single-user app defaults to the allowlist


class _VerifyBody(BaseModel):
    email: str = ""        # optional — defaults to the primary allowlisted email
    code: str


class _PinBody(BaseModel):
    pin: str


class _LockBody(BaseModel):
    lock_minutes: int


def _set_device_cookie(resp: Response, raw: str) -> None:
    resp.set_cookie(
        authcfg.DEVICE_COOKIE, raw,
        max_age=authcfg.DEVICE_TTL_DAYS * 86400,
        httponly=True, secure=True, samesite="none", path="/",
    )


def _clear_device_cookie(resp: Response) -> None:
    resp.delete_cookie(authcfg.DEVICE_COOKIE, path="/", samesite="none", secure=True)


def _device_from_access(request: Request) -> str:
    """Return the device_id from a valid Bearer access token, or 401.
    (The middleware already validated it; this just reads the payload.)"""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    payload = authtok.verify_access(token)
    if not payload:
        raise HTTPException(401, "Session expired")
    return payload["did"]


def _gen_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _mask_email(email: str) -> str:
    try:
        name, dom = email.split("@", 1)
        shown = name[0] + "•••" + (name[-1] if len(name) > 1 else "")
        return f"{shown}@{dom}"
    except Exception:
        return "your email"


@router.get("/whoami")
def whoami(request: Request):
    """
    Multi-user context for the client: whether multi-user is on, whether open
    registration is available, and — if signed in — who the current user is.
    Identity comes from request.state (set by the auth middleware).
    """
    # whoami is a public route, so identity isn't pre-set on request.state —
    # resolve it here from the bearer/cookie when present.
    email = getattr(request.state, "email", None)
    if not email:
        # whoami is public (pre-login), so a missing/expired token is normal —
        # resolve identity if a valid token happens to be present, never raise.
        auth = request.headers.get("authorization", "")
        tok = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        payload = authtok.verify_access(tok) if tok else None
        if payload:
            dev = authstore.device_get(payload.get("did")) or {}
            email = dev.get("email")

    user = None
    if authcfg.multi_user() and email:
        from ..auth import users as authusers

        u = authusers.get_by_email(email)
        if u:
            user = {
                "id": u.get("id"),
                "email": _mask_email(u.get("email") or ""),
                "display_name": u.get("display_name"),
                "status": u.get("status"),
                "is_admin": bool(u.get("is_admin")),
            }
    return {
        "multi_user": authcfg.multi_user(),
        "data_isolated": authcfg.data_isolated(),
        "registration_open": authcfg.registration_open(),
        "user": user,
    }


@router.get("/session")
def auth_session(request: Request):
    """Bootstrap state for the lock screen — looks at the device cookie only.
    Never returns data; just enough to pick the right screen."""
    # The cookie holds "device_id.token" so we can look the device up here.
    raw = request.cookies.get(authcfg.DEVICE_COOKIE, "")
    dev = None
    if "." in raw:
        did, tok = raw.split(".", 1)
        dev = authstore.device_valid(did, tok)
    signin_email = _mask_email(authcfg.primary_email())
    if not dev:
        return {"device_known": False, "has_pin": False, "email": None,
                "signin_email": signin_email,
                "lock_minutes": authcfg.DEFAULT_LOCK_MINUTES,
                "lock_presets": authcfg.LOCK_PRESETS}
    return {
        "device_known": True,
        "has_pin": bool(dev.get("pin_hash")),
        "email": _mask_email(dev.get("email") or ""),
        "signin_email": signin_email,
        "lock_minutes": int(dev.get("lock_minutes") or authcfg.DEFAULT_LOCK_MINUTES),
        "lock_presets": authcfg.LOCK_PRESETS,
    }


@router.post("/request-otp")
def request_otp(body: _EmailBody):
    # Single-user: no email from client → the allowlisted address.
    email = (body.email or "").strip().lower() or authcfg.primary_email()

    # Decide eligibility via the policy (single-user allowlist, or multi-user
    # registration rules with the isolation interlock).
    may, _reason = authcfg.may_request_code(email)
    if may:
        ok, msg = authstore.otp_rate_ok(email)
        if not ok:
            raise HTTPException(429, msg)
        # In multi-user mode, record a pending user so a correct code can
        # activate the account. Email-validated: still 'pending' until verify.
        if authcfg.multi_user():
            from ..auth import users as authusers

            authusers.ensure_pending(email)
        code = _gen_code()
        authstore.otp_put(email, code)
        emailed = emailer.send_otp(email, code)
    else:
        # Don't reveal whether an address is eligible — same shape either way.
        emailed = True
    return {"ok": True, "emailed": bool(emailed), "masked": _mask_email(email)}


@router.post("/verify-otp")
def verify_otp(body: _VerifyBody, response: Response):
    email = (body.email or "").strip().lower() or authcfg.primary_email()
    code = (body.code or "").strip()
    # Same eligibility gate as request-otp — a code for an ineligible email is
    # rejected as "incorrect" (no info leak about who may sign in).
    may, _reason = authcfg.may_request_code(email)
    if not may:
        raise HTTPException(401, "Incorrect code.")
    ok, msg = authstore.otp_check(email, code)
    if not ok:
        raise HTTPException(401, msg)

    # Correct code == email proven. Activate the user (email-validated sign-up).
    if authcfg.multi_user():
        from ..auth import users as authusers

        authusers.ensure_pending(email)
        authusers.mark_verified(email)

    device_id, raw = authstore.device_create(email)
    _set_device_cookie(response, f"{device_id}.{raw}")
    token, exp = authtok.mint_access(device_id, authcfg.DEFAULT_LOCK_MINUTES * 60)
    return {
        "access_token": token, "expires_at": exp,
        "lock_minutes": authcfg.DEFAULT_LOCK_MINUTES,
        "has_pin": False, "email": _mask_email(email),
        "lock_presets": authcfg.LOCK_PRESETS,
    }


@router.post("/set-pin")
def set_pin(body: _PinBody, request: Request):
    """Set or change the PIN for the current device (requires a live session)."""
    device_id = _device_from_access(request)
    pin = (body.pin or "").strip()
    if not pin.isdigit() or not (authcfg.PIN_MIN_LEN <= len(pin) <= authcfg.PIN_MAX_LEN):
        raise HTTPException(400, f"PIN must be {authcfg.PIN_MIN_LEN}–{authcfg.PIN_MAX_LEN} digits.")
    if not authstore.device_get(device_id):
        raise HTTPException(401, "Device not recognised.")
    authstore.device_set_pin(device_id, pin)
    return {"ok": True}


@router.post("/unlock")
def unlock(body: _PinBody, request: Request, response: Response):
    """Lift the idle lock using the device cookie + PIN — no email needed."""
    raw = request.cookies.get(authcfg.DEVICE_COOKIE, "")
    if "." not in raw:
        raise HTTPException(401, "Device not recognised — sign in with email.")
    did, tok = raw.split(".", 1)
    dev = authstore.device_valid(did, tok)
    if not dev:
        raise HTTPException(401, "Device not recognised — sign in with email.")
    ok, msg = authstore.device_check_pin(did, (body.pin or "").strip())
    if not ok:
        raise HTTPException(401, msg)
    authstore.device_touch(did)
    lock_minutes = int(dev.get("lock_minutes") or authcfg.DEFAULT_LOCK_MINUTES)
    token, exp = authtok.mint_access(did, lock_minutes * 60)
    return {"access_token": token, "expires_at": exp, "lock_minutes": lock_minutes}


@router.post("/refresh")
def refresh(request: Request):
    """Slide the access token forward while the user is active. Requires a still-
    valid access token (the middleware enforces this), so once idle has expired
    the token, refresh fails and the PIN is required."""
    device_id = _device_from_access(request)
    dev = authstore.device_get(device_id)
    if not dev or dev.get("revoked"):
        raise HTTPException(401, "Device revoked.")
    lock_minutes = int(dev.get("lock_minutes") or authcfg.DEFAULT_LOCK_MINUTES)
    token, exp = authtok.mint_access(device_id, lock_minutes * 60)
    return {"access_token": token, "expires_at": exp, "lock_minutes": lock_minutes}


@router.post("/logout")
def logout(request: Request, response: Response):
    """Revoke and forget THIS device only (other devices stay signed in)."""
    raw = request.cookies.get(authcfg.DEVICE_COOKIE, "")
    if "." in raw:
        did = raw.split(".", 1)[0]
        authstore.device_delete(did)
    _clear_device_cookie(response)
    return {"ok": True}


@router.get("/settings")
def get_security_settings(request: Request):
    device_id = _device_from_access(request)
    dev = authstore.device_get(device_id) or {}
    return {
        "lock_minutes": int(dev.get("lock_minutes") or authcfg.DEFAULT_LOCK_MINUTES),
        "lock_presets": authcfg.LOCK_PRESETS,
        "has_pin": bool(dev.get("pin_hash")),
        "email": _mask_email(dev.get("email") or ""),
        "email_delivery": "resend" if emailer.is_configured() else "console (dev)",
    }


@router.post("/settings")
def set_security_settings(body: _LockBody, request: Request):
    device_id = _device_from_access(request)
    mins = int(body.lock_minutes)
    if not (authcfg.MIN_LOCK_MINUTES <= mins <= authcfg.MAX_LOCK_MINUTES):
        raise HTTPException(400, f"Auto-lock must be {authcfg.MIN_LOCK_MINUTES}–{authcfg.MAX_LOCK_MINUTES} minutes.")
    if not authstore.device_get(device_id):
        raise HTTPException(401, "Device not recognised.")
    authstore.device_update(device_id, lock_minutes=mins)
    return {"ok": True, "lock_minutes": mins}


@router.get("/supabase-config")
def get_supabase_config():
    """Current Supabase connection settings for the editable Settings form.

    The URL is returned in full (not a secret); the service key is never echoed
    back — we only report whether one is set + a short masked hint. The form
    stays editable: leaving the key field blank on save keeps the existing key.
    """
    import os
    from dotenv import dotenv_values
    vals = {**dotenv_values(state.ENV_PATH), **{k: os.environ.get(k, "") for k in
            ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_KEY")}}
    url     = (vals.get("SUPABASE_URL") or "").strip()
    svc_key = (vals.get("SUPABASE_SERVICE_KEY") or "").strip()
    anon    = (vals.get("SUPABASE_KEY") or "").strip()
    key     = svc_key or anon
    hint    = (key[:6] + "…" + key[-4:]) if len(key) > 12 else ("set" if key else "")
    return {
        "url":      url,
        "key_set":  bool(key),
        "key_kind": "service" if svc_key else ("anon" if anon else None),
        "key_hint": hint,
    }


@router.post("/supabase-config")
def save_supabase_config(body: dict):
    """Persist a new Supabase URL / service key from the UI, then hot-reload all
    Supabase clients so the change takes effect without a server restart."""
    import os
    url = (body.get("url") or "").strip().rstrip("/")
    key = (body.get("service_key") or "").strip()

    if url and not url.startswith("https://"):
        raise HTTPException(400, "URL must start with https:// (e.g. https://abc.supabase.co)")

    updates: dict[str, str] = {}
    if url:
        updates["SUPABASE_URL"] = url
        os.environ["SUPABASE_URL"] = url
    # Blank key field → keep the existing key (editable without forcing re-entry).
    if key:
        updates["SUPABASE_SERVICE_KEY"] = key
        os.environ["SUPABASE_SERVICE_KEY"] = key

    if updates:
        state.write_env(**updates)
    _reset_all_supabase_clients()
    # Re-probe and hand back the same shape /supabase-status returns.
    return supabase_status(retest=True)


@router.get("/supabase-status")
def supabase_status(retest: bool = False):
    """Tell the UI whether Supabase is configured and reachable.

    Returns:
      configured     : bool — env vars present
      key_kind       : 'service' | 'anon' | None — which key the backend will use
      url            : str  — masked Supabase URL (or None)
      reachable      : bool — was the table queryable?
      table_exists   : bool — did the covered_call_positions table respond?
      row_count      : int  — only set when reachable
      error          : str  — populated on failure
    """
    import os
    url     = os.environ.get("SUPABASE_URL", "").strip()
    svc_key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    anon    = os.environ.get("SUPABASE_KEY", "").strip()
    key     = svc_key or anon
    key_kind = "service" if svc_key else ("anon" if anon else None)

    if not url or not key:
        return {
            "configured":   False,
            "key_kind":     None,
            "url":          None,
            "reachable":    False,
            "table_exists": False,
            "message":      "Not configured — set SUPABASE_URL and SUPABASE_KEY (or SUPABASE_SERVICE_KEY) in env vars. Falling back to local JSON file.",
        }

    # Mask the URL/key for the UI (show enough to identify which project)
    masked_url = url.replace("https://", "").split(".")[0][:8] + "…supabase.co"

    try:
        from api import cc_supabase_store
        if retest:
            cc_supabase_store.reset_client_cache()
            # Also reset the hedges Supabase client so it re-reads env vars
            try:
                from api import hedges_supabase_store
                hedges_supabase_store.reset_client_cache()
            except Exception:
                pass
        client = cc_supabase_store._get_client()
        if client is None:
            return {
                "configured":   True,
                "key_kind":     key_kind,
                "url":          masked_url,
                "reachable":    False,
                "table_exists": False,
                "error":        "Client failed to initialise (check supabase-py is installed and URL/KEY are valid)",
            }
        # Plain probe: just fetch one row. Avoid count=exact since the API
        # syntax for that varies by supabase-py version and triggers PGRST125
        # on some configurations.
        res = client.table(cc_supabase_store.TABLE).select("id").limit(1).execute()
        # Best-effort row count (uses len because count=exact would re-trigger PGRST125)
        try:
            full = client.table(cc_supabase_store.TABLE).select("id").execute()
            row_count = len(full.data or [])
        except Exception:
            row_count = len(res.data or [])
        return {
            "configured":   True,
            "key_kind":     key_kind,
            "url":          masked_url,
            "reachable":    True,
            "table_exists": True,
            "row_count":    row_count,
            "message":      f"Connected to Supabase ({masked_url}) using {key_kind} key — {row_count} positions stored.",
        }
    except Exception as e:
        msg = str(e)
        # Common errors → friendlier hints
        lower = msg.lower()
        hint = ""
        if "relation" in lower and "does not exist" in lower:
            hint = " — run the CREATE TABLE SQL from SUPABASE.md in Supabase → SQL Editor"
        elif "jwt" in lower or "invalid api key" in lower:
            hint = " — check the SUPABASE_KEY value (copy it again from Project Settings → API)"
        elif "pgrst125" in lower or "invalid path" in lower:
            hint = " — check SUPABASE_URL has no trailing slash and exactly matches your project URL"
        elif "pgrst106" in lower or "schema cache" in lower:
            hint = " — table exists in a different schema; run the SQL from SUPABASE.md to create it in `public`"
        return {
            "configured":   True,
            "key_kind":     key_kind,
            "url":          masked_url,
            "reachable":    False,
            "table_exists": "does not exist" not in lower,
            "error":        msg[:300] + hint,
        }
