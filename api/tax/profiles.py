"""
Per-person TAX PROFILE — the facts that change how much tax someone pays.

  • residency     resident | nri | rnor        (the single biggest lever)
  • relation      self | spouse | son | daughter | father | mother | brother | sister
  • spouse        name of spouse               (for the clubbing rule, Sec 64)
  • dob           for senior-citizen slabs & minor detection
  • pan
  • other_income  rough annual taxable income   (drives the surcharge band)

Storage: a local JSON file (api/tax_data/profiles.json), seeded once from the family's
known status (Ramprasad = NRI, the others resident). Editable via the chatbot.
"""
from __future__ import annotations

import os
import json
from datetime import date, datetime
from typing import Optional

RESIDENCY = ("resident", "nri", "rnor")
RELATIONS = ("self", "spouse", "son", "daughter", "father", "mother", "brother", "sister", "other")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tax_data")
_FILE = os.path.join(_DATA_DIR, "profiles.json")
_MARKER = os.path.join(_DATA_DIR, ".seeded")

COLUMNS = {"name", "residency", "relation", "spouse", "dob", "pan", "other_income", "note",
           "created_at", "updated_at"}


def _seed() -> list[dict]:
    now = datetime.now().isoformat()
    rows = [
        {"name": "Ramprasad", "residency": "nri", "relation": "father", "spouse": "Maha",
         "note": "Non-resident — no 20%-indexation option, heavier TDS u/s 195 on property sales."},
        {"name": "Maha", "residency": "resident", "relation": "mother", "spouse": "Ramprasad",
         "note": "Spouse of Ramprasad → gifts between them are caught by clubbing (Sec 64)."},
        {"name": "Ranjeev", "residency": "resident", "relation": "son", "spouse": None,
         "note": "Adult resident son — a safe, tax-efficient person to receive a gift (no clubbing)."},
        {"name": "Sanjeev", "residency": "resident", "relation": "son", "spouse": None,
         "note": "Adult resident son — a safe, tax-efficient person to receive a gift (no clubbing)."},
    ]
    for r in rows:
        r.setdefault("dob", None); r.setdefault("pan", None)
        r.setdefault("other_income", None)
        r["created_at"] = now; r["updated_at"] = now
    return rows


def _read() -> list:
    try:
        with open(_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _write(data: list) -> None:
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, _FILE)


def _ensure() -> None:
    if os.path.exists(_MARKER):
        return
    if not _read():
        _write(_seed())
    os.makedirs(_DATA_DIR, exist_ok=True)
    with open(_MARKER, "w") as f:
        f.write(datetime.now().isoformat())


def _age(dob) -> Optional[int]:
    try:
        d = date.fromisoformat(str(dob)[:10])
    except (TypeError, ValueError):
        return None
    t = date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def _decorate(r: dict) -> dict:
    d = {k: r.get(k) for k in COLUMNS}
    age = _age(r.get("dob"))
    d["age"] = age
    d["is_minor"] = (age is not None and age < 18)
    d["is_senior"] = (age is not None and age >= 60)
    return d


def list_profiles() -> list[dict]:
    _ensure()
    return [_decorate(r) for r in _read()]


def get_profile(name: str) -> Optional[dict]:
    _ensure()
    key = (name or "").strip().lower()
    for r in _read():
        if (r.get("name") or "").strip().lower() == key:
            return _decorate(r)
    return None


def upsert_profile(name: str, patch: dict) -> dict:
    _ensure()
    rows = _read()
    key = (name or "").strip().lower()
    clean = {k: patch.get(k) for k in ("residency", "relation", "spouse", "dob", "pan", "other_income", "note")
             if k in patch}
    if clean.get("residency") and clean["residency"] not in RESIDENCY:
        clean["residency"] = "resident"
    if clean.get("relation") and clean["relation"] not in RELATIONS:
        clean["relation"] = "other"
    if "other_income" in clean and clean["other_income"] not in (None, ""):
        try: clean["other_income"] = float(clean["other_income"])
        except (TypeError, ValueError): clean["other_income"] = None
    now = datetime.now().isoformat()
    for r in rows:
        if (r.get("name") or "").strip().lower() == key:
            r.update(clean); r["updated_at"] = now
            _write(rows)
            return _decorate(r)
    row = {"name": (name or "").strip().title(), **clean, "created_at": now, "updated_at": now}
    rows.append(row)
    _write(rows)
    return _decorate(row)


_PREFS_FILE = os.path.join(_DATA_DIR, "land_prefs.json")


def get_prefs() -> dict:
    """Saved land-sale planning preferences (selection + answers), so the user
    doesn't have to redo the wizard each time."""
    try:
        with open(_PREFS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def set_prefs(data: dict) -> dict:
    cur = get_prefs()
    cur.update(data or {})
    cur["updated_at"] = datetime.now().isoformat()
    os.makedirs(_DATA_DIR, exist_ok=True)
    tmp = _PREFS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2, default=str)
    os.replace(tmp, _PREFS_FILE)
    return cur


def clubbed_between(owner: dict, target: dict) -> bool:
    """True if a gift from owner → target is caught by clubbing (Sec 64):
    the target is the owner's spouse, or the owner's minor child."""
    if not owner or not target:
        return False
    o = (owner.get("name") or "").strip().lower()
    t_name = (target.get("name") or "").strip().lower()
    # spouse either way
    if (owner.get("spouse") or "").strip().lower() == t_name:
        return True
    if (target.get("spouse") or "").strip().lower() == o:
        return True
    # minor child of the owner
    if target.get("is_minor") and (target.get("relation") in ("son", "daughter")):
        return True
    return False
