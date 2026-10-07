"""Strict causal dependency DTO validation shared by RPC and portable paths."""

from __future__ import annotations

from typing import Mapping


DEPENDENCY_SCHEMA_VERSION = 1
MAX_DEPENDENCY_REFERENCES = 64
MAX_DEPENDENCY_ID_LENGTH = 512
MAX_DEPENDENCY_TOKEN_LENGTH = 512

_DEPENDENCY_KEYS = frozenset(
    {
        "schema_version",
        "capture_status",
        "world_items",
        "interaction_ids",
        "world_revision",
        "recall_snapshot_token",
        "interaction_snapshot_token",
        "world_context_hash",
        "interaction_context_hash",
        "context_hash",
    }
)
_REQUIRED_DEPENDENCY_KEYS = frozenset(
    {"schema_version", "capture_status", "world_items", "interaction_ids"}
)
_CAPTURE_STATUSES = frozenset(
    {"complete", "complete_empty", "unavailable", "withheld"}
)
_WORLD_ITEM_KEYS = frozenset({"object_kind", "item_id"})
WORLD_ITEM_KINDS = frozenset({"cognition", "entity", "relationship", "event"})
_TOKEN_KEYS = (
    "recall_snapshot_token",
    "interaction_snapshot_token",
    "world_context_hash",
    "interaction_context_hash",
    "context_hash",
)


class DependencyValidationError(ValueError):
    """The dependency DTO is not a closed, bounded schema-v1 value."""


def _identifier(value: object, field: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > MAX_DEPENDENCY_ID_LENGTH
    ):
        raise DependencyValidationError(f"{field} must be a bounded string id")
    return value


def validate_model_context_dependencies(value: object) -> dict[str, object]:
    """Validate and return a canonical JSON-native dependency mapping."""

    if not isinstance(value, Mapping):
        raise DependencyValidationError("model_context_dependencies must be a mapping")
    keys = frozenset(value)
    if keys - _DEPENDENCY_KEYS or _REQUIRED_DEPENDENCY_KEYS - keys:
        raise DependencyValidationError("model_context_dependencies fields are invalid")
    schema_version = value.get("schema_version")
    if type(schema_version) is not int or schema_version != DEPENDENCY_SCHEMA_VERSION:
        raise DependencyValidationError("unsupported dependency schema version")
    capture_status = value.get("capture_status")
    if not isinstance(capture_status, str) or capture_status not in _CAPTURE_STATUSES:
        raise DependencyValidationError("dependency capture_status is invalid")

    raw_world_items = value.get("world_items")
    raw_interaction_ids = value.get("interaction_ids")
    if (
        not isinstance(raw_world_items, list)
        or not isinstance(raw_interaction_ids, list)
        or len(raw_world_items) > MAX_DEPENDENCY_REFERENCES
        or len(raw_interaction_ids) > MAX_DEPENDENCY_REFERENCES
    ):
        raise DependencyValidationError("dependency reference arrays are invalid")

    world_items: list[dict[str, str]] = []
    world_keys: set[tuple[str, str]] = set()
    for raw_item in raw_world_items:
        if not isinstance(raw_item, Mapping) or frozenset(raw_item) != _WORLD_ITEM_KEYS:
            raise DependencyValidationError("world dependency fields are invalid")
        kind = raw_item.get("object_kind")
        if not isinstance(kind, str) or kind not in WORLD_ITEM_KINDS:
            raise DependencyValidationError("world dependency kind is invalid")
        item_id = _identifier(raw_item.get("item_id"), "world item_id")
        world_key = (kind, item_id)
        if world_key in world_keys:
            raise DependencyValidationError("world dependencies must be unique")
        world_keys.add(world_key)
        world_items.append({"object_kind": kind, "item_id": item_id})

    interaction_ids: list[str] = []
    interaction_keys: set[str] = set()
    for raw_interaction_id in raw_interaction_ids:
        interaction_id = _identifier(raw_interaction_id, "interaction_id")
        if interaction_id in interaction_keys:
            raise DependencyValidationError("interaction dependencies must be unique")
        interaction_keys.add(interaction_id)
        interaction_ids.append(interaction_id)

    has_references = bool(world_items or interaction_ids)
    if capture_status == "complete" and not has_references:
        raise DependencyValidationError("complete dependencies must contain a reference")
    if capture_status != "complete" and has_references:
        raise DependencyValidationError(
            "only complete dependencies may contain references"
        )

    result: dict[str, object] = {
        "schema_version": DEPENDENCY_SCHEMA_VERSION,
        "capture_status": capture_status,
        "world_items": world_items,
        "interaction_ids": interaction_ids,
    }
    if "world_revision" in value:
        revision = value.get("world_revision")
        if type(revision) is not int or revision < 0:
            raise DependencyValidationError(
                "world_revision must be a non-negative integer"
            )
        result["world_revision"] = revision
    for token_key in _TOKEN_KEYS:
        if token_key not in value:
            continue
        token = value.get(token_key)
        if (
            not isinstance(token, str)
            or not token
            or token != token.strip()
            or len(token) > MAX_DEPENDENCY_TOKEN_LENGTH
        ):
            raise DependencyValidationError(f"{token_key} must be a bounded string")
        result[token_key] = token
    return result


__all__ = [
    "DEPENDENCY_SCHEMA_VERSION",
    "DependencyValidationError",
    "MAX_DEPENDENCY_REFERENCES",
    "WORLD_ITEM_KINDS",
    "validate_model_context_dependencies",
]
