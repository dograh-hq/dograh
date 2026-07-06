# Tool Marketplace — Design Document

**Data:** 2026-07-06  
**Status:** Draft (post-advisor review)  
**Autore:** brainstorming session  

## Panoramica

Aggiungere a Dograh un **marketplace di tool** (catalogo di integrazioni esterne predefinite) che gli utenti possono attivare con pochi click. Il marketplace riusa l'infrastruttura MCP esistente (`McpToolSession`, `McpToolConfig`, `discover_mcp_tools`) e aggiunge un catalogo curato, connettività OAuth, e supporto per workflow Dify.

**Fase 2 (rinviata):** marketplace di "skill" (template di workflow pre-costruiti). Dograh ha già l'infrastruttura template (`workflow_templates`, `WorkflowTemplateClient`, endpoint `GET /templates` e `POST /templates/duplicate`) — sarà estesa successivamente con un flag `is_public` e un seed di template per settore.

---

## Architettura

```
┌─────────────────────────────────────────────────────────┐
│  UI (Next.js)                                            │
│  src/app/(workspace)/marketplace/                        │
│  Card grid → "Connect" → OAuth callback → tool attivo    │
│  Filtri: categoria (MCP Direct / Dify / HTTP API)        │
└──────────────────────┬──────────────────────────────────┘
                       │ REST
┌──────────────────────▼──────────────────────────────────┐
│  api/routes/marketplace.py         (nuovo)              │
│  GET  /api/v1/marketplace/tools    — lista catalogo     │
│  GET  /api/v1/marketplace/tools/{id} — dettaglio        │
│  POST /api/v1/marketplace/tools/{id}/connect — installa │
│  POST /api/v1/marketplace/tools/{id}/oauth/callback     │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  api/services/tool_marketplace.py  (nuovo)              │
│  • get_catalog(org_id, category) → list                 │
│  • get_marketplace_tool(tool_id)  → dettaglio           │
│  • install_tool(tool_id, org_id)  → ToolModel           │
│    1. Valida URL (SSRF check su URL forniti dall'utente) │
│    2. Carica config dal marketplace                      │
│    3. Crea ToolModel con definition pre-popolata         │
│    4. Se MCP: avvia auto-discovery (riusa McpToolSession)│
│    5. Associa credenziali se OAuth completato            │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  Database (PostgreSQL)                                   │
│  tool_marketplace  (nuova tabella)                       │
│  tool              (esistente)                            │
│  external_credential (esistente)                          │
└─────────────────────────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│  MCP Server (esistente, esteso)                          │
│  + list_marketplace_tools       — LLM può vedere cat.   │
│  + install_marketplace_tool     — LLM può installare     │
└─────────────────────────────────────────────────────────┘
```

### Cosa riusiamo
- `ToolModel`, `ToolCategory`, `ToolStatus` — modelli tool esistenti
- `McpToolSession`, `discover_mcp_tools` — client MCP per auto-discovery
- `ExternalCredentialModel` — storage credenziali OAuth
- `tool_management.py` — validazione, tenant isolation
- `TemplateCard`, `DuplicateWorkflowTemplate` — UI template (per Fase 2 skill)

### Cosa è nuovo
- `tool_marketplace` tabella PostgreSQL
- `api/utils/url_validation.py` — validazione SSRF
- `api/routes/marketplace.py`
- `api/services/tool_marketplace.py`
- UI marketplace (`src/app/(workspace)/marketplace/`)
- Seed data (Day 1: 2 tool; Day 2: 9 tool OAuth con attivazione progressiva)

---

## Schema Database

### Tabella: `tool_marketplace`

```sql
CREATE TABLE tool_marketplace (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL UNIQUE,       -- slug: "hubspot_crm"
    display_name    VARCHAR(200) NOT NULL,              -- "HubSpot CRM"
    category        VARCHAR(50) NOT NULL,               -- "mcp_direct" | "dify_workflow" | "http_api"
    subcategory     VARCHAR(50),                        -- "CRM", "Scheduling", etc.
    icon            VARCHAR(10),                        -- emoji: "🟠"
    description     TEXT NOT NULL,
    
    -- Configurazione tool (template per la creazione)
    tool_category   VARCHAR(50) NOT NULL DEFAULT 'mcp', -- ToolCategory enum
    config_template JSONB NOT NULL,                     -- McpToolConfig o HttpApiConfig pre-popolato
    
    -- OAuth (opzionale)
    oauth_enabled          BOOLEAN DEFAULT FALSE,
    oauth_auth_url         VARCHAR(500),
    oauth_token_url        VARCHAR(500),
    oauth_scopes           VARCHAR(500),
    oauth_redirect_path    VARCHAR(200) DEFAULT '/api/marketplace/callback',
    oauth_client_id_env    VARCHAR(100),               -- nome env var per client_id
    
    -- Metadata
    is_active       BOOLEAN DEFAULT TRUE,
    sort_order      INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### Categorie supportate

| `category` | Significato | `tool_category` risultante |
|---|---|---|
| `mcp_direct` | Server MCP pubblico (HubSpot, Calendly, ...) | `mcp` |
| `dify_workflow` | Workflow Dify esposto come MCP | `mcp` |
| `http_api` | API REST generica | `http_api` |

---

## API Routes

### `GET /api/v1/marketplace/tools`
Lista catalogo marketplace. Query params: `?category=mcp_direct`.

**Response:**
```json
[
  {
    "id": 1,
    "name": "serper_search",
    "display_name": "Serper Google Search",
    "category": "mcp_direct",
    "subcategory": "Search",
    "icon": "🔍",
    "description": "Effettua ricerche Google via API...",
    "oauth_enabled": false,
    "is_installed": false
  }
]
```

### `GET /api/v1/marketplace/tools/{tool_id}`
Dettaglio tool, incluso `is_installed` (controlla se l'org ha già un tool con questo nome).

### `POST /api/v1/marketplace/tools/{tool_id}/connect`
Installa il tool per l'organizzazione corrente. **Eredita gli stessi permission check della creazione tool esistente** (RBAC invariato).

1. Carica `tool_marketplace` record
2. Controlla che non sia già installato
3. **SSRF check:** se l'URL MCP proviene dall'utente (Dify import) → `validate_public_url()`
4. Se `oauth_enabled` e credenziali mancanti → restituisce `redirect_url` OAuth
5. Crea `ToolModel` con `config_template` dal marketplace
6. Se `tool_category == "mcp"`: avvia `discover_mcp_tools()` per popolare `discovered_tools`
7. Restituisce il tool creato

**Response (senza OAuth):**
```json
{
  "tool_uuid": "abc-123",
  "status": "active",
  "discovered_tools": [{"name": "search", "description": "..."}]
}
```

**Response (richiede OAuth):**
```json
{
  "status": "oauth_required",
  "redirect_url": "https://app.hubspot.com/oauth/authorize?..."
}
```

### `POST /api/v1/marketplace/tools/{tool_id}/oauth/callback`
Callback OAuth. Scambia il code per un token, lo salva in `external_credentials`, completa l'installazione.

---

## Sicurezza

### RBAC

Il marketplace **eredita gli stessi permission check della creazione tool esistente.**
Qualsiasi utente autorizzato a creare un `ToolModel` nella propria organizzazione può installare tool dal marketplace.
Non viene introdotto alcun ruolo aggiuntivo.

### SSRF Protection (bloccante)

Il flusso Dify "paste your MCP URL" e la creazione tool MCP generica permettono all'utente
di fornire un URL arbitrario. Prima di chiamare `discover_mcp_tools()` (o qualsiasi connessione
server-side verso un URL fornito dall'utente), il backend **deve** validare l'URL.

Implementazione in `api/utils/url_validation.py`:

```python
async def validate_public_url(url: str) -> bool:
    """Return True if the URL points to a public internet host.
    
    Blocks: localhost, 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12,
    192.168.0.0/16, 169.254.0.0/16 (cloud metadata), 0.0.0.0.
    Only http:// and https:// schemes allowed.
    Resolves hostname and checks resulting IP is not private (DNS rebinding protection).
    """
```

Questa validazione si applica sia al nuovo flusso marketplace che al flusso esistente
di creazione tool MCP (va fixato anche lì, come parte di questo lavoro).

---

## Integrazione Dify

### Flusso
1. Utente crea workflow su Dify → attiva "MCP Server" → ottiene URL
2. In Dograh, l'utente può:
   - Usare l'entry marketplace "Dify Workflow" per incollare l'URL
   - Incollare manualmente l'URL MCP nella creazione tool (flusso esistente)

### Marketplace entry per Dify
Il marketplace includerà un'entry speciale:

```json
{
  "name": "dify_connect",
  "display_name": "Dify Workflow",
  "category": "dify_workflow",
  "description": "Connetti un workflow Dify esistente tramite il suo URL MCP Server...",
  "tool_category": "mcp",
  "config_template": {
    "transport": "streamable_http",
    "url": "",
    "tools_filter": [],
    "timeout_secs": 30,
    "sse_read_timeout_secs": 60
  }
}
```

L'utente incolla il suo URL Dify, il backend esegue `validate_public_url()`, poi fa auto-discovery dei tool esposti.

### Esperienza migliorata (opzionale, 1 giorno extra)
Un pulsante "Import from Dify" nel marketplace che:
1. Apre un dialog per incollare l'URL MCP di Dify
2. Valida l'URL (SSRF check)
3. Fa auto-discovery immediato
4. Mostra i tool scoperti prima dell'installazione

---

## Seed Data

### Day 1 (lancio immediato) — tool senza OAuth

Questi tool funzionano con API key o URL fornito dall'utente. Nessuna dipendenza esterna da app review.

| Nome | Categoria | Auth | URL MCP |
|---|---|---|---|
| Serper (Google Search) | Search | API key | https://mcp.serper.dev |
| Dify Workflow | dify_workflow | URL incollato | _inserito dall'utente_ |

### Day 2 (attivazione progressiva) — tool con OAuth

Ciascuno di questi tool richiede che Dograh registri un'app OAuth presso il vendor.
L'app review è un processo esterno (giorni/settimane per vendor) — **non è effort di codice**.
Ogni vendor va attivato con un ticket separato quando l'app è approvata.

> ⚠️ Gli URL MCP nel seed sono placeholder. Prima dell'attivazione vanno verificati/sostituiti con endpoint MCP reali per ciascun servizio. In alternativa, si possono usare server MCP hosted verificati dal catalogo [mcp.so](https://mcp.so).

| Nome | Categoria | OAuth | URL MCP |
|---|---|---|---|
| HubSpot CRM | CRM | ✅ | https://mcp.hubspot.com/v1 |
| Calendly | Scheduling | ✅ | https://mcp.calendly.com/v1 |
| Shopify | E-commerce | ✅ | https://mcp.shopify.com/v1 |
| Google Calendar | Scheduling | ✅ | https://mcp.googleapis.com/calendar/v1 |
| Slack | Communication | ✅ | https://mcp.slack.com/v1 |
| Notion | Knowledge | ✅ | https://mcp.notion.com/v1 |
| Stripe | Payments | ✅ | https://mcp.stripe.com/v1 |
| Airtable | Database | ✅ | https://mcp.airtable.com/v1 |
| Zendesk | Support | ✅ | https://mcp.zendesk.com/v1 |

---

## MCP Server — Estensioni

Aggiungere al MCP server Dograh due nuovi tool per permettere a Claude/LLM di interagire col marketplace:

### `list_marketplace_tools`
```python
@mcp.tool()
async def list_marketplace_tools(
    category: Optional[str] = None
) -> list[dict]:
    """List available tools in the Dograh marketplace."""
```

### `install_marketplace_tool`
```python
@mcp.tool()
async def install_marketplace_tool(
    marketplace_tool_id: int,
    organization_id: str,
) -> dict:
    """Install a marketplace tool for an organization."""
```

---

## Frontend

### Componenti nuovi
- `src/app/(workspace)/marketplace/page.tsx` — pagina catalogo
- `src/components/marketplace/ToolCard.tsx` — card tool con "Connect" / "Installed" / "OAuth Required"
- `src/components/marketplace/CategoryFilter.tsx` — filtro per categoria (MCP Direct / Dify / HTTP API)
- `src/components/marketplace/DifyImportDialog.tsx` — dialog per incollare URL MCP Dify

### Pattern UI
Riusa `TemplateCard` come base, ma adattato per mostrare:
- Icona + categoria + nome
- Stato: "Available" / "Connect" / "OAuth Required" / "Installed"
- Descrizione tool
- Discovered tools count (se MCP)

---

## Error Handling

- **URL privato / SSRF:** `validate_public_url()` restituisce 400 con messaggio descrittivo
- **Server MCP irraggiungibile:** `discover_mcp_tools()` già restituisce `[]` senza crash — il tool viene creato comunque con `discovered_tools: []`
- **OAuth fallito:** `callback` restituisce errore descrittivo, il tool marketplace resta in stato `oauth_required`
- **Tool già installato:** `connect` restituisce 409 Conflict con il `tool_uuid` esistente
- **Credential scoping:** `validate_tool_credential_references()` già applica tenant isolation

---

## Testing

### Unit test
- `test_url_validation.py` — SSRF: localhost, IP privati, DNS rebinding, URL validi
- `test_tool_marketplace.py` — modelli Pydantic, validazione
- `test_marketplace_routes.py` — endpoint CRUD + OAuth flow
- `test_marketplace_service.py` — logica install, deduplicazione, auto-discovery

### Integration test
- Marketplace seed idempotente (run multipli = stesso stato)
- Installazione tool con MCP discovery mock
- OAuth callback flow end-to-end
- SSRF: URL privato bloccato in tutti i flussi (Dify import + creazione tool MCP esistente)

---

## Stima effort

| Attività | Giorni |
|---|---|
| Tabella `tool_marketplace` + migration | 0.5 |
| `api/utils/url_validation.py` + integrazione SSRF | 1 |
| `api/services/tool_marketplace.py` | 1.5 |
| `api/routes/marketplace.py` | 1 |
| OAuth flow — solo codice (connect + callback) | 2 |
| Seed data (Day 1: 2 tool; Day 2: 9 tool OAuth, attivazione progressiva) | 0.5 |
| Estensioni MCP server | 0.5 |
| UI marketplace page + card | 2 |
| UI Dify import dialog | 1 |
| Test (inclusi test SSRF + OAuth mock) | 2.5 |
| **Totale** | **~12.5 giorni** |

> **Nota:** la stima copre solo il codice. L'attivazione dei tool OAuth richiede app review
> esterne presso ciascun vendor (HubSpot, Slack, ecc.) — processo non incluso in questa stima.

---

## Fase 2: Skill Marketplace (rinviata)

> **Open question da risolvere in fase di design Fase 2:** quando un template/skill viene duplicato
> in una nuova organizzazione, se il template contiene `tool_uuids` che referenziano tool installati
> da marketplace, quegli UUID non esisteranno nella nuova org. Le opzioni sono: (a) auto-installare
> i tool mancanti durante la duplicazione, (b) rimuovere i `tool_uuids` dal template duplicato e
> mostrarli come "missing", (c) usare nomi simbolici invece di UUID. Da decidere.

### Infrastruttura già esistente
- `workflow_templates` tabella
- `WorkflowTemplateClient` (CRUD)
- `GET /api/v1/workflow/templates`
- `POST /api/v1/workflow/templates/duplicate`
- `TemplateCard` UI

### Cosa estendere
- Aggiungere `is_public: BOOLEAN` alla tabella `workflow_templates`
- Seed di template per settore (es. "Prenotazione Appuntamento" + Calendly, "Gestione Lead" + HubSpot)
- UI marketplace per skill (riusa TemplateCard con filtro `is_public`)
- MCP tool: `list_marketplace_skills`, `install_marketplace_skill`

### Stima Fase 2
~3 giorni (l'infrastruttura è già all'80%)
