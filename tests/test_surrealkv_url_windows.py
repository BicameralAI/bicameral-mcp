"""Windows-safe surrealkv:// URL construction tests.

Issue #68: ``f"surrealkv://{db_path}"`` produced URLs containing a Windows
drive letter (e.g. ``surrealkv://C:\\Users\\foo\\.bicameral\\ledger.db``)
that urllib.parse rejected with "Port could not be cast" because it tried
to interpret the colon after ``C`` as a host:port separator.

Fix: normalize the path so the URL is a proper file URL — backslashes
become forward slashes and a leading ``/`` is prepended for drive paths.
"""

from __future__ import annotations

import sys
import urllib.parse
from pathlib import Path, PureWindowsPath

import pytest


def _normalize_for_test(path_str: str) -> str:
    """Helper that mirrors the production normalization (see ledger/adapter.py)."""
    from ledger.adapter import _surrealkv_url_for_path
    return _surrealkv_url_for_path(Path(path_str))


def test_posix_path_unchanged() -> None:
    """POSIX path → no leading slash gymnastics needed."""
    from ledger.adapter import _surrealkv_url_for_path
    url = _surrealkv_url_for_path(Path("/home/foo/.bicameral/ledger.db"))
    assert url == "surrealkv:///home/foo/.bicameral/ledger.db"
    # Round-trips through urllib without raising.
    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme == "surrealkv"


def test_windows_path_produces_parseable_url() -> None:
    """Windows drive-letter path → urllib.parse.urlparse does not raise."""
    from ledger.adapter import _surrealkv_url_for_path
    # Use PureWindowsPath so the test runs the same on POSIX and Windows.
    p = PureWindowsPath("C:/Users/foo/.bicameral/ledger.db")
    url = _surrealkv_url_for_path(p)
    # No "Port could not be cast" — the colon after "C" must not look like
    # a host:port boundary.
    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme == "surrealkv"
    # Drive letter survives in the path component.
    assert "C:" in url or "/C/" in url


def test_windows_path_with_backslashes_normalized() -> None:
    """Backslashes in the input → forward slashes in the URL."""
    from ledger.adapter import _surrealkv_url_for_path
    p = PureWindowsPath(r"C:\Users\foo\.bicameral\ledger.db")
    url = _surrealkv_url_for_path(p)
    assert "\\" not in url
    parsed = urllib.parse.urlparse(url)
    assert parsed.scheme == "surrealkv"


@pytest.mark.asyncio
async def test_round_trip_open_persistent_db(tmp_path: Path) -> None:
    """Deciding probe: the chosen URL shape must successfully open a
    persistent surrealkv DB and round-trip a simple query.

    On Windows tmp_path will contain a drive letter; on POSIX it won't.
    Either way the URL must be acceptable to the Surreal SDK.
    """
    from ledger.adapter import _surrealkv_url_for_path
    from ledger.client import LedgerClient

    db_path = tmp_path / "ledger.db"
    url = _surrealkv_url_for_path(db_path)

    client = LedgerClient(url=url)
    await client.connect()
    try:
        # Round-trip: define a trivial table, insert, select, expect 1 row.
        await client.execute("DEFINE TABLE __probe SCHEMAFULL")
        await client.execute("DEFINE FIELD k ON __probe TYPE string")
        await client.execute("CREATE __probe SET k = 'v'")
        rows = await client.query("SELECT * FROM __probe")
        assert len(rows) == 1
        assert rows[0]["k"] == "v"
    finally:
        await client.close()
