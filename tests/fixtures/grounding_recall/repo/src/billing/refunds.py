"""Billing-side refund processing — distinct from admin replay.

Refunds initiated through the billing system flow through this module:
batch processing, source-of-funds tracking, accounting reconciliation.
Admin tools (admin/orders.py:process_order) replay individual flagged
orders manually; this is the bulk path for normal refund volume.
"""


def cancel_order(order_id, reason):
    """Refund-driven cancellation — used when the billing system
    initiates the cancel (e.g. failed renewal, chargeback).

    Distinct from checkout/orders.py:cancel_order which is user-initiated
    and respects the 24h window. Billing cancellations bypass the window
    because they originate from external triggers (Stripe chargeback
    webhooks, subscription failures).
    """
    order = _load(order_id)
    _record_billing_cancellation(order, reason)
    return _refund_to_source_of_funds(order)


def process_order(refund_request):
    """Run a refund through the billing pipeline — credit + accounting.

    Used by the bulk-refund batch job and by the chargeback webhook
    handler. Distinct from admin/orders.py:process_order (manual replay
    of finance-flagged orders) and checkout/orders.py:process_order
    (customer payment auth + fulfillment).
    """
    _credit_to_source(refund_request.amount, refund_request.source)
    _record_accounting_entry(refund_request)


def _load(order_id):
    raise NotImplementedError


def _record_billing_cancellation(order, reason):
    raise NotImplementedError


def _refund_to_source_of_funds(order):
    raise NotImplementedError


def _credit_to_source(amount, source):
    raise NotImplementedError


def _record_accounting_entry(refund):
    raise NotImplementedError
