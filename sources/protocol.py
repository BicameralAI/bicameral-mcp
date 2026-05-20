"""Source-adapter protocol (#420 Phase 1).

A ``SourceAdapter`` is the minimal contract every external system
implements to feed the bicameral ingest pipeline. Phase 1 ships the
**active** half (fetch on operator demand from a URL); Phase 1b extends
the same protocol with a passive (poller) half.

Design notes:
- Adapters return a *normalized ingest payload* — a plain dict that
  ``handlers.ingest.handle_ingest`` already accepts. No per-source code
  lives inside ``handlers/`` itself; the dispatch is "user/CLI hands a
  URL to an adapter, adapter hands the result to handle_ingest."
- ``source_id`` is the adapter's stable identifier (e.g. ``"linear"``).
  It flows through to the DLQ, audit log, and per-source secret store.
  Must match ``[A-Za-z0-9._-]+`` (enforced by ``dlq.store`` and
  ``secrets_store`` whitelists).
"""

from __future__ import annotations

from typing import Protocol


class SourceAdapter(Protocol):
    """Minimal adapter surface — Phase 1 (active-only)."""

    source_id: str
    """Stable identifier, e.g. ``"linear"``. Flows into the DLQ JSONL
    filename, audit-log source_id field, and keyring service name."""

    def can_handle_url(self, url: str) -> bool:
        """Return True when ``url`` looks like a resource this adapter
        knows how to fetch (e.g. for Linear: a ``linear.app`` issue URL).
        Cheap pattern match — does NOT confirm the URL resolves."""
        ...

    def fetch_active(self, url: str) -> dict:
        """Fetch the resource at ``url`` and return a normalized ingest
        payload (dict suitable for ``handlers.ingest.handle_ingest``).

        Raises:
            ValueError: malformed URL or unsupported resource shape.
            RuntimeError: network / auth failure. Caller decides
                whether to retry or surface to the operator.
        """
        ...
