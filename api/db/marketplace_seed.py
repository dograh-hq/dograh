"""Seed the tool_marketplace table with curated tool entries.

Idempotent — uses INSERT ... ON CONFLICT DO UPDATE so it is safe
to call multiple times (e.g. on every deployment).
"""

from loguru import logger
from sqlalchemy import text

from api.db import db_client

# Day 1: non-OAuth tools available immediately.
# Day 2: OAuth tools activated progressively after vendor app review.
SEED_TOOLS = [
    # --- Day 1 ---
    {
        "name": "serper_search",
        "display_name": "Serper Google Search",
        "category": "mcp_direct",
        "subcategory": "Search",
        "icon": "🔍",
        "description": (
            "Effettua ricerche Google in tempo reale. L'agente può cercare "
            "informazioni aggiornate sul web e rispondere a domande basate "
            "sui risultati di ricerca."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.serper.dev",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": False,
        "oauth_auth_url": None,
        "oauth_token_url": None,
        "oauth_scopes": None,
        "oauth_client_id_env": None,
        "is_active": True,
    },
    {
        "name": "dify_connect",
        "display_name": "Dify Workflow",
        "category": "dify_workflow",
        "subcategory": "AI Workflow",
        "icon": "🔄",
        "description": (
            "Connetti un workflow Dify esistente tramite il suo URL MCP Server. "
            "Crea il workflow su Dify, attiva l'MCP Server, e incolla l'URL qui. "
            "L'agente potrà chiamare il tuo workflow Dify come un tool nativo."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": False,
        "oauth_auth_url": None,
        "oauth_token_url": None,
        "oauth_scopes": None,
        "oauth_client_id_env": None,
        "is_active": True,
    },
    # --- Day 2: OAuth tools (attivazione progressiva) ---
    {
        "name": "hubspot_crm",
        "display_name": "HubSpot CRM",
        "category": "mcp_direct",
        "subcategory": "CRM",
        "icon": "🟠",
        "description": (
            "Accedi a contatti, deal e aziende HubSpot. L'agente può cercare "
            "lead, aggiornare proprietà e creare attività CRM dalla conversazione."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.hubspot.com/v1",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": True,
        "oauth_auth_url": "https://app.hubspot.com/oauth/authorize",
        "oauth_token_url": "https://api.hubapi.com/oauth/v1/token",
        "oauth_scopes": "contacts crm.objects.contacts.read crm.objects.deals.read",
        "oauth_client_id_env": "HUBSPOT_CLIENT_ID",
        "is_active": False,
    },
    {
        "name": "calendly",
        "display_name": "Calendly",
        "category": "mcp_direct",
        "subcategory": "Scheduling",
        "icon": "📅",
        "description": (
            "Prenota e gestisci appuntamenti Calendly. L'agente può verificare "
            "disponibilità, creare eventi e inviare link di prenotazione."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.calendly.com/v1",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": True,
        "oauth_auth_url": "https://auth.calendly.com/oauth/authorize",
        "oauth_token_url": "https://auth.calendly.com/oauth/token",
        "oauth_scopes": "default",
        "oauth_client_id_env": "CALENDLY_CLIENT_ID",
        "is_active": False,
    },
    {
        "name": "slack",
        "display_name": "Slack",
        "category": "mcp_direct",
        "subcategory": "Communication",
        "icon": "💬",
        "description": (
            "Invia notifiche e messaggi su Slack. L'agente può notificare "
            "il team su eventi importanti, escalation e aggiornamenti."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.slack.com/v1",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": True,
        "oauth_auth_url": "https://slack.com/oauth/v2/authorize",
        "oauth_token_url": "https://slack.com/api/oauth.v2.access",
        "oauth_scopes": "chat:write channels:read",
        "oauth_client_id_env": "SLACK_CLIENT_ID",
        "is_active": False,
    },
    {
        "name": "notion",
        "display_name": "Notion",
        "category": "mcp_direct",
        "subcategory": "Knowledge",
        "icon": "📝",
        "description": (
            "Cerca e leggi pagine Notion. L'agente può recuperare documentazione "
            "interna, procedure e knowledge base aziendale."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.notion.com/v1",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": True,
        "oauth_auth_url": "https://api.notion.com/v1/oauth/authorize",
        "oauth_token_url": "https://api.notion.com/v1/oauth/token",
        "oauth_scopes": "read_content",
        "oauth_client_id_env": "NOTION_CLIENT_ID",
        "is_active": False,
    },
    {
        "name": "stripe",
        "display_name": "Stripe",
        "category": "mcp_direct",
        "subcategory": "Payments",
        "icon": "💳",
        "description": (
            "Consulta pagamenti, abbonamenti e clienti Stripe. L'agente può "
            "verificare lo stato di un pagamento e rispondere a domande su fatturazione."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.stripe.com/v1",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": True,
        "oauth_auth_url": "https://connect.stripe.com/oauth/authorize",
        "oauth_token_url": "https://connect.stripe.com/oauth/token",
        "oauth_scopes": "read_only",
        "oauth_client_id_env": "STRIPE_CLIENT_ID",
        "is_active": False,
    },
    {
        "name": "google_calendar",
        "display_name": "Google Calendar",
        "category": "mcp_direct",
        "subcategory": "Scheduling",
        "icon": "📆",
        "description": (
            "Leggi e crea eventi su Google Calendar. L'agente può verificare "
            "disponibilità e fissare riunioni dalla conversazione."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.googleapis.com/calendar/v1",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": True,
        "oauth_auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "oauth_token_url": "https://oauth2.googleapis.com/token",
        "oauth_scopes": "https://www.googleapis.com/auth/calendar.events",
        "oauth_client_id_env": "GOOGLE_CALENDAR_CLIENT_ID",
        "is_active": False,
    },
    {
        "name": "shopify",
        "display_name": "Shopify",
        "category": "mcp_direct",
        "subcategory": "E-commerce",
        "icon": "🛍️",
        "description": (
            "Consulta prodotti, ordini e clienti Shopify. L'agente può "
            "rispondere a domande su stato ordine e disponibilità prodotti."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.shopify.com/v1",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": True,
        "oauth_auth_url": "https://accounts.shopify.com/oauth/authorize",
        "oauth_token_url": "https://accounts.shopify.com/oauth/token",
        "oauth_scopes": "read_orders read_products read_customers",
        "oauth_client_id_env": "SHOPIFY_CLIENT_ID",
        "is_active": False,
    },
    {
        "name": "airtable",
        "display_name": "Airtable",
        "category": "mcp_direct",
        "subcategory": "Database",
        "icon": "🗂️",
        "description": (
            "Leggi e scrivi record Airtable. L'agente può consultare "
            "database strutturati e gestire workflow basati su tabelle."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.airtable.com/v1",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": True,
        "oauth_auth_url": "https://airtable.com/oauth2/v1/authorize",
        "oauth_token_url": "https://airtable.com/oauth2/v1/token",
        "oauth_scopes": "data.records:read data.records:write",
        "oauth_client_id_env": "AIRTABLE_CLIENT_ID",
        "is_active": False,
    },
    {
        "name": "zendesk",
        "display_name": "Zendesk",
        "category": "mcp_direct",
        "subcategory": "Support",
        "icon": "🎫",
        "description": (
            "Gestisci ticket e clienti Zendesk. L'agente può aprire ticket, "
            "aggiornare stato e cercare knowledge base."
        ),
        "tool_category": "mcp",
        "config_template": {
            "transport": "streamable_http",
            "url": "https://mcp.zendesk.com/v1",
            "tools_filter": [],
            "timeout_secs": 30,
            "sse_read_timeout_secs": 60,
        },
        "oauth_enabled": True,
        "oauth_auth_url": "https://lumina.zendesk.com/oauth/authorizations/new",
        "oauth_token_url": "https://lumina.zendesk.com/oauth/tokens",
        "oauth_scopes": "read write",
        "oauth_client_id_env": "ZENDESK_CLIENT_ID",
        "is_active": False,
    },
]


async def seed_tool_marketplace() -> None:
    """Insert/update all catalog tools. Idempotent — safe for repeated runs."""
    import json
    
    async with db_client.async_session() as session:
        for tool in SEED_TOOLS:
            # Convert config_template to JSON string for the query
            config_json = json.dumps(tool["config_template"])
            
            await session.execute(
                text(
                    """INSERT INTO tool_marketplace
                       (name, display_name, category, subcategory, icon, description,
                        tool_category, config_template, oauth_enabled,
                        oauth_auth_url, oauth_token_url, oauth_scopes,
                        oauth_client_id_env, is_active, sort_order)
                       VALUES
                       (:name, :display_name, :category, :subcategory, :icon, :description,
                        :tool_category, :config_template, :oauth_enabled,
                        :oauth_auth_url, :oauth_token_url, :oauth_scopes,
                        :oauth_client_id_env, :is_active, :sort_order)
                       ON CONFLICT (name) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        category = EXCLUDED.category,
                        subcategory = EXCLUDED.subcategory,
                        icon = EXCLUDED.icon,
                        description = EXCLUDED.description,
                        tool_category = EXCLUDED.tool_category,
                        config_template = EXCLUDED.config_template,
                        oauth_enabled = EXCLUDED.oauth_enabled,
                        oauth_auth_url = EXCLUDED.oauth_auth_url,
                        oauth_token_url = EXCLUDED.oauth_token_url,
                        oauth_scopes = EXCLUDED.oauth_scopes,
                        oauth_client_id_env = EXCLUDED.oauth_client_id_env,
                        is_active = EXCLUDED.is_active,
                        updated_at = now()"""
                ),
                {
                    **tool,
                    "config_template": config_json,
                    "sort_order": 0,
                },
            )
        await session.commit()

    active_count = sum(1 for t in SEED_TOOLS if t.get("is_active", True))
    logger.info(
        f"tool_marketplace seeded: {len(SEED_TOOLS)} total, "
        f"{active_count} active"
    )
