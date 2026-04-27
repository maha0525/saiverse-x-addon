"""Check for new X followers since the last poll/spell run."""

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


def x_check_new_followers() -> Tuple[str, ToolResult]:
    """Detect followers added since the previous snapshot.

    First call only establishes the baseline (returns "no new followers")
    and subsequent calls return only the delta. Snapshot is shared with
    the polling integration.
    """
    from integrations.polling import detect_new_followers  # type: ignore

    persona_id = get_active_persona_id()
    if not persona_id:
        return "[X新規フォロワー確認エラー] ペルソナコンテキストが設定されていません。", ToolResult(history_snippet=None)

    try:
        new_followers = detect_new_followers(persona_id)
    except Exception as exc:
        LOGGER.error("[x_check_new_followers] Unexpected error: %s", exc, exc_info=True)
        return f"[X新規フォロワー確認エラー] {exc}", ToolResult(history_snippet=None)

    if not new_followers:
        return "前回以降の新規フォロワーはいません。", ToolResult(history_snippet="[X新規フォロワー] 0名")

    lines = [f"前回以降の新規フォロワー({len(new_followers)}名):"]
    for f in new_followers:
        username = f.get("username", "")
        name = f.get("name", "")
        uid = f.get("id", "")
        lines.append(f"- @{username} ({name}) (id={uid})")

    return "\n".join(lines), ToolResult(history_snippet=f"[X新規フォロワー] {len(new_followers)}名")


def schema() -> ToolSchema:
    from x_lib.credentials import is_connected as _is_x_connected  # type: ignore
    return ToolSchema(
        name="x_check_new_followers",
        description=(
            "X(Twitter)の新規フォロワーを取得します(前回スナップショットからの差分)。"
            "初回呼び出しはベースラインを保存するだけで、新規通知はゼロになります。"
            "定期ポーリングとフォロワーリストを共有します。"
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        result_type="string",
        spell=True,
        spell_display_name="X新規フォロワー確認",
        spell_visible=True,
        availability_check=_is_x_connected,
    )
