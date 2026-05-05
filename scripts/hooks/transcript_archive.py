"""CLI helper — archive one pending transcript via the queue module.

Used by ``bicameral-capture-corrections`` Step 0 (Phase 2 of #156) instead
of raw shell ``mv``. Routes archival through
``events.transcript_queue.archive_processed`` so:
  - idempotent re-replay semantics are preserved (overwrite if dst exists);
  - cross-platform behavior is uniform (Windows ``mv`` differs from POSIX);
  - a future team-server config that overrides retention/merge policy
    via the queue module Just Works without re-editing the SKILL.md.

Argv contract: a single basename (e.g. ``abc-1234.jsonl``). Resolves
``<cwd>/.bicameral/pending-transcripts/<basename>``. Basename-only is
deliberate: it's the constrained shape Step 0 actually needs and removes
the path-traversal surface that a full-path argv would expose. Exit
non-zero on missing file or unsafe basename so the caller can surface
the failure; this is NOT a fail-soft hook (unlike the SessionEnd writer)
because Step 0 wants to know if archival failed.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from events.transcript_queue import _pending_root, archive_processed  # noqa: E402

_BASENAME_RE = re.compile(r"^[A-Za-z0-9._-]+\.jsonl$")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not _BASENAME_RE.match(argv[1]):
        print("usage: transcript_archive.py <basename>.jsonl", file=sys.stderr)
        return 2
    pending = _pending_root(".") / argv[1]
    if not pending.is_file():
        print(f"not found: {pending}", file=sys.stderr)
        return 1
    archive_processed(".", pending)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
