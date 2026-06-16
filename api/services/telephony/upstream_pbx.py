"""Upstream-PBX call control (POC).

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
``initial_context["upstream_pbx"]``.

POC scope: the VICIdial agent-API connection is hardcoded below. The productized
version moves these into the ARI telephony-configuration credentials and selects
the adapter by ``upstream["provider"]`` (see the upstream-PBX seam design doc).
"""

import asyncio

import aiohttp
from loguru import logger

# --- POC hardcoded VICIdial agent-API connection (refine: telephony config creds) ---
_VICIDIAL_API_URL = "http://10.10.10.15/agc/api.php"
_VICIDIAL_API_USER = "6666"
_VICIDIAL_API_PASS = "1234"
_VICIDIAL_API_SOURCE = "dograh"

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=8)

# --- POC hardcoded FreeSWITCH ESL connection (refine: telephony config creds) ---
# FreeSWITCH owns the customer leg; dograh drives hangup/transfer over the Event
# Socket Library by the channel UUID captured from the X-PBX-UUID header.
_FS_ESL_HOST = "10.10.10.17"
_FS_ESL_PORT = 8021
_FS_ESL_PASSWORD = "ClueCon"
_FS_ESL_TIMEOUT = 8


async def _ra_call_control(upstream: dict, stage: str, **extra) -> bool:
    """Invoke VICIdial's agent API ``ra_call_control`` for the captured RA call.

    The call is identified by ``value`` (the VICIdial callerid captured from the
    ``X-VICIDIAL-callerid`` header) plus the remote-agent ``agent_user``.
    """
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


async def _fs_esl_api(command: str) -> tuple[bool, str]:
    """Run a FreeSWITCH ``api`` command over the Event Socket (inbound mode).

    Connects, authenticates, issues ``api <command>`` and returns
    ``(ok, response_body)`` where ok is True when FreeSWITCH replied ``+OK``.
    """

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

    VICIdial: ``ingroup:<id>`` -> INGROUPTRANSFER (to a queue/agent group);
    anything else -> EXTENSIONTRANSFER to that number/extension.
    FreeSWITCH: uuid_transfer the customer leg to the FS dialplan extension that
    bridges to the target. (Both tolerate a leading ``PJSIP/`` in destination.)
    """
    if not upstream:
        return False
    provider = upstream.get("provider")
    if provider == "vicidial":
        if destination.startswith("ingroup:"):
            return await _ra_call_control(
                upstream, "INGROUPTRANSFER", ingroup_choices=destination.split(":", 1)[1]
            )
        number = destination.split("/")[-1]
        return await _ra_call_control(
            upstream, "EXTENSIONTRANSFER", phone_number=number
        )
    if provider == "freeswitch":
        return await _fs_uuid_transfer(upstream, destination)
    return False
