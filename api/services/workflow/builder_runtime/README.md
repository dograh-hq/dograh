# Agent builder harness

Creation collects and approves a `CallBrief`, then builds directly. Editing starts
with the saved SDK source and a separate editor prompt. Editing never submits a
new brief. Guide retrieval takes an optional topic; there are no guide lenses or
planning stages. Legacy graph node names remain only to resume old checkpoints.

## Working copy and context

The session owns SDK source, its SHA-256 revision, five undo snapshots, validation,
confirmed decision notes and complete tool receipts. Targeted edits replace one
unique text region. Writes, edits, validation and finishing check the revision;
finishing requires validation of exactly that revision. Only independent reads
can run in parallel. Explanations may finish without producing a new agent.

New editing turns receive at most 12,000 characters of recent public conversation
and 20,000 characters of initial source. Private model history drops older whole
tool exchanges around a 48,000-character budget; the initial task and newest
exchange remain intact even if larger. Large tool results expose an 8,000-character
preview. Source, earlier conversation and full tool receipts have paged read tools.
Decision notes and receipt IDs are refreshed on every build inference.

Tool create/update schemas accept a compact JSON object and enforce the same
Pydantic models as the API before showing approval. The model retrieves a category's
authoritative schema with `get_tool_definition_schema`. Creating or changing an
organisation tool still requires explicit approval in the conversation.

Incomplete tool JSON or output-limit responses execute no tools; the model gets a
repair instruction, with a three-response limit. Transient inference errors retry
at most twice. Working source survives either failure.

## Durable execution

HTTP admits a request in PostgreSQL before enqueuing an ARQ job. A request ID is
idempotent and only one run per organisation/user/session can be active. Workers
hold a PostgreSQL session lock to exclude duplicate delivery, record ordered events,
and recover checkpointed work after restart. A periodic sweep requeues abandoned
runs or admissions made during a Redis outage, with a three-attempt recovery limit.

SSE only subscribes to persisted progress. Closing a browser does not cancel work.
GET events accepts `after` or `Last-Event-ID`; Stop sets a durable cancellation flag.
Existing-agent proposals save in the worker using the existing draft revision
check. New-agent drafts still save when the user selects Test agent. Publishing
is unchanged. Tool create/update commits its replay receipt in the same transaction
as the mutation, keyed by the server's authenticated interrupt identity.

Apply migration `93d7a618f2c4` and restart both API and ARQ workers when deploying.
The worker timeout is 15 minutes; recovery sweeps run every minute. These tables
retain the same sensitive conversation material as checkpoints; apply the same
access and retention policies. Events and receipt storage are currently retained,
not automatically expired.

## Behavioral checks

`test_workflow_scenario` rehearses the validated working revision with caller
messages, supplied mock results and deterministic assertions. It reuses production
prompt composition, tool schemas and transition names. Reports include transcript,
visited nodes, tool arguments, check outcomes, inference count, character volume
and elapsed time. No assertions means no pass claim. Missing mocks and invalid
calls fail the scenario. Execution is bounded to eight caller messages and sixteen
model responses.

This is text-only coverage. Executable nodes, variable extraction, recorded audio,
MCP and transfers report that a runtime test is required. It never executes live
tools. Voice timing and external integration correctness require the normal agent
test flow. Inference currently uses the builder tier; these checks do not establish
quality of the agent's selected production model.

Run the deterministic regression cases with:

```sh
source venv/bin/activate
set -a && source api/.env.test && set +a
python -m pytest api/tests/test_workflow_builder*.py
```

The small fixture set in `api/tests/fixtures/builder/scenarios.json` tests successful
calls, failed tools, avoiding unrequested calls, corrections and missing mocks.
Those scripted replies test harness behavior, not model quality. For real inference,
provide SDK source and named scenario inputs to
`python -m scripts.evaluate_builder_scenarios --help`; compare its JSON reports
across prompt/model changes. Live evaluation consumes credits and is opt-in.
