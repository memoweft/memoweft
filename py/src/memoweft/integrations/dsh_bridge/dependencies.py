"""DSH bridge compatibility import for the shared dependency DTO contract."""

from ...model_context_dependencies import (
    DEPENDENCY_SCHEMA_VERSION,
    DependencyValidationError,
    MAX_DEPENDENCY_REFERENCES,
    WORLD_ITEM_KINDS,
    validate_model_context_dependencies,
)

__all__ = [
    "DEPENDENCY_SCHEMA_VERSION",
    "DependencyValidationError",
    "MAX_DEPENDENCY_REFERENCES",
    "WORLD_ITEM_KINDS",
    "validate_model_context_dependencies",
]
