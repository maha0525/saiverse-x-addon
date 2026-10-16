"""Post a tweet to X (Twitter) on behalf of the active persona."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Tuple

from tools.confirmation import request_spell_confirmation
from tools.core import ToolResult, ToolSchema
from tools.context import get_active_persona_id

LOGGER = logging.getLogger(__name__)

# Ensure x_lib is importable from the same parent directory
_LIB_DIR = str(Path(__file__).parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)


def x_post_tweet(text: str) -> Tuple[str, ToolResult]:
    """Post a tweet. Shows confirmation dialog unless skip_confirmation is set."""
    from x_lib.credentials import load_credentials
    from x_lib.client import post_tweet, XAPIError

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[X投稿エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return (
            "[X投稿エラー] X連携が設定されていません。アドオン管理画面からXアカウントを連携してください。",
            ToolResult(history_snippet=None),
        )

    if len(text) > 280:
        return (
            f"[X投稿エラー] ツイートが280文字を超えています({len(text)}文字)。短くしてください。",
            ToolResult(history_snippet=None),
        )

    # --- Confirmation (generic spell confirmation; auto_mode handled inside) ---
    if not creds.skip_confirmation:
        confirm = request_spell_confirmation(
            title="X 投稿の確認",
            body=f"@{creds.x_username} として投稿します。",
            editable=True,
            text=text,
            addon="X (Twitter)",
            confirm_text="投稿する",
            max_chars=280,
        )
        if not confirm.approved:
            reason = "拒否" if confirm.reason == "rejected" else "タイムアウト"
            LOGGER.info("[x_post_tweet] confirmation not approved (%s)", confirm.reason)
            return f"[X投稿] 投稿はキャンセルされました（{reason}）。", ToolResult(history_snippet=None)
        if confirm.edited_text is not None:
            text = confirm.edited_text

    # --- Post tweet ---
    try:
        result = post_tweet(text, creds)
        tweet_id = result.get("data", {}).get("id", "unknown")
        msg = (
            f"ツイートを投稿しました!\n\n"
            f"投稿内容: {text}\n"
            f"ツイートID: {tweet_id}\n"
            f"アカウント: @{creds.x_username}"
        )
        snippet = f"[X投稿] @{creds.x_username}: {text[:80]}{'...' if len(text) > 80 else ''}"
        LOGGER.info("[x_post_tweet] Posted tweet id=%s for %s", tweet_id, creds.x_username)
        return msg, ToolResult(history_snippet=snippet)
    except XAPIError as exc:
        LOGGER.error("[x_post_tweet] Failed to post: %s", exc, exc_info=True)
        return f"[X投稿エラー] ツイートの投稿に失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        LOGGER.error("[x_post_tweet] Unexpected error: %s", exc, exc_info=True)
        return f"[X投稿エラー] 予期しないエラーが発生しました: {exc}", ToolResult(history_snippet=None)


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_post_tweet",
        description="X(Twitter)にツイートを投稿します。投稿前にユーザーの確認を求めます。",
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "ツイート内容(最大280文字)",
                },
            },
            "required": ["text"],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xに投稿",
        spell_visible=True,
        availability_check=_is_x_connected,
    )
