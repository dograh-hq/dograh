"""Stable failure classification for customer-owned and platform dependencies.

This module is intentionally independent of persistence and notification policy.  It
classifies a failure at the boundary where it is observed and emits a shadow-mode log
record that later phases can consume without changing runtime behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx
from loguru import logger
from pipecat.utils.run_context import get_current_org_id


class ErrorSource(str, Enum):
    LLM = "llm"
    TTS = "tts"
    STT = "stt"
    TELEPHONY = "telephony"
    TRANSPORT = "transport"
    WEBHOOK = "webhook"
    INTEGRATION = "integration"
    TOOL = "tool"
    KNOWLEDGE_BASE = "knowledge_base"
    PLATFORM = "platform"


class ErrorType(str, Enum):
    CONFIG_ERROR = "config_error"
    QUOTA_ERROR = "quota_error"
    PROVIDER_ERROR = "provider_error"
    SYSTEM_ERROR = "system_error"


class ErrorOwner(str, Enum):
    USER = "user"
    OPERATOR = "operator"


_SECRET_QUERY_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|auth[_-]?token|token|signature|secret|password|key)=)([^&#\s]+)"
)
_SECRET_NAME_PATTERN = (
    r"(?:x[-_])?(?:api[-_]?key|access[-_]?token|auth[-_]?token|authorization|"
    r"token|signature|secret|password|private[-_]?key|client[-_]?secret|"
    r"aws[-_]?access[-_]?key(?:[-_]?id)?|aws[-_]?secret[-_]?access[-_]?key)"
)
# Consume backslash escapes as a unit and allow literal newlines inside either
# quote style, so only an unescaped matching quote can terminate the value.
_QUOTED_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?P<prefix>[\"']?{_SECRET_NAME_PATTERN}[\"']?\s*[:=]\s*)"
    r"(?P<quoted>\"(?:\\[\s\S]|[^\"\\])*\"|'(?:\\[\s\S]|[^'\\])*')",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    rf"(?i)([\"']?{_SECRET_NAME_PATTERN}[\"']?\s*[:=]\s*)([^\s,;\"'}}]+)"
)
_AUTH_HEADER_RE = re.compile(r"(?i)(\b(?:basic|bearer)\s+)[A-Za-z0-9._~+/=-]+")
_URL_USERINFO_RE = re.compile(r"(?i)(\b(?:https?|wss?)://)([^/@\s]+)@")
_HTTP_STATUS_IN_MESSAGE_RE = re.compile(
    r"(?i)\b(?:http(?:\s+status)?|status(?:_code)?)\s*[:=]?\s*(\d{3})\b"
)
_MAX_MESSAGE_LENGTH = 4000
_FAILURE_METADATA_ATTR = "_dograh_failure_metadata"


def _redact_quoted_secret_assignment(match: re.Match[str]) -> str:
    quoted_value = match.group("quoted")
    quote = quoted_value[0]
    return f"{match.group('prefix')}{quote}[REDACTED]{quote}"


def redact_failure_message(message: object) -> str:
    """Remove common credentials from exception text before it reaches a log sink."""

    try:
        value = str(message)
    except Exception:
        value = f"<{type(message).__name__}>"

    value = _URL_USERINFO_RE.sub(r"\1[REDACTED]@", value)
    value = _SECRET_QUERY_RE.sub(r"\1[REDACTED]", value)
    value = _AUTH_HEADER_RE.sub(r"\1[REDACTED]", value)
    value = _QUOTED_SECRET_ASSIGNMENT_RE.sub(_redact_quoted_secret_assignment, value)
    value = _SECRET_ASSIGNMENT_RE.sub(r"\1[REDACTED]", value)
    if len(value) > _MAX_MESSAGE_LENGTH:
        value = f"{value[:_MAX_MESSAGE_LENGTH]}…"
    return value


def _enum_value(value: object) -> str:
    enum_value = getattr(value, "value", value)
    return str(enum_value)


def _normalize_provider(provider: object | None) -> str | None:
    if provider is None:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "-", _enum_value(provider).strip().lower())
    return normalized.strip("-") or None


def _normalize_code(code: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", code.strip().lower()).strip("-")
    return normalized or "platform-unknown"


def _resolve_error_owner(
    error_type: ErrorType, error_owner: ErrorOwner | str | None
) -> ErrorOwner:
    """Route responsibility without treating an external provider as an owner."""

    if error_type in (ErrorType.CONFIG_ERROR, ErrorType.QUOTA_ERROR):
        return ErrorOwner.USER
    if error_type == ErrorType.SYSTEM_ERROR or error_owner is None:
        return ErrorOwner.OPERATOR
    if isinstance(error_owner, ErrorOwner):
        return error_owner
    return ErrorOwner(error_owner)


@dataclass
class DograhFailure:
    source: ErrorSource
    type: ErrorType
    code: str
    internal_message: str
    external_message: str
    error_owner: ErrorOwner | str | None = None
    provider: str | None = None
    provider_error_code: str | None = None
    retryable: bool | None = None
    context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.source, ErrorSource):
            self.source = ErrorSource(self.source)
        if not isinstance(self.type, ErrorType):
            self.type = ErrorType(self.type)
        self.error_owner = _resolve_error_owner(self.type, self.error_owner)
        self.provider = _normalize_provider(self.provider)
        self.provider_error_code = _normalize_provider(self.provider_error_code)
        self.code = _normalize_code(self.code)
        self.internal_message = redact_failure_message(self.internal_message)
        self.external_message = redact_failure_message(self.external_message)


@dataclass(frozen=True)
class ServiceFailureMetadata:
    """Authoritative source/ownership attached by the service factory."""

    source: ErrorSource
    provider: str | None
    error_owner: ErrorOwner


def _external_message(source: ErrorSource, error_type: ErrorType) -> str:
    label = source.value.replace("_", " ")
    if error_type == ErrorType.CONFIG_ERROR:
        return f"Check your {label} configuration and credentials."
    if error_type == ErrorType.QUOTA_ERROR:
        return f"The {label} account has insufficient quota or credits."
    if error_type == ErrorType.PROVIDER_ERROR:
        return f"The external {label} service is temporarily unavailable."
    return f"Dograh encountered an internal error while processing {label}."


def _valid_http_status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 100 <= value <= 599 else None


def extract_http_status(exc: BaseException) -> int | None:
    """Extract an HTTP status without confusing vendor error codes for statuses."""

    for attr in ("status_code", "code", "status"):
        try:
            status = _valid_http_status(getattr(exc, attr, None))
        except Exception:
            status = None
        if status is not None:
            return status

    try:
        response = getattr(exc, "response", None)
        status = _valid_http_status(getattr(response, "status_code", None))
    except Exception:
        status = None
    return status


def extract_provider_error_code(
    exc: BaseException, *, http_status: int | None = None
) -> str | None:
    """Read an optional structured vendor code without interpreting its meaning.

    The code may make a failure easier to correlate with provider documentation,
    but it never influences ``error_type``. Unstructured exception text is not
    inspected here, so provider wording changes cannot change fault attribution.
    """

    for attr in ("error_code", "code"):
        try:
            value = getattr(exc, attr, None)
        except Exception:
            continue
        if value is None or isinstance(value, bool) or callable(value):
            continue
        value = getattr(value, "value", value)
        if not isinstance(value, str | int):
            continue

        raw_code = str(value).strip()
        if not raw_code or len(raw_code) > 128:
            continue
        if raw_code.isdigit() and http_status == int(raw_code):
            continue

        normalized = re.sub(r"[^a-z0-9]+", "-", raw_code.lower()).strip("-")
        if normalized:
            return normalized
    return None


def _type_for_http_status(status_code: int) -> tuple[ErrorType, bool | None]:
    if status_code in (401, 403):
        return ErrorType.CONFIG_ERROR, False
    if status_code in (402, 429):
        return ErrorType.QUOTA_ERROR, False
    if status_code == 408 or 500 <= status_code <= 599:
        return ErrorType.PROVIDER_ERROR, True
    if 400 <= status_code <= 499:
        return ErrorType.CONFIG_ERROR, False
    return ErrorType.SYSTEM_ERROR, None


def classify_http_response(
    status_code: int,
    message: object,
    *,
    source: ErrorSource,
    provider: object | None = None,
    provider_error_code: object | None = None,
    error_owner: ErrorOwner | str | None = None,
    context: dict[str, Any] | None = None,
) -> DograhFailure:
    """Classify an HTTP response at a known external boundary."""

    normalized_provider = _normalize_provider(provider)
    internal_message = redact_failure_message(message)

    if normalized_provider == "dograh":
        if status_code == 402:
            error_type, detail, retryable = (
                ErrorType.QUOTA_ERROR,
                "insufficient-credits",
                False,
            )
        else:
            error_type, detail, retryable = (
                ErrorType.SYSTEM_ERROR,
                str(status_code),
                500 <= status_code <= 599,
            )
        error_owner = (
            ErrorOwner.USER
            if error_type == ErrorType.QUOTA_ERROR
            else ErrorOwner.OPERATOR
        )
    else:
        error_type, retryable = _type_for_http_status(status_code)
        detail = str(status_code)

    code_provider = normalized_provider or source.value.replace("_", "-")
    return DograhFailure(
        source=source,
        type=error_type,
        code=f"{code_provider}-{detail}",
        internal_message=internal_message,
        external_message=_external_message(source, error_type),
        provider=normalized_provider,
        provider_error_code=_normalize_provider(provider_error_code),
        error_owner=error_owner,
        retryable=retryable,
        context=dict(context or {}),
    )


def classify_message(
    message: object,
    *,
    source: ErrorSource,
    provider: object | None = None,
    error_owner: ErrorOwner | str | None = None,
    context: dict[str, Any] | None = None,
) -> DograhFailure:
    """Classify string-only errors using protocol signals or the safe default."""

    internal_message = redact_failure_message(message)
    normalized_provider = _normalize_provider(provider)
    if normalized_provider == "dograh":
        error_type, detail, retryable = (
            ErrorType.SYSTEM_ERROR,
            "unknown",
            None,
        )
        error_owner = ErrorOwner.OPERATOR
    else:
        status_match = _HTTP_STATUS_IN_MESSAGE_RE.search(internal_message)
        if status_match:
            return classify_http_response(
                int(status_match.group(1)),
                internal_message,
                source=source,
                provider=normalized_provider,
                error_owner=error_owner,
                context=context,
            )
        error_type, detail, retryable = ErrorType.SYSTEM_ERROR, "unknown", None

    code_provider = normalized_provider or source.value.replace("_", "-")
    return DograhFailure(
        source=source,
        type=error_type,
        code=f"{code_provider}-{detail}",
        internal_message=internal_message,
        external_message=_external_message(source, error_type),
        provider=normalized_provider,
        error_owner=error_owner,
        retryable=retryable,
        context=dict(context or {}),
    )


def classify_exception(
    exc: BaseException,
    *,
    source: ErrorSource,
    provider: object | None = None,
    error_owner: ErrorOwner | str | None = None,
    context: dict[str, Any] | None = None,
) -> DograhFailure:
    """Pure exception classifier used by all execution seams."""

    failure_context = dict(context or {})
    failure_context.setdefault("exception_type", type(exc).__name__)
    traceback_frames: list[str] = []
    traceback_cursor = exc.__traceback__
    while traceback_cursor is not None:
        code = traceback_cursor.tb_frame.f_code
        traceback_frames.append(
            f"{code.co_filename}:{traceback_cursor.tb_lineno} in {code.co_name}"
        )
        traceback_cursor = traceback_cursor.tb_next
    if traceback_frames:
        failure_context.setdefault(
            "exception_traceback", "\n".join(traceback_frames[-20:])
        )

    try:
        detail = str(exc)
    except Exception:
        detail = ""
    internal_message = redact_failure_message(
        f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    )
    status_code = extract_http_status(exc)
    provider_error_code = extract_provider_error_code(exc, http_status=status_code)
    if status_code is not None:
        return classify_http_response(
            status_code,
            internal_message,
            source=source,
            provider=provider,
            provider_error_code=provider_error_code,
            error_owner=error_owner,
            context=failure_context,
        )

    normalized_provider = _normalize_provider(provider)
    if normalized_provider == "dograh":
        exception_identity = f"{type(exc).__module__}.{type(exc).__name__}".lower()
        transient = isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.ConnectError,
                ConnectionError,
                OSError,
            ),
        ) or ("websocket" in exception_identity and "closed" in exception_identity)
        detail_code = (
            "timeout"
            if isinstance(exc, httpx.TimeoutException | TimeoutError)
            else ("connection" if transient else "unknown")
        )
        return DograhFailure(
            source=source,
            type=ErrorType.SYSTEM_ERROR,
            code=f"dograh-{detail_code}",
            internal_message=internal_message,
            external_message=_external_message(source, ErrorType.SYSTEM_ERROR),
            provider="dograh",
            provider_error_code=provider_error_code,
            error_owner=ErrorOwner.OPERATOR,
            retryable=True if transient else None,
            context=failure_context,
        )

    status_match = _HTTP_STATUS_IN_MESSAGE_RE.search(internal_message)
    if status_match:
        return classify_http_response(
            int(status_match.group(1)),
            internal_message,
            source=source,
            provider=normalized_provider,
            error_owner=error_owner,
            context=failure_context,
        )

    exception_identity = f"{type(exc).__module__}.{type(exc).__name__}".lower()
    websocket_closed = "websocket" in exception_identity and any(
        marker in exception_identity for marker in ("closed", "connection")
    )
    transient_exception = websocket_closed or isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            ConnectionError,
            OSError,
        ),
    )
    error_type = (
        ErrorType.PROVIDER_ERROR if transient_exception else ErrorType.SYSTEM_ERROR
    )
    code_provider = normalized_provider or source.value.replace("_", "-")
    detail_code = "connection" if transient_exception else "unknown"
    if isinstance(exc, httpx.TimeoutException | TimeoutError):
        detail_code = "timeout"
    return DograhFailure(
        source=source,
        type=error_type,
        code=f"{code_provider}-{detail_code}",
        internal_message=internal_message,
        external_message=_external_message(source, error_type),
        provider=normalized_provider,
        provider_error_code=provider_error_code,
        error_owner=error_owner,
        retryable=True if transient_exception else None,
        context=failure_context,
    )


def annotate_failure_metadata(
    service: object,
    *,
    source: ErrorSource,
    provider: object | None,
    error_owner: ErrorOwner | str,
) -> object:
    """Attach authoritative provider metadata to a constructed Pipecat service."""

    try:
        setattr(
            service,
            _FAILURE_METADATA_ATTR,
            ServiceFailureMetadata(
                source=source,
                provider=_normalize_provider(provider),
                error_owner=(
                    error_owner
                    if isinstance(error_owner, ErrorOwner)
                    else ErrorOwner(error_owner)
                ),
            ),
        )
    except Exception:
        # Some third-party services may use slots or prohibit attributes.  Error
        # reporting must never make an otherwise-valid service unusable.
        pass
    return service


def failure_metadata_for_processor(processor: object | None) -> ServiceFailureMetadata:
    """Resolve source/provider, preferring factory metadata over registry hints."""

    if processor is None:
        return ServiceFailureMetadata(ErrorSource.PLATFORM, None, ErrorOwner.OPERATOR)
    metadata = getattr(processor, _FAILURE_METADATA_ATTR, None)
    if isinstance(metadata, ServiceFailureMetadata):
        return metadata

    source = ErrorSource.PLATFORM
    try:
        from pipecat.services.llm_service import LLMService
        from pipecat.services.stt_service import STTService
        from pipecat.services.tts_service import TTSService

        if isinstance(processor, STTService):
            source = ErrorSource.STT
        elif isinstance(processor, TTSService):
            source = ErrorSource.TTS
        elif isinstance(processor, LLMService):
            source = ErrorSource.LLM
    except Exception:
        pass

    class_name = type(processor).__name__.lower()
    if source == ErrorSource.PLATFORM:
        if "stt" in class_name:
            source = ErrorSource.STT
        elif "tts" in class_name:
            source = ErrorSource.TTS
        elif "llm" in class_name or "realtime" in class_name:
            source = ErrorSource.LLM

    provider = None
    try:
        from api.services.configuration.registry import (
            ServiceType,
            match_registered_provider,
        )

        service_types = {
            ErrorSource.STT: (ServiceType.STT,),
            ErrorSource.TTS: (ServiceType.TTS,),
            ErrorSource.LLM: (ServiceType.LLM, ServiceType.REALTIME),
        }.get(source)
        provider = _normalize_provider(
            match_registered_provider(class_name, service_types=service_types)
        )
    except Exception:
        # Failure reporting is best effort and must not interfere with an error
        # path if the configuration registry is unavailable during startup.
        pass

    # A Dograh-prefixed wrapper is not sufficient evidence that the dependency is
    # operator-owned; several BYOK adapters use that prefix. Only factory metadata
    # is authoritative for ownership.
    return ServiceFailureMetadata(source, provider, ErrorOwner.OPERATOR)


def _safe_log_context(context: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in context.items():
        normalized_key = str(key)
        if any(
            secret in normalized_key.lower()
            for secret in ("key", "token", "secret", "password", "authorization", "url")
        ):
            continue
        if value is None or isinstance(value, str | int | float | bool):
            safe[normalized_key] = (
                redact_failure_message(value) if isinstance(value, str) else value
            )
    return safe


def log_failure(
    failure: DograhFailure,
    *,
    level: str = "ERROR",
    **extra_context: Any,
) -> None:
    """Emit one structured and greppable shadow-mode record; never raise."""

    try:
        context = _safe_log_context({**failure.context, **extra_context})
        if "org_id" in context and "organization_id" not in context:
            context["organization_id"] = context.pop("org_id")
        context.setdefault("organization_id", get_current_org_id())
        context = {key: value for key, value in context.items() if value is not None}

        bound = logger.bind(
            error_source=failure.source.value,
            error_type=failure.type.value,
            error_owner=failure.error_owner.value,
            error_code=failure.code,
            provider=failure.provider,
            provider_error_code=failure.provider_error_code,
            retryable=failure.retryable,
            external_message=failure.external_message,
            classified=True,
            classification_mode="explicit",
            **context,
        )
        bound.opt(depth=1).log(
            level.upper(),
            "DOGRAH_FAILURE [src={} type={} code={}] [owner={}] {}",
            failure.source.value,
            failure.type.value,
            failure.code,
            failure.error_owner.value,
            failure.internal_message,
        )
    except Exception:
        # Deliberately do not attempt a fallback log: the logger itself may be what
        # failed, and classification must never mask the original error path.
        return
