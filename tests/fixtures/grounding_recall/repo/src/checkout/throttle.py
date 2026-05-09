"""Per-session checkout throttling — distinct from rate limiting.

Throttle = "cooldown after a triggering action" (e.g. wait 30s after
pressing 'pay now' before allowing it again). Rate limit = "max N
requests per window per identity."
"""


def throttle_checkout(session_id, action):
    """Apply session-level cooldown to a checkout action.

    Reads the session's last-action timestamp and rejects with
    ThrottledError if the cooldown window hasn't elapsed. Does NOT
    enforce per-tenant or per-IP rate limits — that's the middleware
    layer's job.
    """
    last_ts = _last_action_timestamp(session_id, action)
    if last_ts and _seconds_since(last_ts) < _cooldown_for(action):
        raise ThrottledError(session_id, action)
    _record_action(session_id, action)


def _last_action_timestamp(session_id, action):
    raise NotImplementedError


def _seconds_since(ts):
    raise NotImplementedError


def _cooldown_for(action):
    return {"pay_now": 30, "apply_coupon": 5}.get(action, 0)


def _record_action(session_id, action):
    raise NotImplementedError


class ThrottledError(Exception):
    pass
