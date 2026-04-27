"""Look up an X user's profile by @username."""

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


def x_get_user(username: str) -> Tuple[str, ToolResult]:
    from x_lib.credentials import load_credentials
    from x_lib.client import get_user_by_username, XAPIError

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xユーザー取得エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return "[Xユーザー取得エラー] X連携が設定されていません。", ToolResult(history_snippet=None)

    handle = username.lstrip("@")
    try:
        user = get_user_by_username(handle, creds)
    except XAPIError as exc:
        LOGGER.error("[x_get_user] Failed: %s", exc, exc_info=True)
        return f"[Xユーザー取得エラー] 取得に失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        LOGGER.error("[x_get_user] Unexpected error: %s", exc, exc_info=True)
        return f"[Xユーザー取得エラー] 予期しないエラー: {exc}", ToolResult(history_snippet=None)

    if not user or not user.get("id"):
        return f"@{handle} が見つかりませんでした。", ToolResult(history_snippet=f"[Xユーザー取得] @{handle} → 0件")

    metrics = user.get("public_metrics", {}) or {}
    lines = [
        f"@{user.get('username', handle)} のプロフィール",
        f"  名前: {user.get('name', '')}",
        f"  ID: {user.get('id', '')}",
        f"  自己紹介: {user.get('description', '')}",
        f"  フォロワー: {metrics.get('followers_count', 0):,} / フォロー中: {metrics.get('following_count', 0):,}",
        f"  ツイート数: {metrics.get('tweet_count', 0):,}",
        f"  認証済み: {user.get('verified', False)}",
        f"  作成日: {user.get('created_at', '')}",
    ]
    msg = "\n".join(lines)
    LOGGER.info("[x_get_user] Fetched profile of @%s", user.get("username", handle))
    return msg, ToolResult(history_snippet=f"[Xユーザー取得] @{user.get('username', handle)}")


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_get_user",
        description="X(Twitter)で指定した @ユーザー名のプロフィール情報を取得します。",
        parameters={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "取得対象の @ハンドル(@ あり/なしどちらでも可)",
                },
            },
            "required": ["username"],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xユーザー情報取得",
        spell_visible=False,
        availability_check=_is_x_connected,
    )
