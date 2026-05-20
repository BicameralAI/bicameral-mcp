"""Google Drive folder-polling source adapter (#337 Phase 5c).

Pull-based: enumerates new/edited Google Docs in a configured folder,
fetches each through the Phase 5a active-ingest adapter to extract text,
and returns a list of ingest-ready payloads. Watermark is the latest
``modifiedTime`` ingested — two-phase commit via ``confirm_watermark``
so a failed ingest does not lose the un-ingested items.

Config schema (one entry under ``sources:`` in ``.bicameral/config.yaml``)::

    sources:
      - type: google_drive
        folder_id: <Drive folder ID>
        # Optional: operator-facing label flowing into source_type metadata.
        source_type_label: design-doc

Auth: OAuth token managed by ``sources.google_drive.auth`` (Phase 5b);
operator obtains via ``bicameral-mcp source-auth google_drive``. After
Phase 5c lands, that handshake covers both ``documents.readonly`` (Docs
body) and ``drive.metadata.readonly`` (folder enumeration).

Watermark file: ``<watermark_dir>/google_drive.json`` with shape
``{"last_modified": "<RFC 3339>"}``. Corrupt / missing → start from epoch
(mirrors the Granola + LocalDirectory precedents).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_WATERMARK_FILENAME = "google_drive.json"


class GoogleDriveFolderAdapter:
    """Source adapter conforming to ``events.sources.SourceAdapter``.

    Registry key (config ``type``) is ``"google_drive"``. ``source_scope``
    flowing into ``handle_ingest`` therefore matches the active-ingest
    adapter's ``source_id`` from Phase 5a, so DLQ filenames, audit-log
    ``source_id`` fields, and the OS-keyring service name are unified
    across active + passive Drive paths.
    """

    name = "google_drive"

    def __init__(self) -> None:
        self._pending_watermark: str | None = None
        self._watermark_path: Path | None = None

    def pull(self, *, watermark_dir: Path, config: dict) -> list[dict]:
        """Pull new docs since the last confirmed watermark.

        Returns a list of ingest-ready payloads (one per doc). Empty list
        when the folder hasn't changed, the folder_id is missing, or any
        Drive API failure occurs — never raises, matching the
        ``_run_source`` "never raise to caller" discipline.
        """
        watermark_dir = Path(watermark_dir)
        watermark_dir.mkdir(parents=True, exist_ok=True)
        self._watermark_path = watermark_dir / _WATERMARK_FILENAME

        folder_id = (config.get("folder_id") or "").strip()
        if not folder_id:
            print(
                "[google_drive] folder_id is required in source config; skipping.",
                file=sys.stderr,
            )
            self._pending_watermark = None
            return []

        last_watermark = _read_watermark(self._watermark_path)

        try:
            from sources.google_drive.auth import load_credentials
            from sources.google_drive.folder import list_docs_in_folder

            creds = load_credentials()
            docs = list_docs_in_folder(creds, folder_id, modified_after=last_watermark)
        except Exception as exc:  # noqa: BLE001 — never raise to _run_source
            print(
                f"[google_drive] folder enumeration failed: {exc}",
                file=sys.stderr,
            )
            self._pending_watermark = None
            return []

        if not docs:
            self._pending_watermark = None
            return []

        # Build payloads by reusing the Phase 5a active-ingest fetch path.
        # Each doc fetch issues its own Docs API call — cost is one
        # request per new/edited doc per pull, bounded by _MAX_PAGES in
        # folder.py to 2000 per cycle.
        payloads: list[dict] = []
        highest_mtime = last_watermark or ""
        try:
            from sources.google_drive.adapter import GoogleDriveAdapter
        except ImportError as exc:
            print(
                f"[google_drive] adapter import failed: {exc}",
                file=sys.stderr,
            )
            self._pending_watermark = None
            return []

        # #337 cycle 2: universal filter (source-level — folder_id is the
        # resource for Drive polling).
        from filters import FilterSpec, evaluate_filters

        try:
            source_spec = FilterSpec(**(config.get("filters") or {}))
        except Exception as exc:  # noqa: BLE001
            print(
                f"[google_drive] malformed filter block ignored: {exc}",
                file=sys.stderr,
            )
            source_spec = FilterSpec()

        active = GoogleDriveAdapter()
        for doc in docs:
            doc_id = doc.get("id")
            mtime = doc.get("modifiedTime") or ""
            if not doc_id:
                continue
            url = f"https://docs.google.com/document/d/{doc_id}/edit"
            try:
                payload = active.fetch_active(url)
            except Exception as exc:  # noqa: BLE001 — skip individual doc, keep going
                print(
                    f"[google_drive] doc {doc_id!r} fetch failed (skipped): {exc}",
                    file=sys.stderr,
                )
                continue
            text_bits = [
                str(payload.get("query") or ""),
                *(d.get("description", "") for d in (payload.get("decisions") or [])),
            ]
            participants = payload.get("participants") or []
            candidate = {
                "text": " ".join(text_bits),
                "author": participants[0] if participants else "",
                "timestamp": mtime,
            }
            if not evaluate_filters(candidate, source_spec):
                if mtime > highest_mtime:
                    highest_mtime = mtime
                continue
            # Optional operator-facing label override.
            label = config.get("source_type_label")
            if label:
                payload = {**payload, "source": str(label)}
            payloads.append(payload)
            if mtime > highest_mtime:
                highest_mtime = mtime

        # Stage the watermark advance. Only persisted when the caller
        # confirms the ingest batch via ``confirm_watermark``.
        self._pending_watermark = highest_mtime or None
        return payloads

    def confirm_watermark(self) -> None:
        """Persist the staged watermark. Called after a successful ingest batch."""
        if self._watermark_path is None or self._pending_watermark is None:
            return
        try:
            self._watermark_path.write_text(
                json.dumps({"last_modified": self._pending_watermark}),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "[google_drive] watermark persistence failed (will re-pull next run): %s",
                exc,
            )
        self._pending_watermark = None


def _read_watermark(path: Path) -> str | None:
    """Read the last-modified watermark; ``None`` on missing / corrupt."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[google_drive] watermark file corrupt at %s, starting from epoch: %s",
            path,
            exc,
        )
        return None
    value = data.get("last_modified") if isinstance(data, dict) else None
    if not value or not isinstance(value, str):
        return None
    return value
