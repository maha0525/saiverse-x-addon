"""Fetch a specific user's recent tweets on X."""

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


def x_get_user_tweets(
    username: str = "",
    target_user_id: str = "",
    max_results: int = 10,
) -> Tuple[str, ToolResult]:
    """Fetch recent tweets posted by ``username`` (or numeric ``target_user_id``)."""
    from x_lib.credentials import load_credentials
    from x_lib.client import get_user_by_username, get_user_tweets, XAPIError

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xユーザーツイートエラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return "[Xユーザーツイートエラー] X連携が設定されていません。", ToolResult(history_snippet=None)

    if not username and not target_user_id:
        return "[Xユーザーツイートエラー] username か target_user_id のいずれかを指定してください。", ToolResult(history_snippet=None)

    resolved_username = username.lstrip("@")
    try:
        if not target_user_id:
            user = get_user_by_username(resolved_username, creds)
            if not user.get("id"):
                return f"[Xユーザーツイートエラー] @{resolved_username} が見つかりませんでした。", ToolResult(history_snippet=None)
            target_user_id = user["id"]
            resolved_username = user.get("username", resolved_username)

        tweets = get_user_tweets(target_user_id, creds, max_results=max_results)
    except XAPIError as exc:
        LOGGER.error("[x_get_user_tweets] Failed: %s", exc, exc_info=True)
        return f"[Xユーザーツイートエラー] 取得に失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        LOGGER.error("[x_get_user_tweets] Unexpected error: %s", exc, exc_info=True)
        return f"[Xユーザーツイートエラー] 予期しないエラー: {exc}", ToolResult(history_snippet=None)

    label = f"@{resolved_username}" if resolved_username else f"user_id={target_user_id}"
    if not tweets:
        return f"{label} のツイートはありませんでした。", ToolResult(history_snippet=f"[Xユーザーツイート] {label} → 0件")

    lines = [f"{label} のツイート({len(tweets)}件):"]
    for t in tweets:
        text = t.get("text", "")
        created = t.get("created_at", "")
        metrics = t.get("metrics", {})
        likes = metrics.get("like_count", 0)
        rts = metrics.get("retweet_count", 0)
        tid = t.get("id", "")
        line = f"- [{created}] (id={tid}) ♥{likes} 🔁{rts}: {text}"
        for m in t.get("media", []):
            line += f"\n  [{m.get('type', 'media')}] {m['url']}"
        lines.append(line)

    msg = "\n".join(lines)
    LOGGER.info("[x_get_user_tweets] Fetched %d tweets of %s", len(tweets), label)
    return msg, ToolResult(history_snippet=f"[Xユーザーツイート] {label} → {len(tweets)}件")


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_get_user_tweets",
        description=(
            "X(Twitter)で指定したユーザーの最近のツイートを取得します。"
            "username (@ハンドル) か target_user_id (数値ID) のどちらかを指定してください。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "取得対象の @ハンドル(target_user_id を渡せない場合に使用)",
                },
                "target_user_id": {
                    "type": "string",
                    "description": "取得対象の数値ユーザーID(分かっていればこちらを優先)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "取得するツイート数(5-100、デフォルト10)",
                    "default": 10,
                },
            },
            "required": [],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xユーザーのツイート取得",
        spell_visible=False,
        availability_check=_is_x_connected,
    )
