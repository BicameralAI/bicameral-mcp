"""Tests for telemetry_flags.py (issue #192): consolidated BICAMERAL_TELEMETRY parser.

Coverage per #192 acceptance criteria:
- Default unset → relay on, preflight off, raw off (preserves current behavior)
- Boolean forms (0/off/false/no, 1/on/true/yes)
- CSV form (explicit per-source enable)
- Legacy var compat (BICAMERAL_PREFLIGHT_TELEMETRY, BICAMERAL_PREFLIGHT_TELEMETRY_RAW)
  with one-line stderr deprecation warning per process
- Integration with consent.telemetry_allowed() and
  preflight_telemetry.{telemetry_enabled, raw_capture_enabled}

Each test uses monkeypatch.setenv + _reset_for_tests() for cache isolation.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_telemetry_flags():
    """Flush the lru_cache before AND after each test so env var monkeypatches
    take effect. Also clears the once-per-process warning set."""
    from telemetry_flags import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


def _clear_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Helper: legacy vars should be unset unless a test explicitly sets them."""
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", raising=False)


# ── 1. Default unset ────────────────────────────────────────────────────


def test_default_unset_preserves_current_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env vars set → relay on, preflight off, raw off (today's default)."""
    monkeypatch.delenv("BICAMERAL_TELEMETRY", raising=False)
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True
    assert flags.preflight is False
    assert flags.raw is False


# ── 2-3. Boolean OFF forms ─────────────────────────────────────────────


@pytest.mark.parametrize("val", ["0", "off", "false", "no", "OFF", "False"])
def test_boolean_off_disables_all(val: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BICAMERAL_TELEMETRY", val)
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is False
    assert flags.preflight is False
    assert flags.raw is False


# ── 4-5. Boolean ON forms ──────────────────────────────────────────────


@pytest.mark.parametrize("val", ["1", "on", "true", "yes", "ON", "True"])
def test_boolean_on_enables_relay_only(val: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bool ON form preserves current relay-only default — does NOT auto-enable preflight."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", val)
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True
    assert flags.preflight is False
    assert flags.raw is False


# ── 6. CSV form: relay,preflight ───────────────────────────────────────


def test_csv_relay_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "relay,preflight")
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True
    assert flags.preflight is True
    assert flags.raw is False


# ── 7. CSV form: preflight,raw (relay NOT in list = relay off) ─────────


def test_csv_preflight_raw_excludes_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    """CSV form is explicit — what's listed is on, what's not is off.
    Including this footgun-prone case: preflight,raw turns OFF the default-on relay."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "preflight,raw")
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is False
    assert flags.preflight is True
    assert flags.raw is True


# ── 8. CSV form: raw alone implies preflight ───────────────────────────


def test_csv_raw_alone_implies_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """raw without preflight is treated as preflight,raw (raw requires preflight)."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "raw")
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is False  # not in list
    assert flags.preflight is True  # implied by raw
    assert flags.raw is True


# ── 9. CSV form: all three ─────────────────────────────────────────────


def test_csv_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "relay,preflight,raw")
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True
    assert flags.preflight is True
    assert flags.raw is True


# ── 10. CSV with unrecognized source emits warning ─────────────────────


def test_csv_unrecognized_source_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Unknown source ignored; stderr warning lists recognized names."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "relay,foobar")
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True
    assert flags.preflight is False
    assert flags.raw is False

    captured = capsys.readouterr()
    assert "foobar" in captured.err.lower() or "unrecognized" in captured.err.lower()


# ── 10b. Legacy truthy preservation (Codex review fix) ────────────────


@pytest.mark.parametrize("val", ["enabled", "t", "y", "active", "yep"])
def test_legacy_truthy_preserves_relay_on(
    val: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Pre-#192 behavior: any non-_OFF value of BICAMERAL_TELEMETRY enabled
    relay. Preserve that for upgraders — non-recognized non-bool strings
    map to relay-only with a stderr warning pointing at the canonical form.
    Codex P2 finding 2026-05-07."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", val)
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True, f"value {val!r} should preserve relay=True (legacy truthy)"
    assert flags.preflight is False
    assert flags.raw is False

    captured = capsys.readouterr()
    assert "legacy" in captured.err.lower() or "BICAMERAL_TELEMETRY=1" in captured.err


def test_legacy_truthy_with_only_unrecognized_csv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """All-unrecognized csv (e.g. `foo,bar`) → legacy truthy fallback (relay on)."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "foo,bar")
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True
    assert flags.preflight is False
    assert flags.raw is False

    captured = capsys.readouterr()
    assert "legacy" in captured.err.lower()


def test_partially_recognized_csv_does_not_trigger_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """`relay,foobar` has `relay` recognized, so csv form applies (relay=True
    only, foobar ignored with warning). Does NOT fall through to legacy
    truthy fallback (which would also imply preflight=False, but for the
    right reason — the csv was meaningful)."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "relay,foobar")
    _clear_legacy_env(monkeypatch)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True
    assert flags.preflight is False
    assert flags.raw is False

    captured = capsys.readouterr()
    # The "unrecognized sources" warning fires, not the "legacy truthy" one.
    assert "foobar" in captured.err.lower()
    assert "legacy truthy" not in captured.err.lower()


# ── 11. Legacy BICAMERAL_PREFLIGHT_TELEMETRY=1 ─────────────────────────


def test_legacy_preflight_var_enables_preflight_with_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Legacy var continues to work as additive overlay; emits one-line warning."""
    monkeypatch.delenv("BICAMERAL_TELEMETRY", raising=False)
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", raising=False)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True  # default
    assert flags.preflight is True  # from legacy var
    assert flags.raw is False

    captured = capsys.readouterr()
    assert "BICAMERAL_PREFLIGHT_TELEMETRY" in captured.err
    assert "deprecat" in captured.err.lower()


# ── 12. Legacy BICAMERAL_PREFLIGHT_TELEMETRY_RAW=1 ─────────────────────


def test_legacy_raw_var_enables_raw_and_implies_preflight(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Legacy raw var → preflight=True (implied) AND raw=True."""
    monkeypatch.delenv("BICAMERAL_TELEMETRY", raising=False)
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY", raising=False)
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", "1")

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True  # default
    assert flags.preflight is True  # implied by raw
    assert flags.raw is True

    captured = capsys.readouterr()
    assert "BICAMERAL_PREFLIGHT_TELEMETRY_RAW" in captured.err
    assert "deprecat" in captured.err.lower()


# ── 13. Both legacy + new ──────────────────────────────────────────────


def test_legacy_and_new_combine_additively(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Legacy overlay is additive — can force a source ON, never OFF.
    Consolidated says relay-only; legacy says preflight; net = both on."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "relay")
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", raising=False)

    from telemetry_flags import get_flags

    flags = get_flags()
    assert flags.relay is True
    assert flags.preflight is True  # legacy overlay forces ON
    assert flags.raw is False

    captured = capsys.readouterr()
    assert "deprecat" in captured.err.lower()


# ── 14. Warning emitted exactly once per process ───────────────────────


def test_legacy_warning_emitted_once_per_process(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Multiple get_flags() calls → exactly one stderr warning per legacy var."""
    monkeypatch.delenv("BICAMERAL_TELEMETRY", raising=False)
    monkeypatch.setenv("BICAMERAL_PREFLIGHT_TELEMETRY", "1")
    monkeypatch.delenv("BICAMERAL_PREFLIGHT_TELEMETRY_RAW", raising=False)

    from telemetry_flags import get_flags

    get_flags()
    get_flags()
    get_flags()

    captured = capsys.readouterr()
    # Count occurrences of the warning marker — should be exactly 1
    warning_count = captured.err.count("BICAMERAL_PREFLIGHT_TELEMETRY")
    assert warning_count == 1, f"expected 1 warning, got {warning_count}"


# ── 15. consent.telemetry_allowed() integration ────────────────────────


def test_consent_telemetry_allowed_respects_relay_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """When relay=False (BICAMERAL_TELEMETRY=0), telemetry_allowed returns False."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "0")
    _clear_legacy_env(monkeypatch)

    import importlib

    import consent

    importlib.reload(consent)
    assert consent.telemetry_allowed() is False


def test_consent_telemetry_allowed_default_unset_preserves_default_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """No env var, no marker → default-on (relay=True from get_flags + no marker)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BICAMERAL_TELEMETRY", raising=False)
    _clear_legacy_env(monkeypatch)

    import importlib

    import consent

    importlib.reload(consent)
    assert consent.telemetry_allowed() is True


# ── 16. preflight_telemetry.telemetry_enabled() integration ────────────


def test_preflight_telemetry_enabled_reads_consolidated_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "relay,preflight")
    _clear_legacy_env(monkeypatch)

    import importlib

    import preflight_telemetry

    importlib.reload(preflight_telemetry)
    assert preflight_telemetry.telemetry_enabled() is True


def test_preflight_telemetry_enabled_off_when_preflight_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "relay")
    _clear_legacy_env(monkeypatch)

    import importlib

    import preflight_telemetry

    importlib.reload(preflight_telemetry)
    assert preflight_telemetry.telemetry_enabled() is False


# ── 17. preflight_telemetry.raw_capture_enabled() integration ──────────


def test_raw_capture_enabled_requires_both_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """raw=True only when raw IS set AND preflight is set (defensive)."""
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "relay,preflight,raw")
    _clear_legacy_env(monkeypatch)

    import importlib

    import preflight_telemetry

    importlib.reload(preflight_telemetry)
    assert preflight_telemetry.raw_capture_enabled() is True


def test_raw_capture_enabled_false_without_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BICAMERAL_TELEMETRY", "relay,preflight")
    _clear_legacy_env(monkeypatch)

    import importlib

    import preflight_telemetry

    importlib.reload(preflight_telemetry)
    assert preflight_telemetry.raw_capture_enabled() is False
