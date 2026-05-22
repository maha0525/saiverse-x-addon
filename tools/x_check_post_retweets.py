"""Check new retweets on the persona's recent posts."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Tuple

from tools.core import ToolResult, ToolSchema
from tools.context import get_active_persona_id

LOGGER = logging.getLogger(__name__)

_LIB_DIR = str(Path(__file__).parent)
_ADDON_ROOT = str(Path(__file__).resolve().parent.parent)
for _p in (_LIB_DIR, _ADDON_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def x_check_post_retweets(
    with_users: bool = False,
    posts_count: int = 5,
) -> Tuple[str, ToolResult]:
    """Detect retweet-count growth on the persona's most recent posts.

    Args:
        with_users: When True, also fetch the actual users who retweeted
            (extra cost: retweeted_by API per tweet whose count grew).
        posts_count: How many recent posts to monitor (5-100).

    First call establishes baseline. Engagement snapshot is shared with
    the polling integration so this advances the same cursor.
    """
    from integrations.polling import detect_engagement  # type: ignore
    from x_lib.credentials import _global_param  # type: ignore

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xリポスト確認エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    # 設定で「誰が」取得が無効化されてれば API を叩かない (ユーザーのコスト制御)
    users_disabled_notice = ""
    if with_users and not _global_param("poll_retweets_detail", False):
        with_users = False
        users_disabled_notice = "(設定により『誰がリポストしたか』取得は無効化されています。件数のみ表示します)\n"

    try:
        engagement_changes, _, new_retweets, is_first = detect_engagement(
            persona_id,
            tracked_recent_posts_count=posts_count,
            with_like_users=False,
            with_retweet_users=with_users,
        )
    except Exception as exc:
        LOGGER.error("[x_check_post_retweets] Unexpected error: %s", exc, exc_info=True)
        return f"[Xリポスト確認エラー] {exc}", ToolResult(history_snippet=None)

    intro = users_disabled_notice
    if is_first:
        intro += (
            "[初回スナップショット] X APIはリポスト日時を返さないため、"
            f"最新{posts_count}ポストの現在のリポスト数を初回スナップショットとして表示します。"
            "次回以降は前回以降のリポスト増分のみを取得します。\n"
        )

    rt_changes = [c for c in engagement_changes if int(c["new_retweets"]) > int(c["old_retweets"])]

    if not rt_changes:
        body = (
            f"最新{posts_count}ポストにリポストは付いていません。"
            if is_first else "前回以降の新しいリポストはありません。"
        )
        return intro + body, ToolResult(history_snippet="[Xリポスト確認] 0件")

    header = (
        f"最新{posts_count}ポストの現在のリポスト状況({len(rt_changes)}ツイート):"
        if is_first else f"前回以降のリポスト差分({len(rt_changes)}ツイート):"
    )
    lines = [header]
    rters_by_tid = {item["tweet_id"]: item.get("retweeted_by") or [] for item in new_retweets}

    for c in rt_changes:
        tid = c["tweet_id"]
        new_n = int(c["new_retweets"])
        delta = new_n - int(c["old_retweets"])
        preview = (c.get("text_preview") or "").replace("\n", " ")
        if is_first:
            lines.append(f"- (id={tid}) {new_n}件: {preview}")
        else:
            lines.append(f"- (id={tid}) +{delta}件 (累計{new_n}件): {preview}")
        if with_users:
            users = rters_by_tid.get(tid, [])
            if users:
                handles = ", ".join(f"@{u.get('username', '')}" for u in users[:20])
                lines.append(f"  リポストした人: {handles}")

    total = sum(int(c["new_retweets"]) for c in rt_changes) if is_first else sum(
        int(c["new_retweets"]) - int(c["old_retweets"]) for c in rt_changes
    )
    snippet_label = "[X現在のリポスト]" if is_first else "[Xリポスト確認]"
    return intro + "\n".join(lines), ToolResult(
        history_snippet=f"{snippet_label} {'計' if is_first else '+'}{total}件 / {len(rt_changes)}ツイート"
    )


def schema() -> ToolSchema:
    from x_lib.credentials import is_retweets_monitoring_enabled  # type: ignore
    return ToolSchema(
        name="x_check_post_retweets",
        description=(
            "X(Twitter)で自分の最近のポストへのリポスト件数の差分を取得します。"
            "with_users=true にすると誰がリポストしたかも取得します(設定で無効化されてる場合は件数のみ)。"
            "定期ポーリングと engagement スナップショットを共有します。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "with_users": {
                    "type": "boolean",
                    "description": "誰がリポストしたかも取得する",
                    "default": False,
                },
                "posts_count": {
                    "type": "integer",
                    "description": "監視対象とする最新ポスト数(5-100、デフォルト5)",
                    "default": 5,
                },
            },
            "required": [],
        },
        result_type="string",
        spell=True,
        spell_display_name="X被リポスト確認",
        spell_visible=True,
        availability_check=is_retweets_monitoring_enabled,
    )
