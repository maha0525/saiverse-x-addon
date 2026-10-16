"""Delete a tweet on X with confirmation."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Tuple

from tools.confirmation import request_spell_confirmation
from tools.core import ToolResult, ToolSchema
from tools.context import get_active_persona_id

LOGGER = logging.getLogger(__name__)

_LIB_DIR = str(Path(__file__).parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)


def x_delete_tweet(tweet_id: str) -> Tuple[str, ToolResult]:
    """Delete a tweet. Always asks for user confirmation when not in auto mode.

    Deletion is irreversible — the ``skip_confirmation`` setting deliberately
    does NOT bypass this dialog. Auto-mode (no user attached) does skip it
    (handled inside request_spell_confirmation) so automation can complete;
    pair with strict tool gating instead.
    """
    from x_lib.credentials import load_credentials
    from x_lib.client import delete_tweet, XAPIError

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[X削除エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return "[X削除エラー] X連携が設定されていません。", ToolResult(history_snippet=None)

    # Always confirm (skip_confirmation intentionally NOT honored for delete).
    confirm = request_spell_confirmation(
        title="X ツイート削除の確認",
        body=f"ツイート(ID: {tweet_id})を削除します。削除は取り消せません。",
        editable=False,
        addon="X (Twitter)",
        confirm_text="削除する",
    )
    if not confirm.approved:
        reason = "拒否" if confirm.reason == "rejected" else "タイムアウト"
        LOGGER.info("[x_delete_tweet] confirmation not approved (%s)", confirm.reason)
        return f"[X削除] 削除はキャンセルされました（{reason}）。", ToolResult(history_snippet=None)

    try:
        delete_tweet(tweet_id, creds)
    except XAPIError as exc:
        LOGGER.error("[x_delete_tweet] Failed: %s", exc, exc_info=True)
        return f"[X削除エラー] 削除に失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        LOGGER.error("[x_delete_tweet] Unexpected error: %s", exc, exc_info=True)
        return f"[X削除エラー] 予期しないエラー: {exc}", ToolResult(history_snippet=None)

    LOGGER.info("[x_delete_tweet] Deleted tweet=%s as @%s", tweet_id, creds.x_username)
    return (
        f"ツイート(ID: {tweet_id})を削除しました。",
        ToolResult(history_snippet=f"[X削除] @{creds.x_username} → {tweet_id}"),
    )


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_delete_tweet",
        description="X(Twitter)で自分が投稿したツイートを削除します。削除は取り消せないため、必ず確認ダイアログが出ます。",
        parameters={
            "type": "object",
            "properties": {
                "tweet_id": {"type": "string", "description": "削除する自分のツイートID。タイムライン・検索・メンション等の結果に (id=...) として表示される数字"},
            },
            "required": ["tweet_id"],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xツイート削除",
        spell_visible=False,
        availability_check=_is_x_connected,
    )
