"""Regression tests for issue #68 — surrealkv:// URL normalization for Windows paths.

Issue #68: ``urllib.parse.urlparse("surrealkv://C:\\Users\\...")`` treats
the drive letter as a netloc with a port and raises:

    ValueError: Port could not be cast to integer value as 'C'

The SurrealDB Python SDK calls ``urlparse`` internally on connect, so
passing an unmodified Windows path crashes every embedded test that
constructs its URL from a ``tmp_path`` fixture (e.g. all 5 tests in
``tests/test_schema_persistence.py``).

``LedgerClient.__init__`` now calls ``normalize_surrealkv_url`` to
replace backslashes with forward slashes inside the path, which urllib
parses cleanly AND which the SurrealKV Rust backend accepts:

    surrealkv://C:\\Users\\foo\\bar.db    →    surrealkv://C:/Users/foo/bar.db
"""

from __future__ import annotations

from urllib.parse import urlparse

import pytest

from ledger.client import LedgerClient, normalize_surrealkv_url


class TestNormalizeSurrealKVURL:
    """Pure-function tests for ``normalize_surrealkv_url``."""

    def test_windows_backslash_path_normalised(self) -> None:
        out = normalize_surrealkv_url(r"surrealkv://C:\Users\krkna\AppData\Temp\ledger.db")
        assert out == "surrealkv://C:/Users/krkna/AppData/Temp/ledger.db"

    def test_windows_forward_slash_path_unchanged(self) -> None:
        # Already forward-slashed — no backslashes to replace.
        url = "surrealkv://D:/temp/ledger.db"
        assert normalize_surrealkv_url(url) == url

    def test_lowercase_drive_letter_preserved(self) -> None:
        out = normalize_surrealkv_url(r"surrealkv://c:\foo\bar.db")
        assert out == "surrealkv://c:/foo/bar.db"

    def test_versioned_scheme_also_normalised(self) -> None:
        out = normalize_surrealkv_url(r"surrealkv+versioned://C:\foo\bar.db")
        assert out == "surrealkv+versioned://C:/foo/bar.db"

    def test_file_scheme_also_normalised(self) -> None:
        out = normalize_surrealkv_url(r"file://C:\foo\bar.db")
        assert out == "file://C:/foo/bar.db"

    def test_posix_surrealkv_url_unchanged(self) -> None:
        url = "surrealkv:///home/user/.bicameral/ledger.db"
        assert normalize_surrealkv_url(url) == url

    def test_memory_url_unchanged(self) -> None:
        assert normalize_surrealkv_url("memory://") == "memory://"

    def test_ws_url_unchanged(self) -> None:
        assert normalize_surrealkv_url("ws://localhost:8001") == "ws://localhost:8001"

    def test_https_url_unchanged(self) -> None:
        url = "https://api.surrealdb.com/db"
        assert normalize_surrealkv_url(url) == url

    def test_empty_string_unchanged(self) -> None:
        assert normalize_surrealkv_url("") == ""

    def test_normalised_url_parses_cleanly_with_urllib(self) -> None:
        """The output must not raise from ``urllib.parse.urlparse(...).port``."""
        out = normalize_surrealkv_url(r"surrealkv://C:\Users\foo\bar.db")
        parsed = urlparse(out)
        assert parsed.scheme == "surrealkv"
        # ``.port`` is the accessor that previously raised ValueError.
        assert parsed.port is None


class TestLedgerClientUsesNormalizer:
    """Confirm the normalizer is wired into ``LedgerClient.__init__``."""

    def test_constructor_normalises_windows_path(self) -> None:
        c = LedgerClient(url=r"surrealkv://C:\temp\test.db")
        assert c.url == "surrealkv://C:/temp/test.db"

    def test_constructor_passes_memory_url_through(self) -> None:
        c = LedgerClient(url="memory://")
        assert c.url == "memory://"

    def test_constructor_passes_ws_url_through(self) -> None:
        c = LedgerClient(url="ws://localhost:8001")
        assert c.url == "ws://localhost:8001"


class TestNormalizedURLConnectsCleanly:
    """End-to-end: a Windows-style URL constructed in a tmp_path fixture
    must connect without raising. This is the original repro from #68."""

    @pytest.mark.asyncio
    async def test_windows_style_tmp_path_url_connects(self, tmp_path) -> None:
        """The exact pattern from ``test_schema_persistence.py`` fixtures."""
        # On Windows this would previously fail in urllib.parse before
        # ever touching the on-disk store. On POSIX tmp_path is already
        # POSIX-style so this exercises the no-op path. Either way, the
        # connection must succeed.
        url = f"surrealkv://{tmp_path / 'ledger.db'}"
        client = LedgerClient(url=url, ns="bicameral", db="ledger")
        await client.connect()
        try:
            # Sanity: the client survived urlparse and reached SurrealDB.
            rows = await client.query("INFO FOR DB")
            # Either the query returns rows OR returns empty (v2 embedded
            # quirk documented in CLAUDE.md). Both are fine — we only
            # care that the connect path didn't raise on URL parsing.
            assert isinstance(rows, list)
        finally:
            await client.close()
