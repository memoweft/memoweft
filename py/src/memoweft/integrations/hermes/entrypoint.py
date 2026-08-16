"""Bare-module Hermes entry point.

Hermes resolves package-style entry points through its directory-plugin
loader so it can discover optional sibling CLI/config files.  That loader
mounts the directory under a synthetic namespace, which is incompatible with
MemoWeft's normal package-relative imports.  Pointing distribution metadata at
this bare module keeps provider loading on Hermes' entry-point path.
"""

from . import register

# Host-side manual compression helpers inspect this exact entry-point module
# without importing provider code.  Keep the capability declaration a literal
# boolean so that static AST inspection can fail closed.
supports_durable_boundaries = True

__all__ = ["register", "supports_durable_boundaries"]
