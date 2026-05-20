"""Thin Slack Web API client (#337 Phase 4a — active ingest).

stdlib ``urllib.request`` — same hardening as Linear/Notion/GitHub
clients (15s timeout, 4 MiB response cap, Bearer in Authorization
header). Endpoint: ``https://slack.com/api/<method>``.

Slack's API returns 200 even on logical failures, with ``ok: false``
in the JSON body. The client raises ``SlackAPIError`` on either HTTP
non-2xx or ``ok=false`` responses so callers don't have to repeat the
branch.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

_API_BASE = "https://slack.com/api"
_REQUEST_TIMEOUT_SECONDS = 15.0
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_MAX_PAGINATION_PAGES = 10  # 10 * 200 messages = 2000 per fetch


class SlackAPIError(RuntimeError):
    """Raised on Slack API failure (HTTP non-2xx OR ``ok=false`` in body)."""

    def __init__(
        self, message: str, *, status_code: int | None = None, slack_error: str | None = None
    ) -> None:
        self.status_code = status_code
        self.slack_error = slack_error
        super().__init__(message)


def _get(*, token: str, method: str, params: dict | None = None) -> dict:
    """GET ``slack.com/api/<method>`` with optional query params.

    Returns the parsed JSON body on ``ok=true``. Raises ``SlackAPIError``
    otherwise, surfacing Slack's ``error`` string when present.
    """
    url = f"{_API_BASE}/{method}"
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "bicameral-mcp/source-slack",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT_SECONDS) as resp:
            raw = resp.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise SlackAPIError(
                    f"Slack response exceeded {_MAX_RESPONSE_BYTES} bytes",
                    status_code=resp.status,
                )
    except urllib.error.HTTPError as exc:
        raise SlackAPIError(
            f"Slack API HTTP {exc.code}: {exc.reason}",
            status_code=exc.code,
        ) from exc
    except urllib.error.URLError as exc:
        raise SlackAPIError(f"Slack API network error: {exc.reason}") from exc

    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise SlackAPIError(f"Slack API returned non-JSON: {exc}") from exc

    if not data.get("ok"):
        slack_error = data.get("error") or "unknown_error"
        raise SlackAPIError(
            f"Slack API error: {slack_error}",
            slack_error=slack_error,
        )
    return data


def get_thread_replies(*, token: str, channel: str, thread_ts: str) -> list[dict]:
    """Return all messages in a thread (root + replies), oldest first.

    Paginates via Slack's ``next_cursor`` until exhausted or until the
    page cap fires.
    """
    out: list[dict] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str] = {
            "channel": channel,
            "ts": thread_ts,
            "limit": "200",
        }
        if cursor is not None:
            params["cursor"] = cursor
        data = _get(token=token, method="conversations.replies", params=params)
        out.extend(data.get("messages") or [])
        pages += 1
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
        if pages >= _MAX_PAGINATION_PAGES:
            raise SlackAPIError(
                f"thread has more than {_MAX_PAGINATION_PAGES * 200} replies — "
                "narrow the ingest target"
            )
    return out


def get_user_info(*, token: str, user_id: str) -> dict:
    """Fetch user profile metadata. Used to resolve names + emails for
    participant attribution."""
    data = _get(token=token, method="users.info", params={"user": user_id})
    return data.get("user") or {}


def list_channels(*, token: str, include_private: bool = True) -> list[dict]:
    """Enumerate channels the bot has access to.

    Returns dicts shaped ``{"id", "name", "is_private", "is_member",
    "num_members"}``. Used by the ``bicameral-mcp source-list slack``
    discovery primitive — operator picks channel IDs to add to
    ``sources.channels`` config.

    ``include_private`` toggles ``public_channel,private_channel``
    types in the request. The bot must have ``groups:read`` scope
    additionally for private channels; without it, Slack silently
    returns only public ones.
    """
    types = "public_channel,private_channel" if include_private else "public_channel"
    out: list[dict] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str] = {"types": types, "limit": "200", "exclude_archived": "true"}
        if cursor is not None:
            params["cursor"] = cursor
        data = _get(token=token, method="conversations.list", params=params)
        for ch in data.get("channels") or []:
            out.append(
                {
                    "id": ch.get("id") or "",
                    "name": ch.get("name") or "",
                    "is_private": bool(ch.get("is_private")),
                    "is_member": bool(ch.get("is_member")),
                    "num_members": int(ch.get("num_members") or 0),
                }
            )
        pages += 1
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
        if pages >= _MAX_PAGINATION_PAGES:
            raise SlackAPIError(
                f"workspace has more than {_MAX_PAGINATION_PAGES * 200} channels — "
                "narrow include_private or paginate manually"
            )
    return out


def get_conversations_history(
    *,
    token: str,
    channel: str,
    oldest: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return recent messages in a channel since ``oldest`` (a Slack ``ts``).

    Slack's ``conversations.history`` returns messages newest-first by
    default. Caller is responsible for sorting / watermarking on ``ts``.
    Paginates via ``next_cursor``.
    """
    out: list[dict] = []
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, str] = {
            "channel": channel,
            "limit": str(limit),
        }
        if oldest:
            params["oldest"] = oldest
        if cursor is not None:
            params["cursor"] = cursor
        data = _get(token=token, method="conversations.history", params=params)
        out.extend(data.get("messages") or [])
        pages += 1
        cursor = (data.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
        if pages >= _MAX_PAGINATION_PAGES:
            raise SlackAPIError(
                f"channel has more than {_MAX_PAGINATION_PAGES * limit} messages "
                "since the watermark — narrow the channel or shorten the watch window"
            )
    return out
