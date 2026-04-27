"""Unfollow a user on X."""

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


def x_unfollow_user(target_user_id: str = "", username: str = "") -> Tuple[str, ToolResult]:
    """Unfollow ``target_user_id`` (numeric) or look up ``username`` and unfollow."""
    from x_lib.credentials import load_credentials
    from x_lib.client import unfollow_user, get_user_by_username, XAPIError

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xフォロー解除エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return "[Xフォロー解除エラー] X連携が設定されていません。", ToolResult(history_snippet=None)

    if not target_user_id and not username:
        return "[Xフォロー解除エラー] target_user_id か username のいずれかを指定してください。", ToolResult(history_snippet=None)

    resolved_username = username.lstrip("@")
    try:
        if not target_user_id:
            user = get_user_by_username(resolved_username, creds)
            if not user.get("id"):
                return f"[Xフォロー解除エラー] @{resolved_username} が見つかりませんでした。", ToolResult(history_snippet=None)
            target_user_id = user["id"]
            resolved_username = user.get("username", resolved_username)

        unfollow_user(target_user_id, creds)
    except XAPIError as exc:
        LOGGER.error("[x_unfollow_user] Failed: %s", exc, exc_info=True)
        return f"[Xフォロー解除エラー] フォロー解除に失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        LOGGER.error("[x_unfollow_user] Unexpected error: %s", exc, exc_info=True)
        return f"[Xフォロー解除エラー] 予期しないエラー: {exc}", ToolResult(history_snippet=None)

    label = f"@{resolved_username}" if resolved_username else f"user_id={target_user_id}"
    LOGGER.info("[x_unfollow_user] Unfollowed %s as @%s", label, creds.x_username)
    return (
        f"{label} のフォローを解除しました。",
        ToolResult(history_snippet=f"[Xフォロー解除] @{creds.x_username} → {label}"),
    )


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_unfollow_user",
        description=(
            "X(Twitter)で指定したユーザーのフォローを解除します。"
            "target_user_id (数値ID) か username (@ハンドル) のどちらかを指定してください。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "target_user_id": {
                    "type": "string",
                    "description": "フォロー解除対象の数値ユーザーID(分かっていればこちらを優先)",
                },
                "username": {
                    "type": "string",
                    "description": "フォロー解除対象の @ハンドル",
                },
            },
            "required": [],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xフォロー解除",
        spell_visible=False,
        availability_check=_is_x_connected,
    )
