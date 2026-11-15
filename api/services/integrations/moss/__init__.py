"""Moss knowledge retrieval integration.

Searches a Moss index for every Start Call and Agent node of a workflow
with an enabled Moss node: on each caller turn in ambient mode, or through
a ``search_moss_index`` tool in tool mode. The index is built in Moss,
then downloaded once per worker process and searched in memory during calls.
"""

from __future__ import annotations

from api.services.integrations.base import IntegrationPackageSpec
from api.services.integrations.registry import register_package

from .node import NODE
from .tools import create_context_providers, create_tools

PACKAGE = register_package(
    IntegrationPackageSpec(
        name="moss",
        nodes=(NODE,),
        create_tools=create_tools,
        create_context_providers=create_context_providers,
    )
)

__all__ = ["PACKAGE"]
