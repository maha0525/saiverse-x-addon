"""Per-persona polling state for the X addon.

Stores "since" checkpoints for each polling category so that polling
runs and manual spell invocations both advance the same cursor. The
caller that successfully fetches new data is responsible for writing
the updated section back, which guarantees no double-notification
across both code paths.

Layout (one JSON file per persona):
    ~/.saiverse/user_data/addon_data/saiverse-x-addon/poll_state/<persona_id>.json

    {
      "mentions":   {"last_seen_id": "1700..."},
      "followers":  {"known_follower_ids": ["123", ...]},
      "engagement": {"tweet_metrics": {"<tweet_id>": {"likes": N, "retweets": N}, ...}},
      "last_polled_at": 1714200000.0
    }
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

LOGGER = logging.getLogger(__name__)

ADDON_NAME = "saiverse-x-addon"

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _state_dir() -> Path:
    from saiverse.addon_paths import get_addon_data_dir

    base = get_addon_data_dir(ADDON_NAME) / "poll_state"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _state_file(persona_id: str) -> Path:
    return _state_dir() / f"{persona_id}.json"


def _persona_lock(persona_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(persona_id)
        if lock is None:
            lock = threading.Lock()
            _locks[persona_id] = lock
        return lock


def load_state(persona_id: str) -> Dict[str, Any]:
    """Read the persona's full polling state. Missing keys default to {}."""
    path = _state_file(persona_id)
    if not path.exists():
        return {}
    try:
        with _persona_lock(persona_id):
            data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            LOGGER.warning("[poll_state] %s: not a dict, resetting", path)
            return {}
        return data
    except Exception:
        LOGGER.error("[poll_state] Failed to load %s", path, exc_info=True)
        return {}


def save_state(persona_id: str, state: Dict[str, Any]) -> None:
    """Write the persona's full polling state atomically."""
    path = _state_file(persona_id)
    tmp_path = path.with_suffix(".json.tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=2)
    try:
        with _persona_lock(persona_id):
            tmp_path.write_text(payload, encoding="utf-8")
            if path.exists():
                path.unlink()
            tmp_path.rename(path)
    except Exception:
        LOGGER.error("[poll_state] Failed to save %s", path, exc_info=True)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def update_section(persona_id: str, section: str, new_section_state: Dict[str, Any]) -> None:
    """Replace one top-level section atomically (read-modify-write)."""
    with _persona_lock(persona_id):
        path = _state_file(persona_id)
        if path.exists():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    state = {}
            except Exception:
                LOGGER.error("[poll_state] Failed to read for update %s", path, exc_info=True)
                state = {}
        else:
            state = {}
        state[section] = new_section_state
        save_state(persona_id, state)


def get_section(persona_id: str, section: str) -> Dict[str, Any]:
    """Read one top-level section, returning {} if missing."""
    state = load_state(persona_id)
    sub = state.get(section)
    return sub if isinstance(sub, dict) else {}


def touch_last_polled(persona_id: str, when: float) -> None:
    """Record the last polling timestamp (does not affect category sections)."""
    with _persona_lock(persona_id):
        state = load_state(persona_id)
        state["last_polled_at"] = when
        save_state(persona_id, state)


def get_last_polled(persona_id: str) -> Optional[float]:
    state = load_state(persona_id)
    val = state.get("last_polled_at")
    return float(val) if isinstance(val, (int, float)) else None
