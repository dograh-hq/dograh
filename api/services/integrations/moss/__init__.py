"""Moss knowledge retrieval integration.

Adds a ``search_moss_index`` tool to every Start Call and Agent node
of a workflow with an enabled Moss node. The index is built in Moss,
then downloaded once per worker process and searched in memory during calls.
"""

from __future__ import annotations

from api.services.integrations.base import IntegrationPackageSpec
from api.services.integrations.registry import register_package

from .node import NODE
from .tools import create_tools

PACKAGE = register_package(
    IntegrationPackageSpec(
        name="moss",
        nodes=(NODE,),
        create_tools=create_tools,
    )
)

__all__ = ["PACKAGE"]
