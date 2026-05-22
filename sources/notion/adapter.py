"""Notion source adapter (#420 Phase 2 — active ingest).

URL → REST fetch → block walk → normalized ingest payload.

Notion URL forms accepted:
    https://www.notion.so/<workspace>/<slug>-<32hex>
    https://www.notion.so/<32hex>
    https://www.notion.so/<workspace>/<32hex>

The trailing 32-hex page ID is the canonical identifier. Notion accepts
both dashed (``...-...``) and undashed forms in API calls; the adapter
normalizes to the dashed UUID form before the API call so the response
is predictable.

Block walker extracts text from common prose block types only:
    paragraph, heading_1, heading_2, heading_3,
    bulleted_list_item, numbered_list_item, to_do, quote, callout, code

Other types (image, video, file, embed, child_database, child_page,
table, divider) are skipped — they're not text-bearing for decision
extraction purposes. Child pages are not recursed (out-of-scope per
Phase 2 acceptance — operator passes specific pages).
"""

from __future__ import annotations

import re

_URL_RE = re.compile(
    r"^https?://(?:www\.)?notion\.so/(?:[^/]+/)?(?:[^/]+-)?(?P<id>[0-9a-fA-F]{32})(?:[/?#].*)?$"
)

# Block types we extract text from.
_PROSE_BLOCK_TYPES = frozenset(
    {
        "paragraph",
        "heading_1",
        "heading_2",
        "heading_3",
        "bulleted_list_item",
        "numbered_list_item",
        "to_do",
        "quote",
        "callout",
        "code",
    }
)


def parse_notion_url(url: str) -> str:
    """Extract the 32-hex page ID and return the dashed UUID form.

    Raises:
        ValueError: URL doesn't match any accepted Notion shape.
    """
    m = _URL_RE.match(url.strip())
    if not m:
        raise ValueError(
            f"not a recognized Notion URL: {url!r}. "
            "Expected https://www.notion.so/<workspace>/<slug>-<32hex> or similar."
        )
    raw = m.group("id").lower()
    # Normalize to dashed UUID form (8-4-4-4-12).
    return f"{raw[0:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:32]}"


def _extract_rich_text(rich_text: list[dict]) -> str:
    """Concatenate Notion's rich_text array into a plain string."""
    return "".join(item.get("plain_text", "") for item in rich_text or [])


def _extract_block_text(block: dict) -> str:
    """Return the plain-text content of a prose block, or empty string."""
    btype = block.get("type")
    if btype not in _PROSE_BLOCK_TYPES:
        return ""
    body = block.get(btype) or {}
    rich_text = body.get("rich_text") or body.get("text") or []
    text = _extract_rich_text(rich_text)
    # Decorate by type so the ingest sees structural cues — important
    # for the gap-judge chain that uses headings as topic anchors.
    if btype == "heading_1":
        return f"# {text}"
    if btype == "heading_2":
        return f"## {text}"
    if btype == "heading_3":
        return f"### {text}"
    if btype == "bulleted_list_item":
        return f"- {text}"
    if btype == "numbered_list_item":
        return f"1. {text}"
    if btype == "to_do":
        checked = body.get("checked", False)
        return f"- [{'x' if checked else ' '}] {text}"
    if btype == "quote":
        return f"> {text}"
    if btype == "callout":
        return f"📌 {text}"  # Notion's own callout marker convention
    if btype == "code":
        lang = body.get("language") or ""
        return f"```{lang}\n{text}\n```"
    return text


def _extract_page_title(page: dict) -> str:
    """Pull the page title from properties.

    Notion's property model varies — title lives under a property named
    ``"title"`` or ``"Name"`` typically. Look for any property whose
    ``type`` is ``"title"``.
    """
    properties = page.get("properties") or {}
    for prop in properties.values():
        if prop.get("type") == "title":
            return _extract_rich_text(prop.get("title", []))
    return ""


def _extract_participants(page: dict) -> list[str]:
    """Best-effort participant extraction.

    Notion exposes ``created_by`` and ``last_edited_by`` as user objects
    with at most a name; emails aren't available on the page-properties
    surface for most workspaces. Returns names where present.
    """
    out: list[str] = []
    seen: set[str] = set()
    for key in ("created_by", "last_edited_by"):
        user = page.get(key) or {}
        name = user.get("name") or user.get("id")
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def normalize_page_to_payload(page: dict, blocks: list[dict], page_id: str) -> dict:
    """Build the ingest payload from page metadata + child blocks."""
    title = _extract_page_title(page) or page_id
    block_texts = [t for b in blocks if (t := _extract_block_text(b))]
    full_text = "\n".join(block_texts).strip()

    decisions: list[dict] = []
    if full_text:
        decisions.append({"description": full_text, "title": title})

    return {
        "query": title,
        "source": "notion",
        "title": title,
        "date": page.get("last_edited_time") or page.get("created_time") or "",
        "participants": _extract_participants(page),
        "decisions": decisions,
    }


class NotionAdapter:
    """SourceAdapter implementation for Notion (active path)."""

    source_id = "notion"

    def can_handle_url(self, url: str) -> bool:
        return bool(_URL_RE.match(url.strip()))

    def fetch_active(self, url: str) -> dict:
        page_id = parse_notion_url(url)
        api_key = self._resolve_api_key()
        from sources.notion.client import get_all_blocks, get_page

        page = get_page(api_key=api_key, page_id=page_id)
        blocks = get_all_blocks(api_key=api_key, page_id=page_id)
        return normalize_page_to_payload(page, blocks, page_id)

    def _resolve_api_key(self) -> str:
        from secrets_store import get_secret

        key = get_secret(source_id=self.source_id, key="api_key")
        if not key:
            raise RuntimeError(
                "Notion API key not configured. Set it via:\n"
                '  python -c "from secrets_store import put_secret; '
                "put_secret(source_id='notion', key='api_key', value='secret_...')\""
            )
        return key
