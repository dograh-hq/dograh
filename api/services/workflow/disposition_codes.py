"""Canonical catalog of call disposition codes.

``gathered_context.mapped_call_disposition`` is what the Disposition column
and the ``dispositionCode`` filter both read, so this module enumerates every
value the platform can write there. Keeping the list here — rather than
duplicated in the frontend — is what stops the filter dropdown from drifting
behind the code that produces the dispositions.

Two writers feed that field:

* the pipeline, via ``PipecatEngine.end_call_with_reason`` /
  ``record_call_disposition`` — an ``EndTaskReason`` value;
* the telephony status callback, via ``status_processor`` and
  ``mark_workflow_run_failed`` — a ``TelephonyCallStatus`` value, for calls
  that never connected.

Organizations that map dispositions to their own codes (``XFER``, ``DNC``, …)
produce values outside this catalog. Those are learned per workflow by
``add_call_disposition_code`` and merged in by
``get_organization_disposition_codes``.
"""

from pipecat.utils.enums import EndTaskReason

from api.enums import TelephonyCallStatus

# EndTaskReason values that actually reach a persisted disposition. The enum
# also carries members used purely as control-flow signals; those are excluded
# so the dropdown only offers codes a run can really carry.
_PIPELINE_DISPOSITIONS: tuple[str, ...] = (
    EndTaskReason.END_CALL_TOOL_REASON.value,
    EndTaskReason.USER_HANGUP.value,
    EndTaskReason.USER_QUALIFIED.value,
    EndTaskReason.USER_DISQUALIFIED.value,
    EndTaskReason.CALL_DURATION_EXCEEDED.value,
    EndTaskReason.USER_IDLE_MAX_DURATION_EXCEEDED.value,
    # External-PBX transfer stamps CALL_TRANSFERRED; the serializer-driven
    # transfer path ends the pipeline with TRANSFER_CALL, which becomes the
    # disposition when nothing else was recorded first.
    EndTaskReason.CALL_TRANSFERRED.value,
    EndTaskReason.TRANSFER_CALL.value,
    EndTaskReason.VOICEMAIL_DETECTED.value,
    EndTaskReason.SYSTEM_CANCELLED.value,
    EndTaskReason.SYSTEM_CONNECT_ERROR.value,
    EndTaskReason.PIPELINE_ERROR.value,
    EndTaskReason.UNEXPECTED_ERROR.value,
    EndTaskReason.UNKNOWN.value,
)

# Statuses written when the call never reached the pipeline.
_TELEPHONY_DISPOSITIONS: tuple[str, ...] = (
    TelephonyCallStatus.NO_ANSWER.value,
    TelephonyCallStatus.BUSY.value,
    TelephonyCallStatus.FAILED.value,
    TelephonyCallStatus.CANCELED.value,
    TelephonyCallStatus.ERROR.value,
)

SYSTEM_DISPOSITION_CODES: tuple[str, ...] = (
    _PIPELINE_DISPOSITIONS + _TELEPHONY_DISPOSITIONS
)
