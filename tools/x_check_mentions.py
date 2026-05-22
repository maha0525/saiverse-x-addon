"""Check X mentions since the last poll/spell run."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Tuple

from tools.core import ToolResult, ToolSchema
from tools.context import get_active_persona_id

LOGGER = logging.getLogger(__name__)

_LIB_DIR = str(Path(__file__).parent)
_ADDON_ROOT = str(Path(__file__).resolve().parent.parent)
for _p in (_LIB_DIR, _ADDON_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def x_check_mentions(max_results: int = 20) -> Tuple[str, ToolResult]:
    """Fetch mentions newer than the last cursor and advance it.

    The cursor is shared with the polling integration, so calling this
    spell consumes mentions that the polling job would have surfaced
    next time (and vice versa).
    """
    from integrations.polling import detect_mentions  # type: ignore

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[Xメンション確認エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    try:
        mentions, is_first = detect_mentions(persona_id, max_results=max_results)
    except Exception as exc:
        LOGGER.error("[x_check_mentions] Unexpected error: %s", exc, exc_info=True)
        return f"[Xメンション確認エラー] {exc}", ToolResult(history_snippet=None)

    # 初回スナップショット時のアナウンス文 (cursor 不在 → 24h backfill)
    intro = ""
    if is_first:
        intro = (
            "[初回スナップショット] 直近24時間のメンションを取得します。"
            "次回以降は前回確認以降の新着分のみを取得します。\n"
        )

    if not mentions:
        body = (
            "直近24時間に新しいメンションはありません。"
            if is_first else "前回以降の新しいメンションはありません。"
        )
        return intro + body, ToolResult(history_snippet="[Xメンション確認] 0件")

    header = (
        f"直近24時間のメンション({len(mentions)}件):"
        if is_first else f"前回以降の新しいメンション({len(mentions)}件):"
    )
    lines = [header]
    for m in mentions:
        username = m.get("author_username", "")
        name = m.get("author_name", "")
        text = m.get("text", "")
        created = m.get("created_at", "")
        tid = m.get("id", "")
        lines.append(f"- @{username} ({name}) [{created}] (id={tid}): {text}")

    return intro + "\n".join(lines), ToolResult(history_snippet=f"[Xメンション確認] {len(mentions)}件")


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected  # type: ignore
    return ToolSchema(
        name="x_check_mentions",
        description=(
            "X(Twitter)の前回確認以降の新しいメンション/リプライを取得します。"
            "定期ポーリングと since カーソルを共有するため、このスペルで取った分は"
            "次のポーリングでは通知されません。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "max_results": {
                    "type": "integer",
                    "description": "取得するメンション数(5-100、デフォルト20)",
                    "default": 20,
                },
            },
            "required": [],
        },
        result_type="string",
        spell=True,
        spell_display_name="Xメンション確認",
        spell_visible=True,
        availability_check=_is_x_connected,
    )
