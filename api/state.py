"""
Shared mutable application state.

All modules that need the Kite client or PaperEngine import from here
instead of from main.py — this breaks the circular-import chain and lets
every route/scanner be tested in isolation by simply monkey-patching
`state._kite` or `state.engine`.
"""
from __future__ import annotations
import os, re
from typing import Optional

# ── Kite client (None when running on mock data) ──────────────────────────────
_kite = None           # KiteConnect instance or None

def get_kite():
    return _kite

def set_kite(k) -> None:
    global _kite
    _kite = k


# ── Paper engine (always present) ─────────────────────────────────────────────
engine = None          # PaperEngine — set during app startup

def get_engine():
    return engine

def set_engine(e) -> None:
    global engine
    engine = e


# ── .env helpers ──────────────────────────────────────────────────────────────
ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")

def write_env(**kwargs) -> None:
    """Update key=value pairs in the .env file in-place."""
    try:
        with open(ENV_PATH) as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    for key, val in kwargs.items():
        pattern = rf'^{key}=.*$'
        replacement = f'{key}={val}'
        if re.search(pattern, text, re.MULTILINE):
            text = re.sub(pattern, replacement, text, flags=re.MULTILINE)
        else:
            text = text.rstrip('\n') + f'\n{replacement}\n'
    with open(ENV_PATH, 'w') as f:
        f.write(text)


def load_kite() -> bool:
    """Try to initialise KiteConnect.

    Reads credentials from (in order of priority):
      1. process environment (Railway / Docker / shell exports)
      2. .env file at project root (local dev)

    Returns True on success, False on missing/invalid credentials.
    Always non-blocking: never raises, never blocks app startup."""
    global _kite

    # 1. Environment variables take priority (production)
    api_key      = os.environ.get("KITE_API_KEY", "").strip()
    access_token = os.environ.get("KITE_ACCESS_TOKEN", "").strip()

    # 2. Fall back to .env file (local dev)
    if not api_key or not access_token:
        try:
            from dotenv import dotenv_values
            vals = dotenv_values(ENV_PATH)
            api_key      = api_key      or vals.get("KITE_API_KEY", "").strip()
            access_token = access_token or vals.get("KITE_ACCESS_TOKEN", "").strip()
        except Exception:
            pass

    placeholders = ("", "YOUR_API_KEY_HERE", "YOUR_ACCESS_TOKEN_HERE")
    if api_key in placeholders or access_token in placeholders:
        _kite = None
        return False

    try:
        from kiteconnect import KiteConnect
        k = KiteConnect(api_key=api_key)
        k.set_access_token(access_token)
        k.profile()          # validate — raises if token is stale
        _kite = k
        print("✓ Connected to real Kite API (PAPER TRADING — no real orders sent)")
        return True
    except Exception as e:
        # Non-fatal: app boots in mock mode if Kite is down or token is stale.
        print(f"⚠  Kite connect failed (continuing in mock mode): {e}")
        _kite = None
        return False
