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

import aiohttp
from loguru import logger

# --- POC hardcoded VICIdial agent-API connection (refine: telephony config creds) ---
_VICIDIAL_API_URL = "http://10.10.10.15/agc/api.php"
_VICIDIAL_API_USER = "6666"
_VICIDIAL_API_PASS = "1234"
_VICIDIAL_API_SOURCE = "dograh"

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=8)


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


async def terminate_upstream_call(upstream: dict) -> bool:
    """Hang up the upstream PBX's customer leg. Call this BEFORE dropping dograh's leg."""
    if not upstream or upstream.get("provider") != "vicidial":
        return False
    return await _ra_call_control(upstream, "HANGUP")


async def transfer_upstream_call(upstream: dict, destination: str) -> bool:
    """Transfer the upstream PBX's customer leg (scaffolding for the transfer seam).

    ``ingroup:<id>`` -> VICIdial INGROUPTRANSFER (to a queue/agent group);
    anything else -> EXTENSIONTRANSFER to that number/extension (tolerates a
    leading ``PJSIP/`` so dograh's existing transfer destination strings work).
    """
    if not upstream or upstream.get("provider") != "vicidial":
        return False
    if destination.startswith("ingroup:"):
        return await _ra_call_control(
            upstream, "INGROUPTRANSFER", ingroup_choices=destination.split(":", 1)[1]
        )
    number = destination.split("/")[-1]
    return await _ra_call_control(upstream, "EXTENSIONTRANSFER", phone_number=number)
