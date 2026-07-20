"""Upstream-PBX call control.

When a call originates on an upstream PBX (e.g. VICIdial) and is patched into
dograh over a SIP trunk, the *customer's* real call leg lives on the upstream
PBX, not on dograh's Asterisk -- dograh only owns its agent leg (the SIP leg
into Stasis + the externalMedia WebSocket). So when the AI decides to hang up or
transfer, dograh must tell the upstream PBX what to do via its API, and must do
so BEFORE tearing down its own SIP leg -- otherwise the SIP BYE races the
upstream PBX's own conference/remote-agent teardown and can leave it in an
inconsistent state.

The upstream identity (the handle for these API calls) is captured off the
inbound SIP headers in ari_manager and stored on the workflow run's
``initial_context["upstream_pbx"]``. The adapter is selected per call by
``upstream["provider"]``.

This deployment is VICIdial-focused; the FreeSWITCH adapter is retained but
inert unless an upstream tags itself ``freeswitch`` (via ``X-PBX-*`` headers).
Connection settings come from environment variables so the same image works
against a PBX on another server (the api container reaches it over normal egress
-- no shared ``pbx-net`` required):

    VICIDIAL_API_URL       e.g. http://vici.example.com/agc/api.php
    VICIDIAL_API_USER      VICIdial API user
    VICIDIAL_API_PASS      VICIdial API password
    VICIDIAL_API_SOURCE    source tag sent to the API (default: dograh)

    VICIDIAL_NON_AGENT_API_URL    e.g. http://vici.example.com/vicidial/non_agent_api.php
    VICIDIAL_NON_AGENT_API_USER   non-agent API user (distinct from the agent API)
    VICIDIAL_NON_AGENT_API_PASS   non-agent API password
    VICIDIAL_NON_AGENT_API_SOURCE source tag sent to the non-agent API (default: dograh)

    FREESWITCH_ESL_HOST    FreeSWITCH Event Socket host (optional)
    FREESWITCH_ESL_PORT    FreeSWITCH Event Socket port (default: 8021)
    FREESWITCH_ESL_PASSWORD  FreeSWITCH ESL password (default: ClueCon)
"""

import asyncio
import os

import aiohttp
from loguru import logger

# --- VICIdial agent-API connection (from env; remote-server friendly) ---
_VICIDIAL_API_URL = os.getenv("VICIDIAL_API_URL", "")
_VICIDIAL_API_USER = os.getenv("VICIDIAL_API_USER", "")
_VICIDIAL_API_PASS = os.getenv("VICIDIAL_API_PASS", "")
_VICIDIAL_API_SOURCE = os.getenv("VICIDIAL_API_SOURCE", "dograh")

# --- VICIdial non-agent API (update_lead etc.; separate endpoint + creds) ---
_VICIDIAL_NON_AGENT_API_URL = os.getenv("VICIDIAL_NON_AGENT_API_URL", "")
_VICIDIAL_NON_AGENT_API_USER = os.getenv("VICIDIAL_NON_AGENT_API_USER", "")
_VICIDIAL_NON_AGENT_API_PASS = os.getenv("VICIDIAL_NON_AGENT_API_PASS", "")
_VICIDIAL_NON_AGENT_API_SOURCE = os.getenv("VICIDIAL_NON_AGENT_API_SOURCE", "dograh")

# Extraction variables whose name starts with this prefix are forwarded to the
# VICIdial non-agent ``update_lead`` API: the prefix is stripped to yield the
# raw lead column name and the extracted value is sent as that column's value.
# e.g. an extraction variable ``X-VICI-UPDATE-LEAD_address3`` with value ``Y``
# becomes ``address3=Y`` on the lead. This lets a workflow plumb arbitrary,
# conversation-derived fields into the VICIdial flow without code changes (see
# ``collect_update_lead_fields``).
UPDATE_LEAD_VAR_PREFIX = "X-VICI-UPDATE-LEAD_"

# API-control params that must never be overridden by a forwarded field -- a
# variable named e.g. ``X-VICI-UPDATE-LEAD_function`` would otherwise hijack the
# update_lead call. These are dropped (with a warning) from forwarded fields.
_UPDATE_LEAD_RESERVED_FIELDS = frozenset(
    {"source", "user", "pass", "function", "lead_id"}
)

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=8)

# --- FreeSWITCH ESL connection (from env; retained, inert unless used) ---
# FreeSWITCH owns the customer leg; dograh drives hangup/transfer over the Event
# Socket Library by the channel UUID captured from the X-PBX-UUID header.
_FS_ESL_HOST = os.getenv("FREESWITCH_ESL_HOST", "")
_FS_ESL_PORT = int(os.getenv("FREESWITCH_ESL_PORT", "8021"))
_FS_ESL_PASSWORD = os.getenv("FREESWITCH_ESL_PASSWORD", "ClueCon")
_FS_ESL_TIMEOUT = 8


async def _ra_call_control(upstream: dict, stage: str, **extra) -> bool:
    """Invoke VICIdial's agent API ``ra_call_control`` for the captured RA call.

    The call is identified by ``value`` (the VICIdial callerid captured from the
    ``X-VICIDIAL-callerid`` header) plus the remote-agent ``agent_user``.
    """
    if not _VICIDIAL_API_URL:
        logger.warning(
            "[upstream_pbx] VICIDIAL_API_URL not configured — cannot drive "
            f"VICIdial {stage}"
        )
        return False
    params = {
        "source": _VICIDIAL_API_SOURCE,
        "user": _VICIDIAL_API_USER,
        "pass": _VICIDIAL_API_PASS,
        "agent_user": upstream.get("agent_user", ""),
        "function": "ra_call_control",
        "stage": stage,
        "value": upstream.get("callerid", ""),
        **extra,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _VICIDIAL_API_URL, params=params, timeout=_HTTP_TIMEOUT
            ) as resp:
                text = (await resp.text()).strip()
                ok = text.startswith("SUCCESS")
                logger.info(
                    f"[upstream_pbx] VICIdial ra_call_control {stage} "
                    f"(agent_user={params['agent_user']}, value={params['value']}) -> {text}"
                )
                return ok
    except Exception as e:
        logger.error(f"[upstream_pbx] VICIdial ra_call_control {stage} failed: {e}")
        return False


async def _non_agent_update_lead(lead_id: str, **fields) -> bool:
    """Invoke VICIdial's non-agent API ``update_lead`` for one lead.

    Uses the dedicated non-agent API endpoint/credentials (distinct from the
    agent API). ``fields`` are passed straight through as query params, e.g.
    ``address3="Y"``.
    """
    if not _VICIDIAL_NON_AGENT_API_URL:
        logger.warning(
            "[upstream_pbx] VICIDIAL_NON_AGENT_API_URL not configured — cannot "
            "update_lead"
        )
        return False
    if not lead_id:
        logger.warning(
            "[upstream_pbx] update_lead requested but no lead_id captured — skipping"
        )
        return False
    # ``fields`` is spread first so the API-control params below always win even
    # if a forwarded field collides with one of them (defense in depth; the
    # collector also drops reserved names).
    params = {
        **fields,
        "source": _VICIDIAL_NON_AGENT_API_SOURCE,
        "user": _VICIDIAL_NON_AGENT_API_USER,
        "pass": _VICIDIAL_NON_AGENT_API_PASS,
        "function": "update_lead",
        "lead_id": lead_id,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _VICIDIAL_NON_AGENT_API_URL, params=params, timeout=_HTTP_TIMEOUT
            ) as resp:
                text = (await resp.text()).strip()
                ok = text.startswith("SUCCESS")
                logger.info(
                    f"[upstream_pbx] VICIdial update_lead (lead_id={lead_id}, "
                    f"fields={fields}) -> {text}"
                )
                return ok
    except Exception as e:
        logger.error(f"[upstream_pbx] VICIdial update_lead failed: {e}")
        return False


def collect_update_lead_fields(gathered_context: dict) -> dict:
    """Map ``X-VICI-UPDATE-LEAD_<field>`` extracted variables to update_lead fields.

    Scans a workflow run's gathered context (its ``extracted_variables`` map) for
    variables named with the :data:`UPDATE_LEAD_VAR_PREFIX` prefix and returns
    ``{<field>: <value>}`` for each one that has a non-empty value. The prefix is
    stripped to yield the raw VICIdial lead column (e.g.
    ``X-VICI-UPDATE-LEAD_address3`` -> ``address3``).

    Empty/None values are skipped so we never blank out an existing lead column,
    and reserved API-control params are dropped so a stray variable name cannot
    hijack the update_lead request.
    """
    if not gathered_context:
        return {}
    extracted = gathered_context.get("extracted_variables")
    if not isinstance(extracted, dict):
        return {}

    fields: dict[str, str] = {}
    for key, value in extracted.items():
        if not isinstance(key, str) or not key.startswith(UPDATE_LEAD_VAR_PREFIX):
            continue
        field = key[len(UPDATE_LEAD_VAR_PREFIX) :].strip()
        if not field or value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if field in _UPDATE_LEAD_RESERVED_FIELDS:
            logger.warning(
                f"[upstream_pbx] Ignoring reserved update_lead field '{field}' "
                f"from variable '{key}'"
            )
            continue
        fields[field] = text
    return fields


async def update_upstream_lead(upstream: dict, fields: dict) -> bool:
    """Update the upstream lead with ``fields`` before a transfer.

    Dispatched by provider; currently only VICIdial (via the non-agent
    ``update_lead`` API). ``fields`` maps VICIdial lead column -> value, e.g.
    ``{"address3": "Y"}`` (typically built by
    :func:`collect_update_lead_fields` from the run's extracted variables).
    Best-effort: never blocks the transfer if it fails.
    """
    if not upstream or not fields:
        return False
    if upstream.get("provider") == "vicidial":
        return await _non_agent_update_lead(upstream.get("lead_id", ""), **fields)
    return False


async def _fs_esl_api(command: str) -> tuple[bool, str]:
    """Run a FreeSWITCH ``api`` command over the Event Socket (inbound mode).

    Connects, authenticates, issues ``api <command>`` and returns
    ``(ok, response_body)`` where ok is True when FreeSWITCH replied ``+OK``.
    """
    if not _FS_ESL_HOST:
        logger.warning("[upstream_pbx] FREESWITCH_ESL_HOST not configured")
        return False, ""

    async def _run() -> tuple[bool, str]:
        reader, writer = await asyncio.open_connection(_FS_ESL_HOST, _FS_ESL_PORT)
        try:
            await reader.readuntil(b"\n\n")  # "Content-Type: auth/request"
            writer.write(f"auth {_FS_ESL_PASSWORD}\n\n".encode())
            await writer.drain()
            await reader.readuntil(b"\n\n")  # auth command/reply
            writer.write(f"api {command}\n\n".encode())
            await writer.drain()
            headers = (await reader.readuntil(b"\n\n")).decode(errors="replace")
            length = 0
            for line in headers.splitlines():
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
            body = (
                (await reader.readexactly(length)).decode(errors="replace")
                if length
                else ""
            )
            return body.startswith("+OK"), body.strip()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    try:
        return await asyncio.wait_for(_run(), timeout=_FS_ESL_TIMEOUT)
    except Exception as e:
        logger.error(f"[upstream_pbx] FreeSWITCH ESL '{command}' failed: {e}")
        return False, ""


async def _fs_uuid_kill(upstream: dict) -> bool:
    uuid = upstream.get("uuid", "")
    if not uuid:
        return False
    ok, resp = await _fs_esl_api(f"uuid_kill {uuid}")
    logger.info(f"[upstream_pbx] FreeSWITCH uuid_kill {uuid} -> {resp or ok}")
    return ok


async def _fs_uuid_transfer(upstream: dict, destination: str) -> bool:
    uuid = upstream.get("uuid", "")
    if not uuid:
        return False
    number = destination.split("/")[-1]
    # The customer leg is transferred into the FS dialplan extension
    # dograh_xfer_<number>, which bridges to that registered user/agent.
    ok, resp = await _fs_esl_api(
        f"uuid_transfer {uuid} dograh_xfer_{number} XML dograh-customer"
    )
    logger.info(
        f"[upstream_pbx] FreeSWITCH uuid_transfer {uuid} -> {number}: {resp or ok}"
    )
    return ok


async def terminate_upstream_call(upstream: dict) -> bool:
    """Hang up the upstream PBX's customer leg. Call this BEFORE dropping dograh's leg."""
    if not upstream:
        return False
    provider = upstream.get("provider")
    if provider == "vicidial":
        return await _ra_call_control(upstream, "HANGUP")
    if provider == "freeswitch":
        return await _fs_uuid_kill(upstream)
    return False


async def transfer_upstream_call(upstream: dict, destination: str) -> bool:
    """Transfer the upstream PBX's customer leg, dispatched by provider.

    VICIdial: always INGROUPTRANSFER (these upstream customers are bounced back
    to a queue/agent group, never a bare extension). An explicit ``ingroup:<id>``
    destination picks that in-group; anything else (including a plain
    extension/number) falls back to the in-group the call arrived on (captured
    from the ``X-VICIDIAL-ingroup_id`` header).
    FreeSWITCH: uuid_transfer the customer leg to the FS dialplan extension that
    bridges to the target. (Both tolerate a leading ``PJSIP/`` in destination.)
    """
    if not upstream:
        return False
    provider = upstream.get("provider")
    if provider == "vicidial":
        # An explicit "ingroup:<id>" destination names the in-group; everything
        # else defaults to the in-group the call arrived on.
        choice = ""
        if destination.startswith("ingroup"):
            _, _, choice = destination.partition(":")
            choice = choice.strip()
        if not choice or choice == "source":
            choice = upstream.get("ingroup_id", "")
        if not choice:
            logger.warning(
                "[upstream_pbx] VICIdial INGROUPTRANSFER requested but no in-group "
                f"id available (destination={destination!r}, captured ingroup_id "
                "is empty) -- not transferring"
            )
            return False
        return await _ra_call_control(
            upstream, "INGROUPTRANSFER", ingroup_choices=choice
        )
    if provider == "freeswitch":
        return await _fs_uuid_transfer(upstream, destination)
    return False


# --- Hardcoded post-conversation routing (VICIdial "address3" disposition) ---
# The workflow extracts ``X-VICI-UPDATE-LEAD_address3``; its final value decides
# where the customer is sent once the AI conversation ends:
#   "Y" -> INGROUPTRANSFER into in-group "dograhtest1"
#   "N" -> INGROUPTRANSFER into in-group "dograhtest2"
#   anything else (including a missing/blank value) -> do NOT transfer; the
#       customer leg is hung up instead.
# Matched case-insensitively on the stripped value.
ADDRESS3_INGROUP_ROUTES = {
    "Y": "dograhtest1",
    "N": "dograhtest2",
}


async def route_upstream_after_call(upstream: dict, fields: dict) -> tuple[str, bool]:
    """Dispatch the upstream customer leg from the extracted ``address3`` value.

    Hardcoded business routing for VICIdial (see :data:`ADDRESS3_INGROUP_ROUTES`):
    an ``address3`` of "Y"/"N" bounces the customer into in-group
    ``dograhtest1``/``dograhtest2`` respectively; any other value -- including a
    missing one -- is treated as "no transfer" and the customer leg is hung up.

    ``fields`` is the ``{lead_column: value}`` map built by
    :func:`collect_update_lead_fields` from the run's extracted variables.

    Returns ``(action, ok)`` where ``action`` is ``"transfer"`` or ``"hangup"``
    (so the caller can tear down dograh's own leg appropriately) and ``ok`` is the
    upstream API result for that action.
    """
    raw = (fields or {}).get("address3", "")
    address3 = str(raw).strip().upper()
    ingroup = ADDRESS3_INGROUP_ROUTES.get(address3)
    if ingroup:
        logger.info(
            f"[upstream_pbx] address3={raw!r} -> INGROUPTRANSFER to in-group "
            f"'{ingroup}'"
        )
        ok = await transfer_upstream_call(upstream, f"ingroup:{ingroup}")
        return "transfer", ok
    logger.info(
        f"[upstream_pbx] address3={raw!r} is not a routable disposition "
        "(expected Y or N) -- not transferring; hanging up the customer leg"
    )
    ok = await terminate_upstream_call(upstream)
    return "hangup", ok
