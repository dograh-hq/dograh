"""Roark: post-call transcription, scoring and analytics.

Export-only, and deliberately without a live-call collector. Everything Roark
needs is already on the persisted run once the pipeline finishes: the turns and
function calls in ``workflow_run.logs["realtime_feedback_events"]``, the
outcome in ``gathered_context``, and the audio behind ``recording_url``. So the
package registers a completion handler and nothing else, and a call costs the
live pipeline nothing.

Roark fetches the recording itself, which means the Dograh instance has to be
reachable from the public internet for the export to land.
"""

from __future__ import annotations

from api.services.integrations.base import IntegrationPackageSpec
from api.services.integrations.registry import register_package

from .completion import run_completion
from .node import NODE

PACKAGE = register_package(
    IntegrationPackageSpec(
        name="roark",
        nodes=(NODE,),
        run_completion=run_completion,
    )
)

__all__ = ["PACKAGE"]
