from api.db.agent_trigger_client import AgentTriggerClient
from api.db.api_key_client import APIKeyClient
from api.db.campaign_client import CampaignClient
from api.db.embed_token_client import EmbedTokenClient
from api.db.integration_client import IntegrationClient
from api.db.looptalk_client import LoopTalkClient
from api.db.organization_client import OrganizationClient
from api.db.organization_configuration_client import OrganizationConfigurationClient
from api.db.organization_usage_client import OrganizationUsageClient
from api.db.reports_client import ReportsClient
from api.db.user_client import UserClient
from api.db.webhook_credential_client import WebhookCredentialClient
from api.db.workflow_client import WorkflowClient
from api.db.workflow_run_client import WorkflowRunClient
from api.db.workflow_template_client import WorkflowTemplateClient


class DBClient(
    WorkflowClient,
    WorkflowRunClient,
    UserClient,
    OrganizationClient,
    OrganizationConfigurationClient,
    OrganizationUsageClient,
    IntegrationClient,
    WorkflowTemplateClient,
    LoopTalkClient,
    CampaignClient,
    ReportsClient,
    APIKeyClient,
    EmbedTokenClient,
    AgentTriggerClient,
    WebhookCredentialClient,
):
    """
    Unified database client that combines all specialized database operations.

    This client inherits from:
    - WorkflowClient: handles workflow and workflow definition operations
    - WorkflowRunClient: handles workflow run operations
    - UserClient: handles user and user configuration operations
    - OrganizationClient: handles organization operations
    - OrganizationConfigurationClient: handles organization configuration operations
    - OrganizationUsageClient: handles organization usage and quota operations
    - IntegrationClient: handles integration operations
    - WorkflowTemplateClient: handles workflow template operations
    - LoopTalkClient: handles LoopTalk testing operations
    - CampaignClient: handles campaign operations
    - ReportsClient: handles reports and analytics operations
    - APIKeyClient: handles API key operations
    - EmbedTokenClient: handles embed token and session operations
    - AgentTriggerClient: handles agent trigger operations for API-based call triggering
    - WebhookCredentialClient: handles webhook credential operations
    """

    pass
