"""PII archive — operator-erasable storage substrate for GDPR Art. 17 (#221).

Phase A of #221: this module is the foundation. It is **not wired into
the ingest path in this cycle**. See ``docs/policies/gdpr-art-17-erasure-roadmap.md``
for the multi-cycle plan.
"""

from .contracts import ArchiveEntry, ErasePredicate, PiiArchiveError
from .store import PiiArchive

__all__ = [
    "ArchiveEntry",
    "ErasePredicate",
    "PiiArchive",
    "PiiArchiveError",
]
