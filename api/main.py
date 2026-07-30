"""
Zerodha Paper Trading API — FastAPI entry point.

This file is intentionally thin: it wires up the app, registers all routers,
and bootstraps shared state.
"""
import os, sys, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Force unbuffered stdout/stderr so Railway/Docker log streams show our prints
# in real time (rather than buffering them and only flushing at exit).
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Load .env into os.environ as early as possible. Without this, modules that
# read `os.environ.get("SUPABASE_URL")` (and friends) at import time would see
# nothing in local dev — even though the .env file is right there on disk.
# On Railway the env vars are already in process env, so override=False is
# important: we don't want .env (committed by mistake) to clobber Railway vars.
try:
    from dotenv import load_dotenv
    _ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.isfile(_ENV_PATH):
        load_dotenv(_ENV_PATH, override=False)
        print(f"▶ Loaded .env from {_ENV_PATH}")
    else:
        print(f"ⓘ No .env file at {_ENV_PATH} — relying on process environment only")
except Exception as e:
    print(f"⚠ load_dotenv skipped: {e}")

print("─" * 60)
print(f"▶ Booting Zerodha API · python {sys.version.split()[0]} · cwd={os.getcwd()}")
print(f"▶ PORT env = {os.environ.get('PORT', '(unset, default 8000)')}")
print(f"▶ SUPABASE_URL = {'set' if os.environ.get('SUPABASE_URL') else '(unset)'}")
print(f"▶ SUPABASE_KEY = {'set' if os.environ.get('SUPABASE_KEY') or os.environ.get('SUPABASE_SERVICE_KEY') else '(unset)'}")
print("─" * 60)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import state
from api.paper_engine import PaperEngine
from api import mock_market

# ── Bootstrap ──────────────────────────────────────────────────────────────────

def _ltp(exchange: str, symbol: str):
    kite = state.get_kite()
    if kite:
        try:
            key = f"{exchange}:{symbol}"
            r   = kite.ltp([key])
            return r.get(key, {}).get("last_price")
        except Exception:
            pass
    return mock_market._drift(symbol)


engine = PaperEngine(price_fn=_ltp)
state.set_engine(engine)

state.load_kite()
if not state.get_kite():
    print("✓ Running with mock market data (set KITE_API_KEY in env vars to use live data)")

# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(title="networth.io API", version="2.0.0")
# allow_origin_regex (not allow_origins=["*"]) so credentialed requests — needed
# for the httpOnly device cookie — get a reflected Access-Control-Allow-Origin,
# which the spec requires when allow_credentials is True.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=".*",
    allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


# ── Auth guard ───────────────────────────────────────────────────────────────
# Every /api/* request must carry a valid Bearer access token, EXCEPT the public
# auth handshake + health. This is what actually protects the data — the lock
# screen alone never could. WebSocket (/ws/*) and the SPA are not under /api.
from starlette.responses import JSONResponse
from api.auth import tokens as _authtok

_AUTH_PUBLIC = {
    "/api/health",
    "/api/auth/session",
    "/api/auth/whoami",   # multi-user flags + masked identity for the lock screen
    "/api/auth/request-otp",
    "/api/auth/verify-otp",
    "/api/auth/unlock",
    "/api/auth/kite/callback",   # Kite OAuth postback — a top-level redirect can't carry the bearer

    "/api/fno/cron/tick",   # external per-minute P&L-capture heartbeat (own FNO_CRON_KEY)
}


@app.middleware("http")
async def _auth_guard(request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or not path.startswith("/api/") or path in _AUTH_PUBLIC:
        return await call_next(request)

    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    # Browser-native loads — <img src>, <iframe src>, an <a href> "open in new
    # tab", or a plain download — can't set an Authorization header, so they
    # can't carry the bearer the interceptor adds to XHR. For those (e.g. the
    # document vault's file streams) we also accept the same HMAC access token
    # passed as an ?access_token= query param. It's no weaker than the header:
    # both are verified identically below.
    if not token:
        token = (request.query_params.get("access_token") or "").strip()
    if token:
        payload = _authtok.verify_access(token)
        if payload:
            # Resolve identity onto request.state so downstream code (and the
            # coming per-user data isolation) can scope by the signed-in user.
            # Only in multi-user mode — single-user has one dataset and this
            # would add a per-request DB lookup for no benefit. Failure here
            # must never block a valid token; identity is additive.
            request.state.device_id = payload.get("did")
            try:
                from api.auth import config as _acfg

                if _acfg.multi_user():
                    from api.auth import store as _astore, users as _ausers

                    dev = _astore.device_get(payload.get("did")) or {}
                    request.state.email = dev.get("email")
                    u = _ausers.get_by_email(dev.get("email")) if dev.get("email") else None
                    request.state.user_id = u.get("id") if u else None
            except Exception:
                pass
            return await call_next(request)

    # This guard sits OUTSIDE CORSMiddleware (Starlette's add_middleware inserts
    # at index 0, so the decorator-added guard wraps CORS). A short-circuit 401
    # therefore never reaches CORS, so we add the CORS headers ourselves — else
    # the browser hides the 401 behind a CORS error and our interceptor can't
    # see it to drop to the lock screen.
    resp = JSONResponse({"detail": "Authentication required"}, status_code=401)
    origin = request.headers.get("origin")
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Vary"] = "Origin"
    return resp


# ── Lightweight health probes (BEFORE routers, in case any route fails) ──────
# /__healthz is a no-state, no-deps liveness probe — useful as a fallback
# health-check target on Railway if /api/health ever becomes flaky.
@app.get("/__healthz", include_in_schema=False)
def _healthz():
    return {"ok": True}


@app.get("/api/health")
def health():
    return {
        "status":         "ok",
        "mode":           "paper",
        "kite_connected": state.get_kite() is not None,
    }


# ── Routers (defensive — a single bad import won't crash boot) ───────────────
def _safe_register(import_path: str, attr: str = "router") -> None:
    """Import a router module and include it. Prints (but does not raise) on
    failure so the rest of the app still boots."""
    try:
        mod = __import__(import_path, fromlist=[attr])
        app.include_router(getattr(mod, attr))
        print(f"  ✓ {import_path}")
    except Exception as e:
        print(f"  ✗ {import_path}  →  {type(e).__name__}: {e}")
        traceback.print_exc()


print("▶ Registering routers:")
for _path in (
    "api.routes.auth",
    "api.routes.market",
    "api.routes.account",
    "api.routes.strategy",
    "api.routes.options",
    "api.routes.scanners",
    "api.routes.gtt",
    "api.routes.paper",
    "api.routes.ws",
    "api.routes.covered_call",
    "api.routes.protected_wheel",
    "api.routes.hedges",
    "api.routes.cc_simulator",
    "api.routes.cc_math",
    "api.routes.portfolio",
    "api.routes.fundamentals",
    "api.routes.smart_money",
    "api.routes.scorecard",
    "api.routes.exit_strategy",
    "api.routes.networth",
    "api.routes.people",
    "api.routes.land",
    "api.routes.apartments",
    "api.routes.built",
    "api.routes.gold",
    "api.routes.stocks",
    "api.routes.brokerage",
    "api.routes.equity",
    "api.routes.bonds",
    "api.routes.salary",
    "api.routes.ulip",
    "api.routes.lic",
    "api.routes.tax",
    "api.routes.fd",
    "api.routes.loans",
    "api.routes.expenses",
    "api.routes.other_income",
    "api.routes.planner",
    "api.routes.cash",
    "api.routes.house",
    "api.routes.documents",
    "api.routes.dashboard",
    "api.routes.reminders",
    "api.routes.ai",
    "api.routes.fno",
):
    _safe_register(_path)
_safe_register("api.routes.fno", "ws_router")   # /ws/fno lives outside /api
print("─" * 60)

# ── MCP server (Model Context Protocol) — remote connector for Claude ─────────
# Built here, but COMBINED with the app at the very bottom (after the SPA route)
# so it keeps its own auth middleware + state. Gated on env vars.
_mcp_app = None
try:
    from api.mcp_server import mcp_enabled, build_http_app
    if mcp_enabled():
        _mcp_app = build_http_app(path="/mcp")
        print("  ✓ MCP server built — will serve at /mcp (Google OAuth)")
    else:
        print("  ⓘ MCP server disabled — set GOOGLE_MCP_CLIENT_ID / GOOGLE_MCP_CLIENT_SECRET / MCP_BASE_URL to enable")
except Exception as _e:
    print(f"  ✗ MCP server  →  {type(_e).__name__}: {_e}")
    traceback.print_exc()
print("─" * 60)

# ── Daily reminders digest (background thread; never blocks boot) ─────────────
# Emails "what's due" once a day (default ~08:00 IST). Self-contained — no
# external cron: a daemon thread wakes each hour and fires once per calendar day.
try:
    from api.reminders import scheduler as _rem_sched
    _rem_sched.start()
    print("  ✓ reminders digest scheduler started")
except Exception as _e:
    print(f"  ✗ reminders digest scheduler  →  {type(_e).__name__}: {_e}")

# ── Daily declared-dividend sync (background thread; never blocks boot) ───────
# Reads NSE corporate actions once a day and logs new dividends for held stocks.
try:
    from api.corporate_actions import scheduler as _div_sched
    _div_sched.start()
    print("  ✓ dividend sync scheduler started")
except Exception as _e:
    print(f"  ✗ dividend sync scheduler  →  {type(_e).__name__}: {_e}")
print("─" * 60)

# ── Prewarm the stock portfolio cache (background; never blocks boot) ──────────
try:
    from api.portfolio import engine as _eq_engine
    _eq_engine.start_refresher()
    print("  ✓ portfolio refresher started")
except Exception as _e:
    print(f"  ✗ portfolio refresher  →  {type(_e).__name__}: {_e}")

# ── F&O live P&L engine (per-second poll, 1-min snapshots; never blocks boot) ──
try:
    from api.fno import engine as _fno_engine
    _fno_engine.start()
    print("  ✓ F&O live P&L engine started")
except Exception as _e:
    print(f"  ✗ F&O live P&L engine  →  {type(_e).__name__}: {_e}")
print("─" * 60)


# ── Static frontend serving (production) ──────────────────────────────────────
# In production we ship a single service: FastAPI serves the built Angular at
# / and the API at /api/*. Any request that's not an /api or /ws path falls
# through to the SPA's index.html so client-side routing works on refresh.
#
# Build with:   cd frontend && npm run build -- --configuration=production
# Then run:     uvicorn api.main:app --host 0.0.0.0 --port 8000
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Angular 17+ outputs to dist/<project>/browser; older builds use dist/<project>
_FRONTEND_CANDIDATES = [
    _REPO_ROOT / "frontend" / "dist" / "frontend" / "browser",
    _REPO_ROOT / "frontend" / "dist" / "frontend",
]
_FRONTEND_DIR = next((p for p in _FRONTEND_CANDIDATES if (p / "index.html").exists()), None)

if _FRONTEND_DIR:
    print(f"✓ Serving Angular from {_FRONTEND_DIR}")

    # index.html must NEVER be cached: browsers heuristically cache responses
    # with an etag but no Cache-Control, so after a deploy they keep reusing the
    # old index.html — which points at the *old* hashed JS bundle — and users
    # run stale code forever. `no-cache` forces a revalidation each load (cheap:
    # the etag returns 304 when unchanged) so a new deploy is picked up at once.
    # The hashed build assets (main-<hash>.js, styles-<hash>.css, …) are content-
    # addressed and immutable, so those we cache hard.
    _NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}
    _IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}

    def _looks_hashed(name: str) -> bool:
        # Angular emits <name>-<HASH>.<ext>; the hash is 8+ upper/lower/digits.
        import re
        return bool(re.search(r"-[A-Za-z0-9]{8,}\.(js|css|woff2?|ttf|eot)$", name))

    def _index_response():
        return FileResponse(_FRONTEND_DIR / "index.html", headers=_NO_CACHE)

    # Catch-all that:
    #   1. lets API + WebSocket + healthz routes through (404 here, real router handles)
    #   2. serves a real file if it exists in dist/
    #   3. falls back to index.html (SPA routing)
    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa_fallback(full_path: str):
        if full_path.startswith((
            "api/", "ws/", "ws", "docs", "redoc", "openapi.json", "__healthz",
        )):
            return Response(status_code=404)
        candidate = _FRONTEND_DIR / full_path
        if candidate.is_file():
            # index.html itself must revalidate; hashed assets can cache forever;
            # anything else (favicon, manifest, sw, …) gets the safe no-cache default.
            if candidate.name == "index.html":
                return _index_response()
            headers = _IMMUTABLE if _looks_hashed(full_path) else _NO_CACHE
            # correct content-type for the PWA manifest (mimetypes doesn't know it)
            mt = "application/manifest+json" if candidate.suffix == ".webmanifest" else None
            return FileResponse(candidate, headers=headers, media_type=mt)
        if (_FRONTEND_DIR / "index.html").is_file():
            return _index_response()
        return Response("Frontend build not found. Run `cd frontend && npm run build`.", status_code=404)
else:
    print("ⓘ Frontend build not found — running API-only mode (dev)")
    for cand in _FRONTEND_CANDIDATES:
        print(f"   tried: {cand}  →  {'exists' if cand.exists() else 'missing'}")

print("✓ App ready.")


# ── Combine the MCP app with the main app (ASGI-level, no path rewriting) ─────
# The MCP app is a full Starlette app whose auth (BearerAuthBackend) + token-swap
# validation live in ITS middleware stack and app.state — so we must serve the
# WHOLE app for MCP-owned paths, not just copy its routes (that dropped the auth
# middleware → freshly-issued tokens failed validation in a loop). This dispatcher
# passes the scope through UNCHANGED (so /mcp, /authorize, /.well-known/… keep
# their exact paths) and drives both apps' lifespans.
if _mcp_app is not None:
    import contextlib as _contextlib

    _MCP_EXACT = {"/mcp", "/mcp/", "/authorize", "/token", "/register", "/consent", "/auth/callback"}
    _MCP_PREFIX = ("/.well-known/oauth-authorization-server",
                   "/.well-known/oauth-protected-resource")

    def _is_mcp_path(path: str) -> bool:
        return path in _MCP_EXACT or path.startswith(_MCP_PREFIX)

    _fastapi_app = app                      # the fully-built FastAPI app (API + SPA)
    _mcp = _mcp_app

    # Whether the MCP session-manager lifespan actually started. If it doesn't,
    # we still boot (MCP paths just fall back to the main app) — the connector is
    # never allowed to take the whole deploy down.
    _mcp_live = {"ok": False}

    async def _combined(scope, receive, send):
        stype = scope.get("type")
        if stype == "lifespan":
            async with _contextlib.AsyncExitStack() as stack:
                msg = await receive()
                if msg["type"] != "lifespan.startup":
                    return
                # main app first (must always come up); MCP is best-effort.
                try:
                    await stack.enter_async_context(_fastapi_app.router.lifespan_context(_fastapi_app))
                except Exception as e:               # pragma: no cover
                    await send({"type": "lifespan.startup.failed", "message": repr(e)})
                    return
                try:
                    await stack.enter_async_context(_mcp.router.lifespan_context(_mcp))
                    _mcp_live["ok"] = True
                except Exception as e:               # MCP down ≠ deploy down
                    print(f"  ✗ MCP lifespan failed — serving app WITHOUT the connector: {e!r}")
                await send({"type": "lifespan.startup.complete"})
                await receive()                      # lifespan.shutdown
            await send({"type": "lifespan.shutdown.complete"})
            return
        use_mcp = _mcp_live["ok"] and stype in ("http", "websocket") and _is_mcp_path(scope.get("path", ""))
        await (_mcp if use_mcp else _fastapi_app)(scope, receive, send)

    app = _combined                          # uvicorn imports the FINAL `app`
    print("✓ MCP combined — Claude connector at /mcp (best-effort; never blocks boot)")
