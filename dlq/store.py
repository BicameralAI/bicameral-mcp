"""Dead-letter queue store for passive-ingest refusals (#418 Phase 0a).

Writes one JSONL row + one raw-content sidecar per item rejected by a
soft gate (`size_limit_exceeded`, `rate_limit_exceeded`, `injection_canary_match`)
in passive ingest mode. Hard-gate refusals (`sensitive_data:*`,
`malformed_payload`) never reach DLQ — they fail-fast in both modes so the
operator MUST intervene (credential rotation, regulated-data response,
caller bug fix).

Layout:
    <root>/dlq/<source_id>.jsonl      append-mode JSONL index
    <root>/dlq/raw/<dlq_id>.bin       raw payload, mode 0600

Root selection: ``$BICAMERAL_DATA_PATH`` if set, else ``~/.bicameral``.

JSONL row schema (one per refused item):
    {
      "dlq_id": "<uuid4-hex>",
      "source_id": "linear",
      "source_ref": "LIN-123#comment-456",
      "received_at": "2026-05-19T19:45:12.123456+00:00",
      "reason": "size_limit_exceeded",
      "byte_size": 2097153,
      "content_hash": "sha256:...",
      "raw_content_path": "<absolute-path-to-sidecar>"
    }

Retention: this module ships storage only. Rotation / cap enforcement is
out of scope for Phase 0a; the gap is documented in the parent plan.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

_JSONL_MODE = 0o600
_DIR_MODE = 0o700

# Whitelist for ``source_id`` — strict subset that's safe as a filename on
# every platform and impossible to use for traversal. Anything else is
# normalized to ``"unknown"`` so the JSONL still records what the gate saw
# without letting a hostile ``source_scope`` write outside the DLQ root
# (e.g. ``"../../etc/something"``).
_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_SOURCE_REF_MAX_LEN = 512


def _sanitize_source_id(raw: str) -> str:
    """Return ``raw`` if it matches the safe-filename whitelist; else ``"unknown"``."""
    if not raw or not _SOURCE_ID_RE.match(raw):
        return "unknown"
    # Belt-and-suspenders: even within the whitelist, a leading ``.`` could
    # produce a hidden file. Allow it (the whitelist permits ``.``) but cap
    # length so an operator typo doesn't blow filesystem name limits.
    return raw[:64]


def _resolve_dlq_root() -> Path:
    """Return the DLQ root directory (created if missing).

    Honors ``BICAMERAL_DATA_PATH`` for parity with the rest of the
    persistence layer. Default ``~/.bicameral/``. The ``dlq`` subdirectory
    and the ``dlq/raw`` sidecar directory are created with mode 0700 on
    first call so the raw payloads never have group/world read bits.
    """
    base = os.getenv("BICAMERAL_DATA_PATH")
    root = Path(base) if base else Path.home() / ".bicameral"
    dlq_root = root / "dlq"
    raw_root = dlq_root / "raw"
    # exist_ok=True is fine — mkdir doesn't relax existing permissions, and
    # any pre-existing dir is operator-controlled.
    dlq_root.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    raw_root.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    return dlq_root


def write_dlq_entry(
    *,
    source_id: str,
    source_ref: str,
    reason: str,
    byte_size: int,
    content_hash: str,
    raw_content: bytes,
) -> str:
    """Persist one DLQ row + raw sidecar. Returns the dlq_id.

    Caller is responsible for hashing the raw content (so the same hash
    function flows through audit-log emit + DLQ row + sidecar lookup).
    ``raw_content`` should be the exact bytes that failed the gate — for
    JSON payloads, callers pass ``json.dumps(payload, default=str).encode("utf-8")``.

    Failure modes:
    - Sidecar creation collision (uuid4 reuse) is treated as fatal and
      raises ``FileExistsError``. The retry policy is caller-side.
    - JSONL append failure raises ``OSError``. The caller's audit emit
      already fired BEFORE this function ran, so even a DLQ write failure
      doesn't lose observability of the refusal.
    """
    # #418 audit finding: source_id flows from MCP `source_scope` argument,
    # which a malicious / buggy caller could set to `"../../../etc/foo"`.
    # Normalize through the whitelist BEFORE it touches Path arithmetic.
    safe_source_id = _sanitize_source_id(source_id)
    # source_ref lands inside the JSON row (so traversal there is inert),
    # but cap length so a runaway payload can't bloat the JSONL index.
    if len(source_ref) > _SOURCE_REF_MAX_LEN:
        source_ref = source_ref[:_SOURCE_REF_MAX_LEN] + "...[truncated]"

    dlq_root = _resolve_dlq_root()
    raw_root = dlq_root / "raw"
    dlq_id = uuid.uuid4().hex

    sidecar = raw_root / f"{dlq_id}.bin"
    # O_EXCL: atomic create-or-fail (no TOCTOU race).
    # 0o600 from the umask-bypassing os.open path: sidecar is operator-
    # readable only from the first byte. On Windows os.open honors the
    # mode bits via ACL translation but the precise mapping is
    # platform-dependent — POSIX-strict semantics are POSIX-only.
    fd = os.open(str(sidecar), os.O_WRONLY | os.O_CREAT | os.O_EXCL, _JSONL_MODE)
    try:
        os.write(fd, raw_content)
    finally:
        os.close(fd)

    row = {
        "dlq_id": dlq_id,
        "source_id": safe_source_id,
        "source_id_raw": source_id if source_id != safe_source_id else None,
        "source_ref": source_ref,
        "received_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "byte_size": byte_size,
        "content_hash": content_hash,
        "raw_content_path": str(sidecar.resolve()),
    }
    # Drop the None when the source_id was clean so the row stays compact.
    if row["source_id_raw"] is None:
        del row["source_id_raw"]

    jsonl_path = dlq_root / f"{safe_source_id}.jsonl"
    is_new = not jsonl_path.exists()
    # Append-mode write; one JSON record per line.
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    if is_new and sys.platform != "win32":
        # First-create chmod: append-mode `open` doesn't bypass umask the
        # way `os.open(..., 0o600)` does, so a freshly-created JSONL may
        # carry group/world bits. Tighten to 0o600 on POSIX. Windows ACL
        # model doesn't map cleanly here — skip; the parent dir is 0700
        # which is the structural protection that matters.
        os.chmod(jsonl_path, _JSONL_MODE)

    return dlq_id
