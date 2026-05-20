"""Notion polling source adapter (#337 Phase 2b).

Pull-based: enumerates pages in a Notion database that were last edited
after the watermark, fetches each through Phase 2's active-ingest adapter
for property + block-walk normalization, returns ingest-ready payloads.

Config schema::

    sources:
      - type: notion
        database_id: <Notion database ID>
        source_type_label: decision-doc  # optional

Auth: ``secrets_store source_id="notion", key="api_key"``. Operator
stores the Notion integration token there; the integration must be
shared with each database the adapter polls (Notion's share-per-database
permission model — see plan-2-notion-active for caveats).

Watermark: ``<watermark_dir>/notion.json`` with
``{"last_edited_time": "<ISO8601>"}``. Corrupt / missing → epoch.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_WATERMARK_FILENAME = "notion.json"


class NotionPollingAdapter:
    """Source adapter conforming to ``events.sources.SourceAdapter``."""

    name = "notion"

    def __init__(self) -> None:
        self._pending_watermark: str | None = None
        self._watermark_path: Path | None = None

    def pull(self, *, watermark_dir: Path, config: dict) -> list[dict]:
        watermark_dir = Path(watermark_dir)
        watermark_dir.mkdir(parents=True, exist_ok=True)
        self._watermark_path = watermark_dir / _WATERMARK_FILENAME

        database_id = (config.get("database_id") or "").strip()
        if not database_id:
            print(
                "[notion] database_id is required in source config; skipping.",
                file=sys.stderr,
            )
            self._pending_watermark = None
            return []

        last_watermark = _read_watermark(self._watermark_path)

        try:
            from secrets_store import get_secret
            from sources.notion.poller import list_recently_edited_pages

            api_key = get_secret(source_id="notion", key="api_key")
            if not api_key:
                print(
                    "[notion] api_key not configured (secrets_store source_id=notion, "
                    "key=api_key); skipping.",
                    file=sys.stderr,
                )
                self._pending_watermark = None
                return []
            pages = list_recently_edited_pages(
                api_key=api_key,
                database_id=database_id,
                edited_after=last_watermark,
            )
        except Exception as exc:  # noqa: BLE001 — never raise to _run_source
            print(f"[notion] database query failed: {exc}", file=sys.stderr)
            self._pending_watermark = None
            return []

        if not pages:
            self._pending_watermark = None
            return []

        try:
            from sources.notion.adapter import NotionAdapter
        except ImportError as exc:
            print(f"[notion] adapter import failed: {exc}", file=sys.stderr)
            self._pending_watermark = None
            return []

        active = NotionAdapter()
        payloads: list[dict] = []
        highest_edited = last_watermark or ""
        for page in pages:
            page_id = page.get("id") or ""
            edited = page.get("last_edited_time") or ""
            url = page.get("url") or ""
            if not page_id or not url:
                continue
            try:
                payload = active.fetch_active(url)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[notion] page {page_id!r} fetch failed (skipped): {exc}",
                    file=sys.stderr,
                )
                continue
            label = config.get("source_type_label")
            if label:
                payload = {**payload, "source": str(label)}
            payloads.append(payload)
            if edited > highest_edited:
                highest_edited = edited

        self._pending_watermark = highest_edited or None
        return payloads

    def confirm_watermark(self) -> None:
        if self._watermark_path is None or self._pending_watermark is None:
            return
        try:
            self._watermark_path.write_text(
                json.dumps({"last_edited_time": self._pending_watermark}),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "[notion] watermark persistence failed (will re-pull next run): %s",
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
            "[notion] watermark file corrupt at %s, starting from epoch: %s",
            path,
            exc,
        )
        return None
    value = data.get("last_edited_time") if isinstance(data, dict) else None
    if not value or not isinstance(value, str):
        return None
    return value
