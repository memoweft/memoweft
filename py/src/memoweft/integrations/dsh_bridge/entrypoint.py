"""Bare-module DSH bridge entry point (mirrors the Hermes entrypoint pattern).

WeftMate spawns ``python -m memoweft.integrations.dsh_bridge`` directly and
never resolves distribution entry-point metadata; this bare module only keeps
the static capability declaration inspectable the same way Hermes' is.
"""

from . import DshBoundaryError, DshMemoWeftRuntime

# Host-side integrations inspect this exact entry-point module without
# importing bridge code.  Keep the capability declaration a literal boolean so
# that static AST inspection can fail closed.
supports_durable_boundaries = True

__all__ = ["DshBoundaryError", "DshMemoWeftRuntime", "supports_durable_boundaries"]
