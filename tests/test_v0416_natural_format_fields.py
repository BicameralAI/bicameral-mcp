"""v0.4.16 — natural-format ingest field-name regression tests.

Locks in the contract between ``skills/bicameral-ingest/SKILL.md`` and
``handlers/ingest._normalize_payload``. The dogfood failure that
motivated this test set came from a silent drift: the skill documented
``{text: "..."}`` for decisions while the handler read
``d.description or d.title``, so Pydantic silently dropped the unknown
``text`` field and every decision was evaporating.

These tests guard all three surfaces:

1. **Canonical path** — ``description`` / ``title`` / ``action`` produce
   real mappings (the shape documented in the SKILL.md).
2. **Alias path** — ``text`` works as a synonym on both decisions and
   action_items (v0.4.16 backward-compatibility shim).
3. **Empty-drop path** — when every text field on a decision is empty,
   the decision is dropped rather than producing a phantom empty mapping.
4. **Action item empty-drop** — action_items with no text content are
   dropped rather than producing a ``[Action: owner]`` prefix with no body
   (the exact symptom that grounded to unrelated ``use-toast.ts`` Action
   enums during the demo gallery dogfood).
"""

from __future__ import annotations

from handlers.ingest import _normalize_payload


def test_canonical_description_survives():
    """`decisions[].description` is the canonical field — must produce
    a mapping with the description as the intent."""
    out = _normalize_payload(
        {
            "decisions": [{"description": "Use Redis for session cache"}],
        }
    )
    mappings = out.get("mappings", [])
    assert len(mappings) == 1
    assert mappings[0]["intent"] == "Use Redis for session cache"
    assert mappings[0]["span"]["text"] == "Use Redis for session cache"


def test_canonical_title_fallback():
    """`decisions[].title` is the documented secondary field — used when
    `description` is absent."""
    out = _normalize_payload(
        {
            "decisions": [{"title": "Apply 10% discount on orders over $100"}],
        }
    )
    mappings = out.get("mappings", [])
    assert len(mappings) == 1
    assert mappings[0]["intent"] == "Apply 10% discount on orders over $100"


def test_text_alias_for_decisions():
    """v0.4.16 alias: `text` on a decision should flow through as the
    intent. This is the exact shape the old SKILL.md documented; keeping
    it working guards against a regression."""
    out = _normalize_payload(
        {
            "decisions": [{"text": "Cache user sessions in Redis"}],
        }
    )
    mappings = out.get("mappings", [])
    assert len(mappings) == 1
    assert mappings[0]["intent"] == "Cache user sessions in Redis"


def test_description_preferred_over_text_when_both_present():
    """When a decision has both `description` and `text`, the canonical
    `description` wins. This is the documented priority order:
    description > title > text."""
    out = _normalize_payload(
        {
            "decisions": [
                {
                    "description": "canonical description wins",
                    "text": "alias should lose",
                }
            ],
        }
    )
    mappings = out.get("mappings", [])
    assert len(mappings) == 1
    assert mappings[0]["intent"] == "canonical description wins"


def test_decision_with_all_text_fields_empty_is_dropped():
    """If a decision has no text in any accepted field, it must be
    silently dropped rather than producing a phantom mapping."""
    out = _normalize_payload(
        {
            "decisions": [
                {"description": "real decision"},
                {"status": "proposed"},  # no description/title/text
                {"id": "abc", "participants": ["Ian"]},  # metadata only
            ],
        }
    )
    mappings = out.get("mappings", [])
    assert len(mappings) == 1
    assert mappings[0]["intent"] == "real decision"


def test_action_items_not_written_to_ledger():
    """action_items are accepted in payload for backwards compat but NOT
    written to the ledger (not converted to mappings). They belong in a
    ticket tracker, not the decision ledger."""
    out = _normalize_payload(
        {
            "action_items": [{"action": "Write retry tests", "owner": "Ian"}],
        }
    )
    mappings = out.get("mappings", [])
    assert len(mappings) == 0


def test_action_items_mixed_with_decisions():
    """When payload has both decisions and action_items, only decisions
    become mappings — action_items are silently ignored."""
    out = _normalize_payload(
        {
            "decisions": [{"description": "Use Redis for session cache"}],
            "action_items": [{"action": "Write retry tests", "owner": "Ian"}],
        }
    )
    mappings = out.get("mappings", [])
    assert len(mappings) == 1
    assert mappings[0]["intent"] == "Use Redis for session cache"


def test_the_exact_dogfood_payload():
    """Replay the exact payload shape from the original dogfood failure
    (ingest of the demo gallery). Before the fix: 0 decisions surfaced,
    1 phantom '[Action: Ian] ' mapping, grounded to unrelated symbols.
    After the fix: only real decisions surface; action_items are accepted
    for backwards compat but not written to the ledger."""
    out = _normalize_payload(
        {
            "source": "transcript",
            "title": "demo-gallery",
            "decisions": [
                {"text": "Cache user sessions in Redis for horizontal scaling"},
                {"text": "Apply 10% discount on orders over $100"},
            ],
            "action_items": [
                {"text": "Write tests for retry policy", "owner": "Ian"},
            ],
        }
    )
    mappings = out.get("mappings", [])
    intents = [m["intent"] for m in mappings]
    assert "Cache user sessions in Redis for horizontal scaling" in intents
    assert "Apply 10% discount on orders over $100" in intents
    # action_items are not written to the ledger
    assert "[Action: Ian] Write tests for retry policy" not in intents
    assert len(mappings) == 2


def test_mixed_canonical_and_alias_in_same_payload():
    """A payload can mix canonical and alias fields across decisions —
    the handler normalizes each decision independently."""
    out = _normalize_payload(
        {
            "decisions": [
                {"description": "First decision via canonical field"},
                {"title": "Second decision via title fallback"},
                {"text": "Third decision via text alias"},
            ],
        }
    )
    mappings = out.get("mappings", [])
    assert len(mappings) == 3
    assert mappings[0]["intent"] == "First decision via canonical field"
    assert mappings[1]["intent"] == "Second decision via title fallback"
    assert mappings[2]["intent"] == "Third decision via text alias"


def test_action_items_always_produce_zero_mappings():
    """action_items are never written to the ledger regardless of their fields.
    This guards against the '[Action: <owner>] ' phantom-mapping regression."""
    out = _normalize_payload(
        {
            "action_items": [
                {"action": "real action", "owner": "Ian"},
                {"action": "another action"},
            ],
        }
    )
    mappings = out.get("mappings", [])
    assert len(mappings) == 0
