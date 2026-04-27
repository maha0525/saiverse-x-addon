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

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xリポスト確認エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    try:
        engagement_changes, _, new_retweets = detect_engagement(
            persona_id,
            tracked_recent_posts_count=posts_count,
            with_like_users=False,
            with_retweet_users=with_users,
        )
    except Exception as exc:
        LOGGER.error("[x_check_post_retweets] Unexpected error: %s", exc, exc_info=True)
        return f"[Xリポスト確認エラー] {exc}", ToolResult(history_snippet=None)

    rt_changes = [c for c in engagement_changes if int(c["new_retweets"]) > int(c["old_retweets"])]

    if not rt_changes:
        return "前回以降の新しいリポストはありません。", ToolResult(history_snippet="[Xリポスト確認] 0件")

    lines = [f"前回以降のリポスト差分({len(rt_changes)}ツイート):"]
    rters_by_tid = {item["tweet_id"]: item.get("retweeted_by") or [] for item in new_retweets}

    for c in rt_changes:
        tid = c["tweet_id"]
        delta = int(c["new_retweets"]) - int(c["old_retweets"])
        preview = (c.get("text_preview") or "").replace("\n", " ")
        lines.append(f"- (id={tid}) +{delta}件 (累計{c['new_retweets']}件): {preview}")
        if with_users:
            users = rters_by_tid.get(tid, [])
            if users:
                handles = ", ".join(f"@{u.get('username', '')}" for u in users[:20])
                lines.append(f"  リポストした人: {handles}")

    total = sum(int(c["new_retweets"]) - int(c["old_retweets"]) for c in rt_changes)
    return "\n".join(lines), ToolResult(history_snippet=f"[Xリポスト確認] +{total}件 / {len(rt_changes)}ツイート")


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected  # type: ignore
    return ToolSchema(
        name="x_check_post_retweets",
        description=(
            "X(Twitter)で自分の最近のポストへのリポスト件数の差分を取得します。"
            "with_users=true にすると、件数が増えたツイートに対し誰がリポストしたかも取得します"
            "(APIコール数が増えるため必要なときだけ)。"
            "定期ポーリングと engagement スナップショットを共有します。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "with_users": {
                    "type": "boolean",
                    "description": "誰がリポストしたかも取得する(コスト増)",
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
        availability_check=_is_x_connected,
    )
