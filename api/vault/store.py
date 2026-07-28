"""
Document vault — user-created **nested folders** holding arbitrary files
(Aadhaar, insurance, anything). Files reuse the shared best-practice document
storage (private `vault-docs` bucket, opaque object keys, metadata-only rename).

  document_folders   id, name, parent_id (nullable → nesting), created_at
  vault_documents    one row per file, FK folder_id → document_folders.id

The Land/Apartments/Gold "folders" are NOT stored here — the frontend mirrors
those domains' own documents into linked folders. This module only owns the
user's own folder tree + files.
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime
from typing import Optional

from api.documents.store import DocumentStore

FOLDERS_TABLE = "document_folders"
DOCS_TABLE = "vault_documents"
BUCKET = "vault-docs"

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vault_data")
_FOLDERS_FILE = os.path.join(_DATA_DIR, "folders.json")
_DOCS_FILE = os.path.join(_DATA_DIR, "documents.json")
_DOCS_DIR = os.path.join(_DATA_DIR, "docs")

_client = None
_init_attempted = False
_tables_ok = False


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
        print(f"✓ Vault store: Supabase active ({url})")
        return _client
    except Exception as e:
        print(f"⚠  Vault store: Supabase init failed (using JSON): {e}")
        return None


def reset_client_cache():
    global _client, _init_attempted, _tables_ok
    _client = None
    _init_attempted = False
    _tables_ok = False


MIGRATION_HINT = (
    "Document vault tables not found in Supabase. Run the migration in "
    "SUPABASE.md (\"Document vault tables\") once in the Supabase SQL editor to "
    "create document_folders + vault_documents, then retry."
)


def tables_ready() -> bool:
    global _tables_ok
    if _tables_ok:
        return True
    client = _get_client()
    if not client:
        return True
    try:
        client.table(FOLDERS_TABLE).select("id").limit(1).execute()
        client.table(DOCS_TABLE).select("id").limit(1).execute()
        _tables_ok = True
        return True
    except Exception:
        return False


# ── JSON-file fallback ──────────────────────────────────────────────────────────
def _read_json(path: str) -> list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write_json(path: str, data: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


# shared document storage for the vault's files (opaque keys, metadata rename)
_docs = DocumentStore(bucket=BUCKET, table=DOCS_TABLE, fk="folder_id",
                      get_client=_get_client, data_dir=_DOCS_DIR, json_file=_DOCS_FILE)


# ══════════════════════════════════════════════════════════════════════════════
# Folders
# ══════════════════════════════════════════════════════════════════════════════
def _public_folder(f: dict) -> dict:
    return {
        "id": f.get("id"),
        "name": f.get("name"),
        "parent_id": f.get("parent_id"),
        "created_at": f.get("created_at"),
    }


def list_folders() -> list[dict]:
    """All folders (flat); the frontend builds the tree from parent_id."""
    client = _get_client()
    if client:
        rows = client.table(FOLDERS_TABLE).select("*").order("name").execute().data or []
    else:
        rows = _read_json(_FOLDERS_FILE)
    folders = [_public_folder(f) for f in rows]
    # attach immediate file + subfolder counts
    file_counts: dict[str, int] = {}
    if client:
        docs = client.table(DOCS_TABLE).select("folder_id").execute().data or []
    else:
        docs = _read_json(_DOCS_FILE)
    for d in docs:
        fid = d.get("folder_id")
        file_counts[fid] = file_counts.get(fid, 0) + 1
    sub_counts: dict[str, int] = {}
    for f in folders:
        pid = f.get("parent_id")
        if pid:
            sub_counts[pid] = sub_counts.get(pid, 0) + 1
    for f in folders:
        f["file_count"] = file_counts.get(f["id"], 0)
        f["subfolder_count"] = sub_counts.get(f["id"], 0)
    return folders


def create_folder(name: str, parent_id: Optional[str]) -> dict:
    row = {
        "id": uuid.uuid4().hex[:12],
        "name": (name or "Untitled").strip()[:120],
        "parent_id": parent_id or None,
        "created_at": datetime.now().isoformat(),
    }
    client = _get_client()
    if client:
        client.table(FOLDERS_TABLE).insert(row).execute()
    else:
        data = _read_json(_FOLDERS_FILE)
        data.append(row)
        _write_json(_FOLDERS_FILE, data)
    return {**_public_folder(row), "file_count": 0, "subfolder_count": 0}


def rename_folder(folder_id: str, name: str) -> Optional[dict]:
    safe = (name or "").strip()[:120]
    if not safe:
        return None
    client = _get_client()
    if client:
        rows = client.table(FOLDERS_TABLE).update({"name": safe}).eq("id", folder_id).execute().data or []
        return _public_folder(rows[0]) if rows else None
    data = _read_json(_FOLDERS_FILE)
    hit = None
    for f in data:
        if f.get("id") == folder_id:
            f["name"] = safe
            hit = f
    if hit:
        _write_json(_FOLDERS_FILE, data)
        return _public_folder(hit)
    return None


def _descendant_ids(folder_id: str, folders: list[dict]) -> list[str]:
    """folder_id + all nested descendants."""
    out = [folder_id]
    children = [f["id"] for f in folders if f.get("parent_id") == folder_id]
    for c in children:
        out.extend(_descendant_ids(c, folders))
    return out


def delete_folder(folder_id: str) -> bool:
    """Delete a folder and everything inside it (subfolders + files + objects)."""
    client = _get_client()
    if client:
        all_folders = client.table(FOLDERS_TABLE).select("id,parent_id").execute().data or []
    else:
        all_folders = _read_json(_FOLDERS_FILE)
    ids = _descendant_ids(folder_id, all_folders)
    if folder_id not in [f.get("id") for f in all_folders]:
        return False
    # delete files in every affected folder (removes storage objects too)
    for fid in ids:
        for d in _docs.raw_for(fid):
            _docs.delete(d["id"])
    # delete the folder rows
    if client:
        for fid in ids:
            client.table(FOLDERS_TABLE).delete().eq("id", fid).execute()
    else:
        data = _read_json(_FOLDERS_FILE)
        data = [f for f in data if f.get("id") not in ids]
        _write_json(_FOLDERS_FILE, data)
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Files (delegate to the shared DocumentStore)
# ══════════════════════════════════════════════════════════════════════════════
def list_files(folder_id: str) -> list[dict]:
    return [_docs.public(d) for d in _docs.raw_for(folder_id)]


def add_file(folder_id: str, filename: str, content: bytes, mime: str) -> dict:
    return _docs.add(folder_id, filename, content, mime)


def open_file(doc_id: str, preview: bool = False) -> Optional[dict]:
    return _docs.open(doc_id, preview)


def rename_file(doc_id: str, new_name: str) -> Optional[dict]:
    return _docs.rename(doc_id, new_name)


def delete_file(doc_id: str) -> bool:
    return _docs.delete(doc_id)
