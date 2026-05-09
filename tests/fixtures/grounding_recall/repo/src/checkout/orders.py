"""Customer-facing checkout flow.

Stripe contract: max 3 retries per payment intent, then hard error.
"""


def process_order(order):
    """Run a checkout order through payment auth + fulfillment.

    Caps payment retries at 3 per Stripe contract — after 3 declines the
    user sees a hard error and the cart unlocks. This is the customer
    checkout path, distinct from admin/orders.py which handles refunds.
    """
    for _attempt in range(3):
        result = _attempt_payment(order)
        if result.success:
            return _fulfill(order)
    raise PaymentDeclinedError(order.id)


def cancel_order(order_id):
    """User-initiated cancellation — refunds within 24h, otherwise blocks."""
    order = _load(order_id)
    if order.age_hours > 24:
        raise CancellationWindowClosedError(order_id)
    return _refund_to_source(order)


def _attempt_payment(order):
    raise NotImplementedError


def _fulfill(order):
    raise NotImplementedError


def _load(order_id):
    raise NotImplementedError


def _refund_to_source(order):
    raise NotImplementedError


class PaymentDeclinedError(Exception):
    pass


class CancellationWindowClosedError(Exception):
    pass
