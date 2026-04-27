"""Check new likes on the persona's recent posts."""

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


def x_check_post_likes(
    with_users: bool = False,
    posts_count: int = 5,
) -> Tuple[str, ToolResult]:
    """Detect like-count growth on the persona's most recent posts.

    Args:
        with_users: When True, also fetch the actual users who liked
            (extra cost: liking_users API per tweet whose count grew).
        posts_count: How many recent posts to monitor (5-100).

    First call establishes baseline. Engagement snapshot is shared with
    the polling integration so this advances the same cursor.
    """
    from integrations.polling import detect_engagement  # type: ignore

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xいいね確認エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    try:
        engagement_changes, new_likes, _ = detect_engagement(
            persona_id,
            tracked_recent_posts_count=posts_count,
            with_like_users=with_users,
            with_retweet_users=False,
        )
    except Exception as exc:
        LOGGER.error("[x_check_post_likes] Unexpected error: %s", exc, exc_info=True)
        return f"[Xいいね確認エラー] {exc}", ToolResult(history_snippet=None)

    # Filter to changes that have a like delta
    like_changes = [c for c in engagement_changes if int(c["new_likes"]) > int(c["old_likes"])]

    if not like_changes:
        return "前回以降の新しいいいねはありません。", ToolResult(history_snippet="[Xいいね確認] 0件")

    lines = [f"前回以降のいいね差分({len(like_changes)}ツイート):"]
    likers_by_tid = {item["tweet_id"]: item.get("liking_users") or [] for item in new_likes}

    for c in like_changes:
        tid = c["tweet_id"]
        delta = int(c["new_likes"]) - int(c["old_likes"])
        preview = (c.get("text_preview") or "").replace("\n", " ")
        lines.append(f"- (id={tid}) +{delta}件 (累計{c['new_likes']}件): {preview}")
        if with_users:
            users = likers_by_tid.get(tid, [])
            if users:
                handles = ", ".join(f"@{u.get('username', '')}" for u in users[:20])
                lines.append(f"  いいねした人: {handles}")

    total = sum(int(c["new_likes"]) - int(c["old_likes"]) for c in like_changes)
    return "\n".join(lines), ToolResult(history_snippet=f"[Xいいね確認] +{total}件 / {len(like_changes)}ツイート")


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected  # type: ignore
    return ToolSchema(
        name="x_check_post_likes",
        description=(
            "X(Twitter)で自分の最近のポストへのいいね件数の差分を取得します。"
            "with_users=true にすると、件数が増えたツイートに対し誰がいいねしたかも取得します"
            "(APIコール数が増えるため必要なときだけ)。"
            "定期ポーリングと engagement スナップショットを共有します。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "with_users": {
                    "type": "boolean",
                    "description": "誰がいいねしたかも取得する(コスト増)",
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
        spell_display_name="X被いいね確認",
        spell_visible=True,
        availability_check=_is_x_connected,
    )
