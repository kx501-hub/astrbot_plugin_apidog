# -*- coding: utf-8 -*-
"""Load apis.json, auth.json; find api; build config for placeholders."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .log_helper import logger

_CACHE_MISSING = object()
_cache_lock = threading.RLock()
# key: (resolved_data_dir, name) -> value
_cache: dict[tuple[str, str], Any] = {}


def _ddir_key(data_dir: Path) -> str:
    try:
        return str(data_dir.resolve())
    except Exception:
        # best effort: avoid breaking load in odd path cases
        return str(data_dir)


def _cache_get(data_dir: Path, name: str) -> Any:
    key = (_ddir_key(data_dir), name)
    with _cache_lock:
        return _cache.get(key, _CACHE_MISSING)


def _cache_set(data_dir: Path, name: str, value: Any) -> None:
    key = (_ddir_key(data_dir), name)
    with _cache_lock:
        _cache[key] = value


def _cache_invalidate(data_dir: Path, name: str) -> None:
    key = (_ddir_key(data_dir), name)
    with _cache_lock:
        _cache.pop(key, None)


def invalidate_apis(data_dir: Path) -> None:
    _cache_invalidate(data_dir, "apis")


def invalidate_auth(data_dir: Path) -> None:
    _cache_invalidate(data_dir, "auth")


def invalidate_groups(data_dir: Path) -> None:
    _cache_invalidate(data_dir, "groups")


def invalidate_config(data_dir: Path) -> None:
    _cache_invalidate(data_dir, "config")


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("Failed to load %s: %s", path, e)
        return default


def load_apis(data_dir: Path) -> list[dict]:
    cached = _cache_get(data_dir, "apis")
    if cached is not _CACHE_MISSING:
        return cached
    path = data_dir / "apis.json"
    raw = load_json(path, {"apis": []})
    apis = raw.get("apis", []) if isinstance(raw, dict) else []
    out = apis if isinstance(apis, list) else []
    _cache_set(data_dir, "apis", out)
    return out


def load_auth(data_dir: Path) -> dict[str, Any]:
    cached = _cache_get(data_dir, "auth")
    if cached is not _CACHE_MISSING:
        return cached
    path = data_dir / "auth.json"
    out = load_json(path, {})
    if not isinstance(out, dict):
        out = {}
    _cache_set(data_dir, "auth", out)
    return out


def load_groups(data_dir: Path) -> dict[str, Any]:
    """Load groups.json. Returns {"user_groups": {...}, "group_groups": {...}}; missing file or keys -> empty dict."""
    cached = _cache_get(data_dir, "groups")
    if cached is not _CACHE_MISSING:
        return cached
    path = data_dir / "groups.json"
    raw = load_json(path, {})
    if not isinstance(raw, dict):
        out = {"user_groups": {}, "group_groups": {}}
        _cache_set(data_dir, "groups", out)
        return out
    user_groups = raw.get("user_groups") if isinstance(raw.get("user_groups"), dict) else {}
    group_groups = raw.get("group_groups") if isinstance(raw.get("group_groups"), dict) else {}
    out = {"user_groups": user_groups, "group_groups": group_groups}
    _cache_set(data_dir, "groups", out)
    return out


DEFAULT_RETRY_STATUSES: frozenset[int] = frozenset({500, 502, 503, 429})


def load_config(data_dir: Path) -> dict[str, Any]:
    """Load config.json for global defaults (timeout, retry, retry_statuses). Missing file or keys use built-in defaults."""
    cached = _cache_get(data_dir, "config")
    if cached is not _CACHE_MISSING:
        return cached
    path = data_dir / "config.json"
    raw = load_json(path, {})
    if not isinstance(raw, dict):
        raw = {}
    timeout = raw.get("timeout_seconds")
    if isinstance(timeout, (int, float)) and timeout > 0:
        timeout_seconds = float(timeout)
    else:
        timeout_seconds = 30.0
    retry_raw = raw.get("retry")
    retry: dict[str, Any] | None = None
    if isinstance(retry_raw, dict):
        max_a = retry_raw.get("max_attempts")
        backoff = retry_raw.get("backoff_seconds", 1)
        if isinstance(max_a, (int, float)) and max_a > 0:
            retry = {
                "max_attempts": int(max_a),
                "backoff_seconds": float(backoff) if isinstance(backoff, (int, float)) else 1.0,
            }
    raw_statuses = raw.get("retry_statuses")
    if isinstance(raw_statuses, list):
        codes = []
        for x in raw_statuses:
            if isinstance(x, (int, float)) and 100 <= int(x) <= 599:
                codes.append(int(x))
            elif isinstance(x, str) and x.strip().isdigit():
                v = int(x.strip())
                if 100 <= v <= 599:
                    codes.append(v)
        retry_statuses = frozenset(codes) if codes else DEFAULT_RETRY_STATUSES
    else:
        retry_statuses = DEFAULT_RETRY_STATUSES
    out = {
        "timeout_seconds": timeout_seconds,
        "retry": retry,
        "retry_statuses": retry_statuses,
    }
    _cache_set(data_dir, "config", out)
    return out


def merge_client_options(global_config: dict[str, Any], api: dict) -> dict[str, Any]:
    """Merge global config with per-API overrides. Returns effective timeout_seconds and retry."""
    timeout = api.get("timeout_seconds")
    if isinstance(timeout, (int, float)) and timeout > 0:
        timeout_seconds = float(timeout)
    else:
        timeout_seconds = global_config.get("timeout_seconds", 30.0)
    retry_override = api.get("retry")
    if retry_override is False or retry_override == 0:
        retry = None
    elif isinstance(retry_override, dict):
        max_a = retry_override.get("max_attempts")
        backoff = retry_override.get("backoff_seconds", 1)
        if isinstance(max_a, (int, float)) and max_a > 0:
            retry = {
                "max_attempts": int(max_a),
                "backoff_seconds": float(backoff) if isinstance(backoff, (int, float)) else 1.0,
            }
        else:
            retry = None
    else:
        retry = global_config.get("retry")
    retry_statuses = global_config.get("retry_statuses", DEFAULT_RETRY_STATUSES)
    return {"timeout_seconds": timeout_seconds, "retry": retry, "retry_statuses": retry_statuses}


def load_schedules(data_dir: Path) -> list[dict]:
    """Load schedules.json. Returns schedules array; missing file or non-list -> []."""
    path = data_dir / "schedules.json"
    raw = load_json(path, {})
    if isinstance(raw, dict):
        schedules = raw.get("schedules")
        if isinstance(schedules, list):
            return schedules
    return []


def enabled_apis(apis: list[dict]) -> list[dict]:
    """Return only APIs with enabled !== false."""
    return [a for a in apis if a.get("enabled", True) is not False]


def find_api(apis: list[dict], api_key: str) -> dict | None:
    """Find API by id only (e.g. for schedules). For user invocation use find_api_by_id_or_command."""
    for api in apis:
        if api.get("id") == api_key:
            return api
    return None


def find_api_by_id_or_command(apis: list[dict], key_or_command: str) -> dict | None:
    """Find API by id or command (for user /api <name> invocation)."""
    for api in apis:
        if api.get("id") == key_or_command or api.get("command") == key_or_command:
            return api
    return None

