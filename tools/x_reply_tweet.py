"""Reply to a tweet on X (Twitter) on behalf of the active persona.

Uses the addon-private x_reply_log SQLite (see ../storage/reply_log.py)
with a UNIQUE constraint on tweet_id to prevent double-replies.
"""

from __future__ import annotations

import logging
import sys
import threading
import uuid
from pathlib import Path
from typing import Tuple

from tools.core import ToolResult, ToolSchema
from tools.context import (
    get_active_persona_id,
    get_active_manager,
    get_auto_mode,
    get_event_callback,
)

LOGGER = logging.getLogger(__name__)

# Ensure x_lib is importable from the same parent directory
_LIB_DIR = str(Path(__file__).parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

# Ensure ../storage/ is importable for the addon-private reply_log module
_ADDON_ROOT = str(Path(__file__).resolve().parent.parent)
if _ADDON_ROOT not in sys.path:
    sys.path.insert(0, _ADDON_ROOT)


def x_reply_tweet(text: str, in_reply_to_tweet_id: str) -> Tuple[str, ToolResult]:
    """Reply to a tweet. Prevents double-replies via the addon-private reply log.

    Shows confirmation dialog unless skip_confirmation is set or in auto mode.
    """
    from x_lib.credentials import load_credentials
    from x_lib.client import reply_tweet, XAPIError
    from storage.reply_log import try_reserve_reply, commit_reply, cancel_reply

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xリプライエラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return (
            "[Xリプライエラー] X連携が設定されていません。アドオン管理画面からXアカウントを連携してください。",
            ToolResult(history_snippet=None),
        )

    if len(text) > 280:
        return (
            f"[Xリプライエラー] リプライが280文字を超えています({len(text)}文字)。短くしてください。",
            ToolResult(history_snippet=None),
        )

    # --- Double-reply prevention: reserve the slot first ---
    reservation_id = try_reserve_reply(in_reply_to_tweet_id, persona_id)
    if reservation_id is None:
        LOGGER.warning(
            "[x_reply_tweet] Already replied to tweet %s — aborting",
            in_reply_to_tweet_id,
        )
        return (
            f"[Xリプライ] このツイート(ID: {in_reply_to_tweet_id})には既にリプライ済みです。",
            ToolResult(history_snippet=None),
        )

    # --- Confirmation flow ---
    auto_mode = get_auto_mode()
    if not creds.skip_confirmation and not auto_mode:
        event_callback = get_event_callback()
        manager = get_active_manager()

        if event_callback and manager:
            request_id = str(uuid.uuid4())
            event = threading.Event()
            manager._pending_tweet_confirmations[request_id] = event

            LOGGER.info("[x_reply_tweet] Requesting reply confirmation (request_id=%s)", request_id)
            event_callback({
                "type": "tweet_confirmation",
                "request_id": request_id,
                "tweet_text": text,
                "persona_id": persona_id,
                "x_username": creds.x_username,
                "is_reply": True,
                "in_reply_to_tweet_id": in_reply_to_tweet_id,
            })

            responded = event.wait(timeout=120)

            # Cleanup
            manager._pending_tweet_confirmations.pop(request_id, None)
            response = manager._tweet_confirmation_responses.pop(request_id, None)

            if not responded or response is None:
                LOGGER.info("[x_reply_tweet] Confirmation timed out")
                cancel_reply(reservation_id)
                return "[Xリプライ] 確認がタイムアウトしました。リプライはキャンセルされました。", ToolResult(history_snippet=None)

            if response == "reject":
                LOGGER.info("[x_reply_tweet] User rejected reply")
                cancel_reply(reservation_id)
                return "[Xリプライ] ユーザーがリプライを拒否しました。", ToolResult(history_snippet=None)

            if response.startswith("edit:"):
                text = response[5:]
                LOGGER.info("[x_reply_tweet] User edited reply text")

    # --- Post reply ---
    try:
        result = reply_tweet(text, in_reply_to_tweet_id, creds)
        reply_tweet_id = result.get("data", {}).get("id", "unknown")
        commit_reply(reservation_id, reply_tweet_id)

        msg = (
            f"リプライを投稿しました!\n\n"
            f"返信先ツイートID: {in_reply_to_tweet_id}\n"
            f"返信内容: {text}\n"
            f"返信ツイートID: {reply_tweet_id}\n"
            f"アカウント: @{creds.x_username}"
        )
        snippet = f"[Xリプライ] @{creds.x_username}: {text[:80]}{'...' if len(text) > 80 else ''}"
        LOGGER.info(
            "[x_reply_tweet] Posted reply id=%s to tweet %s for %s",
            reply_tweet_id, in_reply_to_tweet_id, creds.x_username,
        )
        return msg, ToolResult(history_snippet=snippet)

    except XAPIError as exc:
        cancel_reply(reservation_id)
        LOGGER.error("[x_reply_tweet] Failed to reply: %s", exc, exc_info=True)
        return f"[Xリプライエラー] リプライの投稿に失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        cancel_reply(reservation_id)
        LOGGER.error("[x_reply_tweet] Unexpected error: %s", exc, exc_info=True)
        return f"[Xリプライエラー] 予期しないエラーが発生しました: {exc}", ToolResult(history_snippet=None)


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_reply_tweet",
        description="X(Twitter)のツイートにリプライを投稿します。二重リプライ防止機能付き。",
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "リプライ内容(最大280文字)",
                },
                "in_reply_to_tweet_id": {
                    "type": "string",
                    "description": "返信先のツイートID",
                },
            },
            "required": ["text", "in_reply_to_tweet_id"],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xにリプライ",
        spell_visible=False,
        availability_check=_is_x_connected,
    )
