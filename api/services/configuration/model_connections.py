"""Resolve organization-owned model references without contacting providers.

Run snapshots contain no credentials. They retain model settings and a pool
index; hydration reads the connection's current credentials.
"""

from copy import deepcopy
from dataclasses import dataclass
from secrets import choice
from types import SimpleNamespace
from urllib.parse import urlparse

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from api.db import db_client
from api.enums import OrganizationConfigurationKey
from api.schemas.ai_model_configuration import EffectiveAIModelConfiguration
from api.schemas.model_connections import (
    CONNECTION_FIELDS,
    CREDENTIAL_FIELDS,
    ModelConfigurationOverride,
    ModelConfigurationSpec,
)
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.registry import REGISTRY, ServiceType
from api.utils.url_security import validate_user_configured_service_url

ROLES = ("llm", "stt", "tts", "realtime", "embeddings")
DEFAULT_KEY = OrganizationConfigurationKey.MODEL_CONFIGURATION_DEFAULT_UUID.value


@dataclass
class ResolvedModelConfiguration:
    effective: EffectiveAIModelConfiguration
    snapshot: dict
    provenance: list[dict]


def _invalid(message):
    return HTTPException(status_code=422, detail=message)


def _provider(value):
    return str(getattr(value, "value", value))


def _schema(role, provider):
    schema = REGISTRY[ServiceType[role.upper()]].get(provider)
    if schema is None:
        raise _invalid(f"Provider does not support {role}")
    return schema


def _validation_failure(role, exc):
    # Never interpolate raw ValidationError: its inputs can contain credentials.
    fields = sorted(
        {
            str(error["loc"][0])
            for error in exc.errors(include_input=False)
            if error["loc"]
        }
    )
    return _invalid(
        f"Invalid {role} configuration"
        + (f"; review fields: {', '.join(fields)}" if fields else "")
    )


def split_service_configuration(service):
    """Split effective registry inputs; computed fields are intentionally omitted."""
    raw = service.model_dump(mode="json", exclude_computed_fields=True)
    return (
        _provider(raw.pop("provider")),
        {key: value for key, value in raw.items() if key in CREDENTIAL_FIELDS},
        {key: value for key, value in raw.items() if key in CONNECTION_FIELDS},
        {
            key: value
            for key, value in raw.items()
            if key not in CREDENTIAL_FIELDS | CONNECTION_FIELDS
        },
    )


def model_connection_catalog():
    services = {}
    for role in ROLES:
        providers = {}
        for provider, cls in REGISTRY[ServiceType[role.upper()]].items():
            schema = cls.model_json_schema(mode="validation")
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            settings = {
                key: value
                for key, value in properties.items()
                if key not in CREDENTIAL_FIELDS | CONNECTION_FIELDS | {"provider"}
            }
            providers[_provider(provider)] = {
                "title": schema.get("title", _provider(provider)),
                "credential_fields": {
                    key: value
                    for key, value in properties.items()
                    if key in CREDENTIAL_FIELDS
                },
                "connection_fields": {
                    key: value
                    for key, value in properties.items()
                    if key in CONNECTION_FIELDS
                },
                "credential_required": [
                    key for key in required if key in CREDENTIAL_FIELDS
                ],
                "connection_required": [
                    key for key in required if key in CONNECTION_FIELDS
                ],
                "settings_schema": {
                    **schema,
                    "properties": settings,
                    "required": [key for key in required if key in settings],
                },
            }
        services[role] = providers
    return {"services": services}


def _validate_connection_urls(connection_settings):
    for field in ("base_url", "endpoint"):
        url = connection_settings.get(field)
        if url:
            try:
                if (
                    not isinstance(url, str)
                    or urlparse(url).username
                    or urlparse(url).password
                    or urlparse(url).query
                    or urlparse(url).fragment
                ):
                    raise ValueError(
                        "Connection URLs cannot contain credentials, queries or fragments"
                    )
                validate_user_configured_service_url(url, field_name=field)
            except ValueError:
                raise _invalid(f"Invalid {field} connection setting") from None


def validate_provider_connection(provider, credentials, connection_settings):
    """A connection must locally validate for at least one registered service."""
    _validate_connection_urls(connection_settings)
    schemas = [
        mapping[provider] for mapping in REGISTRY.values() if provider in mapping
    ]
    if not schemas:
        raise _invalid("Unknown provider")
    allowed = set().union(*(set(schema.model_fields) for schema in schemas))
    if set(credentials) - (allowed & CREDENTIAL_FIELDS):
        raise _invalid("Unknown credential field for provider")
    if set(connection_settings) - (allowed & CONNECTION_FIELDS):
        raise _invalid("Unknown connection setting for provider")
    for value in credentials.values():
        if isinstance(value, str) and "***" in value:
            raise _invalid("Supply actual credentials, not masked values")
        if isinstance(value, list) and any(
            not isinstance(item, str) or not item.strip() or "***" in item
            for item in value
        ):
            raise _invalid("Credential pools require nonempty unmasked strings")
    for schema in schemas:
        payload = {
            key: value
            for key, value in {**credentials, **connection_settings}.items()
            if key in schema.model_fields
        }
        try:
            service = schema.model_validate({**payload, "provider": provider})
            _check_required_credentials(service)
            return service
        except (ValidationError, HTTPException):
            continue
    raise _invalid(
        "Connection credentials or connection settings are incomplete or invalid"
    )


async def validate_provider_connection_credentials(
    provider,
    credentials,
    connection_settings,
    *,
    organization_id,
    created_by,
):
    """Check credentials only on connection writes, never during resolution."""
    service = validate_provider_connection(provider, credentials, connection_settings)
    try:
        await run_in_threadpool(
            UserConfigurationValidator().validate_connection,
            service,
            organization_id=organization_id,
            created_by=created_by,
        )
    except ValueError as exc:
        raise _invalid(str(exc)) from None


def _check_required_credentials(service):
    for field in CREDENTIAL_FIELDS & type(service).model_fields.keys():
        if type(service).model_fields[field].is_required():
            value = service.__dict__.get(field)
            if (
                not value
                or isinstance(value, list)
                and any(not item.strip() for item in value)
            ):
                raise _invalid("Required credentials are missing")


async def _connection(organization_id, uuid, *, active_only=True):
    connection = await db_client.get_provider_connection(
        organization_id, str(uuid), active_only=active_only
    )
    if connection is None:
        raise HTTPException(status_code=404, detail="Provider connection not found")
    return connection


async def _named(organization_id, uuid):
    row = await db_client.get_named_model_configuration(organization_id, str(uuid))
    if row is None:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    return row


def dograh_model_configuration_spec(connection_uuid) -> dict:
    """A pipeline specification running every role on one Dograh connection.

    Settings are left empty so each role takes the registry defaults.
    """
    selection = {"provider_connection_uuid": str(connection_uuid), "settings": {}}
    return {
        "version": 3,
        "mode": "pipeline",
        **{role: dict(selection) for role in ("llm", "stt", "tts", "embeddings")},
    }


async def get_default_model_configuration(organization_id):
    row = await db_client.get_configuration(organization_id, DEFAULT_KEY)
    if row is None or not row.value:
        return None
    return await _named(organization_id, row.value)


async def resolve_model_configuration(
    organization_id,
    workflow_override=None,
    api_override=None,
    pre_call_override=None,
    *,
    preferred_dograh_key=None,
    default_row=None,
):
    default = default_row or await get_default_model_configuration(organization_id)
    config = deepcopy(default.configuration) if default else {}
    provenance = (
        [
            {
                "source": "organization",
                "model_configuration_uuid": default.uuid,
                "revision": default.revision,
            }
        ]
        if default
        else []
    )
    connections = {}

    async def get_connection(uuid):
        uuid = str(uuid)
        if uuid not in connections:
            connections[uuid] = await _connection(organization_id, uuid)
        return connections[uuid]

    for source, value in (
        ("workflow", workflow_override),
        ("api", api_override),
        ("pre_call", pre_call_override),
    ):
        if value is None:
            continue
        try:
            patch = (
                value
                if isinstance(value, ModelConfigurationOverride)
                else ModelConfigurationOverride.model_validate(value)
            )
        except ValidationError as exc:
            raise _validation_failure(source, exc) from None
        patch = patch.model_dump(mode="json", exclude_unset=True)
        named_uuid = patch.pop("model_configuration_uuid", None)
        if named_uuid:
            row = await _named(organization_id, named_uuid)
            config = deepcopy(row.configuration)
            provenance.append(
                {
                    "source": source,
                    "model_configuration_uuid": row.uuid,
                    "revision": row.revision,
                }
            )
        else:
            provenance.append({"source": source})
        await _apply_model_patch(config, patch, get_connection)
    return await _resolve_spec(
        organization_id,
        config,
        provenance,
        connections,
        preferred_dograh_key=preferred_dograh_key,
    )


async def _apply_model_patch(config, patch, get_connection):
    if "mode" in patch:
        config["mode"] = patch.pop("mode")
    if "llm_fallback" in patch:
        # Policies replace atomically. An empty rules array disables them;
        # omitting the policy inherits it from the selected configuration.
        config["llm_fallback"] = patch.pop("llm_fallback")
    for role, selection in patch.items():
        if selection is None:  # Only embeddings accepts explicit null.
            config[role] = None
            continue
        previous = config.get(role) or {}
        old_uuid = previous.get("provider_connection_uuid")
        new_uuid = selection.get("provider_connection_uuid", old_uuid)
        settings = deepcopy(previous.get("settings", {}))
        if new_uuid and new_uuid != old_uuid:
            new_connection = await get_connection(new_uuid)
            old_connection = await get_connection(old_uuid) if old_uuid else None
            if (
                old_connection is None
                or old_connection.provider != new_connection.provider
            ):
                settings = {}
        settings.update(selection.get("settings", {}))
        config[role] = {"provider_connection_uuid": new_uuid, "settings": settings}


async def resolve_inline_model_configuration(organization_id, configuration):
    return await _resolve_spec(organization_id, configuration, [], {})


async def resolve_pre_call_model_configuration(
    organization_id, snapshot, effective, override
):
    """Patch the authorized snapshot, preserving its settings and selected keys.

    Only newly selected connections are read from the catalog. A catalog edit
    during ringing cannot change an already running recognition/realtime service
    or make a settings-only hook resample the run's credential pools.
    """
    from api.services.managed_model_services import get_dograh_service_api_key

    config = {"version": 3, "mode": snapshot["mode"]}
    connections = {}
    pins = {}

    def selection(saved, service):
        uuid = saved["provider_connection_uuid"]
        _, credentials, _, _ = split_service_configuration(service)
        if uuid in connections:
            connections[uuid].credentials.update(credentials)
            connections[uuid].connection_settings.update(saved["connection_settings"])
        else:
            connections[uuid] = SimpleNamespace(
                uuid=uuid,
                provider=saved["provider"],
                revision=saved["connection_revision"],
                credentials=credentials,
                connection_settings=deepcopy(saved["connection_settings"]),
            )
        if saved.get("api_key_index") is not None:
            pins[uuid] = saved["api_key_index"]
        return {
            "provider_connection_uuid": uuid,
            "settings": deepcopy(saved["settings"]),
        }

    for role, saved in snapshot["services"].items():
        config[role] = selection(saved, getattr(effective, role))
    if snapshot.get("llm_fallback") is not None:
        config["llm_fallback"] = {
            "version": 1,
            "rules": [
                {
                    "condition": deepcopy(rule["condition"]),
                    "target": selection(
                        rule["target"],
                        effective.llm_fallback.rules[index].target,
                    ),
                }
                for index, rule in enumerate(snapshot["llm_fallback"]["rules"])
            ],
        }

    async def get_connection(uuid):
        uuid = str(uuid)
        if uuid not in connections:
            connections[uuid] = await _connection(organization_id, uuid)
        return connections[uuid]

    await _apply_model_patch(
        config, override.model_dump(mode="json", exclude_unset=True), get_connection
    )
    authorized_key = get_dograh_service_api_key(effective)
    resolved = await _resolve_spec(
        organization_id,
        config,
        [*deepcopy(snapshot.get("provenance", [])), {"source": "pre_call"}],
        connections,
        preferred_dograh_key=authorized_key,
    )
    if (
        authorized_key is None
        and get_dograh_service_api_key(resolved.effective) is not None
    ):
        raise _invalid(
            "Pre-call overrides cannot introduce an unauthorized Dograh service key"
        )
    selections = list(resolved.snapshot["services"].values())
    selections.extend(
        rule["target"]
        for rule in (resolved.snapshot.get("llm_fallback") or {}).get("rules", [])
    )
    for saved in selections:
        uuid = saved["provider_connection_uuid"]
        if uuid in pins:
            saved["api_key_index"] = pins[uuid]
    return resolved


async def _resolve_spec(
    organization_id,
    configuration,
    provenance,
    connections,
    *,
    preferred_dograh_key=None,
):
    try:
        spec = (
            configuration
            if isinstance(configuration, ModelConfigurationSpec)
            else ModelConfigurationSpec.model_validate(configuration)
        )
    except ValidationError as exc:
        raise _validation_failure("model", exc) from None
    active = (
        {"llm", "embeddings", "realtime"}
        if spec.mode == "realtime"
        else {"llm", "embeddings", "stt", "tts"}
    )
    services = {}
    selections = [(role, role, getattr(spec, role)) for role in ROLES]
    if spec.llm_fallback is not None:
        selections.extend(
            (f"fallback_{index}", "llm", rule.target)
            for index, rule in enumerate(spec.llm_fallback.rules)
        )
    for key, role, selection in selections:
        if selection is None:
            continue
        uuid = str(selection.provider_connection_uuid)
        if uuid not in connections:
            connections[uuid] = await _connection(organization_id, uuid)
        row = connections[uuid]
        if (
            key == "llm"
            and spec.llm_fallback
            and spec.llm_fallback.rules
            and row.provider == "dograh"
        ):
            raise _invalid("LLM fallbacks are unavailable in Dograh mode")
        if key.startswith("fallback_") and row.provider == "dograh":
            raise _invalid(
                "Dograh manages its own fallbacks and cannot be a fallback target"
            )
        schema = _schema(role, row.provider)
        allowed = (
            schema.model_fields.keys()
            - CREDENTIAL_FIELDS
            - CONNECTION_FIELDS
            - {"provider"}
        )
        if set(selection.settings) - allowed:
            raise _invalid(f"Unknown or non-overridable {role} setting")
        # Validate all saved references and settings, but only active services
        # participate in key selection, billing and snapshot execution.
        service = _build_service(
            role,
            row.provider,
            row.credentials,
            row.connection_settings,
            selection.settings,
        )
        if role in active:
            services[key] = (row, service)
    for key, (row, target) in services.items():
        if not key.startswith("fallback_"):
            continue
        primary_row, primary = services["llm"]
        if row.uuid == primary_row.uuid and target.model_dump() == primary.model_dump():
            raise _invalid("Fallback target must differ from the primary LLM")
    dograh_keys = []
    for row, service in services.values():
        if row.provider == "dograh":
            dograh_keys.append(set(service.get_all_api_keys()))
    common = set.intersection(*dograh_keys) if dograh_keys else set()
    if dograh_keys and not common:
        raise _invalid("All Dograh services in one run must share a service key")
    if (
        dograh_keys
        and preferred_dograh_key is not None
        and preferred_dograh_key not in common
    ):
        raise _invalid(
            "Selected Dograh connections must support the run's authorized service key"
        )
    dograh_key = (preferred_dograh_key or choice(sorted(common))) if common else None
    selected = {}
    snapshot_services = {}
    effective_services = {}
    for key, (row, service) in services.items():
        role = "llm" if key.startswith("fallback_") else key
        credentials = deepcopy(row.credentials)
        keys = service.get_all_api_keys() if credentials.get("api_key") else []
        index = None
        if keys:
            if row.uuid not in selected:
                selected[row.uuid] = (
                    keys.index(dograh_key)
                    if row.provider == "dograh"
                    else choice(range(len(keys)))
                )
            index = selected[row.uuid]
            credentials["api_key"] = keys[index]
        provider, _, connection_settings, settings = split_service_configuration(
            service
        )
        effective_services[key] = _build_service(
            role, provider, credentials, connection_settings, settings
        )
        snapshot_services[key] = {
            "provider_connection_uuid": row.uuid,
            "provider": provider,
            "connection_revision": row.revision,
            "api_key_index": index,
            "connection_settings": connection_settings,
            "settings": settings,
        }
    effective_fallback = None
    snapshot_fallback = None
    if spec.llm_fallback is not None:
        effective_fallback = {"version": 1, "rules": []}
        snapshot_fallback = {"version": 1, "rules": []}
        for index, rule in enumerate(spec.llm_fallback.rules):
            key = f"fallback_{index}"
            condition = rule.condition.model_dump(mode="json")
            effective_fallback["rules"].append(
                {"condition": condition, "target": effective_services.pop(key)}
            )
            snapshot_fallback["rules"].append(
                {"condition": condition, "target": snapshot_services.pop(key)}
            )
    effective = EffectiveAIModelConfiguration(
        **effective_services,
        llm_fallback=effective_fallback,
        is_realtime=spec.mode == "realtime",
        managed_service_version=2 if dograh_keys else None,
    )
    snapshot = {
        "version": 3,
        "mode": spec.mode,
        "services": snapshot_services,
        "llm_fallback": snapshot_fallback,
        "provenance": provenance,
        "managed_service_version": effective.managed_service_version,
    }
    return ResolvedModelConfiguration(
        effective=effective, snapshot=snapshot, provenance=provenance
    )


def _build_service(role, provider, credentials, connection_settings, settings):
    schema = _schema(role, provider)
    payload = {
        key: value
        for key, value in {**credentials, **connection_settings}.items()
        if key in schema.model_fields
    }
    try:
        service = schema.model_validate({**payload, **settings, "provider": provider})
        _check_required_credentials(service)
        return service
    except ValidationError as exc:
        raise _validation_failure(role, exc) from None


async def hydrate_model_configuration_snapshot(organization_id, snapshot):
    if snapshot.get("version") != 3 or snapshot.get("mode") not in {
        "pipeline",
        "realtime",
    }:
        raise _invalid("Invalid prepared model configuration")
    services = {}
    connections = {}

    async def hydrate_selection(role, selection):
        if role not in ROLES:
            raise _invalid("Invalid prepared service")
        uuid = selection["provider_connection_uuid"]
        if uuid not in connections:
            connections[uuid] = await _connection(
                organization_id, uuid, active_only=False
            )
        row = connections[uuid]
        if row.provider != selection["provider"]:
            raise _invalid("Prepared provider does not match connection")
        credentials = deepcopy(row.credentials)
        index = selection.get("api_key_index")
        if index is not None:
            keys = credentials.get("api_key")
            keys = keys if isinstance(keys, list) else [keys]
            if (
                not isinstance(index, int)
                or not 0 <= index < len(keys)
                or not keys[index]
            ):
                raise _invalid("Prepared credential selection is unavailable")
            credentials["api_key"] = keys[index]
        return _build_service(
            role,
            selection["provider"],
            credentials,
            selection["connection_settings"],
            selection["settings"],
        )

    for role, selection in snapshot.get("services", {}).items():
        services[role] = await hydrate_selection(role, selection)
    fallback = snapshot.get("llm_fallback")
    if fallback is not None:
        fallback = {
            "version": fallback["version"],
            "rules": [
                {
                    "condition": rule["condition"],
                    "target": await hydrate_selection("llm", rule["target"]),
                }
                for rule in fallback["rules"]
            ],
        }
    return EffectiveAIModelConfiguration(
        **services,
        llm_fallback=fallback,
        is_realtime=snapshot["mode"] == "realtime",
        managed_service_version=snapshot.get("managed_service_version"),
    )


def public_snapshot(snapshot):
    """Remove internal credential pins from previews returned to clients."""
    result = deepcopy(snapshot)
    for service in result.get("services", {}).values():
        service.pop("api_key_index", None)
    for rule in (result.get("llm_fallback") or {}).get("rules", []):
        rule["target"].pop("api_key_index", None)
    return result


async def validate_embedding_compatibility(
    organization_id, effective, document_uuids=None
):
    """Compare query settings with persisted index metadata, without reindexing.

    Historical chunks only record model and dimension. This deliberately does
    not claim to prove provider/endpoint equivalence for identical model names.
    An empty list means the workflow has no retrieval documents.
    """
    if document_uuids == []:
        return
    spaces = await db_client.get_model_configuration_embedding_spaces(
        organization_id, document_uuids=document_uuids
    )
    if not spaces:
        return
    embeddings = effective.embeddings
    if embeddings is None:
        raise _invalid(
            "Selected knowledge-base documents require an embedding configuration"
        )
    if any(
        space["model"] != embeddings.model or space["dimension"] != 1536
        for space in spaces
    ):
        raise _invalid(
            "Embedding configuration does not match existing document indexes. Select a compatible configuration or explicitly reindex those documents before changing embedding models."
        )


def validate_service_configuration(configuration):
    """Validate an already parsed service without sampling keys or provider calls.

    URL safety retains the existing deployment policy (which resolves hostnames
    in SaaS mode); it never sends a request to a model service.
    """
    _check_required_credentials(configuration)
    _, _, connection_settings, _ = split_service_configuration(configuration)
    _validate_connection_urls(connection_settings)
