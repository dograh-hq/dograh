from __future__ import annotations

import importlib
import pkgutil

_INTERNAL_MODULES = {"base", "loader", "registry"}
_loaded = False


def ensure_integrations_loaded() -> None:
    global _loaded
    if _loaded:
        return

    package = importlib.import_module("api.services.integrations")
    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in _INTERNAL_MODULES:
            continue
        importlib.import_module(f"{package.__name__}.{module_info.name}")

    _loaded = True
