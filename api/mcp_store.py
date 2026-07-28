"""Durable storage for the MCP connector's OAuth state.

FastMCP's OAuth proxy keeps three things: the client registrations Claude creates
when you add the connector, the tokens it issues, and short-lived transaction
state during a sign-in. By default those live in a directory under the process's
home — which on Railway is wiped by every deploy. That is the whole reason the
connector kept asking you to sign in again, and why each new chat/client had to
re-register.

Putting them in the same Supabase `app_cache` table the rest of the app uses
makes them survive redeploys and be shared across replicas, so a connection you
make in one chat keeps working in every other one.

The values are OAuth tokens, so they are encrypted at rest with a key derived
from the Google client secret (the same material FastMCP already derives its JWT
signing key from). Rotate the secret and everything simply re-registers.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Mapping, Optional, Sequence

_PREFIX = "mcp_oauth:"


def _kv():
    from .portfolio import store as kv
    return kv


class SupabaseKeyValue:
    """The slice of key_value.aio's AsyncKeyValue protocol FastMCP actually uses,
    over the app's existing KV. Every call is best-effort: a storage blip must
    degrade to "not found" (which just means re-registering), never a 500 in the
    middle of someone's sign-in."""

    def __init__(self, namespace: str = "default") -> None:
        self._ns = namespace

    # ── key shape ─────────────────────────────────────────────────────────────
    def _key(self, key: str, collection: Optional[str]) -> str:
        return f"{_PREFIX}{self._ns}:{collection or 'default'}:{key}"

    # ── sync core (runs off the event loop) ───────────────────────────────────
    def _read(self, key: str, collection: Optional[str]) -> tuple[Optional[dict], Optional[float]]:
        try:
            rec = _kv().cache_get(self._key(key, collection))
        except Exception:
            return None, None
        val = (rec or {}).get("value")
        if not isinstance(val, dict) or "v" not in val:
            return None, None
        exp = val.get("exp")
        if exp is not None and time.time() >= float(exp):
            return None, None                       # expired → a miss, as the protocol expects
        left = (float(exp) - time.time()) if exp is not None else None
        payload = val["v"]
        if isinstance(payload, str):                # stored as text by the encryption wrapper
            try:
                payload = json.loads(payload)
            except (TypeError, ValueError):
                return None, None
        return (payload if isinstance(payload, dict) else None), left

    def _write(self, key: str, value: Mapping[str, Any], collection: Optional[str],
               ttl: Optional[float]) -> None:
        body: dict[str, Any] = {"v": dict(value)}
        if ttl is not None:
            try:
                body["exp"] = time.time() + float(ttl)
            except (TypeError, ValueError):
                pass
        try:
            _kv().cache_set(self._key(key, collection), body)
        except Exception:
            pass

    def _erase(self, key: str, collection: Optional[str]) -> bool:
        try:
            _kv().cache_set(self._key(key, collection), {"v": None, "exp": 0})
            return True
        except Exception:
            return False

    # ── the async protocol ────────────────────────────────────────────────────
    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        value, _ = await asyncio.to_thread(self._read, key, collection)
        return value

    async def put(self, key: str, value: Mapping[str, Any], *, collection: str | None = None,
                  ttl: Any | None = None) -> None:
        await asyncio.to_thread(self._write, key, value, collection,
                                float(ttl) if ttl is not None else None)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        return await asyncio.to_thread(self._erase, key, collection)

    async def ttl(self, key: str, *, collection: str | None = None) -> tuple[dict[str, Any] | None, float | None]:
        return await asyncio.to_thread(self._read, key, collection)

    async def get_many(self, keys: Sequence[str], *, collection: str | None = None) -> list[dict[str, Any] | None]:
        return list(await asyncio.gather(*(self.get(k, collection=collection) for k in keys)))

    async def put_many(self, keys: Sequence[str], values: Sequence[Mapping[str, Any]], *,
                       collection: str | None = None, ttl: Any | None = None) -> None:
        await asyncio.gather(*(self.put(k, v, collection=collection, ttl=ttl)
                               for k, v in zip(keys, values)))

    async def delete_many(self, keys: Sequence[str], *, collection: str | None = None) -> int:
        done = await asyncio.gather(*(self.delete(k, collection=collection) for k in keys))
        return sum(1 for d in done if d)

    async def ttl_many(self, keys: Sequence[str], *, collection: str | None = None):
        return list(await asyncio.gather(*(self.ttl(k, collection=collection) for k in keys)))


def build_client_storage(client_secret: str):
    """The storage to hand FastMCP — encrypted if the crypto bits are available,
    plain (still private to your own Supabase) if not."""
    store = SupabaseKeyValue(namespace="networth")
    try:
        from cryptography.fernet import Fernet
        from fastmcp.server.auth.jwt_issuer import derive_jwt_key
        from key_value.aio.wrappers.encryption.fernet import FernetEncryptionWrapper

        key = derive_jwt_key(high_entropy_material=client_secret,
                             salt="networth-mcp-storage-key")
        return FernetEncryptionWrapper(key_value=store, fernet=Fernet(key=key),
                                       raise_on_decryption_error=False)
    except Exception as e:                          # never block the server booting
        print(f"⚠  MCP storage: falling back to unencrypted Supabase storage ({e})")
        return store


def durable() -> bool:
    """Whether OAuth state will actually survive a redeploy."""
    try:
        return bool(_kv().cache_durable())
    except Exception:
        return False
