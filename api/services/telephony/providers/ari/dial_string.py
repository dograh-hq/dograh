"""Turning a destination into the dial string ARI originates on.

Asterisk needs a channel technology and a route, not a number: ``PJSIP/1001``
names an endpoint called ``1001``, which is why handing a PSTN number straight
to the default template fails with "Allocation failed" on any install whose
endpoints are trunks rather than one per subscriber. Where the number has to go
is a property of the customer's dialplan, so the configuration carries the
template and this module only applies it.
"""

from __future__ import annotations

import re

# What a configuration gets when it does not choose. Right for a PBX whose
# endpoints are named after the extensions being dialled, which is the shape
# every ARI configuration written before templates existed relies on.
DEFAULT_DIAL_STRING_TEMPLATE = "PJSIP/{number}"

# A leading "<Tech>/" — PJSIP/, SIP/, Local/, IAX2/, DAHDI/. Asterisk accepts
# any registered channel driver here, so this matches the shape rather than a
# list of names we would have to keep current.
_TECH_PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*/")


def has_channel_technology(destination: str) -> bool:
    """Whether ``destination`` already names the channel driver to dial with."""
    return bool(_TECH_PREFIX_RE.match(destination.strip()))


def build_dial_string(destination: str, template: str) -> str:
    """Render the ARI ``endpoint`` for ``destination``.

    A destination that names its own channel technology is dialled as written,
    which is what lets a single row address a specific trunk or a Local channel
    regardless of what the configuration's template does with plain numbers.
    """
    destination = destination.strip()
    if has_channel_technology(destination):
        return destination
    return (template or DEFAULT_DIAL_STRING_TEMPLATE).format(number=destination)
