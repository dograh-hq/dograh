import base64

from loguru import logger
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from api.constants import (
    LANGFUSE_HOST,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
)
from pipecat.utils.run_context import get_current_org_id
from pipecat.utils.tracing.setup import setup_tracing

_tracing_initialized = False
_org_routing_exporter = None


class _OrgAttributeSpanProcessor(SpanProcessor):
    """Stamps each span with the current org_id from the async context var."""

    def on_start(self, span, parent_context=None):
        from pipecat.utils.run_context import get_current_org_id

        org_id = get_current_org_id()
        if org_id:
            span.set_attribute("dograh.org_id", str(org_id))

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        return True


class _OrgRoutingExporter(SpanExporter):
    """Routes spans to org-specific or default Langfuse exporter.

    Spans with a ``dograh.org_id`` attribute whose org has registered
    credentials are forwarded to that org's exporter.  All other spans
    go to the default exporter (env-var credentials).
    """

    def __init__(self, default_exporter):
        self._default_exporter = default_exporter
        self._org_exporters = {}
        self._org_hosts = {}

    def get_org_host(self, org_id):
        return self._org_hosts.get(str(org_id))

    def register_org(self, org_id, host, public_key, secret_key):
        key = str(org_id)
        normalized_host = host.rstrip("/")
        auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        endpoint = f"{normalized_host}/api/public/otel/v1/traces"

        # Skip if already registered with identical settings
        if key in self._org_exporters:
            existing = self._org_exporters[key]
            if (
                self._org_hosts.get(key) == normalized_host
                and getattr(existing, "_endpoint", None) == endpoint
                and existing._headers.get("Authorization") == f"Basic {auth}"
            ):
                return
            # Credentials changed — shut down the old exporter
            logger.info(f"Updating OTEL exporter for org {org_id}")
            existing.shutdown()

        self._org_hosts[key] = normalized_host
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers={"Authorization": f"Basic {auth}"},
        )
        self._org_exporters[key] = exporter
        logger.info(f"Registered OTEL exporter for org {org_id}")

    def unregister_org(self, org_id):
        key = str(org_id)
        exporter = self._org_exporters.pop(key, None)
        self._org_hosts.pop(key, None)
        if exporter:
            exporter.shutdown()
            logger.info(f"Unregistered OTEL exporter for org {org_id}")

    def export(self, spans):
        default_spans = []
        org_buckets = {}

        for span in spans:
            # Drop fastmcp's built-in auto-instrumentation spans
            # (`tools/call <name>`, etc.) — our `@traced_tool` decorator
            # in `api/mcp_server/tracing.py` produces the spans we want. Keeping
            # both would just double every trace.
            scope = getattr(span, "instrumentation_scope", None)
            if scope is not None and scope.name == "fastmcp":
                continue

            org_id = span.attributes.get("dograh.org_id") if span.attributes else None
            if org_id and str(org_id) in self._org_exporters:
                org_buckets.setdefault(str(org_id), []).append(span)
            else:
                default_spans.append(span)

        result = SpanExportResult.SUCCESS

        if default_spans and self._default_exporter:
            r = self._default_exporter.export(default_spans)
            if r != SpanExportResult.SUCCESS:
                result = r

        for oid, batch in org_buckets.items():
            r = self._org_exporters[oid].export(batch)
            if r != SpanExportResult.SUCCESS:
                result = r

        return result

    def shutdown(self):
        if self._default_exporter:
            self._default_exporter.shutdown()
        for exp in self._org_exporters.values():
            exp.shutdown()

    def force_flush(self, timeout_millis=30000):
        ok = True
        if self._default_exporter:
            ok = self._default_exporter.force_flush(timeout_millis) and ok
        for exp in self._org_exporters.values():
            ok = exp.force_flush(timeout_millis) and ok
        return ok


def ensure_tracing() -> bool:
    """Initialize OTEL tracing. Returns True once the routing exporter is set up.

    Installs an ``_OrgRoutingExporter`` so that spans can be routed to
    org-specific Langfuse projects at export time. Spans without a matching
    exporter (no env-var defaults, no registered org) are silently dropped, so
    this is safe to call unconditionally.

    Idempotent — safe to call from both the pipeline process and the ARQ worker.
    """
    global _tracing_initialized, _org_routing_exporter
    if _tracing_initialized:
        return True

    # Build the default exporter from env-var credentials (may be None)
    default_exporter = None
    if all([LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY]):
        langfuse_auth = base64.b64encode(
            f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()
        ).decode()
        default_exporter = OTLPSpanExporter(
            endpoint=f"{LANGFUSE_HOST}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {langfuse_auth}"},
        )

    _org_routing_exporter = _OrgRoutingExporter(default_exporter)
    setup_tracing(service_name="dograh-pipeline", exporter=_org_routing_exporter)

    # Add processor that stamps every span with the current org_id context var
    from opentelemetry import trace as otel_trace

    provider = otel_trace.get_tracer_provider()
    if hasattr(provider, "add_span_processor"):
        provider.add_span_processor(_OrgAttributeSpanProcessor())

    _tracing_initialized = True
    return True


def register_org_langfuse_credentials(org_id, host, public_key, secret_key):
    """Register or update org-specific Langfuse credentials for span routing.

    Safe to call multiple times — updates credentials if they changed.
    """
    if not ensure_tracing():
        return
    if not all([host, public_key, secret_key]):
        logger.warning(
            f"Incomplete Langfuse credentials for org {org_id}, skipping registration"
        )
        return
    _org_routing_exporter.register_org(org_id, host, public_key, secret_key)


def unregister_org_langfuse_credentials(org_id):
    """Remove org-specific Langfuse credentials. Spans will fall back to the default exporter."""
    if not ensure_tracing():
        return
    _org_routing_exporter.unregister_org(org_id)


async def load_all_org_langfuse_credentials():
    """Load Langfuse credentials for all orgs at startup.

    Called once during app lifespan so that org-specific exporters are ready
    before any pipeline runs, without per-call DB lookups.
    """
    if not ensure_tracing():
        return

    from api.db import db_client
    from api.enums import OrganizationConfigurationKey

    configs = await db_client.get_all_configurations_by_key(
        OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value,
    )
    for config in configs:
        org_id = config["organization_id"]
        value = config["value"]
        register_org_langfuse_credentials(
            org_id=org_id,
            host=value.get("host"),
            public_key=value.get("public_key"),
            secret_key=value.get("secret_key"),
        )
    logger.info(f"Loaded Langfuse credentials for {len(configs)} org(s)")


async def handle_langfuse_sync(event):
    """Worker sync handler: refresh a single org's Langfuse exporter from DB."""
    from api.db import db_client
    from api.enums import OrganizationConfigurationKey

    org_id = event.org_id

    logger.info(
        f"handle_langfuse_sync for org_id: {event.org_id} action: {event.action}"
    )

    if event.action == "delete":
        unregister_org_langfuse_credentials(org_id)
        return

    config = await db_client.get_configuration(
        org_id, OrganizationConfigurationKey.LANGFUSE_CREDENTIALS.value
    )
    if config and config.value:
        register_org_langfuse_credentials(
            org_id=org_id,
            host=config.value.get("host"),
            public_key=config.value.get("public_key"),
            secret_key=config.value.get("secret_key"),
        )
    else:
        # Credentials were saved then deleted before we got the event
        unregister_org_langfuse_credentials(org_id)


def get_trace_url(trace_id: str, org_id=None) -> str | None:
    """Build a Langfuse trace URL, using org-specific host when available."""
    if org_id is None:
        org_id = get_current_org_id()

    host = None
    if org_id and _org_routing_exporter:
        host = _org_routing_exporter.get_org_host(str(org_id))
    if not host:
        host = LANGFUSE_HOST
    if not host:
        return None

    return f"{host.rstrip('/')}/trace/{trace_id}"
