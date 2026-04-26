"""Telephony package.

Importing this package eagerly loads every provider in
``api/services/telephony/providers/`` so each one self-registers with the
registry before any consumer (factory, routes, schemas) runs. Python
guarantees this ``__init__.py`` runs before any submodule of the package,
so submodules like ``factory`` and ``registry`` can stay free of provider
imports — no lazy flags, no cycle.
"""

from . import providers as _providers  # noqa: F401  -- import for side effects (registration)
