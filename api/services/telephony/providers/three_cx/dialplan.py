"""Builds Asterisk dialplan rows for a 3CX trunk.

Two contexts are generated per trunk:

* ``<endpoint_id>-outbound`` — dialed by the Stasis app when Dograh
  originates a call. Honours ``strip_prefix`` by translating the regex
  to an Asterisk pattern-match exten and using ``${EXTEN:N}`` to skip
  the matched prefix on the way out.
* ``<endpoint_id>-inbound`` — the ``context=`` on the PJSIP endpoint.
  Routes any incoming call from the trunk straight into the Stasis
  app so Dograh's ari_manager picks it up.

We deliberately keep the dialplan minimal — anything fancier (IVR,
office-hours routing) belongs in a hand-written context the admin can
include before/after this generated one.
"""

from __future__ import annotations

import re
from typing import List, Tuple

# Asterisk understands its own ad-hoc pattern syntax — not POSIX/PCRE
# regex. We translate the small subset Italian deployments need
# (``^\+39``) and fall back to a verbatim match when the prefix is empty.
_SUPPORTED_PREFIX_RE = re.compile(r"^\^\\?\+(\d+)$")


def _prefix_to_pattern(strip_prefix: str) -> Tuple[str, int]:
    """Translate a small regex into (Asterisk extension pattern, chars-to-skip).

    Examples
    --------
    >>> _prefix_to_pattern("^\\+39")
    ('_+39N.', 3)
    >>> _prefix_to_pattern("")
    ('_X.', 0)
    """
    if not strip_prefix:
        return ("_X.", 0)
    m = _SUPPORTED_PREFIX_RE.match(strip_prefix)
    if not m:
        raise ValueError(
            f"Unsupported strip_prefix regex {strip_prefix!r}. "
            f"Only literal '^\\+<digits>' is supported."
        )
    digits = m.group(1)
    return (f"_+{digits}N.", len(digits) + 1)  # +1 for the literal '+'


def build_dialplan_rows(
    *,
    endpoint_id: str,
    extension: str,
    stasis_app: str,
    strip_prefix: str,
) -> List[dict]:
    """Return ARA ``extensions`` rows for this trunk's inbound + outbound contexts."""
    pattern, skip = _prefix_to_pattern(strip_prefix)
    dest = f"${{EXTEN:{skip}}}" if skip else "${EXTEN}"

    outbound_context = f"{endpoint_id}-outbound"
    inbound_context = f"{endpoint_id}-inbound"

    return [
        {
            "context": outbound_context,
            "exten": pattern,
            "priority": 1,
            "app": "Dial",
            "appdata": f"PJSIP/{dest}@{endpoint_id},60",
        },
        {
            "context": inbound_context,
            "exten": extension,
            "priority": 1,
            "app": "Stasis",
            "appdata": f"{stasis_app},inbound,{endpoint_id}",
        },
        {
            "context": inbound_context,
            "exten": "_X.",
            "priority": 1,
            "app": "Stasis",
            "appdata": f"{stasis_app},inbound,{endpoint_id}",
        },
    ]


def outbound_context_for(endpoint_id: str) -> str:
    """The dialplan context name the Stasis app should Originate into."""
    return f"{endpoint_id}-outbound"
