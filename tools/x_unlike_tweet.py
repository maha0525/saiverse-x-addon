"""Remove a like from a tweet on X."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Tuple

from tools.core import ToolResult, ToolSchema
from tools.context import get_active_persona_id

LOGGER = logging.getLogger(__name__)

_LIB_DIR = str(Path(__file__).parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)


def x_unlike_tweet(tweet_id: str) -> Tuple[str, ToolResult]:
    from x_lib.credentials import load_credentials
    from x_lib.client import unlike_tweet, XAPIError

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xいいね解除エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return "[Xいいね解除エラー] X連携が設定されていません。", ToolResult(history_snippet=None)

    try:
        unlike_tweet(tweet_id, creds)
    except XAPIError as exc:
        LOGGER.error("[x_unlike_tweet] Failed: %s", exc, exc_info=True)
        return f"[Xいいね解除エラー] いいね解除に失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        LOGGER.error("[x_unlike_tweet] Unexpected error: %s", exc, exc_info=True)
        return f"[Xいいね解除エラー] 予期しないエラー: {exc}", ToolResult(history_snippet=None)

    LOGGER.info("[x_unlike_tweet] Unliked tweet=%s as @%s", tweet_id, creds.x_username)
    return (
        f"ツイート(ID: {tweet_id})のいいねを解除しました。",
        ToolResult(history_snippet=f"[Xいいね解除] @{creds.x_username} → {tweet_id}"),
    )


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_unlike_tweet",
        description="X(Twitter)でつけたいいねを解除します。",
        parameters={
            "type": "object",
            "properties": {
                "tweet_id": {"type": "string", "description": "いいね解除するツイートID"},
            },
            "required": ["tweet_id"],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xいいね解除",
        spell_visible=False,
        availability_check=_is_x_connected,
    )
