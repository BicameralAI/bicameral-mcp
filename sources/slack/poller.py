"""Slack polling helper for Phase 4b passive ingest (#337).

Wraps the Phase 4a client's ``conversations.history`` for per-channel
incremental polling. Each channel has its own ``oldest`` cursor so a
high-traffic channel doesn't re-pull because a low-traffic channel
just advanced.

Channels are passed in by ID (``C01ABC...``). The polling adapter
resolves config-supplied IDs and maintains the per-channel watermark
dict.
"""

from __future__ import annotations


def list_new_messages(
    *,
    token: str,
    channel: str,
    oldest: str | None = None,
):
    """Return new messages in ``channel`` since the ``oldest`` Slack ts.

    Slack's ``conversations.history`` returns newest-first; we re-sort
    ascending so the polling adapter can watermark on the last item.

    Raises ``RuntimeError`` on Slack API failure with the message the
    polling adapter logs.
    """
    from sources.slack.client import SlackAPIError, get_conversations_history

    try:
        messages = get_conversations_history(token=token, channel=channel, oldest=oldest, limit=200)
    except SlackAPIError as exc:
        raise RuntimeError(f"Slack history fetch failed for channel {channel!r}: {exc}") from exc

    # Slack returns newest-first; re-sort by ts ascending.
    messages.sort(key=lambda m: m.get("ts") or "")
    return messages
