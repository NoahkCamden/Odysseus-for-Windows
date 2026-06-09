"""User preferences API — per-user key/value store backed by a JSON file."""
import json
import os
import threading
import time
from typing import Optional
from fastapi import APIRouter, Request
from src.auth_helpers import get_current_user

PREFS_FILE = os.path.join("data", "user_prefs.json")
_PREFS_CACHE_TTL_S = 1.0
_PREFS_CACHE_LOCK = threading.Lock()
_PREFS_CACHE = {
    "loaded_at": 0.0,
    "mtime": None,
    "size": None,
    "data": None,
}


def _invalidate_cache():
    with _PREFS_CACHE_LOCK:
        _PREFS_CACHE["loaded_at"] = 0.0
        _PREFS_CACHE["mtime"] = None
        _PREFS_CACHE["size"] = None
        _PREFS_CACHE["data"] = None


def _load():
    """Load the raw prefs file (internal use only)."""
    now = time.monotonic()
    try:
        st = os.stat(PREFS_FILE)
        mtime = st.st_mtime
        size = st.st_size
    except FileNotFoundError:
        mtime = None
        size = None

    with _PREFS_CACHE_LOCK:
        cached = _PREFS_CACHE.get("data")
        if (
            cached is not None
            and (now - float(_PREFS_CACHE.get("loaded_at", 0.0))) < _PREFS_CACHE_TTL_S
            and _PREFS_CACHE.get("mtime") == mtime
            and _PREFS_CACHE.get("size") == size
        ):
            return dict(cached)

    try:
        with open(PREFS_FILE, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    with _PREFS_CACHE_LOCK:
        _PREFS_CACHE["loaded_at"] = now
        _PREFS_CACHE["mtime"] = mtime
        _PREFS_CACHE["size"] = size
        _PREFS_CACHE["data"] = data if isinstance(data, dict) else {}

    return dict(_PREFS_CACHE["data"])


def _save(prefs):
    os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)
    _invalidate_cache()


def _load_for_user(user: Optional[str] = None) -> dict:
    """Load preferences for a specific user."""
    all_prefs = _load()
    if "_users" in all_prefs:
        if user is None:
            # Auth disabled — return first user's prefs for backward compat
            users = all_prefs["_users"]
            return dict(next(iter(users.values()), {}))
        return dict(all_prefs["_users"].get(user, {}))
    # Legacy flat format — return as-is
    return dict(all_prefs)


def _save_for_user(user: Optional[str], prefs: dict):
    """Save preferences for a specific user."""
    all_prefs = _load()
    if user is None:
        # Auth disabled — save flat
        _save(prefs)
        return
    if "_users" not in all_prefs:
        all_prefs = {"_users": {}}
    all_prefs["_users"][user] = prefs
    _save(all_prefs)


def setup_prefs_routes():
    router = APIRouter(prefix="/api/prefs", tags=["preferences"])

    @router.get("")
    async def get_all_prefs(request: Request):
        user = get_current_user(request)
        return _load_for_user(user)

    @router.get("/{key}")
    async def get_pref(request: Request, key: str):
        user = get_current_user(request)
        prefs = _load_for_user(user)
        return {"key": key, "value": prefs.get(key)}

    @router.put("/{key}")
    async def set_pref(request: Request, key: str, body: dict):
        user = get_current_user(request)
        prefs = _load_for_user(user)
        prefs[key] = body.get("value")
        _save_for_user(user, prefs)
        return {"key": key, "value": prefs[key]}

    return router
