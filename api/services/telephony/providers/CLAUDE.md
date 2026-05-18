# Telephony Providers

Each subdirectory here is a self-registering telephony provider. Adding a new one should touch this folder plus **exactly two lines** outside it. If a change you're making requires editing `factory.py`, `audio_config.py`, `run_pipeline.py`, `routes/telephony.py`, or any frontend file, stop — that's a smell. Push the variation through the registry instead.

## Anatomy of a provider package

```
providers/<name>/
├── __init__.py     # Required. Builds + register()s ProviderSpec
├── config.py       # Required. Pydantic Request + Response, both with `provider: Literal["<name>"]`
├── provider.py     # Required. TelephonyProvider subclass
├── transport.py    # Required. async create_transport(...) -> FastAPIWebsocketTransport
├── serializers.py  # Optional but conventional. Re-export from pipecat
├── routes.py       # Optional. APIRouter mounted lazily under /api/v1/telephony
└── strategies.py   # Optional. Transfer/Hangup strategies for the frame serializer
```

Every file is provider-local. Nothing here imports another provider package.

## The two edits outside this folder

After creating `providers/<name>/`:

1. `providers/__init__.py` — add `<name>` to the import-for-side-effects list. Registration runs at import time.
2. `api/schemas/telephony_config.py` — import `<Name>ConfigurationRequest`/`Response` and add the request to the `TelephonyConfigRequest` `Union[...]` and the response as an optional field on `TelephonyConfigurationResponse`.

If you find yourself editing anything else, re-read the registry plumbing first:

| Want to change... | Source of truth |
| --- | --- |
| Outbound provider lookup | `factory.get_default_telephony_provider`, `get_telephony_provider_by_id`, and `get_telephony_provider_for_run` read `registry.get(name).provider_cls` |
| Stored credentials → constructor dict | `ProviderSpec.config_loader` |
| Audio sample rate / VAD rate | `ProviderSpec.transport_sample_rate` (full `AudioConfig` is built in `pipecat/audio_config.py::create_audio_config`) |
| Which transport runs in `run_pipeline_telephony` | `ProviderSpec.transport_factory` |
| Save-request validation + masked response shape | `ProviderSpec.config_request_cls` / `config_response_cls` |
| Form rendered by the telephony-config UI | `ProviderSpec.ui_metadata` (`ProviderUIField` list) |
| Which credential masks on read | `ui_metadata.fields[*].sensitive=True` (no separate list) |
| Inbound webhook → config row matching | `ProviderSpec.account_id_credential_field` |
| HTTP routes (answer URL, status callbacks) | `providers/<name>/routes.py` (auto-mounted via `importlib`) |

## ProviderSpec — minimum viable shape

```python
SPEC = ProviderSpec(
    name="<name>",                                  # registry key, WorkflowRunMode value, stored discriminator
    provider_cls=YourProvider,
    config_loader=_config_loader,                   # raw dict from DB → constructor dict
    transport_factory=create_transport,
    transport_sample_rate=8000,                     # wire-format rate; pipecat derives the full AudioConfig
    config_request_cls=YourProviderConfigurationRequest,
    config_response_cls=YourProviderConfigurationResponse,
    ui_metadata=ProviderUIMetadata(...),            # drives the form UI
    account_id_credential_field="api_key",          # "" if provider has no account-id concept
)
register(SPEC)
```

`ProviderSpec` is frozen — immutable post-registration. Re-registration with the same instance is a no-op; re-registration with a different instance raises.

## Registration is import-driven, not config-driven

`api/services/telephony/__init__.py` imports `providers/` for side effects. Don't add a registration call elsewhere — by the time `factory`, `audio_config`, or `run_pipeline_telephony` look the spec up, the package init has already executed.

The package init **does not import `routes.py`** — `api/routes/telephony.py::_mount_provider_routers()` walks `registry.all_specs()` and uses `importlib.import_module(f"...providers.{spec.name}.routes")`, treating `ModuleNotFoundError` as "no routes for this provider." This is what keeps `from api.services.telephony.base import TelephonyProvider` from fanning out to every route handler in the app. Don't undo it by importing `.routes` from `__init__.py`.

## Conventions

### `provider: Literal["<name>"]` on both Request and Response

Pydantic's discriminated union dispatches on this field. Forgetting `Literal` makes the union accept any provider's payload as yours. Default it to the literal so save calls don't have to send it explicitly.

### Transports load credentials lazily

Always:

```python
from api.services.telephony.factory import load_credentials_for_transport

config = await load_credentials_for_transport(
    organization_id, telephony_configuration_id, expected_provider="<name>",
)
```

Never read the org's default config from `transport.py`. The workflow run carries `telephony_configuration_id` in `initial_context` for multi-config orgs; `load_credentials_for_transport` resolves the right row and validates the provider matches.

### `_config_loader` is a pure dict reshape

It runs over `TelephonyConfigurationModel.credentials` (the JSONB column). Don't do I/O in it. Don't pull `from_numbers` from credentials — the factory attaches active phone numbers from `telephony_phone_numbers` after the loader runs, by joining and normalizing addresses.

### Sensitive fields

Mark every credential field `sensitive=True` in `ProviderUIMetadata`. The org routes derive masking from `ui_metadata`, not from a separate hardcoded list. If you re-submit a masked value, `preserve_masked_fields` restores the original — relying on this means you should never write `sensitive=False` on a real secret to "make the form simpler."

### Inbound webhook routing

When multiple configs of the same provider live in one org (e.g. two Twilio sub-accounts), the inbound dispatcher matches the webhook to a config by `credentials[<account_id_credential_field>]`. Set this to whatever your provider stamps on inbound payloads (`account_sid` for Twilio, `auth_id` for Plivo, etc.). Set `""` only when the provider truly has no account-id concept (e.g. ARI — there's at most one config per org).

### `configure_inbound` defaults to no-op

Override only when the provider supports programmatic webhook binding (Plivo `application_id`, Telnyx app config). Markup-response providers that learn the webhook URL from console-side configuration leave the default. Returning `ProviderSyncResult(ok=False, message="...")` surfaces a non-fatal warning to the user without aborting the DB write.

## Reference implementations

Pick the closest shape and copy from it.

| Provider | Pick when... |
| --- | --- |
| `twilio/` | Markup-response (TwiML), HMAC-signed webhooks, conference-style transfers, status callbacks. The most full-featured reference. |
| `plivo/` | Markup-response with multi-callback signature schemes, programmatic answer-URL sync via Application API. |
| `vonage/` | JWT auth, 16 kHz Linear PCM wire format, NCCO JSON responses. |
| `cloudonix/` | SIP-trunk-style with custom transfer/hangup strategies. |
| `telnyx/` | Call-control style — REST calls to answer/stream rather than markup response. |
| `vobiz/` | Body-signed webhooks (signature covers raw bytes). |
| `ari/` | Smallest viable: no `routes.py`, no `verify_inbound_signature`, WebSocket-only, no account-id. |

## What NOT to do

- **Don't import another provider's `provider.py` or `transport.py`.** Cross-provider behavior belongs in `services/telephony/` (e.g. `status_processor`, `ari_manager`, `call_transfer_manager`), not in another provider's package.
- **Don't add a hardcoded provider list anywhere.** If you need to iterate, use `registry.all_specs()` / `registry.names()`.
- **Don't add a route under `routes/telephony.py` for a single provider.** Provider-specific handlers go in `providers/<name>/routes.py`. Cross-provider handlers (`/inbound/run`, `/twiml`) stay in `routes/telephony.py`.
- **Don't import `.routes` from a provider's `__init__.py`.** That's the cycle we deliberately broke — see "Registration is import-driven."
- **Don't write a frontend form for a new provider.** The UI consumes `GET /api/v1/organizations/telephony-providers/metadata` and renders generically from `ProviderUIField`. If a `field.type` you need doesn't exist (`text`/`password`/`textarea`/`string-array`/`number`), extend the renderer in `ui/src/app/(authenticated)/telephony-configurations/` once — not per provider.
- **Don't run a database migration to add a provider.** The discriminator lives in JSONB credentials and a `VARCHAR(64)` `mode` column; nothing in the DB schema knows the set of provider names.
