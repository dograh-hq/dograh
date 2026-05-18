"""Provider registry for telephony.

Each provider package registers itself by importing this module and calling
``register(ProviderSpec(...))`` from its ``__init__.py``. Consumers (factory,
audio config, run_pipeline, schemas) look up providers through ``get(name)``
or iterate via ``all_specs()`` instead of branching on provider name.

Adding a new provider should not require any edit outside its own folder
plus a single import line in ``providers/__init__.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Type,
)

from pydantic import BaseModel

if TYPE_CHECKING:
    from api.services.telephony.base import TelephonyProvider


@dataclass(frozen=True)
class ProviderUIField:
    """One form field for the telephony configuration UI.

    Used to generate provider-specific config forms without per-provider
    UI code. Field semantics mirror the Pydantic config_request_cls.
    """

    name: str  # Must match the Pydantic field name on config_request_cls
    label: str
    type: str  # "text" | "password" | "textarea" | "string-array" | "number"
    required: bool = True
    sensitive: bool = False  # If true, mask when displaying stored value
    description: Optional[str] = None
    placeholder: Optional[str] = None


@dataclass(frozen=True)
class ProviderUIMetadata:
    """Display metadata for a provider's configuration form."""

    display_name: str
    fields: List[ProviderUIField]
    docs_url: Optional[str] = None


# Signature every provider's transport factory must satisfy.
# Provider-specific args (stream_sid, call_sid, channel_id, ...) are passed via **kwargs.
TransportFactory = Callable[..., Awaitable[Any]]

# Loader takes the raw config.value dict from the DB and returns a normalized
# config dict that the provider class accepts in its constructor.
ConfigLoader = Callable[[Dict[str, Any]], Dict[str, Any]]

# Optional async hook invoked at create/update time. Receives the credentials
# dict the route is about to persist and returns a (possibly modified) dict.
# Use for provider-side I/O that mutates credentials before save (e.g. an
# external resource that must exist by the time the row lands). I/O is
# allowed; ``config_loader`` is reserved for pure dict reshaping.
CredentialsPreprocessor = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass(frozen=True)
class ProviderSpec:
    """Everything needed to plug a telephony provider into the platform.

    Attributes:
        name: Stable identifier (e.g., "twilio"). Used as the discriminator in
            stored config JSON and as the WorkflowRunMode value.
        provider_cls: The TelephonyProvider subclass.
        config_loader: Normalizes raw stored config into the dict shape the
            provider constructor expects. Replaces the old factory if/elif
            chain.
        transport_factory: Async callable that creates the pipecat transport
            for an accepted WebSocket. Provider-specific kwargs (stream_sid,
            call_sid, etc.) are forwarded as ``**kwargs``.
        transport_sample_rate: Wire-format audio sample rate this provider
            uses (e.g. 8000 for Twilio/Plivo, 16000 for Vonage). The pipecat
            layer derives the full ``AudioConfig`` from this.
        config_request_cls: Pydantic model for incoming save requests.
        config_response_cls: Pydantic model for outgoing (masked) responses.
        ui_metadata: Optional form metadata used by the telephony-config
            UI to render a provider-specific form. Surfaced via
            ``GET /api/v1/telephony/providers/metadata``.

    Note: provider routes (webhooks, status callbacks, answer URLs) are
    NOT carried on the spec. They live in
    ``providers/<name>/routes.py`` and are loaded on-demand by
    ``api.routes.telephony`` via ``importlib`` so route handlers (which
    can have deep dependency chains into campaign/db code) don't get
    pulled in just because someone imported a TelephonyProvider type.
    """

    name: str
    provider_cls: Type["TelephonyProvider"]
    config_loader: ConfigLoader
    transport_factory: TransportFactory
    transport_sample_rate: int
    config_request_cls: Type[BaseModel]
    config_response_cls: Type[BaseModel]
    ui_metadata: Optional[ProviderUIMetadata] = None
    # Credential field that uniquely identifies the provider account. Used to
    # (a) match an inbound webhook to the right org config when multiple configs
    # exist for the same provider, and (b) reject duplicate-account saves.
    # Empty string means the provider has no account-id concept (e.g. ARI).
    account_id_credential_field: str = ""
    # Optional async hook to mutate credentials before they're persisted on
    # create/update. Called with the post-mask, post-merge credentials dict
    # and must return the dict to write. Raise HTTPException to abort save.
    preprocess_credentials_on_save: Optional[CredentialsPreprocessor] = None


_REGISTRY: Dict[str, ProviderSpec] = {}


def register(spec: ProviderSpec) -> None:
    """Register a provider. Called once per provider at import time."""
    if spec.name in _REGISTRY:
        # Re-registration is benign as long as the spec is the same instance.
        # Otherwise it indicates a duplicate provider name, which is a bug.
        if _REGISTRY[spec.name] is not spec:
            raise ValueError(f"Provider '{spec.name}' is already registered")
        return
    _REGISTRY[spec.name] = spec


def get(name: str) -> ProviderSpec:
    """Look up a registered provider by name."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown telephony provider: {name}") from None


def get_optional(name: str) -> Optional[ProviderSpec]:
    """Look up a registered provider by name, returning None if not registered."""
    return _REGISTRY.get(name)


def all_specs() -> List[ProviderSpec]:
    """Return all registered providers in name-sorted order (stable iteration)."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def names() -> Iterable[str]:
    """Return all registered provider names."""
    return sorted(_REGISTRY)
