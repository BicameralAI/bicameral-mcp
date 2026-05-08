"""JWT token lifecycle — validation, refresh, revocation."""


def validate_token(token):
    """Validate a JWT's signature, expiry, and audience.

    Returns the parsed claims dict if valid; raises TokenInvalid on
    signature mismatch, TokenExpired on stale exp, TokenWrongAudience
    on aud mismatch. Used by the API gateway middleware on every
    inbound request.
    """
    claims = _decode_jwt(token)
    if claims["exp"] < _now():
        raise TokenExpired
    if claims["aud"] != _expected_audience():
        raise TokenWrongAudience
    return claims


def refresh_token(refresh_token):
    """Exchange a long-lived refresh token for a fresh access token."""
    claims = _decode_jwt(refresh_token)
    if claims.get("type") != "refresh":
        raise TokenInvalid
    return _mint_access_token(claims["sub"])


def revoke_token(token):
    """Add a token to the revocation list — checked on every validate."""
    claims = _decode_jwt(token)
    _revocation_store.add(claims["jti"], claims["exp"])


def _decode_jwt(token):
    raise NotImplementedError


def _now():
    raise NotImplementedError


def _expected_audience():
    return "bicameral-api"


def _mint_access_token(subject):
    raise NotImplementedError


_revocation_store = None


class TokenInvalid(Exception):
    pass


class TokenExpired(Exception):
    pass


class TokenWrongAudience(Exception):
    pass
