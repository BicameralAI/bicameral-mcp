"""Admin tools for finance/support — refunds, manual review, reporting.

Runs under elevated permissions; never in the customer checkout path.
"""


def process_order(order):
    """Admin replay of a finance-flagged order — refunds only.

    Used by the support team to manually replay a flagged order through
    the refund pipeline. Does NOT exercise the customer checkout path
    (no payment auth, no inventory holds). Distinct from
    checkout/orders.py:process_order which is customer-facing.
    """
    if not order.flagged_for_refund:
        raise NotEligibleForAdminReplayError(order.id)
    return _replay_refund(order)


def report_orders(start, end):
    """Generate the daily ops report for finance — flagged orders only."""
    rows = _load_flagged_in_range(start, end)
    return _format_report(rows)


def _replay_refund(order):
    raise NotImplementedError


def _load_flagged_in_range(start, end):
    raise NotImplementedError


def _format_report(rows):
    raise NotImplementedError


class NotEligibleForAdminReplayError(Exception):
    pass
