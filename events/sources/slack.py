"""Slack polling source adapter (#337 Phase 4b).

Pull-based: enumerates new messages in configured channels via
``conversations.history`` since the last watermark, fetches each
top-level message's thread (if any), and returns ingest-ready payloads.

A "decision" in Slack is most often the originating message of a
thread — replies are commentary. Phase 4b ingests **top-level messages
only** by default; full thread context per message is reserved for a
future cycle (the active path at Phase 4a already fetches threads on
demand for operator-pasted URLs).

Config schema::

    sources:
      - type: slack
        channels: ["C01ABC123", "C02DEF456"]
        source_type_label: slack-message  # optional

Auth: bot token in ``secrets_store source_id="slack", key="api_key"``.
Same store as the active-ingest path (Phase 4a #438) — one token works
for both.

Watermark: per-channel. ``<watermark_dir>/slack.json`` stores
``{"C01ABC123": "1700000000.123456", ...}`` so a busy channel doesn't
strand a quiet one.

Policy: channel-only — config entries that look like DM IDs (``D…``)
or multi-party IM IDs (``G…`` — Slack uses ``G`` for private groups,
which DO include private channels, so the check is permissive: only
``D`` is explicitly rejected) are skipped with a stderr warning.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_WATERMARK_FILENAME = "slack.json"


class SlackPollingAdapter:
    """Source adapter conforming to ``events.sources.SourceAdapter``."""

    name = "slack"

    def __init__(self) -> None:
        self._pending_watermarks: dict[str, str] = {}
        self._watermark_path: Path | None = None

    def pull(self, *, watermark_dir: Path, config: dict) -> list[dict]:
        watermark_dir = Path(watermark_dir)
        watermark_dir.mkdir(parents=True, exist_ok=True)
        self._watermark_path = watermark_dir / _WATERMARK_FILENAME

        # #337 foundations cycle 2: parse source-level filters + per-resource
        # entries. ``channels`` accepts either bare strings (legacy: inherit
        # source-level filter, no overrides) or dicts of shape
        # ``{id: "C…", filters: {...}}`` (per-resource filter overrides).
        from filters import FilterSpec, evaluate_filters, merge_specs

        source_level_filter = _parse_filter_block(config.get("filters") or {})
        channels_raw = config.get("channels") or []
        # Each entry becomes (channel_id, effective_filter_spec).
        channel_specs: list[tuple[str, FilterSpec]] = []
        for entry in channels_raw:
            if isinstance(entry, str):
                cid = entry.strip()
                spec = source_level_filter
            elif isinstance(entry, dict):
                cid = str(entry.get("id") or "").strip()
                res_spec = _parse_filter_block(entry.get("filters") or {})
                spec = merge_specs(source_level_filter, res_spec)
            else:
                continue
            if not cid or cid.upper().startswith("D"):
                continue
            channel_specs.append((cid, spec))

        channels = [c for c, _ in channel_specs]
        if not channels:
            print(
                "[slack] at least one channel ID is required in source.channels; "
                "skipping. (DM IDs starting with 'D' are filtered by policy.)",
                file=sys.stderr,
            )
            self._pending_watermarks = {}
            return []

        try:
            from secrets_store import get_secret

            token = get_secret(source_id="slack", key="api_key")
            if not token:
                print(
                    "[slack] api_key not configured (secrets_store source_id=slack, "
                    "key=api_key); skipping.",
                    file=sys.stderr,
                )
                self._pending_watermarks = {}
                return []
        except Exception as exc:  # noqa: BLE001
            print(f"[slack] secret lookup failed: {exc}", file=sys.stderr)
            self._pending_watermarks = {}
            return []

        all_watermarks = _read_watermarks(self._watermark_path)
        new_watermarks: dict[str, str] = dict(all_watermarks)

        try:
            from sources.slack.adapter import (
                _is_decision_bearing,
                normalize_thread_to_payload,
            )
            from sources.slack.client import get_user_info
            from sources.slack.poller import list_new_messages
        except ImportError as exc:
            print(f"[slack] adapter import failed: {exc}", file=sys.stderr)
            self._pending_watermarks = {}
            return []

        payloads: list[dict] = []
        # Local cache so users.info is hit once per user per pull.
        user_cache: dict[str, dict] = {}

        def _resolver(user_id: str) -> dict:
            if user_id in user_cache:
                return user_cache[user_id]
            try:
                profile = get_user_info(token=token, user_id=user_id)
            except Exception:  # noqa: BLE001 — resolver is best-effort
                profile = {}
            user_cache[user_id] = profile
            return profile

        for channel_id, channel_spec in channel_specs:
            last_ts = all_watermarks.get(channel_id)
            try:
                messages = list_new_messages(token=token, channel=channel_id, oldest=last_ts)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[slack] {channel_id} history fetch failed: {exc}",
                    file=sys.stderr,
                )
                continue

            highest_ts = last_ts or ""
            for msg in messages:
                msg_ts = msg.get("ts") or ""
                # Only top-level messages — replies don't have parent_user_id
                # and don't carry thread_ts == ts? Actually Slack sets
                # ``thread_ts == ts`` for thread roots, and ``thread_ts``
                # differing from ``ts`` for replies. Skip replies.
                if msg.get("thread_ts") and msg.get("thread_ts") != msg_ts:
                    if msg_ts > highest_ts:
                        highest_ts = msg_ts
                    continue
                if not _is_decision_bearing(msg):
                    if msg_ts > highest_ts:
                        highest_ts = msg_ts
                    continue
                # #337 cycle 2: universal-filter gate. Slack candidate
                # shape: text from message body, author from user_id
                # (email resolution happens in normalize, but for filter
                # eval we use the raw user ID — operators can configure
                # author_include with user IDs OR delegate to extensions
                # in a future cycle).
                candidate = {
                    "text": msg.get("text") or "",
                    "author": msg.get("user") or "",
                    "timestamp": _slack_ts_to_iso_safe(msg_ts),
                }
                if not evaluate_filters(candidate, channel_spec):
                    if msg_ts > highest_ts:
                        highest_ts = msg_ts
                    continue
                payload = normalize_thread_to_payload(
                    [msg],
                    channel=channel_id,
                    thread_url=f"slack://{channel_id}/{msg_ts}",
                    user_resolver=_resolver,
                )
                label = config.get("source_type_label")
                if label:
                    payload = {**payload, "source": str(label)}
                payloads.append(payload)
                if msg_ts > highest_ts:
                    highest_ts = msg_ts

            if highest_ts:
                new_watermarks[channel_id] = highest_ts

        self._pending_watermarks = new_watermarks
        return payloads

    def confirm_watermark(self) -> None:
        if self._watermark_path is None or not self._pending_watermarks:
            return
        try:
            self._watermark_path.write_text(
                json.dumps(self._pending_watermarks),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "[slack] watermark persistence failed (will re-pull next run): %s",
                exc,
            )
        self._pending_watermarks = {}


def _parse_filter_block(raw: dict | None):
    """Coerce a YAML filter block into a FilterSpec, never raises.

    Malformed filter config logs to stderr and falls through to the
    no-filter default — operator gets stderr feedback but the poller
    doesn't halt on a typo.
    """
    from filters import FilterSpec

    if not raw or not isinstance(raw, dict):
        return FilterSpec()
    try:
        return FilterSpec(**raw)
    except Exception as exc:  # noqa: BLE001 — surface, don't halt
        print(f"[slack] malformed filter block ignored: {exc}", file=sys.stderr)
        return FilterSpec()


def _slack_ts_to_iso_safe(ts: str) -> str:
    """Slack ts → ISO 8601 for filter time-window comparison. Empty on parse failure."""
    try:
        from datetime import UTC, datetime

        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return ""


def _read_watermarks(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[slack] watermark file corrupt at %s, starting from epoch: %s",
            path,
            exc,
        )
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
