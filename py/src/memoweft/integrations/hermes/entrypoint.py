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
# Terminal outcomes are a separate durable-delivery capability.  Keep this as
# a top-level literal because Hermes inspects the selected entrypoint source
# without importing provider code.
supports_terminal_outcomes = True
# Trust Query is a separate, read-only capability.  Hermes and future DSH
# hosts can inspect this literal without importing provider/runtime code.
supports_trust_queries = True
# Trust Command is a separate explicit mutation capability. Keep this literal
# so a host can fail closed before exposing command tools.
supports_trust_commands = True
# Clarification answers are an exact-session human-input capability, separate
# from model-visible Trust tools and ordinary per-turn writes.
supports_clarifications = True

__all__ = [
    "register",
    "supports_durable_boundaries",
    "supports_terminal_outcomes",
    "supports_trust_queries",
    "supports_trust_commands",
    "supports_clarifications",
]
