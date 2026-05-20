# Telephony

Shared telephony code lives here. Provider-specific code lives in `providers/`;
read `providers/AGENTS.md` before changing a provider package.

- Keep cross-provider contracts, registry/factory wiring, shared status/transfer handling, and org-scoped config resolution in this folder.
- Keep provider-specific transports, serializers, config models, and webhook handlers in `providers/`.
- Resolve providers through the shared telephony helpers in this folder; do not instantiate provider classes directly from routes, tasks, or unrelated services.
- Keep telephony config lookups tenant-safe and respect any run-scoped telephony configuration carried on a workflow run.
- Keep provider-specific HTTP routes in provider packages; shared route glue belongs in `api/routes/`.
