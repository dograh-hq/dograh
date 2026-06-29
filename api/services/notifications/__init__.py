"""Outbound notification services (email, etc.).

Currently exposes lead-notification email delivery used by the public
``/api/v1/leads`` routes. Delivery is best-effort: when SMTP is not
configured the send is skipped (logged) rather than raising, so the
client-facing forms never break.
"""

from .email import send_lead_notification

__all__ = ["send_lead_notification"]
