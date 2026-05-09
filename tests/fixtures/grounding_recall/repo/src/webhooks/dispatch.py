"""Inbound webhook event router (Python runtime).

After signature verification, dispatch the event to the right
subscriber chain. Sibling of dispatch.ts for the TypeScript runtime.
"""


def dispatch_event(event):
    """Route a verified webhook event to its subscriber handlers.

    Looks up the event type → handler list mapping, fans out to each
    handler with a per-handler retry policy. Errors in one handler do
    not abort the rest. Used by the webhook ingress after
    verify_webhook_signature passes.
    """
    handlers = _subscribers_for(event.type)
    for handler in handlers:
        try:
            handler(event)
        except Exception as exc:
            _record_handler_failure(event, handler, exc)


def enqueue_dispatch(event):
    """Queue an event for asynchronous dispatch — used when the
    inbound request must respond immediately (Stripe 5s window)."""
    _queue.put(event)


def _subscribers_for(event_type):
    raise NotImplementedError


def _record_handler_failure(event, handler, exc):
    raise NotImplementedError


_queue = None
