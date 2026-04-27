"""X polling integration: mentions / new followers / engagement.

Single integration that bundles all four polling jobs into one
TriggerEvent per polling pass (per persona). The same ``detect_*``
helpers below are also invoked by the manual spell tools so the
"since" cursor advances in lock-step regardless of the trigger.

Event payload shape — see TRIGGER_SCHEMAS[X_POLL_DETECTED] in
``phenomena/triggers.py`` for the canonical definition.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from phenomena.triggers import TriggerEvent, TriggerType
from saiverse.integrations.base import BaseIntegration

if TYPE_CHECKING:
    from saiverse.saiverse_manager import SAIVerseManager

LOGGER = logging.getLogger(__name__)

ADDON_NAME = "saiverse-x-addon"

# Make the addon's tools/x_lib and storage importable.
_ADDON_DIR = Path(__file__).resolve().parent.parent
_TOOLS_DIR = str(_ADDON_DIR / "tools")
_ADDON_ROOT = str(_ADDON_DIR)
for _p in (_TOOLS_DIR, _ADDON_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Param helpers
# ---------------------------------------------------------------------------

def _global_params() -> Dict[str, Any]:
    from saiverse.addon_config import get_params
    return get_params(ADDON_NAME)


def _persona_params(persona_id: str) -> Dict[str, Any]:
    from saiverse.addon_config import get_params
    return get_params(ADDON_NAME, persona_id=persona_id)


# ---------------------------------------------------------------------------
# Per-category detection helpers (also used directly by spells)
# ---------------------------------------------------------------------------

def detect_mentions(persona_id: str, max_results: int = 20) -> List[Dict[str, Any]]:
    """Fetch mentions newer than the stored ``since_id`` and advance the cursor.

    Returns the list of mention dicts (each with id, text, author_username,
    author_name, created_at). Empty list if no creds, no new mentions, or
    on API error (errors are logged).
    """
    from x_lib.credentials import load_credentials  # type: ignore
    from x_lib.client import read_mentions, XAPIError  # type: ignore
    from storage.poll_state import get_section, update_section  # type: ignore

    creds = load_credentials(persona_id)
    if creds is None:
        return []

    section = get_section(persona_id, "mentions")
    since_id = section.get("last_seen_id")

    try:
        mentions = read_mentions(creds, max_results=max_results, since_id=since_id)
    except XAPIError as exc:
        LOGGER.warning(
            "[XPolling] mentions API error for %s: %s (status %d)",
            persona_id, exc.body, exc.status_code,
        )
        return []

    if not mentions:
        return []

    max_id: Optional[str] = since_id
    for m in mentions:
        tid = m.get("id")
        if tid and (max_id is None or tid > max_id):
            max_id = tid
    if max_id and max_id != since_id:
        update_section(persona_id, "mentions", {"last_seen_id": max_id})

    return mentions


def detect_new_followers(persona_id: str) -> List[Dict[str, Any]]:
    """Detect followers who appeared since the previous snapshot.

    First call only establishes the baseline (returns []). Subsequent calls
    return the set difference between the current follower list and the
    stored ``known_follower_ids``.
    """
    from x_lib.credentials import load_credentials  # type: ignore
    from x_lib.client import get_all_followers, XAPIError  # type: ignore
    from storage.poll_state import get_section, update_section  # type: ignore

    creds = load_credentials(persona_id)
    if creds is None:
        return []

    section = get_section(persona_id, "followers")
    is_first = not bool(section)
    known_ids = set(section.get("known_follower_ids") or [])

    try:
        followers = get_all_followers(creds.x_user_id, creds)
    except XAPIError as exc:
        LOGGER.warning(
            "[XPolling] followers API error for %s: %s (status %d)",
            persona_id, exc.body, exc.status_code,
        )
        return []

    current_ids = {f.get("id") for f in followers if f.get("id")}
    update_section(persona_id, "followers", {"known_follower_ids": list(current_ids)})

    if is_first:
        LOGGER.info(
            "[XPolling] First follower snapshot for %s: %d followers (baseline only)",
            persona_id, len(current_ids),
        )
        return []

    new_ids = current_ids - known_ids
    return [f for f in followers if f.get("id") in new_ids]


def detect_engagement(
    persona_id: str,
    tracked_recent_posts_count: int = 5,
    with_like_users: bool = False,
    with_retweet_users: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Detect like/retweet count growth on the persona's most recent posts.

    Returns ``(engagement_changes, new_likes, new_retweets)``:
      - ``engagement_changes``: per-tweet count delta (always populated when
        a delta is detected, regardless of with_*_users flags)
      - ``new_likes``: per-tweet liking_users payload (only when
        with_like_users=True AND likes increased)
      - ``new_retweets``: per-tweet retweeted_by payload (only when
        with_retweet_users=True AND retweets increased)

    First call only establishes the baseline (returns three empty lists).
    """
    from x_lib.credentials import load_credentials  # type: ignore
    from x_lib.client import (  # type: ignore
        get_user_tweets, get_liking_users, get_retweeted_by, XAPIError,
    )
    from storage.poll_state import get_section, update_section  # type: ignore

    creds = load_credentials(persona_id)
    if creds is None:
        return [], [], []

    section = get_section(persona_id, "engagement")
    is_first = not bool(section)
    prev_metrics: Dict[str, Dict[str, int]] = section.get("tweet_metrics") or {}

    try:
        tweets = get_user_tweets(
            creds.x_user_id, creds, max_results=tracked_recent_posts_count,
        )
    except XAPIError as exc:
        LOGGER.warning(
            "[XPolling] user_tweets API error for %s: %s (status %d)",
            persona_id, exc.body, exc.status_code,
        )
        return [], [], []

    current_metrics: Dict[str, Dict[str, int]] = {}
    engagement_changes: List[Dict[str, Any]] = []
    new_likes: List[Dict[str, Any]] = []
    new_retweets: List[Dict[str, Any]] = []

    for t in tweets:
        tid = t.get("id")
        if not tid:
            continue
        metrics = t.get("metrics") or {}
        likes = int(metrics.get("like_count") or 0)
        retweets = int(metrics.get("retweet_count") or 0)
        current_metrics[tid] = {"likes": likes, "retweets": retweets}

        if is_first:
            continue

        prev = prev_metrics.get(tid) or {"likes": 0, "retweets": 0}
        old_likes = int(prev.get("likes") or 0)
        old_retweets = int(prev.get("retweets") or 0)

        likes_grew = likes > old_likes
        retweets_grew = retweets > old_retweets
        if not (likes_grew or retweets_grew):
            continue

        engagement_changes.append({
            "tweet_id": tid,
            "text_preview": (t.get("text") or "")[:100],
            "old_likes": old_likes,
            "new_likes": likes,
            "old_retweets": old_retweets,
            "new_retweets": retweets,
        })

        if with_like_users and likes_grew:
            try:
                users = get_liking_users(tid, creds)
                new_likes.append({"tweet_id": tid, "liking_users": users})
            except XAPIError as exc:
                LOGGER.warning(
                    "[XPolling] liking_users error for tweet %s: %s",
                    tid, exc,
                )

        if with_retweet_users and retweets_grew:
            try:
                users = get_retweeted_by(tid, creds)
                new_retweets.append({"tweet_id": tid, "retweeted_by": users})
            except XAPIError as exc:
                LOGGER.warning(
                    "[XPolling] retweeted_by error for tweet %s: %s",
                    tid, exc,
                )

    update_section(persona_id, "engagement", {"tweet_metrics": current_metrics})

    if is_first:
        LOGGER.info(
            "[XPolling] First engagement snapshot for %s: %d tweets (baseline only)",
            persona_id, len(current_metrics),
        )
        return [], [], []

    return engagement_changes, new_likes, new_retweets


# ---------------------------------------------------------------------------
# Per-persona poll (returns one TriggerEvent or None)
# ---------------------------------------------------------------------------

def _build_summary(
    mentions: List[Dict[str, Any]],
    new_followers: List[Dict[str, Any]],
    engagement_changes: List[Dict[str, Any]],
) -> str:
    parts: List[str] = []
    if mentions:
        parts.append(f"メンション{len(mentions)}件")
    if new_followers:
        parts.append(f"新規フォロワー{len(new_followers)}名")
    if engagement_changes:
        like_delta = sum(
            int(c["new_likes"]) - int(c["old_likes"]) for c in engagement_changes
        )
        rt_delta = sum(
            int(c["new_retweets"]) - int(c["old_retweets"]) for c in engagement_changes
        )
        if like_delta > 0:
            parts.append(f"いいね+{like_delta}件")
        if rt_delta > 0:
            parts.append(f"リポスト+{rt_delta}件")
    return "、".join(parts) if parts else "(検出なし)"


def poll_persona(persona_id: str) -> Optional[TriggerEvent]:
    """Run all enabled polling jobs for one persona and emit ≤1 event.

    Returns ``None`` when nothing was detected (or no jobs are enabled).
    """
    from storage.poll_state import touch_last_polled  # type: ignore

    params = _persona_params(persona_id)

    mentions: List[Dict[str, Any]] = []
    new_followers: List[Dict[str, Any]] = []
    engagement_changes: List[Dict[str, Any]] = []
    new_likes: List[Dict[str, Any]] = []
    new_retweets: List[Dict[str, Any]] = []

    if params.get("poll_mentions", True):
        mentions = detect_mentions(
            persona_id, max_results=int(params.get("mentions_max_results", 20)),
        )

    if params.get("poll_followers", False):
        new_followers = detect_new_followers(persona_id)

    needs_count = bool(params.get("poll_engagement_count", False))
    needs_like_users = bool(params.get("poll_likes_detail", False))
    needs_retweet_users = bool(params.get("poll_retweets_detail", False))
    if needs_count or needs_like_users or needs_retweet_users:
        changes, likes_detail, retweets_detail = detect_engagement(
            persona_id,
            tracked_recent_posts_count=int(params.get("tracked_recent_posts_count", 5)),
            with_like_users=needs_like_users,
            with_retweet_users=needs_retweet_users,
        )
        if needs_count:
            engagement_changes = changes
        if needs_like_users:
            new_likes = likes_detail
        if needs_retweet_users:
            new_retweets = retweets_detail

    touch_last_polled(persona_id, time.time())

    if not (mentions or new_followers or engagement_changes or new_likes or new_retweets):
        return None

    summary = _build_summary(mentions, new_followers, engagement_changes)

    playbook_args = {"selected_playbook": "x_poll_handler", "summary": summary}

    return TriggerEvent(
        type=TriggerType.X_POLL_DETECTED,
        data={
            "persona_id": persona_id,
            "summary": summary,
            "mentions": mentions,
            "new_followers": new_followers,
            "engagement_changes": engagement_changes,
            "new_likes": new_likes,
            "new_retweets": new_retweets,
            "args_json": json.dumps(playbook_args, ensure_ascii=False),
        },
    )


# ---------------------------------------------------------------------------
# Integration class
# ---------------------------------------------------------------------------

class XPollingIntegration(BaseIntegration):
    """Bundled X polling: mentions / new followers / engagement deltas."""

    name = "x_polling"

    @property  # type: ignore[override]
    def poll_interval_seconds(self) -> int:  # type: ignore[override]
        """Read the user-configured interval from addon_config every tick.

        Floored at 60s to prevent accidental misconfiguration from hammering
        the X API. IntegrationManager reads this attribute each tick, so a
        change in the UI takes effect on the next polling decision.
        """
        try:
            params = _global_params()
            val = int(params.get("polling_interval_seconds", 86400))
            return max(60, val)
        except Exception:
            LOGGER.warning(
                "[XPolling] Failed to read polling_interval_seconds, "
                "falling back to 86400",
                exc_info=True,
            )
            return 86400

    def poll(self, manager: "SAIVerseManager") -> List[TriggerEvent]:
        params = _global_params()
        if not params.get("polling_enabled", True):
            LOGGER.debug("[XPolling] polling_enabled=False — skipping")
            return []

        connected = self._find_connected_personas(manager)
        if not connected:
            LOGGER.debug("[XPolling] No X-connected personas found")
            return []

        events: List[TriggerEvent] = []
        for persona_id in connected:
            try:
                event = poll_persona(persona_id)
                if event is not None:
                    events.append(event)
                    LOGGER.info(
                        "[XPolling] Detected for %s: %s",
                        persona_id, event.data.get("summary"),
                    )
            except Exception:
                LOGGER.error(
                    "[XPolling] poll_persona failed for %s", persona_id, exc_info=True,
                )
        return events

    def _find_connected_personas(self, manager: "SAIVerseManager") -> List[str]:
        from x_lib.credentials import is_connected  # type: ignore

        connected: List[str] = []
        for persona_id in manager.all_personas:
            try:
                if is_connected(persona_id):
                    connected.append(persona_id)
            except Exception:
                LOGGER.warning(
                    "[XPolling] is_connected check failed for %s",
                    persona_id, exc_info=True,
                )
        return connected
