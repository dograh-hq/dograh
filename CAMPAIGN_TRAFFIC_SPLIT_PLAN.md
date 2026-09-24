# Campaign traffic split: plan

Let a campaign split its calls between several agents, and between versions of the same agent, by percentage. The split can be changed while the campaign is running.

## Summary

- The split is stored as JSON in `campaigns.orchestrator_metadata["traffic_split"]`. No new table, column or migration.
- The agent and version for each call are chosen at dial time from a hash of the contact's phone number. Nothing is assigned or stored when the CSV is imported.
- With an unchanged split, a contact stays on the same variant for retries and redials. Editing the split applies from the next batch, with no resync.
- Each run already records which agent and version it used (`workflow_runs.workflow_id` and `definition_id`), and the pipeline runs exactly that version. Pinned execution reuses that plumbing. Per-variant attribution is also recorded in `workflow_runs.extra["campaign_traffic_split"]` so latest and pinned variants remain distinct when they execute the same definition.

## How it works today

- A campaign has one `workflow_id` (`api/db/models.py:772`) and no version field.
- `CampaignCallDispatcher.dispatch_call` (`api/services/campaign/campaign_call_dispatcher.py:195`) calls `prepare_workflow_run_inputs` (`api/services/workflow/run_creation.py:43`). That resolves the agent's live published version and saves it on the run as `definition_id`.
- The call pipeline runs `workflow_run.definition` (`api/services/pipecat/run_pipeline.py:418`, `:681`).
- The billing check uses the run's own version when given a run id (`api/services/quota_service.py:759`).
- `create_workflow_run` already refuses a version that belongs to a different agent (`api/db/workflow_run_client.py:72`).

So a run pinned to an older version already works from start to finish. The work is choosing an agent and version for each contact and passing that choice through.

## Data shape

Stored at `campaigns.orchestrator_metadata["traffic_split"]`, next to `max_concurrency`, `schedule_config` and `circuit_breaker`:

```json
{
  "seed": "9f3a1c",
  "revision": 1,
  "variants": [
    {"id": "12:latest", "workflow_id": 12, "workflow_definition_id": null, "weight": 50},
    {"id": "12:341", "workflow_id": 12, "workflow_definition_id": 341,  "weight": 30},
    {"id": "18:latest", "workflow_id": 18, "workflow_definition_id": null, "weight": 20}
  ]
}
```

- `workflow_definition_id: null` means "latest published version at dial time", which is today's behavior. A number pins that exact version.
- `weight` is a whole percentage from 1 to 100. The weights add up to 100.
- Variant IDs are server-generated from the agent/version pair. Each run records its variant ID, split revision, selected version policy, and target weight in existing run metadata.
- `seed` is created once when the split is first set. It keeps each campaign's buckets independent, so the same phone number isn't always in the first variant. Edits keep it, and redials copy it.
- **No `traffic_split` key** means 100% to `campaigns.workflow_id` at its latest version. Existing campaigns work unchanged.
- **`campaigns.workflow_id` stays** as the primary agent: the variant with the highest weight, ties going to the first. The list page, the public API response and a few display paths read it. Update it whenever the split changes.

**Trade-off:** the database no longer checks that these ids exist. Validate them whenever the split is created or edited (see [Validation](#validation)). At dial time, a missing agent or version fails that contact, which is what happens today when the campaign's agent is missing.

## Choosing a variant at dial time

A small pure helper, for example `api/services/campaign/traffic_split.py`:

```python
def pick_variant(split: dict, phone_number: str) -> dict:
    digest = hashlib.sha256(f"{split['seed']}:{phone_number}".encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10_000
    edge = 0
    for variant in split["variants"]:
        edge += variant["weight"] * 100
        if bucket < edge:
            return variant
    return split["variants"][-1]  # unreachable while weights sum to 100
```

- **What the hash is based on:** the contact's `phone_number` from `queued_run.context_variables`. Retries and redials copy those variables, so they hash the same way. CSV validation rejects duplicate numbers. Surrounding whitespace is stripped before hashing.
- **Use a fixed hash.** Python's built-in `hash()` gives different results in every process.
- **Why not `random.random()`:** it isn't sticky. A contact who hit voicemail on agent A could get agent B on the retry, a redial reshuffles everyone, and an assignment can't be explained afterwards.
- **Weight changes can move multiple boundaries.** Going from 50/50 to 40/60 moves buckets 4000–4999, but changing five weights from 20/20/20/20/20 to 10/20/20/20/30 moves about 40% of contacts. Existing variants keep their saved order and new pairs append; reordering the request alone has no effect. No minimum-reassignment guarantee is made.
- **Counts are approximate, not exact.** On 1,000 contacts at 50/50, results within about ±30 of 500 are normal. On small lists the gap is bigger. The UI should show target and actual side by side.

## What happens when the split is edited mid-campaign

| Contacts | Effect |
|---|---|
| Already dialed, or on a live call | None. The version is saved on the run. |
| Not dialed yet | They use the new split from the next batch. `process_batch` loads the campaign once per batch, so the batch already running (up to 20 calls) finishes on the old split. |
| Waiting for a retry | They stay on their variant unless their bucket now belongs to another one (for example, their variant was removed or its share shrank). |
| On a "latest" variant when a new version is published | Later calls use the new version. This is today's behavior, and it's the reason to pin a version for a controlled test. |

Editing is already blocked only for `completed` and `failed` campaigns (`api/routes/campaign.py:705`), so running and paused campaigns can be edited.

## Changes

### Backend: dispatcher

`api/services/campaign/campaign_call_dispatcher.py`

- `process_batch` (`:75`): read `traffic_split` from the campaign once per batch. Fall back to a single variant (`campaign.workflow_id`, latest, 100%).
- `dispatch_call` (`:195`): pick the variant with `pick_variant(split, phone_number)`. Replace every `campaign.workflow_id` with `variant["workflow_id"]`:
  - `db_client.get_workflow(...)`
  - `create_workflow_run(workflow_id=...)`
  - `authorize_workflow_run_start(workflow_id=...)`
  - the webhook URL `?workflow_id=...`
  - `provider.initiate_call(workflow_id=...)`
- Pass the pinned version through by adding an optional `definition_id` to `definition_to_run` and `prepare_workflow_run_inputs` (`api/services/workflow/run_creation.py`). Pinned and latest then share one rule. In-call transfers to other agents keep using the destination's live version, which is current behavior.

### Backend: code that assumes one agent per campaign

These silently skip runs from non-primary variants. They must change whichever storage is used.

- **Stuck-call recovery:** `recover_stale_campaign_dispatches` (`api/db/campaign_client.py:129`) joins on `CampaignModel.workflow_id == WorkflowRunModel.workflow_id`. Calls to other variants would never be recovered, and their concurrency slots would stay taken. Drop that condition; the `WorkflowModel` organization join already keeps the query scoped.
- **Retries:** `record_campaign_retry_decision` (`api/db/campaign_client.py:321`) filters `WorkflowRunModel.workflow_id == campaign.workflow_id`. Contacts on other variants would never be retried. Drop it; `campaign_id` and the organization check are enough.
- **Redial:** `create_redial_campaign` (`api/db/campaign_client.py:697`) copies only some settings to the new campaign. Add `traffic_split` to that list, with the same seed, so redialed contacts stay on their variant with no mapping step.

### Backend: API (`api/routes/campaign.py`)

- **`CreateCampaignRequest` (`:198`):**
  - Add `traffic_split: TrafficSplitRequest | None` (`variants: list[{workflow_id, workflow_definition_id | None, weight}]`).
  - Make `workflow_id` optional, and require exactly one of the two. `workflow_id` alone keeps working, so the public create API (`docs/api-reference/campaigns/create.mdx`) stays backward compatible.
- **`UpdateCampaignRequest` (`:213`):** add the same `traffic_split`.
  - On update, keep the existing seed, re-run validation including the template check against the campaign's CSV (`source_id`), and update `campaigns.workflow_id` to the new primary.
  - Merge settings in `update_campaign_settings` while holding a campaign row lock. Read the existing seed/revision under that lock and update the split and primary agent in the same transaction; all settings edits use this path to avoid overwriting concurrent JSON updates.
- **Template-variable check (`:452`):** today it checks only the one agent's published version. Check every variant's version (pinned or latest) and require all of their columns, since any contact can land on any variant.
- **Billing check on start and resume (`:625`, `:974`):** resolve the selected versions and run `authorize_workflow_run_start(definition_id=...)` for each distinct agent/definition pair. Preflight must use that definition’s configuration rather than the draft-synced workflow configuration.
- **Response (`_build_campaign_response`, `:305`):**
  - Add `traffic_split` with each variant's agent name, version number (or "latest") and weight.
  - Keep `workflow_id` and `workflow_name` as the primary.
  - On the list endpoint (`:535`), look up names for every agent in every split.
- **Per-variant stats:** a new campaign-client method that groups by recorded variant identity, agent, executed definition, state, and outcome. `/traffic-stats` aggregates per variant while retaining executed version counts and removed variants. Actual share covers all attempts, including retries and earlier revisions; target is the current weight. No new column is needed; runs are indexed on `campaign_id`.

### Validation

Run on both create and update:

- 1 to 5 variants (the cap is adjustable). Weights are whole numbers of at least 1 and add up to 100.
- No duplicate `(workflow_id, workflow_definition_id)` pairs.
- Every agent belongs to the user's organization.
- A pinned version belongs to its agent and has status `published` or `archived`. **Drafts are refused** because they can change while the campaign runs.
- Every variant's template variables exist, with values, in the CSV (see the template-variable check above).

### Frontend

- **Create page (`ui/src/app/campaigns/new/page.tsx:386`):**
  - Replace the single agent dropdown with an "Agents & traffic split" list. Each row has an agent picker, a version picker ("Latest published" by default, then published and archived versions), a % field and a remove button.
  - Add "Add agent" and "Split evenly" buttons, and show a running total that must equal 100%.
  - Start with one row at 100% so the single-agent flow looks unchanged. Label it "Agent", not "Workflow".
- **Edit page (`ui/src/app/campaigns/[campaignId]/edit/page.tsx`):** the same split editor. Note that changes apply to contacts not yet dialed.
- **Detail page (`ui/src/app/campaigns/[campaignId]/page.tsx`):** a variants table showing target % and actual %, with per-variant progress and outcomes.
- **Runs table:** `ui/src/components/workflow-runs/WorkflowRunsTable.tsx:96`, `:224` build run links from the campaign's agent id. Use `run.workflow_id`. Add an agent/version column when the campaign has more than one variant.
- **List page (`ui/src/app/campaigns/page.tsx`):** primary agent name plus "+N".
- **Version list endpoint:** `GET /workflow/{id}/versions` (`api/routes/workflow.py:810`) returns the full agent JSON for every version, which is too heavy for a dropdown. Add `GET /workflow/{id}/version-summaries`, returning only `id`, `version_number`, `status` and `published_at` for published and archived definitions.
- **Regenerate the API client:** `npm run generate-client` in `ui/`.

### Docs

- `docs/api-reference/campaigns/create.mdx`: add `traffic_split`.
- Regenerate `docs/api-reference/openapi.json` (`python -m scripts.dump_docs_openapi`).
- The CSV report already has "Agent ID" and "Agent Definition ID" columns (`api/services/reports/run_report.py`) for executed-version comparisons. Use traffic statistics to distinguish latest and pinned variants that execute the same definition.

## Tests

- **`pick_variant`:**
  - The same input always gives the same variant.
  - Weights are respected within tolerance over about 100k synthetic numbers.
  - Changing 50/50 to 40/60 moves only about 10% of contacts.
  - Two seeds give independent buckets.
  - A campaign with no split gives 100% to the primary agent.
- **Dispatcher:** uses the variant's agent id, pinned version id and webhook URL, and "latest" resolves to the live version.
- **Retry and recovery:** a retry of a non-primary variant is scheduled, and stuck-call recovery finds runs from non-primary variants.
- **Redial:** copies `traffic_split` with the same seed, and a redialed contact lands on the same variant.
- **Create and update validation:**
  - Weights not adding to 100 are refused.
  - Drafts are refused, and so are agents or versions from another organization.
  - A version that belongs to a different agent is refused.
  - A missing template column is refused.
  - The old `workflow_id`-only request still works.
- **Update:** keeps the seed and updates `campaigns.workflow_id`, preserves concurrent schedule edits, and rejects edits after completion under the same lock.
- **Attribution:** latest and pinned variants executing the same definition stay distinct, removed variants remain in statistics, and cross-organization statistics requests are rejected.
- **Billing:** pinned preflight uses the selected version’s configuration.

Wrap pipeline and async tests in `asyncio.wait_for`. Run them with `api/.env.test` sourced.

## Similar systems

- **Feature-flag and experiment tools** (PostHog multivariate flags, LaunchDarkly percentage rollouts, Optimizely, Unleash, GrowthBook, nginx `split_clients`) hash a stable id with a per-experiment salt into a bucket, then match it against the variants' weight ranges. This design copies that. Assignment sticks without storing anything, changing weights can move multiple bucket boundaries, and the salt keeps experiments independent.
- **Tools that split traffic between versions of a service** (AWS Lambda alias weights, Istio/Envoy weighted routes, Argo Rollouts canaries) pick at random for each request, with no stickiness. That's fine when each request stands alone. Contacts with retries and redials don't.
- **Email A/B tests** (Mailchimp-style) split the list before sending. That gives exact counts, but any change to the split means re-splitting the list, which is why this design picks at dial time instead.

## Adopted decisions

1. **What the hash is based on:** phone number, with surrounding whitespace stripped.
2. **Drafts:** published and archived versions only.
3. **Variant cap:** 5.
4. **Stickiness after an edit:** contacts waiting for a retry can move when their bucket changes owner. A redial inherits the current split, so it can differ from an original attempt made before an edit.


## Rollout

No schema migration is required. Deploy the API and campaign workers with split support before creating or editing a split campaign; older workers only understand the primary agent. Then deploy the UI. Existing single-agent requests and campaigns remain supported.
