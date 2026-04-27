"""Search recent tweets on X (Twitter)."""

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


def x_search_tweets(query: str, max_results: int = 10) -> Tuple[str, ToolResult]:
    """Search recent tweets (7-day window)."""
    from x_lib.credentials import load_credentials
    from x_lib.client import search_tweets, XAPIError

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[X検索エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    creds = load_credentials(persona_id)
    if not creds:
        return (
            "[X検索エラー] X連携が設定されていません。アドオン管理画面からXアカウントを連携してください。",
            ToolResult(history_snippet=None),
        )

    try:
        tweets = search_tweets(query, creds, max_results=max_results)
    except XAPIError as exc:
        LOGGER.error("[x_search_tweets] Failed: %s", exc, exc_info=True)
        return f"[X検索エラー] 検索に失敗しました: {exc}", ToolResult(history_snippet=None)
    except Exception as exc:
        LOGGER.error("[x_search_tweets] Unexpected error: %s", exc, exc_info=True)
        return f"[X検索エラー] 予期しないエラー: {exc}", ToolResult(history_snippet=None)

    if not tweets:
        return f"「{query}」に一致するツイートは見つかりませんでした。", ToolResult(history_snippet=f"[X検索] \"{query}\" → 0件")

    lines = [f"「{query}」の検索結果({len(tweets)}件):"]
    for t in tweets:
        name = t.get("author_name", "")
        username = t.get("author_username", "")
        text = t.get("text", "")
        created = t.get("created_at", "")
        metrics = t.get("metrics", {})
        likes = metrics.get("like_count", 0)
        rts = metrics.get("retweet_count", 0)
        line = f"- @{username} ({name}) [{created}] ♥{likes} 🔁{rts}: {text}"
        for m in t.get("media", []):
            line += f"\n  [{m.get('type', 'media')}] {m['url']}"
        lines.append(line)

    msg = "\n".join(lines)
    snippet = f"[X検索] \"{query}\" → {len(tweets)}件"
    LOGGER.info("[x_search_tweets] Search '%s' returned %d results", query, len(tweets))
    return msg, ToolResult(history_snippet=snippet)


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected
    return ToolSchema(
        name="x_search_tweets",
        description="X(Twitter)でツイートを検索します(直近7日間)。",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索クエリ",
                },
                "max_results": {
                    "type": "integer",
                    "description": "取得する結果数(10-100、デフォルト10)",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xを検索",
        spell_visible=True,
        availability_check=_is_x_connected,
    )
