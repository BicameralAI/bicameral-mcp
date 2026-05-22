"""Slack source adapter (#337 Phase 4a — active ingest).

Active path: operator pastes a Slack thread URL → adapter fetches the
thread + replies → normalizes to an IngestPayload. Passive (channel
polling) is Phase 4b.

URL forms accepted:
    https://<workspace>.slack.com/archives/<channel_id>/p<ts_no_dot>
    https://<workspace>.slack.com/archives/<channel_id>/p<ts_no_dot>?thread_ts=<root>

The ``p<ts>`` segment encodes the message timestamp by stripping the
decimal: ``p1700000000123456`` corresponds to Slack ``ts="1700000000.123456"``.
When ``thread_ts`` is present it identifies the thread root; otherwise
the message ``p<ts>`` IS the root.

DM URLs (``/messages/<im_id>``) are rejected by design — Bicameral's
ingest policy is channel-only per the parent tracker.
"""

from __future__ import annotations

import re

_THREAD_URL_RE = re.compile(
    r"^https?://(?:[a-z0-9-]+\.)?slack\.com/archives/"
    r"(?P<channel>[A-Z0-9]+)/p(?P<ts_compact>\d{16})"
    r"(?:\?(?P<query>[^#]*))?(?:#.*)?$",
    re.IGNORECASE,
)


class _ParsedSlackURL:
    """URL parts used by the adapter."""

    __slots__ = ("channel", "thread_ts", "url")

    def __init__(self, *, channel: str, thread_ts: str, url: str) -> None:
        self.channel = channel
        self.thread_ts = thread_ts
        self.url = url


def parse_slack_url(url: str) -> _ParsedSlackURL:
    """Extract channel_id + thread_ts from a Slack archive URL.

    Returns a struct rather than a tuple so the call site reads cleanly
    when both fields are referenced.

    Raises:
        ValueError: not a recognized Slack thread URL, or a DM URL
            (DMs are out of scope by policy).
    """
    if "/messages/" in url.lower():
        raise ValueError(f"DM URL not supported (Bicameral policy: channel-only): {url!r}")
    m = _THREAD_URL_RE.match(url.strip())
    if not m:
        raise ValueError(
            f"not a recognized Slack thread URL: {url!r}. "
            "Expected slack.com/archives/<channel_id>/p<ts>."
        )
    channel = m.group("channel").upper()
    ts_compact = m.group("ts_compact")
    # p1700000000123456 → 1700000000.123456 (last 6 digits are microseconds).
    message_ts = ts_compact[:-6] + "." + ts_compact[-6:]
    # If the URL carries thread_ts=<root>, use that as the canonical
    # thread anchor; otherwise the message itself is the root.
    thread_ts = message_ts
    query = m.group("query") or ""
    for part in query.split("&"):
        if part.startswith("thread_ts="):
            thread_ts = part.split("=", 1)[1]
            break
    return _ParsedSlackURL(channel=channel, thread_ts=thread_ts, url=url.strip())


def _is_decision_bearing(message: dict) -> bool:
    """Filter out messages that aren't candidates for decision capture.

    Out: bot messages (subtype set), channel-join/leave noise, empty text.
    In: human messages with non-empty ``text`` or attachments.
    """
    if message.get("subtype") in {
        "bot_message",
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_archive",
        "channel_unarchive",
    }:
        return False
    text = (message.get("text") or "").strip()
    if not text and not message.get("attachments"):
        return False
    return True


def normalize_thread_to_payload(
    messages: list[dict],
    *,
    channel: str,
    thread_url: str,
    user_resolver=None,
) -> dict:
    """Build the ingest payload from a Slack thread's messages.

    ``user_resolver`` (optional) is a callable ``user_id -> dict`` that
    returns a user-profile dict (``name``, ``profile.email``). When
    supplied it's used to enrich ``participants``; when omitted the
    Slack user IDs (``U…``) flow through unresolved.
    """
    decisions: list[dict] = []
    participants: list[str] = []
    seen_users: set[str] = set()

    root_ts = messages[0].get("ts") if messages else ""
    title = f"{channel}#{root_ts}"

    for msg in messages:
        if not _is_decision_bearing(msg):
            continue
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        msg_ts = msg.get("ts") or ""
        decisions.append(
            {
                "description": text,
                "title": f"{channel}#{msg_ts}" if msg_ts else title,
            }
        )
        user_id = msg.get("user") or ""
        if not user_id or user_id in seen_users:
            continue
        seen_users.add(user_id)
        if user_resolver is not None:
            try:
                profile = user_resolver(user_id) or {}
            except Exception:  # noqa: BLE001 — resolver is best-effort
                profile = {}
            email = ((profile.get("profile") or {}).get("email") or "").strip()
            name = (profile.get("real_name") or profile.get("name") or "").strip()
            participants.append(email or name or user_id)
        else:
            participants.append(user_id)

    return {
        "query": (decisions[0]["description"][:80] if decisions else title),
        "source": "slack",
        "title": title,
        "date": _slack_ts_to_iso(root_ts) if root_ts else "",
        "participants": participants,
        "decisions": decisions,
    }


def _slack_ts_to_iso(ts: str) -> str:
    """Convert Slack ``ts`` (epoch float as string) to ISO 8601.

    Returns empty string on malformed input.
    """
    try:
        from datetime import UTC, datetime

        epoch = float(ts)
        return datetime.fromtimestamp(epoch, tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return ""


class SlackAdapter:
    """SourceAdapter implementation for Slack (active path)."""

    source_id = "slack"

    def can_handle_url(self, url: str) -> bool:
        if "/messages/" in url.lower():
            return False
        return bool(_THREAD_URL_RE.match(url.strip()))

    def fetch_active(self, url: str) -> dict:
        parsed = parse_slack_url(url)
        token = self._resolve_token()
        from sources.slack.client import get_thread_replies

        messages = get_thread_replies(
            token=token, channel=parsed.channel, thread_ts=parsed.thread_ts
        )
        if not messages:
            raise RuntimeError(
                f"Slack returned no messages for {parsed.channel}#{parsed.thread_ts}. "
                "Check that the bot is in the channel and has channels:history scope."
            )

        def _resolver(user_id: str) -> dict:
            from sources.slack.client import get_user_info

            return get_user_info(token=token, user_id=user_id)

        return normalize_thread_to_payload(
            messages,
            channel=parsed.channel,
            thread_url=parsed.url,
            user_resolver=_resolver,
        )

    def _resolve_token(self) -> str:
        from secrets_store import get_secret

        token = get_secret(source_id=self.source_id, key="api_key")
        if not token:
            raise RuntimeError(
                "Slack bot token not configured. Create a Slack app at "
                "api.slack.com, grant channels:history + groups:history "
                "scopes, then store the bot token (xoxb-...) via:\n"
                '  python -c "from secrets_store import put_secret; '
                "put_secret(source_id='slack', key='api_key', value='xoxb-...')\""
            )
        return token
