"""Dead-letter queue for passive-ingest refusals (#418 Phase 0a).

Public API re-exports for callers (`from dlq import write_dlq_entry`).
"""

from dlq.store import write_dlq_entry

__all__ = ["write_dlq_entry"]
