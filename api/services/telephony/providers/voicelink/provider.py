"""VoiceLink implementation of the TelephonyProvider interface.

VoiceLink carries call audio over a WebSocket bot, so there is no markup to
return and no webhook-first inbound flow:

- Outbound: ``add_lead`` queues the call and carries Dograh's media WebSocket
  URL as a per-call ``websocket_url``. The DID's outbound route must point at
  a WebSocket bot for VoiceLink to stream the call anywhere at all.
- Inbound: the DID's WebSocket bot dials
  ``/api/v1/agent-stream/voicelink/{workflow_uuid}`` directly, which lands in
  :meth:`VoiceLinkProvider.handle_external_websocket`.

VoiceLink never sends a ``connected`` event; ``start`` is the first frame.

The VoiceLink SDK client is synchronous, so every API call runs in a worker
thread to keep the event loop free.
"""

import asyncio
import json
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, TypeVar

from fastapi import HTTPException, Response
from loguru import logger
from voicelink import VoiceLinkClient
from voicelink.errors import VoiceLinkAPIError, VoiceLinkError
from voicelink.integrations.pipecat.provisioning import PipecatProvisioner
from voicelink.media.events import StartEvent, parse_message
from voicelink.webhooks import parse_webhook

from api.db import db_client
from api.enums import TelephonyCallStatus, WorkflowRunMode
from api.services.telephony import ws_auth
from api.services.telephony.base import (
    CallInitiationResult,
    NormalizedInboundData,
    ProviderPhoneNumberLookupError,
    ProviderSyncResult,
    TelephonyProvider,
)
from api.services.workflow.initial_context import merge_external_initial_context
from api.utils.common import get_backend_endpoints
from api.utils.telephony_address import normalize_telephony_address

from .config import PRODUCTION_API_BASE_URL

if TYPE_CHECKING:
    from fastapi import WebSocket

HANDSHAKE_TIMEOUT_S = 10

# VoiceLink is verified for Indian numbers only: it wants the national number
# and the country code as separate fields, and a number with the country code
# baked in fails with cause 38 ("Network out of order").
INDIA_COUNTRY_CODE = "91"

# Where a DID's bot points while no agent is attached. Dograh closes the socket
# with "Workflow not found", so a stray inbound call fails fast.
UNASSIGNED_WORKFLOW_UUID = "00000000-0000-0000-0000-000000000000"

T = TypeVar("T")


class VoiceLinkProvider(TelephonyProvider):
    PROVIDER_NAME = WorkflowRunMode.VOICELINK.value
    WEBHOOK_ENDPOINT = "voicelink"

    def __init__(self, config: Dict[str, Any]):
        self.api_token = config.get("api_token")
        # Credentials keep client_id as text (see config.py); the SDK wants an int.
        raw_client_id = config.get("client_id")
        self.client_id = int(raw_client_id) if raw_client_id not in (None, "") else None
        self.api_base_url = config.get("api_base_url") or PRODUCTION_API_BASE_URL
        self.from_numbers = config.get("from_numbers", [])
        if isinstance(self.from_numbers, str):
            self.from_numbers = [self.from_numbers]
        self.default_from_number = config.get("default_from_number")

    # ------------------------------------------------------------------ helpers

    async def _run(self, operation: Callable[[VoiceLinkClient], T]) -> T:
        """Run one blocking SDK call in a worker thread with a fresh client."""

        def _call() -> T:
            with VoiceLinkClient(
                self.api_token,
                base_url=self.api_base_url,
                client_id=self.client_id,
            ) as client:
                return operation(client)

        return await asyncio.to_thread(_call)

    @staticmethod
    def _did_digits(number: str) -> str:
        """VoiceLink identifies a DID by its digits, e.g. ``919XXXXXXXXX``."""
        return "".join(ch for ch in (number or "") if ch.isdigit())

    @staticmethod
    def _split_indian_number(number: str) -> tuple[str, str]:
        """Split ``+91XXXXXXXXXX`` into (national number, country code)."""
        digits = "".join(ch for ch in (number or "") if ch.isdigit())
        if digits.startswith(INDIA_COUNTRY_CODE) and len(digits) == 12:
            return digits[2:], INDIA_COUNTRY_CODE
        if len(digits) == 10:
            return digits, INDIA_COUNTRY_CODE
        raise ValueError(
            f"VoiceLink supports Indian (+91) numbers only; got {number!r}"
        )

    # ------------------------------------------------------------- outbound

    async def initiate_call(
        self,
        to_number: str,
        webhook_url: str,
        workflow_run_id: Optional[int] = None,
        from_number: Optional[str] = None,
        **kwargs: Any,
    ) -> CallInitiationResult:
        """Queue an outbound call with VoiceLink ``add_lead``.

        ``webhook_url`` is ignored: the media WebSocket URL travels with the
        lead instead, the same way Exotel attaches ``StreamUrl`` at dial time.
        """
        if not self.validate_config():
            raise ValueError("VoiceLink provider not properly configured")

        workflow_id = kwargs["workflow_id"]
        organization_id = kwargs["organization_id"]

        from_number = self.select_from_number(from_number)
        if not from_number:
            raise ValueError(
                "No phone numbers configured for VoiceLink. "
                "Add at least one VoiceLink DID as caller ID."
            )

        customer_number, country_code = self._split_indian_number(to_number)
        did_number = self._did_digits(from_number)

        _, wss_backend_endpoint = await get_backend_endpoints()
        stream_url = ws_auth.build_media_ws_url(
            wss_backend_endpoint, workflow_id, organization_id, workflow_run_id
        )

        logger.info(
            f"[VoiceLink] Initiating call to={to_number} from={from_number} "
            f"run={workflow_run_id}"
        )

        try:
            lead = await self._run(
                lambda client: client.calls.create(
                    did_number=did_number,
                    customer_number=customer_number,
                    country_code=country_code,
                    websocket_url=stream_url,
                    custom_parameters={"workflow_run_id": workflow_run_id},
                )
            )
        except VoiceLinkAPIError as e:
            logger.error(f"[VoiceLink] add_lead failed: {e}")
            raise HTTPException(
                status_code=e.status_code or 502,
                detail=f"VoiceLink add_lead failed: {e.message}",
            ) from e
        except VoiceLinkError as e:
            logger.error(f"[VoiceLink] add_lead failed: {e}")
            raise HTTPException(
                status_code=502, detail=f"VoiceLink add_lead failed: {e}"
            ) from e

        if lead.outbound_queue_id is None:
            raise HTTPException(
                status_code=502,
                detail=f"VoiceLink response missing outbound_queue_id: {lead.raw}",
            )
        call_id = str(lead.outbound_queue_id)

        return CallInitiationResult(
            call_id=call_id,
            status="queued",
            caller_number=from_number,
            provider_metadata={"call_id": call_id},
            raw_response=lead.raw,
        )

    async def get_call_status(self, call_id: str) -> Dict[str, Any]:
        if not self.validate_config():
            raise ValueError("VoiceLink provider not properly configured")
        return await self._run(lambda client: client.call_logs.details(call_id))

    async def get_available_phone_numbers(self) -> List[str]:
        return list(self.from_numbers)

    def validate_config(self) -> bool:
        return bool(self.api_token)

    async def validate_phone_number(self, address: str) -> ProviderSyncResult:
        """Check the DID against the account's purchased-numbers list."""
        normalized = normalize_telephony_address(address)
        if normalized.address_type != "pstn":
            return ProviderSyncResult(ok=True)
        if not self.validate_config():
            raise ProviderPhoneNumberLookupError(
                "VoiceLink API token is required to validate phone-number ownership"
            )

        wanted = self._did_digits(normalized.canonical)
        try:
            page = await self._run(
                lambda client: client.dids.list(search=wanted, per_page=100)
            )
        except VoiceLinkAPIError as e:
            raise ProviderPhoneNumberLookupError(
                f"VoiceLink API {e.status_code}: {e.message}",
                status_code=e.status_code,
            ) from e
        except VoiceLinkError as e:
            raise ProviderPhoneNumberLookupError(
                f"VoiceLink phone-number lookup failed: {e}"
            ) from e

        if any(self._did_digits(did.did_number or "") == wanted for did in page):
            return ProviderSyncResult(ok=True)
        return ProviderSyncResult(
            ok=False,
            message=(
                f"Phone number {normalized.canonical} is not owned by this "
                "VoiceLink account. Purchase or assign it in VoiceLink first."
            ),
        )

    async def verify_webhook_signature(
        self, url: str, params: Dict[str, Any], signature: str
    ) -> bool:
        # Unused: the media URL is attached to the lead at dial time.
        logger.warning("verify_webhook_signature called for VoiceLink - unexpected")
        return False

    async def get_webhook_response(
        self, workflow_id: int, organization_id: int, workflow_run_id: int
    ) -> str:
        return ""

    async def get_call_cost(self, call_id: str) -> Dict[str, Any]:
        # VoiceLink exposes no per-call cost API; billing is per channel.
        return {"cost_usd": 0.0, "duration": 0, "status": "unknown", "raw_response": {}}

    def parse_status_callback(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Map a VoiceLink Call Event webhook to Dograh's generic shape."""
        event = parse_webhook(data)
        call = event.call
        status_raw = (call.status if call else None) or ""
        # ``initiate_call`` records the lead's outbound_queue_id as the call id.
        # The webhook's own ``call.id`` is a different (UUID) identifier, so
        # prefer the queue id VoiceLink echoes back in customParameters.
        custom = (call.custom_parameters if call else None) or {}
        call_id = custom.get("outboundQueueId") or (call.id if call else None)
        return {
            "call_id": str(call_id) if call_id is not None else "",
            "status": TelephonyCallStatus.from_raw(status_raw) or status_raw,
            "from_number": call.from_number if call else None,
            "to_number": call.to_number if call else None,
            "direction": call.direction if call else None,
            "duration": call.duration_sec if call else None,
            "extra": data,
        }

    # ------------------------------------------------------------- media

    @staticmethod
    async def _receive_start(
        websocket: "WebSocket", workflow_run_id: int
    ) -> Optional[tuple[str, StartEvent]]:
        """Read VoiceLink's first frame and require it to be ``start``.

        Returns the raw text too: the transport replays it into the frame
        serializer, which learns the stream id and audio format from it.
        Closes the socket and returns ``None`` on timeout or a bad frame.
        """
        try:
            first_msg = await asyncio.wait_for(
                websocket.receive_text(), timeout=HANDSHAKE_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"VoiceLink handshake timed out for workflow_run {workflow_run_id}"
            )
            await websocket.close(code=4408, reason="Handshake timeout")
            return None

        try:
            event = parse_message(first_msg)
        except ValueError as e:
            logger.error(f"VoiceLink first frame is not valid JSON: {e}")
            await websocket.close(code=4400, reason="Invalid start frame")
            return None

        if not isinstance(event, StartEvent):
            logger.error(f"Expected VoiceLink 'start' event first, got: {event.type}")
            await websocket.close(code=4400, reason="Expected start event")
            return None
        if not event.stream_sid or not event.call_sid:
            logger.error(f"Missing stream_sid/call_sid in VoiceLink start: {event.raw}")
            await websocket.close(code=4400, reason="Missing stream identifiers")
            return None
        return first_msg, event

    async def handle_websocket(
        self,
        websocket: "WebSocket",
        workflow_id: int,
        organization_id: int,
        workflow_run_id: int,
    ) -> None:
        """Outbound media socket, dialled back by VoiceLink after ``add_lead``."""
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        try:
            received = await self._receive_start(websocket, workflow_run_id)
            if received is None:
                return
            start_msg, start = received

            logger.info(
                f"VoiceLink WebSocket connected for workflow_run {workflow_run_id} "
                f"stream_sid={start.stream_sid} call_sid={start.call_sid}"
            )

            await run_pipeline_telephony(
                websocket,
                provider_name=self.PROVIDER_NAME,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
                call_id=str(start.call_sid),
                transport_kwargs={"start_message": start_msg},
            )
        except Exception as e:
            logger.error(f"Error in VoiceLink WebSocket handler: {e}")
            raise

    async def handle_external_websocket(
        self,
        websocket: "WebSocket",
        *,
        organization_id: int,
        workflow_id: int,
        workflow_run_id: int,
        params: Dict[str, str],
    ) -> None:
        """Inbound media socket, dialled by the DID's VoiceLink WebSocket bot.

        The agent-stream route builds this provider with an empty config, so
        the org's VoiceLink configuration is looked up here, matched by the
        ``account_sid`` VoiceLink stamps on the ``start`` frame (its client id).
        """
        from api.services.pipecat.run_pipeline import run_pipeline_telephony

        try:
            received = await self._receive_start(websocket, workflow_run_id)
            if received is None:
                return
            start_msg, start = received

            config = await self._find_config_for_account(
                organization_id, start.account_sid
            )
            if config is None:
                logger.error(
                    f"VoiceLink agent-stream: no configuration in org "
                    f"{organization_id} for account_sid={start.account_sid}"
                )
                await websocket.close(code=4400, reason="Unknown VoiceLink account")
                return

            builtin_context = {
                "caller_number": start.from_number,
                "called_number": start.to_number,
                "direction": "inbound",
                # The transport loads credentials from this; without it Dograh
                # falls back to the org's default config, which may be another
                # provider.
                "telephony_configuration_id": config.id,
            }
            custom_context = merge_external_initial_context(
                {}, start.custom_parameters or {}
            )
            await db_client.update_workflow_run(
                run_id=workflow_run_id,
                initial_context={
                    key: value
                    for key, value in {
                        **{
                            k: v
                            for k, v in custom_context.items()
                            if k not in builtin_context
                        },
                        **builtin_context,
                    }.items()
                    if value is not None
                },
                gathered_context={
                    "call_id": start.call_sid,
                    "voicelink_stream_sid": start.stream_sid,
                },
                logs={
                    "inbound_webhook": {
                        "callSid": start.call_sid,
                        "streamSid": start.stream_sid,
                        "accountSid": start.account_sid,
                        "from": start.from_number,
                        "to": start.to_number,
                        "encoding": start.encoding,
                        "sampleRate": start.sample_rate,
                    },
                },
            )

            logger.info(
                f"VoiceLink agent-stream connected for workflow_run "
                f"{workflow_run_id} stream_sid={start.stream_sid} "
                f"call_sid={start.call_sid} telephony_configuration_id={config.id}"
            )

            await run_pipeline_telephony(
                websocket,
                provider_name=self.PROVIDER_NAME,
                workflow_id=workflow_id,
                workflow_run_id=workflow_run_id,
                organization_id=organization_id,
                call_id=str(start.call_sid),
                transport_kwargs={"start_message": start_msg},
            )
        except Exception as e:
            logger.error(f"Error in VoiceLink agent-stream handler: {e}")
            raise

    async def _find_config_for_account(
        self, organization_id: int, account_sid: Optional[str]
    ):
        """This org's VoiceLink configuration for the stream's account.

        Scoped to ``organization_id`` so another org's configuration can never
        match. A configuration with a ``client_id`` must equal ``account_sid``;
        one without is accepted only when it is the org's sole VoiceLink
        configuration, since there is then nothing to confuse it with.
        """
        candidates = await db_client.list_telephony_configurations_by_provider(
            organization_id, self.PROVIDER_NAME
        )
        if account_sid:
            for cand in candidates:
                client_id = (cand.credentials or {}).get("client_id")
                if client_id is not None and str(client_id) == str(account_sid):
                    return cand
        if len(candidates) == 1 and not (candidates[0].credentials or {}).get(
            "client_id"
        ):
            return candidates[0]
        return None

    # ------------------------------------------------------------- automatic setup
    #
    # Dograh calls these when a number is added and when an agent is attached to
    # it. They do on VoiceLink what the portal steps would: one WebSocket bot per
    # DID, the DID routed to it inbound and outbound, and the bot's URL set to
    # the attached agent's agent-stream endpoint. Every step is idempotent, so
    # re-saving in Dograh only updates what changed.

    def _bot_name(self, address: str) -> str:
        return f"dograh-{self._did_digits(address)}"

    async def _agent_stream_url(self, workflow_uuid: str) -> str:
        _, wss_backend_endpoint = await get_backend_endpoints()
        return f"{wss_backend_endpoint}/api/v1/agent-stream/voicelink/{workflow_uuid}"

    def _sync_bot(
        self,
        client: VoiceLinkClient,
        address: str,
        websocket_url: str,
        *,
        inbound: bool,
        keep_existing_url: bool = False,
    ) -> None:
        """Ensure the DID's bot and one of its routes on VoiceLink.

        Runs in a worker thread (the SDK is synchronous). With
        ``keep_existing_url`` an existing bot keeps the URL it already has,
        so re-adding a number never detaches an agent that is still attached.
        """
        provisioner = PipecatProvisioner(client, client_id=self.client_id)
        name = self._bot_name(address)
        bot = provisioner.find_bot(name) if keep_existing_url else None
        if bot is None:
            bot, _ = provisioner.ensure_bot(bot_name=name, websocket_url=websocket_url)
        did = self._did_digits(address)
        if inbound:
            provisioner.route_inbound_to_bot(did=did, bot_id=bot.id)
        else:
            provisioner.route_outbound_to_bot(did=did, bot_id=bot.id)

    async def provision_phone_number(self, address: str) -> ProviderSyncResult | None:
        """Own the number, then route its outbound calls to a Dograh bot.

        VoiceLink only streams a call when the DID's route points at a
        WebSocket bot, so outbound is routed here. Inbound is routed once an
        agent is attached (:meth:`configure_inbound`); until then the bot
        points at an endpoint Dograh refuses.
        """
        if normalize_telephony_address(address).address_type != "pstn":
            return ProviderSyncResult(ok=True)
        owned = await self.validate_phone_number(address)
        if not owned.ok:
            return owned
        placeholder = await self._agent_stream_url(UNASSIGNED_WORKFLOW_UUID)
        try:
            await self._run(
                lambda client: self._sync_bot(
                    client, address, placeholder, inbound=False, keep_existing_url=True
                )
            )
        except VoiceLinkError as e:
            return ProviderSyncResult(ok=False, message=f"VoiceLink setup failed: {e}")
        return ProviderSyncResult(ok=True)

    async def configure_inbound(
        self, address: str, webhook_url: Optional[str]
    ) -> ProviderSyncResult:
        """Point the DID's bot at the attached agent's agent-stream endpoint.

        Dograh passes its generic ``/inbound/run`` URL, but VoiceLink needs the
        WebSocket endpoint of one specific agent, so the attached workflow is
        looked up here. Detaching parks the bot on an unassigned endpoint that
        Dograh refuses, so an inbound call fails fast instead of reaching a
        stale agent; outbound is unaffected because ``add_lead`` carries its
        own per-call URL.
        """
        if not self.validate_config():
            return ProviderSyncResult(
                ok=False, message="VoiceLink provider not properly configured"
            )
        if webhook_url is None:
            url = await self._agent_stream_url(UNASSIGNED_WORKFLOW_UUID)
        else:
            route = await self._find_phone_route(address)
            phone = route[1] if route else None
            if phone is None or phone.inbound_workflow_id is None:
                return ProviderSyncResult(
                    ok=False,
                    message=(
                        f"No active number {address} with an attached agent was "
                        "found on this VoiceLink configuration"
                    ),
                )
            workflow = await db_client.get_workflow_by_id(phone.inbound_workflow_id)
            if workflow is None or not workflow.workflow_uuid:
                return ProviderSyncResult(
                    ok=False, message="The attached agent has no UUID yet"
                )
            url = await self._agent_stream_url(str(workflow.workflow_uuid))
        try:
            await self._run(
                lambda client: self._sync_bot(client, address, url, inbound=True)
            )
        except VoiceLinkError as e:
            return ProviderSyncResult(ok=False, message=f"VoiceLink setup failed: {e}")
        return ProviderSyncResult(ok=True)

    async def _find_phone_route(self, address: str):
        """This configuration's phone-number row for ``address``.

        The factory does not hand the provider its organization, so the lookup
        keys on ``client_id`` when one is configured; otherwise the number must
        be unique across VoiceLink configurations, which Dograh's inbound
        dispatcher requires anyway.
        """
        if self.client_id is not None:
            return await db_client.find_inbound_route_by_account(
                provider=self.PROVIDER_NAME,
                account_id_field="client_id",
                account_id=str(self.client_id),
                to_number=address,
            )
        return await db_client.find_inbound_route_by_called_number(
            provider=self.PROVIDER_NAME, to_number=address
        )

    # ------------------------------------------------------------- inbound webhooks
    #
    # VoiceLink has no webhook-first inbound flow: its WebSocket bot dials the
    # agent-stream endpoint directly. These exist to satisfy the interface.

    @classmethod
    def can_handle_webhook(
        cls, webhook_data: Dict[str, Any], headers: Dict[str, str]
    ) -> bool:
        return False

    @staticmethod
    def parse_inbound_webhook(webhook_data: Dict[str, Any]) -> NormalizedInboundData:
        return NormalizedInboundData(
            provider=VoiceLinkProvider.PROVIDER_NAME,
            call_id=str(webhook_data.get("call_sid") or ""),
            from_number=str(webhook_data.get("from") or ""),
            to_number=str(webhook_data.get("to") or ""),
            direction="inbound",
            call_status=str(webhook_data.get("status") or ""),
            account_id=webhook_data.get("account_sid"),
            raw_data=webhook_data,
        )

    @staticmethod
    def validate_account_id(config_data: dict, webhook_account_id: str) -> bool:
        if not webhook_account_id:
            return False
        stored = config_data.get("client_id")
        return stored is not None and str(stored) == str(webhook_account_id)

    async def verify_inbound_signature(
        self,
        url: str,
        webhook_data: Dict[str, Any],
        headers: Dict[str, str],
        body: str = "",
    ) -> bool:
        # Fail closed: VoiceLink sends no inbound webhooks to verify.
        return False

    async def start_inbound_stream(
        self,
        *,
        websocket_url: str,
        workflow_run_id: int,
        normalized_data: NormalizedInboundData,
        backend_endpoint: str,
    ) -> Any:
        raise NotImplementedError(
            "VoiceLink inbound calls use the agent-stream WebSocket, not a webhook"
        )

    @staticmethod
    def generate_error_response(error_type: str, message: str) -> tuple:
        return Response(
            content=json.dumps({"error": error_type, "message": message}),
            media_type="application/json",
        )

    # ------------------------------------------------------------- transfers

    async def transfer_call(
        self,
        destination: str,
        transfer_id: str,
        conference_name: str,
        timeout: int = 30,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        raise NotImplementedError("VoiceLink provider does not support call transfers")

    def supports_transfers(self) -> bool:
        return False
