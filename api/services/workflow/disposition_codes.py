"""Canonical catalog of call disposition codes.

``gathered_context.mapped_call_disposition`` is what the Disposition column
and the ``dispositionCode`` filter both read, so this module enumerates every
value the platform can write there. Keeping the list here — rather than
duplicated in the frontend — is what stops the filter dropdown from drifting
behind the code that produces the dispositions.

Three kinds of built-in value feed that field:

* the pipeline, via ``PipecatEngine.end_call_with_reason`` /
  ``set_call_disposition`` — an ``EndTaskReason`` value, or one of the
  engine's own end reasons that live outside that enum
  (``api.services.workflow.end_reasons``);
* the telephony status callback, via ``status_processor`` and
  ``mark_workflow_run_failed`` — a ``TelephonyCallStatus`` value, for calls
  that never connected;
* terminal variable extraction — one of Dograh's default business outcomes,
  such as ``do_not_call`` or ``callback_requested``.

Organizations that map dispositions to their own codes (``XFER``, ``DNC``, …)
produce values outside this catalog. Those are learned per workflow by
``add_call_disposition_code`` and merged in by
``get_organization_disposition_codes``.
"""

from pipecat.utils.enums import EndTaskReason

from api.enums import TelephonyCallStatus
from api.services.workflow.disposition_extraction import DEFAULT_DISPOSITION_CODES
from api.services.workflow.end_reasons import VOICEMAIL_MESSAGE_LEFT

# Keep this derived directly from the enum so every pipeline disposition is
# available to clients without maintaining a second list.
END_TASK_REASON_DISPOSITION_CODES: tuple[str, ...] = tuple(
    reason.value for reason in EndTaskReason
)

# Pipeline end reasons stamped by the engine that are not EndTaskReason
# members (that enum lives in the pipecat submodule).
_ENGINE_DISPOSITIONS: tuple[str, ...] = (VOICEMAIL_MESSAGE_LEFT,)

# Statuses written when the call never reached the pipeline.
_TELEPHONY_DISPOSITIONS: tuple[str, ...] = (
    TelephonyCallStatus.NO_ANSWER.value,
    TelephonyCallStatus.BUSY.value,
    TelephonyCallStatus.FAILED.value,
    TelephonyCallStatus.CANCELED.value,
    TelephonyCallStatus.ERROR.value,
)

SYSTEM_DISPOSITION_CODES: tuple[str, ...] = tuple(
    # Preserve the useful grouping/order while preventing a duplicate row if a
    # future Pipecat reason or telephony status adopts a business-outcome name.
    dict.fromkeys(
        END_TASK_REASON_DISPOSITION_CODES
        + _ENGINE_DISPOSITIONS
        + _TELEPHONY_DISPOSITIONS
        + DEFAULT_DISPOSITION_CODES
    )
)
