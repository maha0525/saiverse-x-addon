"""Regression tests for storage.poll_state.

update_section() / touch_last_polled() hold the per-persona lock while calling
save_state() / load_state(), which take the same lock again. With a plain
threading.Lock this deadlocked the calling thread forever: the
"X新規フォロワー確認" spell never returned, and the persona stopped replying
until SAIVerse was restarted.

Run from the SAIVerse repository root:
    python -m pytest expansion_data/saiverse-x-addon/tests -n 0
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

_ADDON_ROOT = Path(__file__).resolve().parent.parent
if str(_ADDON_ROOT) not in sys.path:
    sys.path.insert(0, str(_ADDON_ROOT))

from storage import poll_state  # noqa: E402

_TIMEOUT = 5.0


@pytest.fixture(autouse=True)
def _isolated_state_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(poll_state, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(poll_state, "_locks", {})


def _finishes(fn, *args) -> bool:
    worker = threading.Thread(target=fn, args=args, daemon=True)
    worker.start()
    worker.join(_TIMEOUT)
    return not worker.is_alive()


def test_update_section_returns_and_persists():
    assert _finishes(poll_state.update_section, "p", "followers", {"known_follower_ids": ["1"]})
    assert poll_state.get_section("p", "followers") == {"known_follower_ids": ["1"]}


def test_update_section_twice_keeps_other_sections():
    assert _finishes(poll_state.update_section, "p", "mentions", {"last_seen_id": "10"})
    assert _finishes(poll_state.update_section, "p", "followers", {"known_follower_ids": ["2"]})
    state = poll_state.load_state("p")
    assert state["mentions"] == {"last_seen_id": "10"}
    assert state["followers"] == {"known_follower_ids": ["2"]}


def test_touch_last_polled_returns_on_existing_file():
    assert _finishes(poll_state.update_section, "p", "followers", {"known_follower_ids": []})
    assert _finishes(poll_state.touch_last_polled, "p", 123.0)
    assert poll_state.get_last_polled("p") == 123.0
    assert poll_state.get_section("p", "followers") == {"known_follower_ids": []}
