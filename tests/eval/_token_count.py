"""Approximate token counter for cost/latency baseline.

Uses tiktoken with the ``cl100k_base`` encoding. This is an approximation —
there is no public Claude tokenizer. Absolute counts run ~10–15% off Claude's
actual tokenization, but the *delta* between runs on the same encoder is
stable, which is what the regression rule depends on.

tiktoken is pinned in ``pyproject.toml`` ``[test]`` extras to avoid silent
count drift across CI runs.
"""
from __future__ import annotations

import functools
import json


@functools.lru_cache(maxsize=1)
def _encoder():
    import tiktoken
    return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Return approximate Claude token count for ``text``."""
    return len(_encoder().encode(text))


def count_tokens_json(payload: dict | list) -> int:
    """Token count of ``payload`` JSON-serialized as the skill receives it.

    Uses ``ensure_ascii=False`` and no indentation — matches the wire-format
    payload size, not a pretty-printed view.
    """
    return count_tokens(json.dumps(payload, ensure_ascii=False))
