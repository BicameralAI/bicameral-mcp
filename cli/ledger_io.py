"""Constants + canonical record shape for ledger export/import (#252 Layer 4).

Closes #252 Layer 4 per
``docs/research-brief-252-privacy-preserving-ledger-remediation.md``.

Pure-data layer: constants enumerating the canonical bicameral table set,
the ``Diagnosis``-shape ``ImportSummary`` dataclass, custom exceptions,
the ``_canonical_record`` shaper that stamps export records with metadata,
and the ``_record_sort_key`` for diff-stable round-trip ordering.

The actual export/import async logic lives in ``cli/_ledger_io_engine.py``
to keep both files under the 250-LOC Razor ceiling (round-1 audit
mandate). CLI shims at ``cli/ledger_export_cli.py`` /
``cli/ledger_import_cli.py``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

EXPORT_RECORD_VERSION = 1

# Data tables (DEFINE TABLE ... not RELATION). Hardcoded canonical list
# from ``ledger/schema.py``'s grep at plan-text time. Adding a new table
# requires updating both the schema AND this constant; the parity is
# locked by ``tests/test_ledger_io_canonical_record.py``.
_DATA_TABLES: frozenset[str] = frozenset(
    {
        "input_span",
        "decision",
        "symbol",
        "code_region",
        "vocab_cache",
        "ledger_sync",
        "source_cursor",
        "compliance_check",
        "graph_proposal",
        "code_subject",
        "subject_identity",
        "subject_version",
        # Data-shaped despite edge semantics; no TYPE RELATION marker.
        "identity_supersedes",
        "schema_meta",
        "bicameral_meta",
    }
)

# Edge tables (DEFINE TABLE ... TYPE RELATION).
_EDGE_TABLES: frozenset[str] = frozenset(
    {
        "yields",
        "binds_to",
        "locates",
        "supersedes",
        "context_for",
        "depends_on",
        "has_identity",
        "has_version",
        "about",
    }
)

# Round-1 audit Path B: tables that the destination auto-populates at
# adapter.connect time (init_schema/migrate/sentinel), so the import
# DELETEs them before writing source rows. Preserves source-provenance
# round-trip semantics per Layer 2's drift-detection contract.
_DELETE_BEFORE_IMPORT: frozenset[str] = frozenset({"bicameral_meta", "schema_meta"})

_RESERVED_FIELD_NAMES = frozenset({"_table", "_schema_version", "_record_version"})


class ExportError(Exception):
    """Raised on export-side failure (e.g., reserved field-name collision)."""


class ImportError_(Exception):
    """Raised on import-side validation failure with operator-readable summary."""


@dataclasses.dataclass(frozen=True)
class ImportSummary:
    """Returned by ``import_jsonl`` on success: counts written per table.

    Phase A (validation) failures raise ``ImportError_`` before any
    write; callers receive ``ImportSummary`` only when Phase B (write)
    completed.
    """

    data_records_written: dict[str, int]
    edge_records_written: dict[str, int]
    total_records_written: int


def _canonical_record(table: str, row: dict[str, Any], schema_version: int) -> dict[str, Any]:
    """Stamp the row with ``_table`` + ``_schema_version`` + ``_record_version``.

    Returns a fresh dict with the metadata fields prepended (preserved
    by ``json.dumps(sort_keys=True)``'s alphabetical ordering — names
    starting with underscore sort first). Never mutates input.

    Raises ``ExportError`` if the source row carries any reserved
    metadata field name (collision means schema-source field conflicts
    with export metadata; needs operator attention).
    """
    record: dict[str, Any] = {
        "_table": table,
        "_schema_version": schema_version,
        "_record_version": EXPORT_RECORD_VERSION,
    }
    for key, val in row.items():
        if key in _RESERVED_FIELD_NAMES:
            raise ExportError(
                f"row in table {table!r} has reserved field name {key!r}; "
                "schema-source field name conflicts with export metadata"
            )
        record[key] = val
    return record


def _record_sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
    """Sort key: ``(table, created_at, id)``.

    ``created_at`` is the primary post-table sort so diff-stable backups
    don't churn on non-lexicographical ULID/time-based record IDs. Empty
    strings sort first (records without ``created_at`` group together).
    """
    return (
        record.get("_table", ""),
        str(record.get("created_at", "")),
        str(record.get("id", "")),
    )
