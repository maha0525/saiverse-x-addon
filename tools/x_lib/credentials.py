"""Per-persona X (Twitter) credential access via AddonPersonaConfig.

Tokens are stored in the AddonPersonaConfig table by saiverse.oauth.handler
when the user completes the OAuth flow. This module exposes a thin facade
for the X tools: callers ask for a persona_id and get back a ready-to-use
XCredentials with a *valid* (auto-refreshed) access token.

The legacy on-disk ``~/.saiverse/personas/<id>/x_credentials.json`` storage
is no longer used.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

LOGGER = logging.getLogger(__name__)

ADDON_NAME = "saiverse-x-addon"
OAUTH_FLOW_KEY = "x"


@dataclass
class XCredentials:
    """Ready-to-use credentials for X API calls.

    ``access_token`` is guaranteed to be valid at call time when produced
    by :func:`load_credentials` (the OAuth handler refreshes it transparently).
    """

    access_token: str
    x_user_id: str
    x_username: str
    skip_confirmation: bool = False


def load_credentials(persona_id: str) -> Optional[XCredentials]:
    """Return ready-to-use X credentials for ``persona_id`` or ``None``.

    Pulls the latest params from AddonPersonaConfig, calls the OAuth handler
    to refresh the access token if it has expired (or is about to), and
    returns an ``XCredentials`` instance. Returns ``None`` if the persona
    has not connected an X account.
    """
    if not persona_id:
        return None

    from saiverse.addon_config import get_params
    from saiverse.oauth.handler import get_valid_token

    params = get_params(ADDON_NAME, persona_id=persona_id)

    user_id = params.get("x_user_id")
    username = params.get("x_username")
    if not user_id or not username:
        return None

    try:
        access_token = get_valid_token(ADDON_NAME, OAUTH_FLOW_KEY, persona_id)
    except Exception:
        LOGGER.exception(
            "[x_credentials] get_valid_token failed for persona=%s", persona_id
        )
        return None

    if not access_token:
        return None

    return XCredentials(
        access_token=access_token,
        x_user_id=str(user_id),
        x_username=str(username),
        skip_confirmation=bool(params.get("skip_confirmation", False)),
    )


def is_connected(persona_id: str) -> bool:
    """Return True if ``persona_id`` has an X account connected."""
    if not persona_id:
        return False
    from saiverse.addon_config import get_params

    params = get_params(ADDON_NAME, persona_id=persona_id)
    return bool(params.get("x_access_token") and params.get("x_user_id"))
