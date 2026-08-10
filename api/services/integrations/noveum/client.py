from __future__ import annotations

from typing import Any

DEFAULT_NOVEUM_ENDPOINT = "https://api.noveum.ai/api"


def build_noveum_client(
    *,
    api_key: str,
    project: str,
    environment: str = "production",
    service_version: str | None = None,
) -> Any:
    """
    Construct a per-node ``NoveumClient`` for the completion phase.

    A ``Config`` object is passed explicitly instead of ``api_key=``/``project=``
    kwargs: the kwarg path mutates noveum-trace's process-global config, which
    would race between concurrently completing runs with different BYOK
    credentials in the same arq worker.
    """
    from noveum_trace.core.client import NoveumClient
    from noveum_trace.core.config import Config

    config = Config.create(
        api_key=api_key,
        project=project,
        environment=environment,
        endpoint=DEFAULT_NOVEUM_ENDPOINT,
        service_version=service_version,
    )
    return NoveumClient(config=config)
