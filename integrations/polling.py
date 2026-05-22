"""X polling integration: mentions / new followers / engagement.

Single integration that bundles all four polling jobs into one
TriggerEvent per polling pass (per persona). The same ``detect_*``
helpers below are also invoked by the manual spell tools so the
"since" cursor advances in lock-step regardless of the trigger.

Event payload shape — see TRIGGER_SCHEMAS[X_POLL_DETECTED] in
``phenomena/triggers.py`` for the canonical definition.

**Initial snapshot policy** (per category):
 - mentions: 初回はサーバー側 ``start_time = now - 24h`` で取得 (X API が
   start_time をサポートしているため)。次回以降は ``since_id`` で差分。
 - new_followers: 初回は現在の全フォロワーを返す (API は followed_at を
   返さないので 24h 絞り不可。AI ペルソナのフォロワー数前提なら許容)。
 - engagement: 初回は最新 N ツイートの **現在の** like_count/retweet_count
   を「初回スナップショット時点の数」として返す (with_users が ON なら
   liking_users / retweeted_by も全件取得する)。次回以降は差分。

各 ``detect_*`` は ``is_first`` フラグを併せて返し、呼び出し側 (スペル/
ポーリング) は冒頭に「初回スナップショット取得しました。次回以降は前回
からの増分のみ通知します」のアナウンスを付ける責務を持つ。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

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

def detect_mentions(persona_id: str, max_results: int = 20) -> Tuple[List[Dict[str, Any]], bool]:
    """Fetch mentions newer than the stored ``since_id`` and advance the cursor.

    Returns ``(mentions, is_first)``:
      - mentions: list of mention dicts (id, text, author_username, author_name, created_at)
      - is_first: True if this was the initial snapshot (no since_id stored yet)

    On the initial call, ``start_time = now - 24h`` is sent to backfill recent
    mentions instead of returning an empty list. Subsequent calls use ``since_id``
    for delta detection. Empty list on no creds / no mentions / API error.
    """
    from x_lib.credentials import load_credentials  # type: ignore
    from x_lib.client import read_mentions, XAPIError  # type: ignore
    from storage.poll_state import get_section, update_section  # type: ignore

    creds = load_credentials(persona_id)
    if creds is None:
        return [], False

    section = get_section(persona_id, "mentions")
    since_id = section.get("last_seen_id")
    is_first = since_id is None

    start_time: Optional[str] = None
    if is_first:
        start_time = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    # API エラーは raise する。空 list で隠蔽すると呼び出し側で「ヒット 0 件」と
    # 区別がつかず、誤った "no new mentions" 表示や initial flag のロストにつながる。
    mentions = read_mentions(
        creds, max_results=max_results, since_id=since_id, start_time=start_time,
    )

    # メンションを 1 件でも取れた時のみ cursor を進める。0 件のままなら
    # is_first = True を維持して次回も 24h backfill (= ポーリング間隔と同じ)。
    # 1 件でも来たら以降は since_id ベースの差分検出に移る。
    max_id: Optional[str] = since_id
    for m in mentions:
        tid = m.get("id")
        if tid and (max_id is None or tid > max_id):
            max_id = tid
    if max_id and max_id != since_id:
        update_section(persona_id, "mentions", {"last_seen_id": max_id})

    return mentions, is_first


def detect_new_followers(persona_id: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Detect followers who appeared since the previous snapshot.

    Returns ``(followers, is_first)``:
      - followers: 初回は **現在の全フォロワー一覧**、次回以降は新規分のみ
      - is_first: True if this was the initial snapshot (no known_ids stored yet)

    X API は ``followed_at`` を返さないので「24h 以内にフォローした人」で
    絞ることはできない。代わりに初回は全件を返す (内部 API call は同じく
    1 ペルソナ 1 回なのでコスト変わらず)。
    """
    from x_lib.credentials import load_credentials  # type: ignore
    from x_lib.client import get_all_followers, XAPIError  # type: ignore
    from storage.poll_state import get_section, update_section  # type: ignore

    creds = load_credentials(persona_id)
    if creds is None:
        return [], False

    section = get_section(persona_id, "followers")
    is_first = not bool(section)
    known_ids = set(section.get("known_follower_ids") or [])

    # API エラーは raise する (誤った baseline 確立や初回フラグのロストを防ぐ)
    followers = get_all_followers(creds.x_user_id, creds)

    current_ids = {f.get("id") for f in followers if f.get("id")}
    update_section(persona_id, "followers", {"known_follower_ids": list(current_ids)})

    if is_first:
        LOGGER.info(
            "[XPolling] First follower snapshot for %s: %d followers (returning all as initial)",
            persona_id, len(current_ids),
        )
        return followers, True

    new_ids = current_ids - known_ids
    return [f for f in followers if f.get("id") in new_ids], False


def detect_engagement(
    persona_id: str,
    tracked_recent_posts_count: int = 5,
    with_like_users: bool = False,
    with_retweet_users: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    """Detect like/retweet count growth on the persona's most recent posts.

    Returns ``(engagement_changes, new_likes, new_retweets, is_first)``:
      - ``engagement_changes``: per-tweet count delta (初回は old=0 として現在の数を返す)
      - ``new_likes``: per-tweet liking_users payload (with_like_users=True かつ
        likes が増えた時のみ。初回は old=0 なので「増えた」扱い)
      - ``new_retweets``: per-tweet retweeted_by payload (同上、retweet版)
      - ``is_first``: True if this was the initial snapshot

    X API は「いつ いいねしたか」を返さないので「24h で増えた数」を絞ることは
    できない。初回は最新 N ツイートの **現在の** like_count/retweet_count を
    「初回スナップショット時点の数」として返す (with_users=True なら誰がも
    取得する。コストは普段のポーリングと同じく N ツイート分)。
    """
    from x_lib.credentials import load_credentials  # type: ignore
    from x_lib.client import (  # type: ignore
        get_user_tweets, get_liking_users, get_retweeted_by, XAPIError,
    )
    from storage.poll_state import get_section, update_section  # type: ignore

    creds = load_credentials(persona_id)
    if creds is None:
        return [], [], [], False

    section = get_section(persona_id, "engagement")
    is_first = not bool(section)
    prev_metrics: Dict[str, Dict[str, int]] = section.get("tweet_metrics") or {}

    # API エラーは raise する (誤った baseline 確立や初回フラグのロストを防ぐ)
    tweets = get_user_tweets(
        creds.x_user_id, creds, max_results=tracked_recent_posts_count,
    )

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

        # 初回は old=0 として「現在の数を初回スナップショット」に。差分通知と同じ
        # フォーマットで返すことで呼び出し側の処理を共通化できる。
        if is_first:
            old_likes = 0
            old_retweets = 0
        else:
            prev = prev_metrics.get(tid) or {"likes": 0, "retweets": 0}
            old_likes = int(prev.get("likes") or 0)
            old_retweets = int(prev.get("retweets") or 0)

        likes_grew = likes > old_likes
        retweets_grew = retweets > old_retweets
        # 増分なし、かつ初回でもない: 何も通知しない
        # 初回 + 0いいね 0リポスト: スキップ (ノイズ回避)
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
            "[XPolling] First engagement snapshot for %s: %d tweets (returning current as initial)",
            persona_id, len(current_metrics),
        )

    return engagement_changes, new_likes, new_retweets, is_first


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
    The event payload includes ``initial_categories``: the set of category
    keys ("mentions", "followers", "engagement") that returned an initial
    snapshot. The handler playbook uses this to prepend an "初回スナップ
    ショット" announcement so the persona understands the timing.
    """
    from storage.poll_state import touch_last_polled  # type: ignore

    params = _persona_params(persona_id)

    mentions: List[Dict[str, Any]] = []
    new_followers: List[Dict[str, Any]] = []
    engagement_changes: List[Dict[str, Any]] = []
    new_likes: List[Dict[str, Any]] = []
    new_retweets: List[Dict[str, Any]] = []
    initial_categories: Set[str] = set()
    errors: Dict[str, str] = {}  # category -> error message。ペルソナへ通知される

    # 各カテゴリ独立して try。片方の API エラーで全体停止せず、かつエラーは
    # ``errors`` に集約してペルソナへ通知される (バックエンドログだけにしない)。
    if params.get("poll_mentions", True):
        try:
            mentions, m_first = detect_mentions(
                persona_id, max_results=int(params.get("mentions_max_results", 20)),
            )
            if m_first:
                initial_categories.add("mentions")
        except Exception as exc:
            LOGGER.error("[XPolling] mentions failed for %s: %s", persona_id, exc, exc_info=True)
            errors["mentions"] = str(exc)

    if params.get("poll_followers", False):
        try:
            new_followers, f_first = detect_new_followers(persona_id)
            if f_first:
                initial_categories.add("followers")
        except Exception as exc:
            LOGGER.error("[XPolling] followers failed for %s: %s", persona_id, exc, exc_info=True)
            errors["followers"] = str(exc)

    needs_count = bool(params.get("poll_engagement_count", False))
    needs_like_users = bool(params.get("poll_likes_detail", False))
    needs_retweet_users = bool(params.get("poll_retweets_detail", False))
    if needs_count or needs_like_users or needs_retweet_users:
        try:
            changes, likes_detail, retweets_detail, e_first = detect_engagement(
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
            if e_first:
                initial_categories.add("engagement")
        except Exception as exc:
            LOGGER.error("[XPolling] engagement failed for %s: %s", persona_id, exc, exc_info=True)
            errors["engagement"] = str(exc)

    # last_polled は **エラーが無かった時のみ** 更新。エラー時に時刻を進めると、
    # 次の polling_interval_seconds (デフォルト 24h) まで再試行されず、エラー原因
    # 解消後も復旧が遅れる。エラーカテゴリがあれば次の tick で即リトライさせる。
    if not errors:
        touch_last_polled(persona_id, time.time())

    # データもエラーも何もない時のみ event 抑制 (= 通常の "no new data")。
    # エラーがあれば必ずペルソナに通知 event を出す。
    if not (mentions or new_followers or engagement_changes or new_likes or new_retweets or errors):
        return None

    summary = _build_summary(mentions, new_followers, engagement_changes)
    if initial_categories:
        summary = f"[初回スナップショット: {','.join(sorted(initial_categories))}] " + summary
    if errors:
        summary = f"[エラー: {','.join(sorted(errors.keys()))}] " + summary

    # アナウンス文は polling 側で pre-render する。空の時は空文字列にして
    # playbook 側で固定文言を毎回流さない (トークン節約)。
    errors_announcement = ""
    if errors:
        errors_announcement = (
            f"\n\n⚠ エラーが起きたカテゴリ: {errors} "
            "(取得失敗。OAuth再認可・scope不足・rate limit等の可能性。"
            "errorsに含まれるカテゴリのデータは今回ゼロ件として扱われているため信頼しないこと。"
            "ユーザーに状況を伝えるか、設定見直しを促すこと)"
        )
    initial_announcement = ""
    if initial_categories:
        initial_announcement = (
            f"\n\n初回スナップショットを含むカテゴリ: {sorted(initial_categories)} "
            "(記載カテゴリは「前回からの増分ではない」点に注意。次回以降は差分のみ通知)"
        )

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
            "initial_categories": sorted(initial_categories),
            "errors": errors,
            "errors_announcement": errors_announcement,
            "initial_announcement": initial_announcement,
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
