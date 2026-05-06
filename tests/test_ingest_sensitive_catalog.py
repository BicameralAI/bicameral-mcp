"""Functionality tests for the sensitive-data catalog (#213 Phase 1):
secret patterns, PHI patterns, PAN candidate regex + Luhn validator +
label-context filter."""

from __future__ import annotations

from handlers.sensitive_patterns import (
    _PAN_CANDIDATE_RE,
    _PHI_PATTERNS,
    _SECRET_PATTERNS,
    _SENSITIVE_CATALOG_VERSION,
    _is_id_preceded,
    _luhn_valid,
)


def _secret_for(label: str):
    for lbl, pat in _SECRET_PATTERNS:
        if lbl == label:
            return pat
    raise AssertionError(f"secret label {label!r} not in catalog")


def _phi_for(label: str):
    for lbl, pat in _PHI_PATTERNS:
        if lbl == label:
            return pat
    raise AssertionError(f"phi label {label!r} not in catalog")


# ── secret class ─────────────────────────────────────────────────────


def test_aws_access_key_matches_canonical_shape() -> None:
    pat = _secret_for("aws-access-key")
    assert pat.search("AKIAIOSFODNN7EXAMPLE") is not None


def test_aws_access_key_does_not_match_lookalike() -> None:
    pat = _secret_for("aws-access-key")
    assert pat.search("AKIA12") is None  # too short
    assert pat.search("BKIAIOSFODNN7EXAMPLE") is None  # wrong prefix


def test_github_pat_matches_each_token_class() -> None:
    pat = _secret_for("github-pat")
    body = "A" * 36
    for prefix in ("ghp", "gho", "ghu", "ghs", "ghr"):
        assert pat.search(f"{prefix}_{body}") is not None, prefix


def test_github_pat_does_not_match_wrong_prefix_or_length() -> None:
    pat = _secret_for("github-pat")
    assert pat.search(f"foo_{'A' * 36}") is None
    assert pat.search("ghp_short") is None


def test_private_key_pem_matches_rsa_ec_openssh_dsa_variants() -> None:
    pat = _secret_for("private-key-pem")
    for variant in (
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN DSA PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----",
    ):
        assert pat.search(variant) is not None, variant


def test_private_key_pem_does_not_match_certificate_or_public_key() -> None:
    pat = _secret_for("private-key-pem")
    assert pat.search("-----BEGIN CERTIFICATE-----") is None
    assert pat.search("-----BEGIN PUBLIC KEY-----") is None


def test_jwt_matches_three_part_b64_shape() -> None:
    pat = _secret_for("jwt")
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0.signaturepart"
    assert pat.search(jwt) is not None


def test_jwt_does_not_match_two_part_or_malformed() -> None:
    pat = _secret_for("jwt")
    assert pat.search("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0ZXN0In0") is None
    assert pat.search("prefix-eyJ-suffix") is None


def test_azure_connection_string_matches_canonical_shape() -> None:
    pat = _secret_for("azure-storage-key")
    key = "x" * 88
    conn = f"DefaultEndpointsProtocol=https;AccountName=mystorage;AccountKey={key};"
    assert pat.search(conn) is not None


def test_azure_connection_string_does_not_match_short_key() -> None:
    pat = _secret_for("azure-storage-key")
    short = "x" * 20
    conn = f"DefaultEndpointsProtocol=https;AccountName=mystorage;AccountKey={short};"
    assert pat.search(conn) is None


# ── phi class (label-required) ───────────────────────────────────────


def test_mrn_with_label_matches_explicit_forms() -> None:
    pat = _phi_for("mrn-with-label")
    assert pat.search("MRN: 12345") is not None
    assert pat.search("medical record number = 1234567") is not None
    assert pat.search("Patient ID: 99999") is not None


def test_mrn_does_not_match_bare_digits() -> None:
    pat = _phi_for("mrn-with-label")
    assert pat.search("12345") is None
    assert pat.search("the file has 1234567 records") is None


def test_phi_field_label_matches_canonical_field_names() -> None:
    pat = _phi_for("phi-field-label")
    assert pat.search("patient_id:") is not None
    assert pat.search("date_of_birth:") is not None
    assert pat.search("ssn:") is not None
    assert pat.search("social_security_number =") is not None


def test_phi_field_label_does_not_match_legitimate_non_phi_names() -> None:
    pat = _phi_for("phi-field-label")
    assert pat.search("feature_id:") is None
    assert pat.search("created_date:") is None


# ── pan class ────────────────────────────────────────────────────────


def test_pan_candidate_regex_matches_13_19_digit_sequences() -> None:
    assert _PAN_CANDIDATE_RE.search("4111111111111111") is not None  # 16
    assert _PAN_CANDIDATE_RE.search("4111111111111") is not None  # 13
    assert _PAN_CANDIDATE_RE.search("4111111111111111119") is not None  # 19


def test_pan_candidate_regex_does_not_match_too_short_or_too_long() -> None:
    assert _PAN_CANDIDATE_RE.search("123456789012") is None  # 12
    assert _PAN_CANDIDATE_RE.search("12345678901234567890") is None  # 20


def test_luhn_valid_returns_true_for_known_valid_pan() -> None:
    """4111111111111111 is the canonical Visa test PAN; passes Luhn.
    5555555555554444 is the canonical Mastercard test PAN; passes Luhn."""
    assert _luhn_valid("4111111111111111") is True
    assert _luhn_valid("5555555555554444") is True


def test_luhn_valid_returns_false_for_invalid_check() -> None:
    """Last-digit-off-by-one is the simplest Luhn-invalid mutation."""
    assert _luhn_valid("4111111111111112") is False
    assert _luhn_valid("0000000000000000") is True  # all-zeros checksum is 0; valid by Luhn def
    assert _luhn_valid("0000000000000001") is False


def test_pan_id_preceded_filter_excludes_order_id_context() -> None:
    """`_is_id_preceded(content, start)` returns True iff an ID-class label
    sits within _PAN_CONTEXT_LOOKBACK chars before `start`."""
    content = "order_id: 4111111111111111"
    digit_start = content.index("4")
    assert _is_id_preceded(content, digit_start) is True


def test_pan_id_preceded_filter_returns_false_for_payment_value_context() -> None:
    content = "payment value 4111111111111111"
    digit_start = content.index("4")
    assert _is_id_preceded(content, digit_start) is False


def test_pan_id_preceded_filter_lookback_bounded() -> None:
    """An ID-class label more than _PAN_CONTEXT_LOOKBACK chars away must NOT
    be matched (locks the bounded-window contract)."""
    padding = " " * 50  # > _PAN_CONTEXT_LOOKBACK (30)
    content = f"order_id: {padding}4111111111111111"
    digit_start = content.index("4")
    assert _is_id_preceded(content, digit_start) is False


# ── catalog version pin ──────────────────────────────────────────────


def test_sensitive_catalog_version_is_pinned_string() -> None:
    assert isinstance(_SENSITIVE_CATALOG_VERSION, str)
    assert _SENSITIVE_CATALOG_VERSION == "v1"
