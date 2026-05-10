"""Fixture: v3-era ``yields.in = source_span:*`` rows.

Reproduces the dogfood crash (#296) where a DB carried v3-vintage
``yields`` edges whose ``in`` is a ``source_span:*`` record. The
current schema declares ``yields`` as ``RELATION IN input_span OUT
decision``, so applying ``DEFINE INDEX OVERWRITE idx_yields_unique``
re-validates the existing rows and fails with::

    Found source_span:<id> for field `in`, with record `yields:<id>`,
    but expected a record<input_span>

The v4 → v5 cleanup deletes these rows, but only on the v4→v5
boundary. Any DB whose ``schema_meta.version`` rolled past 5 with the
corruption intact is permanently broken until the v16 → v17 cleanup
re-runs the same logic.
"""

from __future__ import annotations


async def build(client) -> None:
    """Insert a v3-era ``yields`` row whose ``in`` is a ``source_span``.

    Sets ``schema_meta.version = 16`` so the migration loop sees a
    legitimate "past v5" DB that v4→v5 cannot reach. The v16 → v17
    cleanup is the only path that should fix it.
    """
    # Define a minimal source_span shadow table so the RecordID parses.
    # We can't recreate the full v3 schema — we just need the row id to
    # use the source_span:<id> form.
    await client.execute("DEFINE TABLE source_span SCHEMAFULL")
    await client.execute("CREATE source_span:legacy_span_1 SET text = 'legacy'")
    await client.execute("CREATE input_span:span_1 SET text = 'fresh', source_type = 'transcript'")
    await client.execute(
        "CREATE decision:dec_1 SET description = 'd1', source_type = 'transcript', "
        "source_ref = '', status = 'ungrounded', canonical_id = 'fixture-cid-1'"
    )
    # RELATE refuses cross-table on a typed edge, so write the bad row
    # by direct CREATE on the yields table after defining it permissively.
    await client.execute("DEFINE TABLE yields SCHEMAFULL TYPE RELATION")
    await client.execute("RELATE source_span:legacy_span_1 -> yields -> decision:dec_1")
    # And one valid row so the dedupe step has something legitimate.
    await client.execute("RELATE input_span:span_1 -> yields -> decision:dec_1")
    # Mark the schema as v16 so the migration loop targets v16 → v17.
    await client.execute("DEFINE TABLE schema_meta SCHEMAFULL")
    await client.execute("DEFINE FIELD version ON schema_meta TYPE int")
    await client.execute("CREATE schema_meta SET version = 16")


# Post-migration assertions specific to this fixture.
async def assert_clean(client) -> None:
    """All ``yields`` rows must reference an ``input_span`` IN."""
    rows = await client.query("SELECT id, type::string(in) AS in_table FROM yields")
    bad = [r for r in (rows or []) if not str(r.get("in_table", "")).startswith("input_span:")]
    assert not bad, f"v16→v17 left stale yields rows: {bad}"
