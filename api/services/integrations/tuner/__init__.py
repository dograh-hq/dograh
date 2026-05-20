from __future__ import annotations

from api.services.integrations.base import IntegrationPackageSpec
from api.services.integrations.registry import register_package

from .completion import run_completion
from .node import NODE
from .runtime import create_runtime_sessions

PACKAGE = register_package(
    IntegrationPackageSpec(
        name="tuner",
        nodes=(NODE,),
        create_runtime_sessions=create_runtime_sessions,
        run_completion=run_completion,
    )
)

__all__ = ["PACKAGE"]
