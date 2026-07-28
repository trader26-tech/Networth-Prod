"""
Cross-device persistence for the analyzed portfolio.

The portfolio page's "result" (parsed holdings + stats + history) was
previously cached only in browser localStorage, so uploading on phone A
left phone B blank. This module stores a single "current" snapshot
server-side so any device hitting the API sees the same holdings.

Storage hierarchy:
  1. Supabase table  portfolio_snapshots  (id='current', data JSONB)
       — preferred. Survives Railway deploys.
  2. JSON file       api/fundamentals/data/saved_portfolio.json
       — fallback for local dev or when Supabase env isn't configured.

The Supabase table can be created once with:

    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        id          TEXT PRIMARY KEY,
        data        JSONB NOT NULL,
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

TABLE = "portfolio_snapshots"
SNAPSHOT_ID = "current"

_FILE_PATH = Path(__file__).resolve().parent / "fundamentals" / "data" / "saved_portfolio.json"

_client = None
_init_attempted = False


def _read_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.is_file():
            return (dotenv_values(env_path).get(key) or "").strip()
    except Exception:
        pass
    return ""


def _get_client():
    """Return a cached Supabase client (or None)."""
    global _client, _init_attempted
    if _client is not None or _init_attempted:
        return _client
    _init_attempted = True
    url = _read_env("SUPABASE_URL").rstrip("/")
    key = _read_env("SUPABASE_SERVICE_KEY") or _read_env("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        print(f"✓ Portfolio Supabase persist active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Portfolio Supabase init failed (falling back to file): {e}")
        return None


# ─── Public API ─────────────────────────────────────────────────────────────

def load_snapshot() -> Optional[dict]:
    """Return the most recently saved analysis result (or None if nothing saved)."""
    client = _get_client()
    if client is not None:
        try:
            resp = client.table(TABLE).select("data, updated_at").eq("id", SNAPSHOT_ID).limit(1).execute()
            rows = resp.data or []
            if rows:
                row = rows[0]
                return {"result": row.get("data"), "updated_at": row.get("updated_at"),
                        "source": "supabase"}
        except Exception as e:
            print(f"⚠  portfolio_persist.load_snapshot supabase err: {e} — trying file")
    if _FILE_PATH.exists():
        try:
            payload = json.loads(_FILE_PATH.read_text())
            return {"result": payload.get("result"),
                    "updated_at": payload.get("updated_at"),
                    "source": "file"}
        except Exception as e:
            print(f"⚠  portfolio_persist.load_snapshot file err: {e}")
    return None


def save_snapshot(result: dict, *, file_name: str = "") -> dict:
    """Persist the analyzed result. Returns metadata about where it landed."""
    now = datetime.now(timezone.utc).isoformat()
    payload = {"result": result, "updated_at": now, "file_name": file_name}

    written_supabase = False
    client = _get_client()
    if client is not None:
        try:
            client.table(TABLE).upsert({
                "id":         SNAPSHOT_ID,
                "data":       result,
                "updated_at": now,
            }).execute()
            written_supabase = True
        except Exception as e:
            print(f"⚠  portfolio_persist.save_snapshot supabase err: {e} — falling back to file")

    # Always write the file too (cheap, gives us a local cache + redundancy).
    written_file = False
    try:
        _FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FILE_PATH.write_text(json.dumps(payload))
        written_file = True
    except Exception as e:
        print(f"⚠  portfolio_persist.save_snapshot file err: {e}")

    return {
        "ok": written_supabase or written_file,
        "supabase": written_supabase,
        "file": written_file,
        "updated_at": now,
    }


def clear_snapshot() -> dict:
    """Wipe both stores."""
    cleared = {"supabase": False, "file": False}
    client = _get_client()
    if client is not None:
        try:
            client.table(TABLE).delete().eq("id", SNAPSHOT_ID).execute()
            cleared["supabase"] = True
        except Exception as e:
            print(f"⚠  portfolio_persist.clear_snapshot supabase err: {e}")
    try:
        if _FILE_PATH.exists():
            _FILE_PATH.unlink()
        cleared["file"] = True
    except Exception as e:
        print(f"⚠  portfolio_persist.clear_snapshot file err: {e}")
    return {"ok": True, **cleared}
