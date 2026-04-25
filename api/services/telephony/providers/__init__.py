"""Telephony provider implementations.

Importing this module triggers each provider package to register itself
with ``api.services.telephony.registry``. Adding a new provider requires
exactly one new line below — no edits to factory, audio_config, schemas,
or run_pipeline.
"""

from api.services.telephony.providers import (  # noqa: F401  -- import for side effects (registration)
    ari,
    cloudonix,
    plivo,
    telnyx,
    twilio,
    vobiz,
    vonage,
)
