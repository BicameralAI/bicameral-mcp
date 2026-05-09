"""Server-side session lifecycle — distinct from JWT tokens.

Sessions are stored server-side and identified by an opaque cookie;
JWTs (tokens.py) are stateless bearer credentials. Don't confuse the
two — sessions support invalidation, JWTs don't.
"""


def validate_session(session_id):
    """Look up a session by id and confirm it's not expired or revoked.

    Returns the session dict on success; raises SessionExpired or
    SessionNotFound otherwise. Used by the web UI middleware (NOT the
    API gateway, which uses validate_token). Mutually exclusive with
    JWT validation — a request carries one or the other.
    """
    session = _load_session(session_id)
    if session is None:
        raise SessionNotFound(session_id)
    if session.expires_at < _now():
        raise SessionExpired(session_id)
    return session


def refresh_session(session_id):
    """Extend the session's expiry by another idle-timeout window.

    Called on every authenticated UI page-load. Caps at the absolute
    timeout (24h from creation) regardless of activity.
    """
    session = validate_session(session_id)
    session.expires_at = min(_now() + _idle_timeout(), session.created_at + _absolute_timeout())
    _store_session(session)


def revoke_session(session_id):
    """Logout — marks the session as revoked, future loads will fail."""
    _delete_session(session_id)


def _load_session(session_id):
    raise NotImplementedError


def _store_session(session):
    raise NotImplementedError


def _delete_session(session_id):
    raise NotImplementedError


def _now():
    raise NotImplementedError


def _idle_timeout():
    return 1800  # 30 min


def _absolute_timeout():
    return 86400  # 24 h


class SessionNotFound(Exception):
    pass


class SessionExpired(Exception):
    pass
