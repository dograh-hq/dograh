"""External-PBX adapter entrypoint for the ARI provider."""

from .base import ExternalPBXAdapter, ExternalPBXResult
from .registry import create_adapter, register_adapter, registered_adapter_types
from .vicidial import VicidialAdapter

register_adapter("vicidial", VicidialAdapter)

__all__ = [
    "ExternalPBXAdapter",
    "ExternalPBXResult",
    "create_adapter",
    "registered_adapter_types",
]
