from api.services.integrations.base import IntegrationPackageSpec
from api.services.integrations.registry import register_package
from api.services.observability.call_events.base import CallEventSinkRegistration

from .sink import BigQueryConfig, BigQuerySink

PACKAGE = register_package(
    IntegrationPackageSpec(
        name="bigquery",
        call_event_sink=CallEventSinkRegistration(
            config_model=BigQueryConfig,
            create=BigQuerySink,
            sensitive_fields=("private_key",),
            destination_fields=("table",),
        ),
    )
)
