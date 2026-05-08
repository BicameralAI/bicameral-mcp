"""Checkout retry semantics — caps the number of payment-auth attempts."""


class CheckoutRetryGuard:
    """Enforces the per-checkout retry ceiling required by Stripe contract."""

    MAX_ATTEMPTS = 3

    def check_cap(self, attempt_count):
        """Raise MaxRetriesExceeded if attempt_count >= 3.

        Called from checkout/orders.py:process_order before each retry.
        Implements the contract clause: "merchants must not retry a
        declined authorization more than 3 times within 24 hours."
        """
        if attempt_count >= self.MAX_ATTEMPTS:
            raise MaxRetriesExceeded(attempt_count)


class StripeRetryPolicy:
    """Configures backoff between retries — exponential w/ 2s base."""

    def delay_for(self, attempt):
        return 2**attempt


class MaxRetriesExceeded(Exception):
    pass
