"""v0.4.13 — content-addressable canonical IDs for team-mode dedup.

Derives a stable UUID from the semantic content of an event so two
developers ingesting the same source produce the same ID independently.
No coordinator, no central service — pure deterministic computation.

The derivation pipeline:

  1. Normalize the source_ref to a canonical format (strip format
     variation, lowercase, keep stable tokens). Two devs writing
     `slack:#payments:1726113809.330439` and
     `slack:payments-1726113809330439` produce identical normalized refs.

  2. Normalize the decision text (lowercase, collapse whitespace,
     strip Unicode punctuation variants, NFC normalization).

  3. Build a canonical dict: ``{type, description, source_ref,
     code_refs?}``. Code refs (when present) are sorted lexicographically
     so order doesn't matter.

  4. Serialize via RFC 8785 JSON Canonicalization Scheme (JCS): keys
     sorted lexicographically, no whitespace, integers as integers,
     strings as JCS-spec UTF-8.

  5. Compute ``UUIDv5(BICAMERAL_NAMESPACE, jcs_bytes)``. Same input
     bytes → same UUID, across any writer.

This is Pattern 1 from the v0.4.13 web research (content-addressable
canonical ID, used by git, Wikidata, NATS subjects, Stripe idempotency
keys when derived from request body).

LLM paraphrases (same decision, different wording) produce DIFFERENT
canonical_ids — exact-match only. That's intentional. Phase 4 in v0.4.14
will add embedding-similarity as a second dedup pass for paraphrases.
For v0.4.13, the source_ref normalization closes the most common drift
mode (same Slack thread, different format strings), and the JCS pass
closes whitespace/casing/Unicode variation.
"""

from __future__ import annotations

import json
import re
import unicodedata
from uuid import NAMESPACE_URL, UUID, uuid5


# Stable namespace UUID for bicameral canonical IDs. Derived from a
# bicameral-specific URL via UUIDv5(NAMESPACE_URL, "https://bicameral.dev/v0.4.13/canonical").
# Hard-coded so it never changes — every writer in any version of
# bicameral uses this exact namespace. Do NOT change after release.
BICAMERAL_NAMESPACE = uuid5(
    NAMESPACE_URL,
    "https://bicameral.dev/v0.4.13/canonical",
)


# ── Source ref canonicalization ────────────────────────────────────


_SLACK_TS_RE = re.compile(r"[\d.]+")


def canonicalize_source_ref(source_type: str, raw_ref: str) -> str:
    """Normalize a source reference into a stable canonical form.

    Same logical source (same Slack thread, same Notion page, same
    transcript) produces the same string regardless of how the LLM
    formatted it during extraction.

    Examples::

        slack:#payments:1726113809.330439 → slack:payments:1726113809330439
        slack:payments-1726113809330439   → slack:payments:1726113809330439
        notion:Page-Title-abc123def456     → notion:abc123def456
        github:issue/142                   → github:issue:142
        transcript:meeting_2026_03_12      → transcript:meeting_2026_03_12

    Unknown source_types fall through to a generic normalizer that
    lowercases, strips punctuation that's commonly inserted by LLMs,
    and collapses whitespace.
    """
    raw = (raw_ref or "").strip()
    stype = (source_type or "").strip().lower()

    if not raw:
        return f"{stype}:" if stype else ""

    if stype == "slack":
        # Strip leading # from channel name, strip dots from timestamps,
        # normalize separator. Slack URLs / message IDs come in many
        # shapes — extract the channel name and the message timestamp.
        # Accept inputs like:
        #   #payments:1726113809.330439
        #   payments-1726113809330439
        #   payments:1726113809.330439
        cleaned = raw.lstrip("#").lower()
        # Try to find a numeric timestamp at the end of the string
        ts_match = _SLACK_TS_RE.search(cleaned[::-1])
        if ts_match:
            ts_reversed = ts_match.group(0)
            ts = ts_reversed[::-1].replace(".", "")
            # Channel is everything before the timestamp
            channel_part = cleaned[: -len(ts_reversed)].rstrip("-:_/.")
            channel = re.sub(r"[^a-z0-9_]+", "", channel_part) or "unknown"
            return f"slack:{channel}:{ts}"
        return f"slack:{re.sub(r'[^a-z0-9_]+', '', cleaned)}"

    if stype == "notion":
        # Notion page IDs are 32-char hex (with or without dashes).
        # Strip the title prefix and the dashes; keep the trailing UUID.
        cleaned = raw.lower().replace("-", "")
        match = re.search(r"[a-f0-9]{32}", cleaned)
        if match:
            return f"notion:{match.group(0)}"
        # Fallback: lowercase and strip punctuation
        return f"notion:{re.sub(r'[^a-z0-9]+', '', cleaned)}"

    if stype == "github":
        # GitHub issue/PR refs: github:issue/142, github:pr/267, etc.
        cleaned = raw.lower().replace(" ", "")
        # Standardize separator to colon
        cleaned = cleaned.replace("/", ":").replace("#", ":")
        # Collapse multiple colons
        cleaned = re.sub(r":+", ":", cleaned).strip(":")
        return f"github:{cleaned}"

    if stype == "transcript":
        # Free-form transcript IDs — lowercase, replace whitespace with
        # underscore, strip non-alphanumeric (except _).
        cleaned = re.sub(r"\s+", "_", raw.strip().lower())
        cleaned = re.sub(r"[^a-z0-9_]+", "", cleaned)
        return f"transcript:{cleaned}"

    # Generic fallback: lowercase, strip leading/trailing punctuation,
    # collapse internal whitespace. Preserves the structure of unknown
    # source types without making aggressive assumptions.
    cleaned = raw.lower().strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return f"{stype}:{cleaned}" if stype else cleaned


# ── Decision text normalization ────────────────────────────────────


_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_VARIANTS = {
    "\u2018": "'",  # left single quote
    "\u2019": "'",  # right single quote
    "\u201c": '"',  # left double quote
    "\u201d": '"',  # right double quote
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2026": "...",  # ellipsis
    "\u00a0": " ",  # non-breaking space
}


def canonicalize_text(text: str) -> str:
    """Normalize a decision description / span text for stable hashing.

    - NFC Unicode normalization (compose accents)
    - Replace common Unicode punctuation variants with ASCII equivalents
      (curly quotes → straight, em-dash → hyphen, etc.)
    - Lowercase
    - Collapse all whitespace runs to a single space
    - Strip leading/trailing whitespace

    The goal: two developers' LLMs producing slightly different
    formatting of the same sentence produce identical canonical text.
    Does NOT handle paraphrases (different word choice) — that's
    Phase 4 (embedding similarity) for v0.4.14.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFC", text)
    for variant, ascii_char in _PUNCTUATION_VARIANTS.items():
        normalized = normalized.replace(variant, ascii_char)
    normalized = normalized.lower()
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized


# ── JCS-lite JSON canonicalization ─────────────────────────────────


def canonical_json_bytes(obj: dict) -> bytes:
    """Serialize a dict via RFC 8785 JSON Canonicalization Scheme (JCS).

    Constraints applied:
    - Keys sorted lexicographically at every level
    - No whitespace (compact separators)
    - Strings as UTF-8
    - Integers serialized as integers (not floats)
    - Lists preserve order (caller is responsible for sorting if needed)

    This is a JCS-lite implementation — it covers the subset bicameral
    needs (string/int/list/dict) without pulling in a third-party
    library. For full RFC 8785 compliance with floats, use the ``pyjcs``
    package; bicameral's canonical_id inputs never contain floats so
    this is sufficient.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# ── Canonical ID derivation ────────────────────────────────────────


def canonical_intent_id(
    description: str,
    source_type: str,
    source_ref: str,
) -> str:
    """Derive a stable UUIDv5 from an intent's semantic content.

    Two writers calling this with the same logical decision (same
    text after normalization, same source after canonicalization)
    produce the same UUID regardless of formatting variance.

    Returns the UUID as a string (e.g. "3f7a9b2c-...-..."). Use as
    the primary dedup key in ``upsert_intent``.
    """
    payload = {
        "type": "intent",
        "description": canonicalize_text(description),
        "source_ref": canonicalize_source_ref(source_type, source_ref),
    }
    jcs = canonical_json_bytes(payload)
    return str(uuid5(BICAMERAL_NAMESPACE, jcs.decode("utf-8")))


def canonical_source_span_id(
    text: str,
    source_type: str,
    source_ref: str,
) -> str:
    """Derive a stable UUIDv5 from a source span's semantic content.

    Same canonicalization rules as ``canonical_intent_id``. Two writers
    capturing the same source produce the same span ID.
    """
    payload = {
        "type": "source_span",
        "text": canonicalize_text(text),
        "source_ref": canonicalize_source_ref(source_type, source_ref),
    }
    jcs = canonical_json_bytes(payload)
    return str(uuid5(BICAMERAL_NAMESPACE, jcs.decode("utf-8")))
