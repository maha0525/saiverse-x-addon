"""X (Twitter) API v2 client.

Token acquisition and refresh are delegated to ``saiverse.oauth.handler``;
this module assumes ``XCredentials.access_token`` is already valid at
call time (the loader is responsible for that).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

import requests

from .credentials import XCredentials

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_X_API_BASE = "https://api.x.com/2"
_REQUEST_TIMEOUT = 20
_MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class XAPIError(Exception):
    """X API returned an error."""

    def __init__(self, status_code: int, body: str, endpoint: str = ""):
        self.status_code = status_code
        self.body = body
        self.endpoint = endpoint
        super().__init__(f"X API error {status_code} on {endpoint}: {body}")


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class XRateLimiter:
    """Sliding-window rate limiter (thread-safe)."""

    def __init__(self, max_calls: int, period: float):
        self.max_calls = max_calls
        self.period = period
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        deadline = time.monotonic() + (timeout if timeout is not None else self.period)
        while True:
            with self._lock:
                now = time.monotonic()
                while self._timestamps and self._timestamps[0] <= now - self.period:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.max_calls:
                    self._timestamps.append(now)
                    return True
                wait_until = self._timestamps[0] + self.period

            if time.monotonic() >= deadline:
                return False
            sleep_time = max(0, min(wait_until - time.monotonic(), 0.5))
            time.sleep(sleep_time)


# Per-endpoint rate limiters (requests per 15-min window).
# These are conservative local caps; the X API may itself return 429
# which is handled separately in _make_request with exponential backoff.
_RATE_LIMITERS: Dict[str, XRateLimiter] = {
    "post_tweet": XRateLimiter(200, 900),
    "read_timeline": XRateLimiter(15, 900),
    "read_mentions": XRateLimiter(10, 900),
    "search_tweets": XRateLimiter(60, 900),
    "write_action": XRateLimiter(50, 900),   # like/unlike/retweet/unretweet/follow/unfollow
    "delete_tweet": XRateLimiter(50, 900),
    "user_lookup": XRateLimiter(100, 900),
    "user_tweets": XRateLimiter(60, 900),
    "follower_list": XRateLimiter(15, 900),  # /2/users/{id}/followers (pagination)
    "engagement_lookup": XRateLimiter(60, 900),  # liking_users / retweeted_by
}


# ---------------------------------------------------------------------------
# Internal request helper
# ---------------------------------------------------------------------------

def _make_request(
    method: str,
    url: str,
    creds: XCredentials,
    limiter_key: str,
    **kwargs: Any,
) -> requests.Response:
    """Execute an authenticated X API request with rate limiting and retry."""

    limiter = _RATE_LIMITERS.get(limiter_key)
    if limiter and not limiter.acquire(timeout=60):
        raise XAPIError(429, f"Local rate limit reached for {limiter_key}", url)

    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {creds.access_token}"

    last_exc: Optional[Exception] = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            LOGGER.debug("[x_client] %s %s (attempt %d)", method, url, attempt + 1)
            resp = requests.request(
                method, url, headers=headers, timeout=_REQUEST_TIMEOUT, **kwargs
            )
            LOGGER.debug("[x_client] Response %d: %s", resp.status_code, resp.text[:500])

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", "60"))
                LOGGER.warning("[x_client] Rate limited by X API. Waiting %ds", retry_after)
                if attempt < _MAX_RETRIES:
                    time.sleep(min(retry_after, 120))
                    continue
                raise XAPIError(429, resp.text, url)

            if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                wait = 2 ** attempt
                LOGGER.warning("[x_client] Server error %d, retrying in %ds", resp.status_code, wait)
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                raise XAPIError(resp.status_code, resp.text, url)

            return resp

        except requests.RequestException as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                wait = 2 ** attempt
                LOGGER.warning("[x_client] Request error: %s, retrying in %ds", exc, wait)
                time.sleep(wait)
            else:
                raise XAPIError(0, str(exc), url) from exc

    raise XAPIError(0, str(last_exc), url)


# ---------------------------------------------------------------------------
# Public API functions
# ---------------------------------------------------------------------------

def post_tweet(text: str, creds: XCredentials) -> dict:
    """Post a tweet. Returns the API response dict."""
    resp = _make_request(
        "POST",
        f"{_X_API_BASE}/tweets",
        creds,
        limiter_key="post_tweet",
        json={"text": text},
    )
    return resp.json()


def reply_tweet(
    text: str,
    in_reply_to_tweet_id: str,
    creds: XCredentials,
) -> dict:
    """Post a reply to a specific tweet. Returns the API response dict."""
    resp = _make_request(
        "POST",
        f"{_X_API_BASE}/tweets",
        creds,
        limiter_key="post_tweet",
        json={
            "text": text,
            "reply": {"in_reply_to_tweet_id": in_reply_to_tweet_id},
        },
    )
    return resp.json()


def read_timeline(creds: XCredentials, max_results: int = 10) -> List[dict]:
    """Read home timeline. Returns list of tweet dicts."""
    params = {
        "max_results": min(max(max_results, 1), 100),
        "tweet.fields": "created_at,author_id,text,public_metrics,attachments",
        "expansions": "author_id,attachments.media_keys",
        "user.fields": "username,name",
        "media.fields": "url,preview_image_url,type",
    }
    resp = _make_request(
        "GET",
        f"{_X_API_BASE}/users/{creds.x_user_id}/timelines/reverse_chronological",
        creds,
        limiter_key="read_timeline",
        params=params,
    )
    data = resp.json()
    return _merge_users_into_tweets(data)


def read_mentions(
    creds: XCredentials,
    max_results: int = 10,
    since_id: Optional[str] = None,
    start_time: Optional[str] = None,
) -> List[dict]:
    """Read mentions. Returns list of tweet dicts.

    Args:
        since_id: If provided, only return mentions newer than this tweet ID.
        start_time: ISO 8601 (UTC, e.g. "2026-04-26T12:00:00Z"). Returns mentions
            on or after this time. Mutually exclusive with ``since_id`` (since_id
            wins if both passed). Used for the initial 24h backfill on first call.
    """
    params: Dict[str, Any] = {
        "max_results": min(max(max_results, 5), 100),
        "tweet.fields": "created_at,author_id,text,public_metrics,attachments",
        "expansions": "author_id,attachments.media_keys",
        "user.fields": "username,name",
        "media.fields": "url,preview_image_url,type",
    }
    if since_id:
        params["since_id"] = since_id
    elif start_time:
        params["start_time"] = start_time
    resp = _make_request(
        "GET",
        f"{_X_API_BASE}/users/{creds.x_user_id}/mentions",
        creds,
        limiter_key="read_mentions",
        params=params,
    )
    data = resp.json()
    return _merge_users_into_tweets(data)


def search_tweets(query: str, creds: XCredentials, max_results: int = 10) -> List[dict]:
    """Search recent tweets (7-day window). Returns list of tweet dicts."""
    params = {
        "query": query,
        "max_results": min(max(max_results, 10), 100),
        "tweet.fields": "created_at,author_id,text,public_metrics,attachments",
        "expansions": "author_id,attachments.media_keys",
        "user.fields": "username,name",
        "media.fields": "url,preview_image_url,type",
        "sort_order": "recency",
    }
    resp = _make_request(
        "GET",
        f"{_X_API_BASE}/tweets/search/recent",
        creds,
        limiter_key="search_tweets",
        params=params,
    )
    data = resp.json()
    return _merge_users_into_tweets(data)


# ---------------------------------------------------------------------------
# Engagement: like / unlike / retweet / unretweet
# ---------------------------------------------------------------------------

def like_tweet(tweet_id: str, creds: XCredentials) -> dict:
    """Like a tweet on behalf of the authenticated user."""
    resp = _make_request(
        "POST",
        f"{_X_API_BASE}/users/{creds.x_user_id}/likes",
        creds,
        limiter_key="write_action",
        json={"tweet_id": tweet_id},
    )
    return resp.json()


def unlike_tweet(tweet_id: str, creds: XCredentials) -> dict:
    """Remove a like from a tweet."""
    resp = _make_request(
        "DELETE",
        f"{_X_API_BASE}/users/{creds.x_user_id}/likes/{tweet_id}",
        creds,
        limiter_key="write_action",
    )
    return resp.json()


def retweet(tweet_id: str, creds: XCredentials) -> dict:
    """Retweet a tweet."""
    resp = _make_request(
        "POST",
        f"{_X_API_BASE}/users/{creds.x_user_id}/retweets",
        creds,
        limiter_key="write_action",
        json={"tweet_id": tweet_id},
    )
    return resp.json()


def unretweet(source_tweet_id: str, creds: XCredentials) -> dict:
    """Cancel a retweet of ``source_tweet_id``."""
    resp = _make_request(
        "DELETE",
        f"{_X_API_BASE}/users/{creds.x_user_id}/retweets/{source_tweet_id}",
        creds,
        limiter_key="write_action",
    )
    return resp.json()


# ---------------------------------------------------------------------------
# Tweet lifecycle: delete
# ---------------------------------------------------------------------------

def delete_tweet(tweet_id: str, creds: XCredentials) -> dict:
    """Delete one of the authenticated user's tweets."""
    resp = _make_request(
        "DELETE",
        f"{_X_API_BASE}/tweets/{tweet_id}",
        creds,
        limiter_key="delete_tweet",
    )
    return resp.json()


# ---------------------------------------------------------------------------
# Following: follow / unfollow
# ---------------------------------------------------------------------------

def follow_user(target_user_id: str, creds: XCredentials) -> dict:
    """Follow ``target_user_id`` (numeric X user ID, not username)."""
    resp = _make_request(
        "POST",
        f"{_X_API_BASE}/users/{creds.x_user_id}/following",
        creds,
        limiter_key="write_action",
        json={"target_user_id": target_user_id},
    )
    return resp.json()


def unfollow_user(target_user_id: str, creds: XCredentials) -> dict:
    """Unfollow ``target_user_id`` (numeric X user ID)."""
    resp = _make_request(
        "DELETE",
        f"{_X_API_BASE}/users/{creds.x_user_id}/following/{target_user_id}",
        creds,
        limiter_key="write_action",
    )
    return resp.json()


# ---------------------------------------------------------------------------
# User lookup
# ---------------------------------------------------------------------------

def get_user_by_username(username: str, creds: XCredentials) -> dict:
    """Look up a user by their @handle.

    Returns the ``data`` portion of X's response (id, name, username,
    description, public_metrics, etc.) or an empty dict on miss.
    """
    handle = username.lstrip("@")
    params = {
        "user.fields": "id,name,username,description,public_metrics,profile_image_url,verified,created_at",
    }
    resp = _make_request(
        "GET",
        f"{_X_API_BASE}/users/by/username/{handle}",
        creds,
        limiter_key="user_lookup",
        params=params,
    )
    return resp.json().get("data", {})


def get_user_tweets(
    target_user_id: str,
    creds: XCredentials,
    max_results: int = 10,
) -> List[dict]:
    """Fetch recent tweets posted by ``target_user_id`` (numeric X user ID)."""
    params: Dict[str, Any] = {
        "max_results": min(max(max_results, 5), 100),
        "tweet.fields": "created_at,author_id,text,public_metrics,attachments",
        "expansions": "author_id,attachments.media_keys",
        "user.fields": "username,name",
        "media.fields": "url,preview_image_url,type",
    }
    resp = _make_request(
        "GET",
        f"{_X_API_BASE}/users/{target_user_id}/tweets",
        creds,
        limiter_key="user_tweets",
        params=params,
    )
    return _merge_users_into_tweets(resp.json())


# ---------------------------------------------------------------------------
# Followers (for new-follower detection)
# ---------------------------------------------------------------------------

def get_followers(
    target_user_id: str,
    creds: XCredentials,
    max_results: int = 100,
    pagination_token: Optional[str] = None,
) -> dict:
    """Fetch one page of ``target_user_id``'s followers.

    Returns the raw JSON ``{"data": [...], "meta": {"next_token": ...}}``.
    Caller is responsible for pagination if all followers are needed.
    """
    params: Dict[str, Any] = {
        "max_results": min(max(max_results, 1), 1000),
        "user.fields": "id,name,username",
    }
    if pagination_token:
        params["pagination_token"] = pagination_token
    resp = _make_request(
        "GET",
        f"{_X_API_BASE}/users/{target_user_id}/followers",
        creds,
        limiter_key="follower_list",
        params=params,
    )
    return resp.json()


def get_all_followers(target_user_id: str, creds: XCredentials) -> List[dict]:
    """Fetch the most-recent 1 page of ``target_user_id``'s followers (max 1000).

    **Cost**: each follower returned costs $0.010 (Non-Owned Read). 25 followers
    = $0.25, 1000 followers = $10. Pagination intentionally NOT performed even
    if a next_token exists, since a persona with > 1000 followers would generate
    a single call costing tens of dollars and is rare for AI persona accounts.

    The X API returns followers in reverse chronological order, so the most
    recent followers are always included in the first page. Diff detection
    against ``known_follower_ids`` may miss followers who fall off page 1
    between two polls (only matters for accounts ≥ 1000 followers).
    """
    page = get_followers(target_user_id, creds, max_results=1000, pagination_token=None)
    return page.get("data") or []


# ---------------------------------------------------------------------------
# Engagement lookup (who liked / who retweeted)
# ---------------------------------------------------------------------------

def get_liking_users(tweet_id: str, creds: XCredentials) -> List[dict]:
    """Fetch the list of users who liked ``tweet_id``."""
    params = {"user.fields": "id,name,username"}
    resp = _make_request(
        "GET",
        f"{_X_API_BASE}/tweets/{tweet_id}/liking_users",
        creds,
        limiter_key="engagement_lookup",
        params=params,
    )
    return resp.json().get("data") or []


def get_retweeted_by(tweet_id: str, creds: XCredentials) -> List[dict]:
    """Fetch the list of users who retweeted ``tweet_id``."""
    params = {"user.fields": "id,name,username"}
    resp = _make_request(
        "GET",
        f"{_X_API_BASE}/tweets/{tweet_id}/retweeted_by",
        creds,
        limiter_key="engagement_lookup",
        params=params,
    )
    return resp.json().get("data") or []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _merge_users_into_tweets(api_response: dict) -> List[dict]:
    """Merge user and media info from 'includes' into each tweet's data."""
    tweets = api_response.get("data") or []
    includes = api_response.get("includes") or {}
    users_list = includes.get("users") or []
    media_list = includes.get("media") or []
    users_by_id = {u["id"]: u for u in users_list}
    media_by_key = {m["media_key"]: m for m in media_list}

    result = []
    for tweet in tweets:
        author_id = tweet.get("author_id", "")
        user = users_by_id.get(author_id, {})

        # Resolve attached media
        media_keys = (tweet.get("attachments") or {}).get("media_keys") or []
        media_urls = []
        for mk in media_keys:
            m = media_by_key.get(mk, {})
            url = m.get("url") or m.get("preview_image_url") or ""
            if url:
                media_urls.append({"type": m.get("type", ""), "url": url})

        result.append({
            "id": tweet.get("id", ""),
            "text": tweet.get("text", ""),
            "created_at": tweet.get("created_at", ""),
            "author_id": author_id,
            "author_username": user.get("username", ""),
            "author_name": user.get("name", ""),
            "metrics": tweet.get("public_metrics", {}),
            "media": media_urls,
        })
    return result
