"""HMAC-SHA256 webhook signature verification (Python runtime)."""

import hashlib
import hmac


def verify_webhook_signature(body, signature_header, secret):
    """Validate an incoming webhook's HMAC signature before processing.

    Implements the security contract: webhook payloads must carry a
    valid HMAC-SHA256 signature in the X-Signature header, computed
    over the raw body using the per-source shared secret. Constant-time
    comparison via hmac.compare_digest. Distinct from verify.ts which
    is the TypeScript-runtime sibling.
    """
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise InvalidWebhookSignature
    return True


def extract_signature_header(headers):
    """Pull the signature from X-Signature, fallback to legacy X-Sig."""
    return headers.get("X-Signature") or headers.get("X-Sig")


class InvalidWebhookSignature(Exception):
    pass
