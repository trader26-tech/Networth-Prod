"""
Shared document storage — one implementation for every domain (land, apartments,
gold, …). Each domain just constructs a `DocumentStore` with its bucket, table
and foreign-key column.

Design (best-practice object storage):
  • Blobs live in a **private** Supabase Storage bucket; metadata lives in a DB
    row. (JSON-file fallback for local dev.)
  • The stored object key is **opaque and immutable**: ``{parent_id}/{doc_id}``
    — the human filename is kept ONLY in the DB row. So **renaming is a pure
    one-row metadata update** (atomic, no object move, no orphans).
  • Downloads are **proxied through the backend**, which streams the bytes with
    ``Content-Disposition: inline; filename="<db filename>"`` — so the file
    always carries its current name and previews inline, regardless of the
    opaque object key. Nothing is ever public.
  • Uploads roll back the object if the metadata insert fails (no orphans).
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from typing import Callable, Optional


def _preview_convert(data: bytes, mime: str, name: str) -> Optional[tuple[bytes, str]]:
    """Browsers can't render HEIC/HEIF/TIFF in <img>. For previews, transcode
    them to JPEG so the image actually shows. Returns (jpeg_bytes, 'image/jpeg')
    or None to serve the original untouched."""
    m = (mime or "").lower()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not ("heic" in m or "heif" in m or "tiff" in m or ext in ("heic", "heif", "tif", "tiff")):
        return None
    try:
        import io as _io
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        img = Image.open(_io.BytesIO(data)).convert("RGB")
        img.thumbnail((1600, 1600))           # cap size — these are previews
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return None


class DocumentStore:
    def __init__(self, *, bucket: str, table: str, fk: str,
                 get_client: Callable[[], object], data_dir: str, json_file: str):
        self.bucket = bucket
        self.table = table
        self.fk = fk                      # foreign-key column, e.g. "land_id"
        self._get_client = get_client
        self.data_dir = data_dir
        self.json_file = json_file
        self._bucket_ready = False

    # ── JSON-file fallback helpers ──────────────────────────────────────────────
    def _read(self) -> list:
        try:
            with open(self.json_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _write(self, data: list) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        tmp = self.json_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, self.json_file)

    def _ensure_bucket(self, client) -> None:
        if self._bucket_ready:
            return
        try:
            client.storage.create_bucket(self.bucket, options={"public": False})
        except Exception:
            pass                          # already exists / created elsewhere
        self._bucket_ready = True

    # ── metadata shape returned to the API (never leaks storage_path) ───────────
    def public(self, d: dict) -> dict:
        return {
            "id": d.get("id"),
            self.fk: d.get(self.fk),
            "filename": d.get("filename"),
            "mime_type": d.get("mime_type"),
            "size": d.get("size"),
            "created_at": d.get("created_at"),
        }

    def raw_for(self, parent_id: str) -> list[dict]:
        client = self._get_client()
        if client:
            return client.table(self.table).select("*").eq(self.fk, parent_id).execute().data or []
        return [d for d in self._read() if d.get(self.fk) == parent_id]

    def get_raw(self, doc_id: str) -> Optional[dict]:
        client = self._get_client()
        if client:
            rows = client.table(self.table).select("*").eq("id", doc_id).limit(1).execute().data or []
            return rows[0] if rows else None
        rows = [d for d in self._read() if d.get("id") == doc_id]
        return rows[0] if rows else None

    def add(self, parent_id: str, filename: str, content: bytes, mime: str) -> dict:
        doc_id = uuid.uuid4().hex[:12]
        meta = {
            "id": doc_id,
            self.fk: parent_id,
            "filename": os.path.basename(filename or "document"),
            "mime_type": mime or "application/octet-stream",
            "size": len(content),
            "created_at": datetime.now().isoformat(),
        }
        client = self._get_client()
        if client:
            self._ensure_bucket(client)
            object_path = f"{parent_id}/{doc_id}"          # OPAQUE, immutable
            client.storage.from_(self.bucket).upload(
                object_path, content,
                {"content-type": meta["mime_type"], "upsert": "true"},
            )
            meta["storage_path"] = object_path
            try:
                client.table(self.table).insert(meta).execute()
            except Exception:
                # roll back the orphaned object if the metadata insert fails
                try:
                    client.storage.from_(self.bucket).remove([object_path])
                except Exception:
                    pass
                raise
        else:
            os.makedirs(self.data_dir, exist_ok=True)
            local_path = os.path.join(self.data_dir, doc_id)
            with open(local_path, "wb") as f:
                f.write(content)
            meta["storage_path"] = local_path
            data = self._read()
            data.append(meta)
            self._write(data)
        return self.public(meta)

    def open(self, doc_id: str, preview: bool = False) -> Optional[dict]:
        """Return {data, filename, mime} for the route to stream, or None.
        With preview=True, non-web image formats (HEIC/HEIF/TIFF) are transcoded
        to JPEG so they actually display in the browser."""
        d = self.get_raw(doc_id)
        if not d:
            return None
        name = d.get("filename") or "document"
        mime = d.get("mime_type") or "application/octet-stream"
        client = self._get_client()
        if client:
            try:
                raw = client.storage.from_(self.bucket).download(d["storage_path"])
            except Exception:
                return None
            data = bytes(raw)
        else:
            path = d.get("storage_path")
            if not path or not os.path.isfile(path):
                return None
            with open(path, "rb") as f:
                data = f.read()
        if preview:
            conv = _preview_convert(data, mime, name)
            if conv:
                data, mime = conv
        return {"data": data, "filename": name, "mime": mime}

    def rename(self, doc_id: str, new_name: str) -> Optional[dict]:
        """Pure metadata update — the opaque object is never touched."""
        d = self.get_raw(doc_id)
        if not d:
            return None
        safe = os.path.basename((new_name or "").strip()) or d.get("filename")
        client = self._get_client()
        if client:
            rows = client.table(self.table).update({"filename": safe}).eq("id", doc_id).execute().data or []
            d = rows[0] if rows else {**d, "filename": safe}
        else:
            data = self._read()
            for x in data:
                if x.get("id") == doc_id:
                    x["filename"] = safe
                    d = x
            self._write(data)
        return self.public(d)

    def _remove_object(self, d: dict) -> None:
        client = self._get_client()
        if client:
            try:
                client.storage.from_(self.bucket).remove([d["storage_path"]])
            except Exception:
                pass
        else:
            try:
                if d.get("storage_path") and os.path.isfile(d["storage_path"]):
                    os.remove(d["storage_path"])
            except OSError:
                pass

    def delete(self, doc_id: str) -> bool:
        d = self.get_raw(doc_id)
        if not d:
            return False
        self._remove_object(d)
        client = self._get_client()
        if client:
            res = client.table(self.table).delete().eq("id", doc_id).execute()
            return bool(res.data)
        data = self._read()
        remaining = [x for x in data if x.get("id") != doc_id]
        if len(remaining) == len(data):
            return False
        self._write(remaining)
        return True
