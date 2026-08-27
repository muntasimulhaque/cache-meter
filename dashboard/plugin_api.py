"""Cache Meter backend.

Reads the local profile's state.db sessions table and reports pi-style
cache stats for a session: cache_read / cache_write tokens, cumulative
cache-hit ratio, cost and context usage.

Convention matches badlogic's pi coding agent:
  prompt volume = input + cacheRead + cacheWrite   (input EXCLUDES cached)
  hit rate      = cacheRead / prompt volume

Hermes stores exactly these buckets in `sessions` (normalize_usage subtracts
cached tokens from input before writing rows), so this is a pure read - no
core patches required on any Hermes >= 0.20.x that persists token counts.

Mounted by the gateway/dashboard web server at /api/plugins/cache-meter/
when this plugin is in plugins.enabled in config.yaml.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    from hermes_constants import get_hermes_home
except ImportError:  # standalone import (tests, lint)
    def get_hermes_home() -> Path:  # type: ignore[misc]
        val = (os.environ.get("HERMES_HOME") or "").strip()
        return Path(val) if val else Path.home() / ".hermes"

try:
    from fastapi import APIRouter
except Exception:  # Allows unit tests without dashboard dependencies.
    class APIRouter:  # type: ignore[no-redef]
        def get(self, *_args, **_kwargs):
            return lambda fn: fn

router = APIRouter()

# Exclusive sources: subagent runs and kanban dispatcher workers are not user turns.
_DENY_SOURCES = ("tool", "kanban")


def _profile_db_path(profile: str | None) -> Path:
    home = get_hermes_home()
    name = (profile or "").strip().strip("/")
    if not name or name.lower() == "default":
        return home / "state.db"
    return home / "profiles" / name / "state.db"


def _stats(row: dict[str, Any]) -> dict[str, Any]:
    inp = int(row.get("input_tokens") or 0)
    out = int(row.get("output_tokens") or 0)
    read = int(row.get("cache_read_tokens") or 0)
    write = int(row.get("cache_write_tokens") or 0)
    reasoning = int(row.get("reasoning_tokens") or 0)
    calls = int(row.get("api_call_count") or 0)
    cost_actual = row.get("actual_cost_usd")
    cost_est = row.get("estimated_cost_usd")

    prompt_volume = inp + read + write
    has_cache = bool(read or write)

    return {
        "session_id": row.get("id"),
        "title": row.get("title"),
        "model": row.get("model"),
        "last_active": row.get("last_activity_at") or row.get("started_at"),
        "input": inp,
        "output": out,
        "cache_read": read,
        "cache_write": write,
        "prompt_volume": prompt_volume,
        "cache_hit_rate": round(read / prompt_volume * 100, 1) if prompt_volume > 0 and has_cache else None,
        "reasoning": reasoning,
        "calls": calls,
        "cost_usd": float(cost_actual or 0.0) or float(cost_est or 0.0),
        "cost_source": "actual" if cost_actual else ("estimated" if cost_est else None),
    }


def _open_db(profile: str):
    from hermes_state import SessionDB

    db = SessionDB(db_path=_profile_db_path(profile), read_only=True)
    db.flush_token_counts()  # drain queued deltas so mid-turn reads are exact
    return db


def _resolve_session_id(db, session_id: str):
    """Map a desktop *runtime* session id to the stored row id.

    The desktop status bar speaks runtime ids (short-lived, gateway memory);
    state.db rows use stored ids (``20260827_...``). Try the stored id first,
    then ask the in-process gateway registry for the mapping. Fails soft:
    returns None and the caller answers ``not found``.
    """
    if db.get_session(session_id):
        return session_id
    try:
        from tui_gateway import server as tg  # same process as the web server

        for rid, s in (getattr(tg, "_sessions", None) or {}).items():
            agent = (s or {}).get("agent")
            candidates = (
                rid,
                str((s or {}).get("session_key") or ""),
                str(getattr(agent, "session_id", "") or ""),
            )
            if session_id in candidates:
                for cand in candidates:
                    if cand and cand != rid and db.get_session(cand):
                        return cand
    except Exception:
        pass
    return None


@router.get("/usage/{session_id}")
async def usage(session_id: str, profile: str = "") -> dict[str, Any]:
    """pi-style usage snapshot for one session, from its stored session row."""
    try:
        db = _open_db(profile)
    except ImportError:
        return {"error": "hermes_state unavailable outside a Hermes install"}
    try:
        resolved = _resolve_session_id(db, session_id)
        row = db.get_session(resolved) if resolved else None
    finally:
        db.close()
    if not row:
        return {"error": f"session {session_id} not found"}
    if (row.get("source") or "").strip().lower() in _DENY_SOURCES:
        return {"error": "session is not a user conversation"}

    return _stats(row)


@router.get("/summary")
async def summary(limit: int = 10, profile: str = "") -> dict[str, Any]:
    """Aggregate over the most recent user conversations - fleet-wide CH%."""
    try:
        db = _open_db(profile)
    except ImportError:
        return {"error": "hermes_state unavailable outside a Hermes install"}
    try:
        rows = db.list_sessions_rich(
            exclude_sources=list(_DENY_SOURCES),
            limit=max(1, min(int(limit or 10), 100)),
            order_by_last_active=True,
            compact_rows=True,
        )
    finally:
        db.close()

    agg_in = sum(int(r.get("input_tokens") or 0) for r in rows)
    agg_out = sum(int(r.get("output_tokens") or 0) for r in rows)
    agg_read = sum(int(r.get("cache_read_tokens") or 0) for r in rows)
    agg_write = sum(int(r.get("cache_write_tokens") or 0) for r in rows)
    vol = agg_in + agg_read + agg_write

    return {
        "sessions": len(rows),
        "window_limit": limit,
        "input": agg_in,
        "output": agg_out,
        "cache_read": agg_read,
        "cache_write": agg_write,
        "prompt_volume": vol,
        "cache_hit_rate": round(agg_read / vol * 100, 1) if vol > 0 else None,
        "cost_usd": sum(
            float(r.get("actual_cost_usd") or 0.0) or float(r.get("estimated_cost_usd") or 0.0)
            for r in rows
        ),
        "items": [_stats(r) for r in rows],
    }
