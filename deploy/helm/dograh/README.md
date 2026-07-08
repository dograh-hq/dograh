# Dograh Helm chart

Deploys Dograh on Kubernetes with decomposed backend workloads (web,
ARQ workers, telephony singleton, campaign singleton), Next.js UI, and
coturn for WebRTC media relay. Implements the architecture defined in
`HELM_DEPLOYMENT_PLAN.md` at the repo root.

## Status

v1, alpha. Validated with `helm lint` and `helm template`. Not yet
exercised against a live cluster.

## Quick start

```bash
cd deploy/helm/dograh

# Install with defaults (all internal deps, Gateway API exposure).
# The bundled Postgres/Redis/MinIO are in-chart manifests on official upstream
# images — no `helm dependency` / subchart pull step needed.
helm install dograh . \
  --set secrets.ossJwtSecret="$(openssl rand -hex 32)" \
  --set secrets.turnSecret="$(openssl rand -hex 32)" \
  --set exposure.gatewayApi.gatewayClassName=istio
```

See `examples/values-single-node.yaml`, `examples/values-managed.yaml`,
and `examples/values-aws.yaml` for topology-specific overrides.

## Architecture summary

| Workload                     | Replicas    | Strategy        | Notes |
|------------------------------|-------------|-----------------|-------|
| `dograh-web`                 | 2 (HPA opt) | RollingUpdate   | Long-lived WS, graceful drain |
| `dograh-arq-worker`          | 1 (knob)    | RollingUpdate   | Stateless |
| `dograh-ari-manager`         | **1 fixed** | **Recreate**    | Telephony singleton |
| `dograh-campaign-orchestrator` | **1 fixed** | **Recreate**  | Campaign singleton (in-memory locks) |
| `dograh-ui`                  | 2           | RollingUpdate   | Next.js SSR |
| `dograh-coturn`              | 1           | Recreate        | LoadBalancer Service, port-pinned |

HTTP traffic: Gateway API (default) or Ingress (fallback).
TURN traffic: dedicated L4 Service of type `LoadBalancer`.

## Decisions log

These are choices the chart made where `HELM_DEPLOYMENT_PLAN.md` was
silent. Each is exposed in `values.yaml` for operator override.

- **terminationGracePeriodSeconds for web: 600s.** Covers a 10-minute
  call; tune to your call-length distribution.
- **preStop sleep: 15s.** Conservative window for the gateway/ingress
  to observe pod NotReady and stop dispatching new connections.
- **Liveness probes on singletons: `exec` (`pgrep`).** No HTTP endpoint
  exists on ari-manager / campaign-orchestrator; process-alive check is
  the simplest correct signal.
- **HPA on web: CPU/memory, disabled by default.** Plan recommends HPA
  but CPU/memory is a poor signal for WS workloads. Default
  `autoscaling.web.enabled=false`; flip on with a knowing eye and plan
  to replace with a connection-count metric.
- **Singleton replica counts: hard-coded.** No `replicaCount` knob
  exposed on ari-manager / campaign-orchestrator. Prevents accidental
  `kubectl scale` corrupting in-memory dedup state.
- **MinIO browser exposure: shared host, path prefix `/voice-audio/`.**
  Mirrors current nginx behavior. Operators wanting a separate
  hostname can override by editing `httproute-minio.yaml` or
  `ingress.yaml` post-install.
- **NetworkPolicy: not in v1.** TODO below.
- **ServiceMonitor / Prometheus: not in v1.** TODO below.
- **TURN TLS (turns://): not in v1.** Original docker-compose exposed
  port 5349 but never wired certs. Chart scopes v1 to plain TURN.

## `/tmp` audit (review fix #6)

Resolved. End-of-call artifacts (recordings, transcript) are uploaded to
object storage directly from the web process
(`api/services/workflow_run_artifacts.py`) before the ARQ completion job
is enqueued; the job carries only the workflow run id. No file handoff
crosses a pod boundary, so web and arq-worker pods need no shared
volume. The remaining `/tmp` uses (`audio_file_cache.py`,
`knowledge_base_processing.py`) write and read within a single process.

## Open TODOs (deferred from v1)

- **Leader election for singletons.** Adopt Kubernetes lease-based
  leader election so `ari-manager` / `campaign-orchestrator` can run
  HA. Until then, replicas remain hard-coded to 1.
- **Connection-count HPA metric.** Expose active WS sessions per pod
  (Prometheus or KEDA) and replace CPU/memory HPA target.
- **NetworkPolicy.** Add default-deny + explicit egress to Postgres,
  Redis, MinIO/S3, and (for ari-manager) Asterisk.
- **ServiceMonitor.** First-class Prometheus integration once
  observability stack is selected.
- **TURN TLS (turns://).** Wire certificate paths through coturn config
  and document the cert-manager pattern.
- **MinIO public route via separate hostname.** Make `/voice-audio/`
  path-prefix the default but allow operators to opt into a dedicated
  hostname.
- **KEDA for ARQ workers.** When a queue-depth metric is available,
  switch ARQ from fixed replicas to KEDA-driven scaling.

## Validation

```bash
cd deploy/helm/dograh

helm lint .
helm template test-release . > /tmp/render-default.yaml
helm template test-release . -f examples/values-single-node.yaml > /tmp/render-single.yaml
helm template test-release . -f examples/values-managed.yaml > /tmp/render-managed.yaml
helm template test-release . -f examples/values-aws.yaml > /tmp/render-aws.yaml
```

Spot-check expectations:
- `Deployment/<release>-ari-manager` has `replicas: 1` and
  `strategy.type: Recreate`.
- `Deployment/<release>-campaign-orchestrator` has `replicas: 1` and
  `strategy.type: Recreate`.
- `Deployment/<release>-web` has `terminationGracePeriodSeconds: 600`
  and a `lifecycle.preStop` exec hook.
- Liveness probe on ari-manager / campaign-orchestrator uses `exec`,
  not `httpGet`.

## Layout

```
deploy/helm/dograh/
├── Chart.yaml
├── values.yaml             # heavily commented
├── values.schema.json      # enforces mode enums
├── README.md               # this file
├── examples/
│   ├── values-single-node.yaml
│   ├── values-managed.yaml
│   └── values-aws.yaml
└── templates/
    ├── _helpers.tpl
    ├── NOTES.txt
    ├── serviceaccount.yaml
    ├── configmap.yaml
    ├── secret.yaml
    ├── migrate-job.yaml
    ├── web-deployment.yaml
    ├── web-service.yaml
    ├── web-hpa.yaml
    ├── web-pdb.yaml
    ├── arq-worker-deployment.yaml
    ├── ari-manager-deployment.yaml
    ├── campaign-orchestrator-deployment.yaml
    ├── ui-deployment.yaml
    ├── ui-service.yaml
    ├── ui-pdb.yaml
    ├── coturn-deployment.yaml
    ├── coturn-service.yaml
    ├── coturn-configmap.yaml
    ├── gateway.yaml
    ├── httproute-api.yaml
    ├── httproute-ui.yaml
    ├── httproute-minio.yaml
    └── ingress.yaml
```
