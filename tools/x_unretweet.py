"""Cancel a retweet on X."""

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


def x_unretweet(source_tweet_id: str) -> Tuple[str, ToolResult]:
    from x_lib.credentials import load_credentials
    from x_lib.client import unretweet, XAPIError

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xリツイート解除エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return "[Xリツイート解除エラー] X連携が設定されていません。", ToolResult(history_snippet=None)

    try:
        unretweet(source_tweet_id, creds)
    except XAPIError as exc:
        LOGGER.error("[x_unretweet] Failed: %s", exc, exc_info=True)
        return f"[Xリツイート解除エラー] リツイート解除に失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        LOGGER.error("[x_unretweet] Unexpected error: %s", exc, exc_info=True)
        return f"[Xリツイート解除エラー] 予期しないエラー: {exc}", ToolResult(history_snippet=None)

    LOGGER.info(
        "[x_unretweet] Unretweeted source_tweet=%s as @%s",
        source_tweet_id, creds.x_username,
    )
    return (
        f"ツイート(元ID: {source_tweet_id})のリツイートを解除しました。",
        ToolResult(history_snippet=f"[Xリツイート解除] @{creds.x_username} → {source_tweet_id}"),
    )


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_unretweet",
        description="X(Twitter)でしたリツイートを解除します。元のツイートIDを渡してください。",
        parameters={
            "type": "object",
            "properties": {
                "source_tweet_id": {
                    "type": "string",
                    "description": "解除したいリツイートの元ツイートID",
                },
            },
            "required": ["source_tweet_id"],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xリツイート解除",
        spell_visible=False,
        availability_check=_is_x_connected,
    )
