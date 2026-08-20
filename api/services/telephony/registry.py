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
from functools import wraps
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Literal,
    Optional,
    Type,
)

from pydantic import BaseModel

from api.errors.telephony_errors import log_telephony_error

if TYPE_CHECKING:
    from api.services.telephony.base import TelephonyProvider


@dataclass(frozen=True)
class ProviderUIOption:
    """One selectable value for a provider configuration field."""

    value: str
    label: str


@dataclass(frozen=True)
class ProviderUICondition:
    """Display a field only when another form value matches ``equals``."""

    field: str
    equals: Any


@dataclass(frozen=True)
class ProviderUIField:
    """One form field for the telephony configuration UI.

    Used to generate provider-specific config forms without per-provider
    UI code. Field semantics mirror the Pydantic config_request_cls.
    """

    name: str  # Must match the Pydantic field name on config_request_cls
    label: str
    # "text" | "password" | "textarea" | "string-array" | "number" | "boolean"
    # | "select" | "readonly". "readonly" reports a server-assigned value for
    # the customer to copy and collects no input; the form hides it until the
    # value exists, so it never reads as a blank the customer has to fill in.
    type: str
    required: bool = True
    sensitive: bool = False  # If true, mask when displaying stored value
    description: Optional[str] = None
    placeholder: Optional[str] = None
    options: Optional[List[ProviderUIOption]] = None
    visible_when: Optional[ProviderUICondition] = None
    section: Optional[str] = None
    feature_gate: Optional[str] = None


@dataclass(frozen=True)
class ProviderUIMetadata:
    """Display metadata for a provider's configuration form."""

    display_name: str
    fields: List[ProviderUIField]
    docs_url: Optional[str] = None


# How a customer gets phone service through this provider. Presentation only —
# nothing at runtime branches on it — but it is what lets the UI offer "connect
# a provider account" and "bring your own SIP" as two paths without naming any
# provider. Cloudonix is the first SIP connector, not the last.
#
#   "api" — the provider is the carrier. Create an account there, paste
#           credentials, and rent numbers from them.
#   "sip" — the customer brings their own carrier or PBX and connects it to a
#           SIP endpoint Dograh exposes. Credentials alone carry no calls.
ProviderConnectivity = Literal["api", "sip"]


@dataclass(frozen=True)
class SetupStep:
    """One thing the customer must do before a configuration can carry calls.

    ``blocks_outbound`` separates "not done yet" from "outbound calls will
    fail". Inbound-only steps are reported so the checklist is complete
    without making them gate the Phone Call button.
    """

    key: str
    title: str
    description: str
    complete: bool
    blocks_outbound: bool = False


@dataclass(frozen=True)
class ProviderSetupChecklist:
    """A configuration's setup progress, as computed by its provider.

    ``ready_for_outbound`` is derived rather than reported independently so a
    provider cannot claim readiness while leaving a blocking step incomplete.
    """

    ready_for_outbound: bool
    outbound_blocked_reason: Optional[str]
    steps: List[SetupStep]
    docs_url: Optional[str] = None

    @classmethod
    def from_steps(
        cls, steps: List[SetupStep], *, docs_url: Optional[str] = None
    ) -> "ProviderSetupChecklist":
        blocking = [s for s in steps if s.blocks_outbound and not s.complete]
        return cls(
            ready_for_outbound=not blocking,
            outbound_blocked_reason=blocking[0].description if blocking else None,
            steps=steps,
            docs_url=docs_url,
        )


def caller_id_only_checklist(
    *,
    display_name: str,
    has_caller_id: bool,
    docs_url: Optional[str] = None,
) -> ProviderSetupChecklist:
    """The checklist for a provider whose only outstanding step is a number.

    Every carrier-style provider needs the same thing beyond credentials — at
    least one number to dial out from — so they share this instead of each
    writing a resolver that says it again.
    """
    return ProviderSetupChecklist.from_steps(
        [
            SetupStep(
                key="caller_id",
                title="Add a phone number to use as caller ID",
                description=(
                    f"Outbound calls need a number to dial from. Add at least "
                    f"one number from your {display_name} account under Phone "
                    f"numbers below."
                ),
                complete=has_caller_id,
                blocks_outbound=True,
            )
        ],
        docs_url=docs_url,
    )


@dataclass(frozen=True)
class ConfigurationSetupState:
    """Stored facts a checklist resolver needs beyond the credentials blob.

    Passed in by the caller, which has already loaded the phone numbers and
    trunks, so resolvers stay pure and synchronous and a list endpoint doesn't
    pay an extra query per row.
    """

    active_phone_number_count: int
    inbound_routed_phone_number_count: int
    enabled_trunk_count: int = 0
    # Active numbers with no trunk chosen. Harmless while the configuration has
    # one trunk — the call path falls back to it — but once there are several
    # there is no way to tell which carrier authorised the number.
    unassigned_active_phone_number_count: int = 0


# Pure, synchronous: given the stored credentials and the state above, report
# what setup remains. No I/O — this runs on read paths, including list ones.
SetupChecklistResolver = Callable[
    [Dict[str, Any], ConfigurationSetupState], ProviderSetupChecklist
]


# Signature every provider's transport factory must satisfy.
# Provider-specific args (stream_sid, call_sid, channel_id, ...) are passed via **kwargs.
TransportFactory = Callable[..., Awaitable[Any]]

# Loader takes the raw config.value dict from the DB and returns a normalized
# config dict that the provider class accepts in its constructor.
ConfigLoader = Callable[[Dict[str, Any]], Dict[str, Any]]

# Optional async hook invoked at create/update time. Receives the credentials
# dict the route is about to persist plus the stored credentials being replaced
# (``None`` on create), and returns a (possibly modified) dict. Use for
# provider-side I/O that mutates credentials before save (e.g. an external
# resource that must exist by the time the row lands). I/O is allowed;
# ``config_loader`` is reserved for pure dict reshaping.
#
# The second argument is what lets a hook tell create from update. That matters
# for any value the customer mirrors into a system we do not control: minting
# one on create is setup, minting one on update silently invalidates whatever
# they configured on their side.
CredentialsPreprocessor = Callable[
    [Dict[str, Any], Dict[str, Any] | None], Awaitable[Dict[str, Any]]
]


@dataclass(frozen=True)
class TrunkDesiredState:
    """A trunk as the operator wants it, handed to the provider to realise.

    ``external_id`` is the provider's own identifier from the last successful
    apply, or None for a trunk that has never been provisioned. Providers use
    it to update the existing remote object rather than create a second one
    when the trunk is renamed.
    """

    name: str
    enabled: bool
    settings: Dict[str, Any]
    external_id: Optional[str] = None


# Provision or update the provider-side trunk, returning the external id to
# store. Raise HTTPException to abort the save. Called only when the provider
# declares ``trunk_settings_cls``.
TrunkApplier = Callable[[Dict[str, Any], TrunkDesiredState], Awaitable[Optional[str]]]

# Tear down (or deactivate) the provider-side trunk. Best-effort: a trunk the
# provider has already lost is not an error.
TrunkRemover = Callable[[Dict[str, Any], TrunkDesiredState], Awaitable[None]]


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
        ui_metadata: Optional form metadata used by the telephony-config
            UI to render a provider-specific form. Surfaced via
            ``GET /api/v1/telephony/providers/metadata``.
        server_managed_credential_fields: Credential keys produced by the
            save preprocessor rather than accepted from clients. Stored values
            are carried forward on update and supplied to the preprocessor.
        account_scoped_server_managed_credential_fields: Server-managed fields
            to discard when the provider account identifier changes, allowing
            the preprocessor to resolve replacements from the new account.
        setup_checklist_resolver: Optional hook reporting what setup a stored
            configuration still needs, and whether outbound calls can be
            placed at all. Providers whose credentials are sufficient on their
            own leave this unset and are treated as ready.
        connectivity: Whether the customer buys phone service from this
            provider ("api") or connects their own carrier to it ("sip").
            Drives how the UI offers the choice; nothing at runtime reads it.
        requires_caller_id: Whether an outbound call fails without at least one
            phone number on the configuration. True for every carrier, so it is
            the default and a new provider is honest about readiness for free.
            Only providers that can originate without a caller ID (a PBX
            dialling an extension, say) opt out. Ignored when the provider
            supplies its own ``setup_checklist_resolver``.
        trunk_settings_cls: Schema for a trunk's provider-specific settings.
            Providing it declares that this provider's calls travel over named
            carrier paths, which phone numbers are then assigned to. Paired
            with ``apply_trunk_on_save``/``remove_trunk_on_delete`` when those
            trunks also exist on the provider's side.

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
    ui_metadata: Optional[ProviderUIMetadata] = None
    # Credential field that uniquely identifies the provider account. Used to
    # (a) match an inbound webhook to the right org config when multiple configs
    # exist for the same provider, and (b) reject duplicate-account saves.
    # Empty string means the provider has no account-id concept (e.g. ARI).
    account_id_credential_field: str = ""
    # Fields generated by preprocess_credentials_on_save. They are never
    # trusted from request payloads and are preserved from the stored config
    # when an update re-submits the provider's editable credentials.
    server_managed_credential_fields: tuple[str, ...] = ()
    # Subset of server_managed_credential_fields whose values identify remote
    # resources within the account named by account_id_credential_field.
    account_scoped_server_managed_credential_fields: tuple[str, ...] = ()
    # Optional async hook to mutate credentials before they're persisted on
    # create/update. Called with the post-mask, post-merge credentials dict
    # and must return the dict to write. Raise HTTPException to abort save.
    preprocess_credentials_on_save: Optional[CredentialsPreprocessor] = None
    # Optional hook reporting outstanding setup for a stored configuration.
    # Unset means the provider is ready as soon as its credentials are saved.
    setup_checklist_resolver: Optional[SetupChecklistResolver] = None
    # Whether phone service is bought from this provider or brought to it.
    # Defaults to "api": most providers are carriers in their own right.
    connectivity: ProviderConnectivity = "api"
    # Whether outbound calls need a phone number on the configuration. True by
    # default so a provider is never reported ready when it cannot dial.
    requires_caller_id: bool = True
    # Schema for a trunk's provider-specific settings. Setting it is what
    # declares that this provider has trunks at all: the trunk endpoints,
    # the checklist's trunk step and the number-to-trunk picker all key off it.
    # Integrations that route through the provider account itself leave it
    # None, whether or not the vendor sells SIP trunking separately.
    trunk_settings_cls: Optional[Type[BaseModel]] = None
    # Optional hooks for providers whose trunks exist remotely as well as in
    # our table. Called on save and delete respectively.
    apply_trunk_on_save: Optional[TrunkApplier] = None
    remove_trunk_on_delete: Optional[TrunkRemover] = None

    @property
    def supports_trunks(self) -> bool:
        """Whether this provider's calls travel over named carrier paths."""
        return self.trunk_settings_cls is not None


_REGISTRY: Dict[str, ProviderSpec] = {}


def _instrument_validation_error_response(spec: ProviderSpec) -> None:
    """Emit classification whenever a provider renders an inbound rejection."""

    original = getattr(spec.provider_cls, "generate_validation_error_response", None)
    if not callable(original):
        return
    if getattr(original, "_dograh_failure_reporting_instrumented", False):
        return

    @wraps(original)
    def generate_validation_error_response(error_type, *args, **kwargs):
        log_telephony_error(error_type, provider=spec.name)
        return original(error_type, *args, **kwargs)

    generate_validation_error_response._dograh_failure_reporting_instrumented = True
    spec.provider_cls.generate_validation_error_response = staticmethod(
        generate_validation_error_response
    )


def register(spec: ProviderSpec) -> None:
    """Register a provider. Called once per provider at import time."""
    if spec.name in _REGISTRY:
        # Re-registration is benign as long as the spec is the same instance.
        # Otherwise it indicates a duplicate provider name, which is a bug.
        if _REGISTRY[spec.name] is not spec:
            raise ValueError(f"Provider '{spec.name}' is already registered")
        return
    _instrument_validation_error_response(spec)
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
