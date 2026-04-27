"""OAuth post-authorize handlers for the X addon.

Called by saiverse.oauth.handler after a successful token exchange.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

LOGGER = logging.getLogger(__name__)

_X_USERS_ME_URL = "https://api.x.com/2/users/me"


def fetch_user_info(
    persona_id: str,
    tokens: Dict[str, Any],
    params: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Fetch the authenticated user's info via /users/me and return extras to persist.

    Args:
        persona_id: The persona that just completed OAuth.
        tokens: Raw token endpoint response (includes access_token).
        params: Current AddonPersonaConfig.params merged with the new tokens.

    Returns:
        Dict of extra keys to merge into AddonPersonaConfig.params_json
        (``x_user_id`` and ``x_username``), or ``None`` on failure.
    """
    access_token = tokens.get("access_token") or params.get("x_access_token")
    if not access_token:
        LOGGER.warning(
            "[x_handlers] fetch_user_info: no access_token for persona=%s",
            persona_id,
        )
        return None

    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                _X_USERS_ME_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
                params={"user.fields": "username,name"},
            )
    except httpx.HTTPError as exc:
        LOGGER.warning(
            "[x_handlers] /users/me request failed for persona=%s: %s",
            persona_id, exc,
        )
        return None

    if resp.status_code >= 400:
        LOGGER.warning(
            "[x_handlers] /users/me returned %d for persona=%s: %s",
            resp.status_code, persona_id, resp.text[:200],
        )
        return None

    try:
        data = resp.json().get("data") or {}
    except ValueError:
        LOGGER.warning("[x_handlers] /users/me returned non-JSON for persona=%s", persona_id)
        return None

    user_id = data.get("id")
    username = data.get("username")
    if not user_id or not username:
        LOGGER.warning(
            "[x_handlers] /users/me response missing id/username for persona=%s: %r",
            persona_id, data,
        )
        return None

    LOGGER.info(
        "[x_handlers] Connected persona=%s as @%s (id=%s)",
        persona_id, username, user_id,
    )
    return {"x_user_id": user_id, "x_username": username}
