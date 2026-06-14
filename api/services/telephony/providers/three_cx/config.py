"""3CX (via Asterisk PJSIP trunk) telephony configuration schemas."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class ThreeCxConfigurationRequest(BaseModel):
    """Request schema for a 3CX trunk fronted by an Asterisk ARA instance.

    The provider owns two distinct credential groups:

    * **Asterisk side** (``ari_endpoint``, ``app_name``, ``app_password``,
      ``ws_client_name``) — how Dograh's REST + externalMedia loop talks to
      the bridging Asterisk box at call time. Identical in role to the ARI
      provider.
    * **3CX side** (``sip_domain``, ``extension``, ``sip_password``,
      ``strip_prefix``) — the upstream PBX peer credentials. Dograh never
      speaks SIP itself; these are consumed at save time by
      ``preprocess_credentials_on_save`` to provision the matching PJSIP
      endpoint/aor/auth/registration rows on the Asterisk ARA Postgres.
    """

    provider: Literal["three_cx"] = Field(default="three_cx")

    ari_endpoint: str = Field(
        ..., description="ARI base URL of the bridging Asterisk (e.g., http://asterisk:8088)"
    )
    app_name: str = Field(
        ..., description="Stasis application name registered in Asterisk"
    )
    app_password: str = Field(..., description="ARI user password")
    ws_client_name: str = Field(
        default="",
        description="websocket_client.conf connection name for externalMedia",
    )

    sip_domain: str = Field(
        ..., description="3CX SIP host/domain (e.g., 1156.3cx.cloud)"
    )
    extension: str = Field(..., description="3CX extension number (e.g., 12611)")
    sip_password: str = Field(..., description="SIP auth password for the extension")
    strip_prefix: str = Field(
        default="",
        description=(
            "Optional regex stripped from outbound destinations before the call "
            "hits the trunk. Italian deployments typically use '^\\+39'."
        ),
    )

    from_numbers: List[str] = Field(
        default_factory=list,
        description="E.164 numbers permitted as caller-id for outbound calls",
    )

    @field_validator("sip_domain")
    @classmethod
    def _strip_sip_domain(cls, v: str) -> str:
        return (v or "").strip().lower()

    @field_validator("extension")
    @classmethod
    def _strip_extension(cls, v: str) -> str:
        return (v or "").strip()


class ThreeCxConfigurationResponse(BaseModel):
    """Response schema for a 3CX configuration.

    ``app_password`` and ``sip_password`` are masked by the org route layer
    before serialization — see ``ProviderUIField.sensitive`` in __init__.py.
    """

    provider: Literal["three_cx"] = Field(default="three_cx")
    ari_endpoint: str
    app_name: str
    app_password: str  # Masked
    ws_client_name: str = ""
    sip_domain: str
    extension: str
    sip_password: str  # Masked
    strip_prefix: str = ""
    from_numbers: List[str]
