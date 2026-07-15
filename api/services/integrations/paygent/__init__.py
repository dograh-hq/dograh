"""Paygent integration package.

Self-registers on import via ``register_package``.  Auto-discovered by
``api/services/integrations/loader.py`` (scans all submodules of
``api.services.integrations`` except ``base``, ``loader``, and ``registry``).

Provides:
- ``PaygentNodeData`` – Pydantic config node shown in the Dograh UI under
  INTEGRATIONS → "Paygent"
- ``create_runtime_sessions`` – live-call observer that accumulates usage data
- ``run_completion`` – post-call REST delivery to the Paygent API
"""

from __future__ import annotations

from api.services.integrations.base import IntegrationPackageSpec
from api.services.integrations.registry import register_package

from .completion import run_completion
from .node import NODE
from .runtime import create_runtime_sessions

PACKAGE = register_package(
    IntegrationPackageSpec(
        name="paygent",
        nodes=(NODE,),
        create_runtime_sessions=create_runtime_sessions,
        run_completion=run_completion,
    )
)

__all__ = ["PACKAGE"]
