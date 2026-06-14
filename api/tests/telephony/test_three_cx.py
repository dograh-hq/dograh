"""Unit tests for the 3CX telephony provider.

Scope:
* Config schemas (validators, mask/unmask roundtrip via discriminated union)
* Pure-Python helpers (endpoint_id, dialplan row generation)
* Provider methods that don't need a transport (validate_config,
  parse_inbound_webhook, validate_account_id)
* Provisioning hook with mocked asyncpg pool — no real Postgres
* SPEC wiring (preprocessor + account_id field)

These tests deliberately use no DB fixtures, so they don't trigger the
session-scoped test-database setup in ``api/conftest.py``. They still
require ``api/.env.test`` to define ``DATABASE_URL`` and ``REDIS_URL``,
because the root conftest reads ``api.constants`` at import time.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.schemas.telephony_config import (
    TelephonyConfigurationResponse,
    ThreeCxConfigurationRequest,
    ThreeCxConfigurationResponse,
)
from api.services.telephony import registry
from api.services.telephony.providers.three_cx import SPEC
from api.services.telephony.providers.three_cx.dialplan import (
    _prefix_to_pattern,
    build_dialplan_rows,
    outbound_context_for,
)
from api.services.telephony.providers.three_cx.provider import ThreeCxProvider
from api.services.telephony.providers.three_cx.provisioning import (
    _provision_3cx_trunk,
    endpoint_id_for,
)

_FULL_CREDS = {
    "ari_endpoint": "http://asterisk.example.com:8088",
    "app_name": "dograh",
    "app_password": "secret",
    "ws_client_name": "dograh_staging",
    "sip_domain": "1156.3cx.cloud",
    "extension": "12611",
    "sip_password": "3cx-sip-secret",
    "strip_prefix": "^\\+39",
    "from_numbers": ["+393331112222"],
}


# ---------------------------------------------------------------------------
# endpoint_id_for
# ---------------------------------------------------------------------------


def test_endpoint_id_for_italian_3cx_tenant():
    assert endpoint_id_for("1156.3cx.cloud", "12611") == "dograh_1156_3cx_cloud_12611"


def test_endpoint_id_for_normalizes_uppercase_and_dots():
    assert (
        endpoint_id_for("ACME.PBX.3CX.cloud", "200")
        == "dograh_acme_pbx_3cx_cloud_200"
    )


def test_endpoint_id_for_collapses_runs_of_separators():
    assert endpoint_id_for("foo..bar--baz", "9") == "dograh_foo_bar_baz_9"


def test_endpoint_id_for_rejects_empty_sip_domain():
    with pytest.raises(ValueError):
        endpoint_id_for("", "12611")


def test_endpoint_id_for_rejects_empty_extension():
    with pytest.raises(ValueError):
        endpoint_id_for("1156.3cx.cloud", "")


# ---------------------------------------------------------------------------
# dialplan
# ---------------------------------------------------------------------------


def test_prefix_to_pattern_italian():
    pattern, skip = _prefix_to_pattern("^\\+39")
    assert pattern == "_+39N."
    assert skip == 3  # '+39' is 3 characters


def test_prefix_to_pattern_empty_falls_back_to_match_all():
    pattern, skip = _prefix_to_pattern("")
    assert pattern == "_X."
    assert skip == 0


def test_prefix_to_pattern_unsupported_regex_raises():
    with pytest.raises(ValueError):
        _prefix_to_pattern("^\\+[0-9]{2}")


def test_build_dialplan_rows_outbound_dials_into_pjsip_endpoint_with_skip():
    rows = build_dialplan_rows(
        endpoint_id="dograh_1156_3cx_cloud_12611",
        extension="12611",
        stasis_app="dograh",
        strip_prefix="^\\+39",
    )
    outbound = next(r for r in rows if r["context"].endswith("-outbound"))
    assert outbound["app"] == "Dial"
    assert outbound["exten"] == "_+39N."
    assert outbound["appdata"] == "PJSIP/${EXTEN:3}@dograh_1156_3cx_cloud_12611,60"


def test_build_dialplan_rows_inbound_routes_extension_and_wildcard_to_stasis():
    rows = build_dialplan_rows(
        endpoint_id="dograh_1156_3cx_cloud_12611",
        extension="12611",
        stasis_app="dograh",
        strip_prefix="",
    )
    inbound = [r for r in rows if r["context"].endswith("-inbound")]
    extens = {r["exten"] for r in inbound}
    assert extens == {"12611", "_X."}
    for r in inbound:
        assert r["app"] == "Stasis"
        assert r["appdata"].startswith("dograh,inbound,")


def test_outbound_context_for_matches_dialplan_naming():
    rows = build_dialplan_rows(
        endpoint_id="ep1",
        extension="10",
        stasis_app="dograh",
        strip_prefix="",
    )
    outbound = next(r for r in rows if r["app"] == "Dial")
    assert outbound["context"] == outbound_context_for("ep1")


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


def test_config_request_validators_strip_and_lowercase_sip_domain():
    req = ThreeCxConfigurationRequest(
        ari_endpoint="http://asterisk:8088",
        app_name="dograh",
        app_password="x",
        sip_domain="  1156.3CX.Cloud  ",
        extension="  12611  ",
        sip_password="y",
    )
    assert req.sip_domain == "1156.3cx.cloud"
    assert req.extension == "12611"


def test_config_request_provider_literal_defaults_to_three_cx():
    req = ThreeCxConfigurationRequest(
        ari_endpoint="x",
        app_name="x",
        app_password="x",
        sip_domain="1156.3cx.cloud",
        extension="12611",
        sip_password="x",
    )
    assert req.provider == "three_cx"


def test_telephony_config_response_can_carry_three_cx():
    """The top-level response model must expose a `three_cx` slot."""
    resp = TelephonyConfigurationResponse(
        three_cx=ThreeCxConfigurationResponse(
            ari_endpoint="x",
            app_name="dograh",
            app_password="***",  # already masked by caller
            sip_domain="1156.3cx.cloud",
            extension="12611",
            sip_password="***",  # already masked by caller
            from_numbers=["+393331112222"],
        )
    )
    assert resp.three_cx is not None
    assert resp.three_cx.app_password == "***"


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


def test_provider_validate_config_with_full_data():
    p = ThreeCxProvider(_FULL_CREDS)
    assert p.validate_config() is True


def test_provider_validate_config_missing_ari_endpoint_is_false():
    creds = {**_FULL_CREDS, "ari_endpoint": ""}
    assert ThreeCxProvider(creds).validate_config() is False


def test_provider_parse_inbound_webhook_populates_account_id_from_extension():
    webhook = {
        "channel": {
            "id": "ch-1",
            "state": "Ringing",
            "caller": {"number": "+393331112222"},
            "dialplan": {"exten": "12611"},
        }
    }
    n = ThreeCxProvider.parse_inbound_webhook(webhook)
    assert n.provider == "three_cx"
    assert n.to_number == "12611"
    assert n.account_id == "12611"
    assert n.from_number == "+393331112222"


def test_provider_parse_inbound_webhook_uses_none_for_missing_extension():
    n = ThreeCxProvider.parse_inbound_webhook({"channel": {}})
    assert n.account_id is None


def test_provider_validate_account_id_matches_extension():
    assert ThreeCxProvider.validate_account_id({"extension": "12611"}, "12611") is True


def test_provider_validate_account_id_rejects_wrong_extension():
    assert ThreeCxProvider.validate_account_id({"extension": "12611"}, "9999") is False


def test_provider_validate_account_id_rejects_missing_config_extension():
    assert ThreeCxProvider.validate_account_id({}, "12611") is False


# ---------------------------------------------------------------------------
# SPEC registration
# ---------------------------------------------------------------------------


def test_spec_registered_with_account_id_extension_and_preprocessor():
    spec = registry.get("three_cx")
    assert spec is SPEC
    assert spec.account_id_credential_field == "extension"
    assert spec.preprocess_credentials_on_save is not None
    assert spec.transport_sample_rate == 8000


def test_spec_ui_metadata_marks_passwords_sensitive():
    by_name = {f.name: f for f in SPEC.ui_metadata.fields}
    assert by_name["app_password"].sensitive is True
    assert by_name["sip_password"].sensitive is True
    # Non-secret fields should NOT be marked sensitive.
    assert by_name["sip_domain"].sensitive is False
    assert by_name["extension"].sensitive is False


# ---------------------------------------------------------------------------
# Provisioning (mocked asyncpg)
# ---------------------------------------------------------------------------


def _make_mock_pool():
    """Build a mock asyncpg pool whose ``acquire()`` yields a recording conn."""
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="OK")

    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


@patch(
    "api.services.telephony.providers.three_cx.provisioning.get_pool",
    new_callable=AsyncMock,
)
async def test_provision_writes_six_table_set_in_single_transaction(get_pool_mock):
    pool, conn = _make_mock_pool()
    get_pool_mock.return_value = pool

    out = await _provision_3cx_trunk(dict(_FULL_CREDS))

    # Returns the credentials unchanged — endpoint_id is rederived at runtime.
    assert out == _FULL_CREDS

    statements = [call.args[0] for call in conn.execute.await_args_list]
    # Idempotency deletes come first (5 statements covering 4 ps_* + extensions).
    assert sum(1 for s in statements if s.lstrip().startswith("DELETE")) == 5
    # Then one INSERT per ps_* table + one INSERT per dialplan row (3 rows).
    inserts = [s for s in statements if "INSERT" in s]
    assert any("ps_auths" in s for s in inserts)
    assert any("ps_aors" in s for s in inserts)
    assert any("ps_endpoints" in s for s in inserts)
    assert any("ps_registrations" in s for s in inserts)
    assert sum(1 for s in inserts if "INTO extensions" in s) == 3
    # All inserts must happen inside one transaction context.
    assert conn.transaction.call_count == 1


@patch(
    "api.services.telephony.providers.three_cx.provisioning.get_pool",
    new_callable=AsyncMock,
)
async def test_provision_is_idempotent_on_resave(get_pool_mock):
    pool, conn = _make_mock_pool()
    get_pool_mock.return_value = pool

    await _provision_3cx_trunk(dict(_FULL_CREDS))
    first_call_count = conn.execute.await_count

    await _provision_3cx_trunk(dict(_FULL_CREDS))
    # Second call performs the same delete-then-insert work.
    assert conn.execute.await_count == 2 * first_call_count


async def test_provision_raises_400_on_missing_required_field():
    bad = {**_FULL_CREDS}
    bad.pop("extension")
    with pytest.raises(HTTPException) as exc:
        await _provision_3cx_trunk(bad)
    assert exc.value.status_code == 400
    assert "extension" in exc.value.detail


@patch(
    "api.services.telephony.providers.three_cx.provisioning.get_pool",
    new_callable=AsyncMock,
)
async def test_provision_translates_ara_not_configured_to_400(get_pool_mock):
    from api.services.telephony.providers.three_cx.ara_db import (
        AraNotConfiguredError,
    )

    get_pool_mock.side_effect = AraNotConfiguredError("ASTERISK_ARA_DSN not set")
    with pytest.raises(HTTPException) as exc:
        await _provision_3cx_trunk(dict(_FULL_CREDS))
    assert exc.value.status_code == 400
    assert "ASTERISK_ARA_DSN" in exc.value.detail


@patch(
    "api.services.telephony.providers.three_cx.provisioning.get_pool",
    new_callable=AsyncMock,
)
async def test_provision_translates_db_error_to_502(get_pool_mock):
    pool, conn = _make_mock_pool()
    conn.execute = AsyncMock(side_effect=RuntimeError("relation \"ps_auths\" does not exist"))
    get_pool_mock.return_value = pool

    with pytest.raises(HTTPException) as exc:
        await _provision_3cx_trunk(dict(_FULL_CREDS))
    assert exc.value.status_code == 502
    assert "ps_auths" in exc.value.detail
