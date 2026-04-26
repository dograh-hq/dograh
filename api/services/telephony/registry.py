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
    from fastapi import APIRouter

    from api.services.pipecat.audio_config import AudioConfig
    from api.services.telephony.base import TelephonyProvider


# Signature every provider's transport factory must satisfy.
# Provider-specific args (stream_sid, call_sid, channel_id, ...) are passed via **kwargs.
TransportFactory = Callable[..., Awaitable[Any]]

# Loader takes the raw config.value dict from the DB and returns a normalized
# config dict that the provider class accepts in its constructor.
ConfigLoader = Callable[[Dict[str, Any]], Dict[str, Any]]


@dataclass(frozen=True)
class ProviderSpec:
    """Everything needed to plug a telephony provider into the platform.

    Attributes:
        name: Stable identifier (e.g., "twilio"). Used as the discriminator in
            stored config JSON and as the WorkflowRunMode value.
        provider_cls: The TelephonyProvider subclass.
        config_loader: Normalizes raw stored config into the dict shape the
            provider constructor expects. Replaces the if/elif chain in the
            old factory.load_telephony_config().
        transport_factory: Async callable that creates the pipecat transport
            for an accepted WebSocket. Provider-specific kwargs (stream_sid,
            call_sid, etc.) are forwarded as ``**kwargs``.
        audio_config: The AudioConfig this provider's wire format requires.
        config_request_cls: Pydantic model for incoming save requests.
        config_response_cls: Pydantic model for outgoing (masked) responses.
        router: Optional FastAPI router exposing the provider's webhooks /
            status callbacks / answer URLs. Mounted under
            ``/api/v1/telephony`` by ``api.routes.telephony`` at startup.
    """

    name: str
    provider_cls: Type["TelephonyProvider"]
    config_loader: ConfigLoader
    transport_factory: TransportFactory
    audio_config: "AudioConfig"
    config_request_cls: Type[BaseModel]
    config_response_cls: Type[BaseModel]
    router: Optional["APIRouter"] = None


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
