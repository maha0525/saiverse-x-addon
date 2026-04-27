"""Follow a user on X."""

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


def x_follow_user(target_user_id: str = "", username: str = "") -> Tuple[str, ToolResult]:
    """Follow ``target_user_id`` (numeric) or look up ``username`` (@handle) and follow."""
    from x_lib.credentials import load_credentials
    from x_lib.client import follow_user, get_user_by_username, XAPIError

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xフォローエラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return "[Xフォローエラー] X連携が設定されていません。", ToolResult(history_snippet=None)

    if not target_user_id and not username:
        return "[Xフォローエラー] target_user_id か username のいずれかを指定してください。", ToolResult(history_snippet=None)

    resolved_username = username.lstrip("@")
    try:
        if not target_user_id:
            user = get_user_by_username(resolved_username, creds)
            if not user.get("id"):
                return f"[Xフォローエラー] @{resolved_username} が見つかりませんでした。", ToolResult(history_snippet=None)
            target_user_id = user["id"]
            resolved_username = user.get("username", resolved_username)

        follow_user(target_user_id, creds)
    except XAPIError as exc:
        LOGGER.error("[x_follow_user] Failed: %s", exc, exc_info=True)
        return f"[Xフォローエラー] フォローに失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        LOGGER.error("[x_follow_user] Unexpected error: %s", exc, exc_info=True)
        return f"[Xフォローエラー] 予期しないエラー: {exc}", ToolResult(history_snippet=None)

    label = f"@{resolved_username}" if resolved_username else f"user_id={target_user_id}"
    LOGGER.info("[x_follow_user] Followed %s as @%s", label, creds.x_username)
    return (
        f"{label} をフォローしました。",
        ToolResult(history_snippet=f"[Xフォロー] @{creds.x_username} → {label}"),
    )


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_follow_user",
        description=(
            "X(Twitter)で指定したユーザーをフォローします。"
            "target_user_id (数値ID) か username (@ハンドル) のどちらかを指定してください。"
            "username 指定時は内部で X API のユーザー検索を1回行います。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "target_user_id": {
                    "type": "string",
                    "description": "フォロー対象の数値ユーザーID(分かっていればこちらを優先)",
                },
                "username": {
                    "type": "string",
                    "description": "フォロー対象の @ハンドル(target_user_id を渡せない場合に使用)",
                },
            },
            "required": [],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xでフォロー",
        spell_visible=False,
        availability_check=_is_x_connected,
    )
