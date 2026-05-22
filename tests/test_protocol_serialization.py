"""Phase 1 contracts: Pydantic serialization round-trip + size-cap enforcement.

Per the audit-passed plan in
``docs/Planning/plan-daemon-extract-universal-surface.md``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from protocol.contracts import (
    BATCH_REGION_LIMIT,
    BatchAnalyzeRequest,
    CodeRegion,
    IngestRequest,
)


def test_ingest_request_roundtrips_lossless() -> None:
    """Serialization must not silently drop or coerce fields."""
    original = IngestRequest(
        adapter_name="mcp",
        payload="we decided to use idempotency keys",
        source_id="mcp:session_2026-05-19",
        source_ref="session_2026-05-19",
        mode="passive",
        repo_id="bicameral-payments",
    )
    roundtripped = IngestRequest.model_validate(original.model_dump())
    assert roundtripped == original


def test_batch_request_rejects_over_thousand_regions() -> None:
    """BATCH_REGION_LIMIT defends the daemon against flooded payloads."""
    region = CodeRegion(
        file="x.py", symbol="f", start_line=1, end_line=2, stored_hash="aaa"
    )
    with pytest.raises(ValidationError):
        BatchAnalyzeRequest(
            repo_id="repo",
            ref="HEAD",
            regions=[region] * (BATCH_REGION_LIMIT + 1),
        )
