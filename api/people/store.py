"""Canonical people directory — the single source of truth for the household's
four members and their avatars.

Owner/person/member fields are entered as free text across ~19 asset tables and
under three column names (``owner`` / ``person`` / ``member``), historically with
several spellings for the same person (Ranju, Ram Prasad, Mahalakshmi …). This
module fixes that:

  * ``CANONICAL`` — the ONLY four people the app recognises, in display order.
  * ``ALIASES``   — every historical spelling → its canonical name.
  * ``canon_owner`` — normalise any free-text owner string to one of the four
    (used by the dashboard aggregator so the per-person view is never fragmented).
  * a tiny store for each person's photo. The image file itself is uploaded to a
    **Supabase Storage** bucket (``people-photos``) — a real object you can see
    in the Supabase dashboard under Storage — and its public URL + object path
    are recorded in the generic ``app_settings`` table (key ``people``). When
    Supabase isn't configured (local dev), the photo falls back to a base64
    data-URL kept in a JSON file so the feature still works offline.

Uploaded images are downscaled to a ~256px square JPEG before storage.
"""
from __future__ import annotations

import base64
import io
import json
import os
import uuid
from datetime import datetime

PHOTO_BUCKET = "people-photos"

SETTINGS_TABLE = "app_settings"
SETTINGS_KEY = "people"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "people_data")
_FILE = os.path.join(_DATA_DIR, "people.json")

# The four — and only four — members, in the order the cards should render.
CANONICAL: list[dict] = [
    {"name": "Ranjeev",   "color": "#5b8def", "risk_profile": "balanced"},
    {"name": "Sanjeev",   "color": "#7c3fbf", "risk_profile": "balanced"},
    {"name": "Ramprasad", "color": "#f0883e", "risk_profile": "aggressive"},
    {"name": "Maha",      "color": "#20c4a8", "risk_profile": "conservative"},
]
CANON_NAMES = [p["name"] for p in CANONICAL]

# Every historical / nickname spelling → canonical name. Keys are lower-cased.
ALIASES: dict[str, str] = {
    "ranjeev": "Ranjeev", "ranju": "Ranjeev",
    "sanjeev": "Sanjeev", "sanju": "Sanjeev",
    "ramprasad": "Ramprasad", "ram prasad": "Ramprasad", "ram": "Ramprasad",
    "maha": "Maha", "mahalakshmi": "Maha",
}


def canon_owner(name) -> str:
    """Normalise a free-text owner/person/member string to one of the four
    canonical names. Unknown / blank values fall back to the raw title-cased
    string (dashboard shows it as-is) or ``—`` when empty."""
    key = (name or "").strip().lower()
    if not key:
        return "—"
    return ALIASES.get(key, (name or "").strip().title())


# --------------------------------------------------------------------------
# Supabase-or-JSON persistence for photos (mirrors dashboard/store.py)
# --------------------------------------------------------------------------
_client = None
_init_attempted = False


def _read_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if val:
        return val
    try:
        from dotenv import dotenv_values
        root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env_path = os.path.join(root, ".env")
        if os.path.isfile(env_path):
            return (dotenv_values(env_path).get(key) or "").strip()
    except Exception:
        pass
    return ""


def _get_client():
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
        return _client
    except Exception:
        return None


def _read_overrides() -> dict:
    """{name: {photo, color, risk_profile}} of stored per-person overrides."""
    client = _get_client()
    if client:
        try:
            rows = client.table(SETTINGS_TABLE).select("value").eq("key", SETTINGS_KEY).limit(1).execute().data or []
            if rows:
                return rows[0].get("value") or {}
        except Exception:
            pass
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_overrides(data: dict) -> None:
    client = _get_client()
    if client:
        try:
            client.table(SETTINGS_TABLE).upsert(
                {"key": SETTINGS_KEY, "value": data, "updated_at": datetime.now().isoformat()}
            ).execute()
            return
        except Exception:
            pass
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, _FILE)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def get_people() -> list[dict]:
    """The four canonical people, each merged with any stored photo override."""
    overrides = _read_overrides()
    out = []
    for base in CANONICAL:
        ov = overrides.get(base["name"], {}) or {}
        out.append({
            "name": base["name"],
            "color": ov.get("color") or base["color"],
            "risk_profile": ov.get("risk_profile") or base["risk_profile"],
            "photo": ov.get("photo"),   # data-URL or None
        })
    return out


def people_by_name() -> dict[str, dict]:
    return {p["name"]: p for p in get_people()}


def _process_image(raw: bytes) -> bytes:
    """Downscale/centre-crop an uploaded image to a 256px square JPEG.
    Returns the original bytes untouched if Pillow isn't available."""
    try:
        from PIL import Image
        try:
            import pillow_heif  # noqa: F401 — registers HEIC/HEIF opener
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = img.size
        side = min(w, h)
        img = img.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
        img = img.resize((256, 256), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        return buf.getvalue()
    except Exception:
        return raw


_bucket_ready = False


def _ensure_bucket(client) -> None:
    global _bucket_ready
    if _bucket_ready:
        return
    try:
        # public so the avatar renders straight from <img src> without a signed
        # URL; the object key carries a random component so it isn't guessable.
        client.storage.create_bucket(PHOTO_BUCKET, options={"public": True})
    except Exception:
        pass                      # already exists / created elsewhere
    _bucket_ready = True


def set_photo(name: str, raw: bytes, content_type: str | None = None) -> dict:
    canon = canon_owner(name)
    if canon not in CANON_NAMES:
        raise ValueError(f"Unknown person '{name}'. Must be one of {CANON_NAMES}.")
    jpeg = _process_image(raw)
    data = _read_overrides()
    entry = dict(data.get(canon, {}) or {})
    old_path = entry.get("photo_path")

    client = _get_client()
    if client:
        _ensure_bucket(client)
        # a fresh, immutable object per upload → no CDN cache staleness on replace
        object_path = f"{canon.lower()}/{uuid.uuid4().hex[:10]}.jpg"
        client.storage.from_(PHOTO_BUCKET).upload(
            object_path, jpeg, {"content-type": "image/jpeg", "upsert": "true"},
        )
        pub = client.storage.from_(PHOTO_BUCKET).get_public_url(object_path)
        entry["photo"] = pub.rstrip("?")           # some SDKs append a stray '?'
        entry["photo_path"] = object_path
        _write_overrides(data | {canon: entry})
        # best-effort: delete the previous object so we don't accumulate orphans
        if old_path and old_path != object_path:
            try:
                client.storage.from_(PHOTO_BUCKET).remove([old_path])
            except Exception:
                pass
    else:
        # offline dev — no Supabase: embed as a data-URL in the JSON fallback
        entry["photo"] = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")
        entry.pop("photo_path", None)
        _write_overrides(data | {canon: entry})
    return people_by_name()[canon]


def clear_photo(name: str) -> dict:
    canon = canon_owner(name)
    if canon not in CANON_NAMES:
        raise ValueError(f"Unknown person '{name}'. Must be one of {CANON_NAMES}.")
    data = _read_overrides()
    entry = dict(data.get(canon, {}) or {})
    old_path = entry.get("photo_path")
    entry.pop("photo", None)
    entry.pop("photo_path", None)
    data[canon] = entry
    _write_overrides(data)
    if old_path:
        client = _get_client()
        if client:
            try:
                client.storage.from_(PHOTO_BUCKET).remove([old_path])
            except Exception:
                pass
    return people_by_name()[canon]
