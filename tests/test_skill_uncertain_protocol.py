"""Issue #44 Phase 2 — bicameral-sync uncertain-band sub-protocol
conformance tests.

Asserts structural invariants on ``skills/bicameral-sync/SKILL.md``:
the section that the LLM follows when handed a
``pre_classification: uncertain`` hint must (a) exist and (b) name
the two-axis output fields and the four advisory signals so the
caller LLM has enough information to follow the rubric.

Pure text parsing — no SurrealDB, no LLM, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

_SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills" / "bicameral-sync" / "SKILL.md"
)
_HEADING_PATTERN = re.compile(r"uncertain[- ]band sub-protocol", re.IGNORECASE)
_AXIS_TERMS = ("semantic_status", "semantically_preserved", "semantic_change")
_SIGNAL_TERMS = ("signature", "neighbors", "diff_lines", "no_new_calls")
_AXIS_1_FIRST_PATTERN = re.compile(
    r"axis\s*1.*(first|before)|not[_ ]relevant.*(first|short[- ]circuit)",
    re.IGNORECASE,
)


def _read_skill() -> str:
    """Return the full SKILL.md text. Fails the test loudly if the
    file moved or was renamed (catches plan-vs-reality drift)."""
    assert _SKILL_PATH.exists(), (
        f"skill file not at expected path: {_SKILL_PATH}"
    )
    return _SKILL_PATH.read_text(encoding="utf-8")


def _subsection_text() -> str:
    """Return the body of the uncertain-band sub-protocol heading
    through the next heading-or-EOF. Fails the test loudly if the
    heading is missing — used by every test that needs the body."""
    text = _read_skill()
    match = _HEADING_PATTERN.search(text)
    assert match, "no uncertain-band sub-protocol heading found in SKILL.md"
    body_start = match.end()
    next_heading = re.search(r"^#+\s", text[body_start:], re.MULTILINE)
    end = body_start + next_heading.start() if next_heading else len(text)
    return text[body_start:end]


def test_skill_md_has_uncertain_band_subsection() -> None:
    """The skill must declare an ``Uncertain-band sub-protocol``
    section so the caller LLM knows which rubric to apply when
    ``pre_classification.verdict == "uncertain"``."""
    text = _read_skill()
    assert _HEADING_PATTERN.search(text), (
        "SKILL.md does not contain an 'Uncertain-band sub-protocol' "
        "heading; rubric for the [0.30, 0.80) band is missing."
    )


def test_uncertain_subsection_names_both_axes() -> None:
    """The sub-protocol must name the two-axis output fields by
    their literal Pydantic names so the LLM emits a verdict the
    server actually accepts."""
    body = _subsection_text()
    missing = [term for term in _AXIS_TERMS if term not in body]
    assert not missing, (
        f"sub-protocol omits axis terms {missing}; LLM cannot emit "
        f"contract-valid semantic_status without them."
    )


def test_uncertain_subsection_describes_signal_use() -> None:
    """The sub-protocol must reference all four advisory signals so
    the LLM knows the deterministic evidence it's overriding."""
    body = _subsection_text()
    missing = [s for s in _SIGNAL_TERMS if s not in body]
    assert not missing, (
        f"sub-protocol omits classifier signals {missing}; LLM "
        f"cannot reason about hint quality without them."
    )


def test_uncertain_subsection_states_axis_1_first_rule() -> None:
    """Plan D5 step 1: axis 1 (compliance) is decided FIRST.
    ``not_relevant`` short-circuits axis 2 — the rubric must say so
    explicitly, otherwise the LLM applies axis-2 reasoning to a
    misretrieved region and emits a meaningless semantic_status."""
    body = _subsection_text()
    assert _AXIS_1_FIRST_PATTERN.search(body), (
        "sub-protocol does not state the axis-1-first short-circuit; "
        "LLM may emit semantic_status for not_relevant regions."
    )
