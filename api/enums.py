from enum import Enum


class IntegrationAction(Enum):
    ALL_CALLS = "All Calls"
    QUALIFIED_CALLS = "Qualified Calls"


class Environment(Enum):
    LOCAL = "local"
    PRODUCTION = "production"
    TEST = "test"


class CallType(Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class TelephonyCallStatus(str, Enum):
    INITIATED = "initiated"
    RINGING = "ringing"
    IN_PROGRESS = "in-progress"
    ANSWERED = "answered"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    NO_ANSWER = "no-answer"
    CANCELED = "canceled"
    ERROR = "error"

    @classmethod
    def from_raw(cls, value: object) -> "TelephonyCallStatus | None":
        if isinstance(value, cls):
            return value

        if value in (None, ""):
            return None

        try:
            return cls(str(value).lower())
        except ValueError:
            return None


class WorkflowRunMode(Enum):
    ARI = "ari"
    PLIVO = "plivo"
    TWILIO = "twilio"
    VONAGE = "vonage"
    VOBIZ = "vobiz"
    CLOUDONIX = "cloudonix"
    TELNYX = "telnyx"
    WEBRTC = "webrtc"
    SMALLWEBRTC = "smallwebrtc"
    TEXTCHAT = "textchat"

    # Historical, not used anymore. Don't
    # use and don't remove
    STASIS = "stasis"
    VOICE = "VOICE"
    CHAT = "CHAT"


class StorageBackend(Enum):
    """Storage backend enumeration.

    Currently supported backends:
    - S3: Amazon S3
    - MINIO: MinIO

    Future extensibility: Additional backends like GCS, Azure can be added by:
    1. Adding new enum values as strings
    2. Implementing storage logic in services/storage.py
    3. Database will automatically support new values via SQLAlchemy Enum type
    """

    # Currently implemented backends
    S3 = "s3"  # AWS S3 for cloud deployments
    MINIO = "minio"  # MinIO for local/OSS deployments

    @classmethod
    def get_current_backend(cls):
        """Get current backend based on ENABLE_AWS_S3 flag."""
        from api.constants import ENABLE_AWS_S3

        if ENABLE_AWS_S3:
            return cls.S3
        else:
            return cls.MINIO


class WorkflowRunState(Enum):
    INITIALIZED = "initialized"  # Workflow run created, ready for connection
    RUNNING = "running"  # Websocket connected and pipeline active
    COMPLETED = "completed"  # Workflow run finished


class WorkflowRunStatus(Enum):
    # historical modes
    VOICE = "VOICE"
    CHAT = "CHAT"


class OrganizationConfigurationKey(Enum):
    CONCURRENT_CALL_LIMIT = "CONCURRENT_CALL_LIMIT"
    TELEPHONY_CONFIGURATION = (
        "TELEPHONY_CONFIGURATION"  # Stores all providers + active one
    )
    TWILIO_CONFIGURATION = (
        "TWILIO_CONFIGURATION"  # Deprecated - for backward compatibility
    )
    LANGFUSE_CREDENTIALS = (
        "LANGFUSE_CREDENTIALS"  # Org-level Langfuse tracing credentials
    )
    MODEL_CONFIGURATION_V2 = (
        "MODEL_CONFIGURATION_V2"  # Org-level v2 AI model configuration
    )
    ORGANIZATION_PREFERENCES = "ORGANIZATION_PREFERENCES"  # Org-level defaults such as timezone/test call number
    MODEL_CONFIGURATION_PREFERENCES = "MODEL_CONFIGURATION_PREFERENCES"  # Deprecated; read fallback for old org preferences


class UserConfigurationKey(Enum):
    """Keys for the per-user keyed JSON store (user_configurations)."""

    MODEL_CONFIGURATION = (
        "MODEL_CONFIGURATION"  # Legacy per-user v1 AI model configuration
    )
    ONBOARDING = "ONBOARDING"  # Post-signup onboarding state (gate, tooltips, actions)


class WorkflowStatus(Enum):
    """Workflow status values"""

    ACTIVE = "active"
    ARCHIVED = "archived"
    # Future statuses can be added here like:
    # DRAFT = "draft"
    # PAUSED = "paused"


class RedisChannel(Enum):
    """Redis pub/sub channel names"""

    CAMPAIGN_EVENTS = "campaign_events"
    WORKER_SYNC = "worker_sync"


class TriggerState(Enum):
    """Agent trigger state values"""

    ACTIVE = "active"
    ARCHIVED = "archived"


class WebhookCredentialType(Enum):
    """Webhook credential authentication types"""

    NONE = "none"  # No authentication
    API_KEY = "api_key"  # API key in header
    BEARER_TOKEN = "bearer_token"  # Bearer token auth
    BASIC_AUTH = "basic_auth"  # Username/password
    CUSTOM_HEADER = "custom_header"  # Custom header key-value


class ToolCategory(Enum):
    """Tool category types"""

    HTTP_API = "http_api"  # Custom HTTP API calls (implemented)
    END_CALL = "end_call"  # End call tool
    TRANSFER_CALL = "transfer_call"  # Transfer call to phone number (Twilio only)
    CALCULATOR = "calculator"  # Built-in calculator tool
    NATIVE = "native"  # Built-in integrations (future: dtmf_input)
    INTEGRATION = "integration"  # Third-party integrations (future: Google Calendar, Salesforce, etc.)
    MCP = "mcp"  # Customer-provided MCP server exposing a tool catalog


class ToolStatus(Enum):
    """Tool status values"""

    ACTIVE = "active"  # Tool is available for use
    ARCHIVED = "archived"  # Tool is soft-deleted
    DRAFT = "draft"  # Tool is being configured (not ready for use)


class PostHogEvent(str, Enum):
    """PostHog event names — backend events only."""

    WORKFLOW_CREATED = "workflow_created"
    WORKFLOW_PUBLISHED = "workflow_published"
    WORKFLOW_DUPLICATED = "workflow_duplicated"
    CALL_STARTED = "call_started"
    CALL_COMPLETED = "call_completed"
    CALL_FAILED = "call_failed"
    TELEPHONY_CONFIGURED = "telephony_configured"
    KNOWLEDGE_BASE_CREATED = "knowledge_base_created"
    TOOL_CREATED = "tool_created"
    AGENT_EMBEDDED = "agent_embedded"
    SIGNED_UP = "signed_up"
    SIGNED_IN = "signed_in"
    ORGANIZATION_CREATED = "organization_created"
    ORGANIZATION_USER_ASSOCIATED = "organization_user_associated"
