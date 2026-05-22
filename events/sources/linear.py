"""Linear polling source adapter (#337 Phase 1b).

Pull-based: enumerates Linear issues completed since the last watermark,
fetches each through Phase 1a's active-ingest adapter to extract decisions
+ comments, returns ingest-ready payloads.

Config schema (one entry under ``sources:`` in ``.bicameral/config.yaml``)::

    sources:
      - type: linear
        # Optional: restrict to specific team prefixes (e.g. ["BIC", "ENG"]).
        team_keys: [BIC]
        # Optional: operator-facing label override.
        source_type_label: linear-ticket

Auth: Linear API key persisted via ``secrets_store`` under
``source_id="linear"``, key ``"api_key"``. Operator stores it once via:
    python -c "from secrets_store import put_secret; \\
               put_secret(source_id='linear', key='api_key', value='lin_...')"

Watermark: ``<watermark_dir>/linear.json`` with
``{"last_completed_at": "<RFC3339>"}``. Corrupt / missing → epoch.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_WATERMARK_FILENAME = "linear.json"


class LinearPollingAdapter:
    """Source adapter conforming to ``events.sources.SourceAdapter``."""

    name = "linear"

    def __init__(self) -> None:
        self._pending_watermark: str | None = None
        self._watermark_path: Path | None = None

    def pull(self, *, watermark_dir: Path, config: dict) -> list[dict]:
        watermark_dir = Path(watermark_dir)
        watermark_dir.mkdir(parents=True, exist_ok=True)
        self._watermark_path = watermark_dir / _WATERMARK_FILENAME

        team_keys_raw = config.get("team_keys") or []
        team_keys: list[str] | None = [str(k) for k in team_keys_raw] if team_keys_raw else None

        last_watermark = _read_watermark(self._watermark_path)

        try:
            from secrets_store import get_secret
            from sources.linear.poller import list_completed_issues

            api_key = get_secret(source_id="linear", key="api_key")
            if not api_key:
                print(
                    "[linear] api_key not configured (secrets_store source_id=linear, key=api_key); "
                    "skipping.",
                    file=sys.stderr,
                )
                self._pending_watermark = None
                return []
            issues = list_completed_issues(
                api_key=api_key,
                completed_after=last_watermark,
                team_keys=team_keys,
            )
        except Exception as exc:  # noqa: BLE001 — never raise to _run_source
            print(f"[linear] issue listing failed: {exc}", file=sys.stderr)
            self._pending_watermark = None
            return []

        if not issues:
            self._pending_watermark = None
            return []

        try:
            from sources.linear.adapter import LinearAdapter
        except ImportError as exc:
            print(f"[linear] adapter import failed: {exc}", file=sys.stderr)
            self._pending_watermark = None
            return []

        # #337 cycle 2: universal filter for Linear. Source-level only —
        # Linear team-keys already serve as per-resource selection.
        from filters import (
            FilterSpec,
            evaluate_filters,
            get_max_bytes,
            get_max_items,
            payload_within_cap,
        )

        try:
            source_spec = FilterSpec(**(config.get("filters") or {}))
        except Exception as exc:  # noqa: BLE001
            print(f"[linear] malformed filter block ignored: {exc}", file=sys.stderr)
            source_spec = FilterSpec()

        # #337 cycle 4: per-source quota. max_items_per_pull caps payloads
        # this pull (0 = no cap); max_payload_bytes overrides the global
        # ingest_max_bytes for size-rejection.
        max_items = get_max_items(config)
        max_bytes = get_max_bytes(config)

        active = LinearAdapter()
        payloads: list[dict] = []
        highest_completed = last_watermark or ""
        for issue in issues:
            # cycle 4: stop processing if cap reached. Watermark stays
            # at the last actually-processed item; unprocessed items
            # remain for next pull.
            if max_items and len(payloads) >= max_items:
                break
            url = issue.get("url") or ""
            completed_at = issue.get("completedAt") or ""
            if not url:
                continue
            try:
                payload = active.fetch_active(url)
            except Exception as exc:  # noqa: BLE001 — skip individual
                identifier = issue.get("identifier", "?")
                print(
                    f"[linear] issue {identifier!r} fetch failed (skipped): {exc}",
                    file=sys.stderr,
                )
                continue
            # Filter on title + description aggregate.
            text_bits = [
                str(payload.get("query") or ""),
                *(d.get("description", "") for d in (payload.get("decisions") or [])),
            ]
            participants = payload.get("participants") or []
            author = participants[0] if participants else ""
            candidate = {
                "text": " ".join(text_bits),
                "author": author,
                "timestamp": completed_at,
            }
            if not evaluate_filters(candidate, source_spec):
                if completed_at > highest_completed:
                    highest_completed = completed_at
                continue
            label = config.get("source_type_label")
            if label:
                payload = {**payload, "source": str(label)}
            # cycle 4: per-source byte cap, applied before append. Oversize
            # payload is rejected as if it failed the universal filter —
            # watermark still advances so we don't re-fetch it.
            if not payload_within_cap(payload, max_bytes):
                print(
                    f"[linear] issue {issue.get('identifier', '?')!r} exceeds "
                    f"max_payload_bytes ({max_bytes}); skipped.",
                    file=sys.stderr,
                )
                if completed_at > highest_completed:
                    highest_completed = completed_at
                continue
            payloads.append(payload)
            if completed_at > highest_completed:
                highest_completed = completed_at

        self._pending_watermark = highest_completed or None
        return payloads

    def confirm_watermark(self) -> None:
        if self._watermark_path is None or self._pending_watermark is None:
            return
        try:
            self._watermark_path.write_text(
                json.dumps({"last_completed_at": self._pending_watermark}),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "[linear] watermark persistence failed (will re-pull next run): %s",
                exc,
            )
        self._pending_watermark = None


def _read_watermark(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[linear] watermark file corrupt at %s, starting from epoch: %s",
            path,
            exc,
        )
        return None
    value = data.get("last_completed_at") if isinstance(data, dict) else None
    if not value or not isinstance(value, str):
        return None
    return value
