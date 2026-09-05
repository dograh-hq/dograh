"""What a Cloudonix configuration still needs before it can carry calls.

Two shapes share this provider and they fail differently:

* **Dograh-managed** (``managed_by == "dograh-mps"``) — Dograh owns the
  Cloudonix domain and hands it to the customer empty. Nothing routes until
  the customer points their own SIP carrier or PBX at it, so a missing
  outbound trunk means there is no path off the platform at all.
* **Customer-owned** — the customer brought their own Cloudonix account and
  may already have trunks configured in the Cockpit that Dograh cannot see.
  A missing Dograh-managed trunk is then only a missing pin, not a dead end.

Either way Cloudonix rejects an outbound call with no ``caller-id``, so at
least one active phone number is required in both.
"""

from typing import Any

from api.services.telephony.registry import (
    ConfigurationSetupState,
    ProviderSetupChecklist,
    SetupStep,
)

from .config import MANAGED_BY

MANAGED_DOCS_URL = "https://docs.dograh.com/integrations/telephony/dograh-sip"
SELF_SERVE_DOCS_URL = "https://docs.dograh.com/integrations/telephony/cloudonix"


def resolve_setup_checklist(
    credentials: dict[str, Any],
    state: ConfigurationSetupState,
) -> ProviderSetupChecklist:
    managed = credentials.get("managed_by") == MANAGED_BY
    connected = bool(credentials.get("bearer_token") and credentials.get("domain_id"))

    steps = [
        SetupStep(
            key="sip_domain",
            title=(
                "SIP domain provisioned" if managed else "Cloudonix credentials saved"
            ),
            description=(
                "Dograh provisioned a Cloudonix SIP domain for this "
                "organization. Its inbound hostname and outbound origin IP "
                "are listed under SIP connectivity below — you will need "
                "them when configuring your carrier."
                if managed
                else "Your Cloudonix bearer token and domain are stored and "
                "used for every call on this configuration."
            ),
            complete=connected,
            blocks_outbound=True,
        ),
        SetupStep(
            key="outbound_trunk",
            title="Connect your SIP carrier for outbound calls",
            description=(
                "Under Outbound trunks, add a trunk pointing at your SIP "
                "carrier or PBX and allow its origin IP on your side. "
                "Without one Dograh has nowhere to send outbound calls."
                if managed
                else "Optional: add a trunk under Outbound trunks to pin "
                "Dograh's calls to one route. Without it Cloudonix picks "
                "among the active trunks on your domain."
            ),
            complete=state.enabled_trunk_count > 0,
            # Only the managed domain starts empty; a customer-owned domain may
            # already route through trunks configured in the Cloudonix Cockpit.
            blocks_outbound=managed,
        ),
        SetupStep(
            key="caller_id",
            title="Add a phone number to use as caller ID",
            description=(
                "Add at least one number your carrier delivers to you. "
                "Cloudonix requires a caller ID on every outbound call and "
                "rejects the call without one."
            ),
            complete=state.active_phone_number_count > 0,
            blocks_outbound=True,
        ),
    ]

    # Omitted entirely below two trunks rather than reported as already done:
    # with one trunk the call path falls back to it and there is nothing to
    # assign, so a ticked step would credit the customer for work they never
    # did — under a description asserting a second trunk that isn't there.
    if state.enabled_trunk_count > 1:
        steps.append(
            SetupStep(
                key="trunk_assignment",
                title="Tell Dograh which trunk each number dials out on",
                description=(
                    "This configuration has more than one trunk, so a number's "
                    "carrier is no longer obvious. Assign each phone number to "
                    "the trunk whose carrier authorised it — otherwise Dograh "
                    "cannot pin the call and Cloudonix picks among your active "
                    "trunks."
                ),
                complete=state.unassigned_active_phone_number_count == 0,
            )
        )

    steps.append(
        SetupStep(
            key="inbound_routing",
            title="Point a number at an agent for inbound calls",
            description=(
                "Only needed to receive calls: set an inbound workflow on a "
                "phone number below, and bind its DNID to the Voice "
                "Application in your Cloudonix Cockpit."
            ),
            complete=state.inbound_routed_phone_number_count > 0,
        )
    )

    return ProviderSetupChecklist.from_steps(
        steps,
        docs_url=MANAGED_DOCS_URL if managed else SELF_SERVE_DOCS_URL,
    )


__all__ = ["resolve_setup_checklist"]
