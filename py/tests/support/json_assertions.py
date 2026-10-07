"""Narrow dynamic JSON response fields without bypassing strict type checks."""
from __future__ import annotations

from typing import cast


def as_object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    assert all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


def as_objects(value: object) -> list[dict[str, object]]:
    assert isinstance(value, list)
    return [as_object(item) for item in value]


def as_string(value: object) -> str:
    assert isinstance(value, str)
    return value


def as_int(value: object) -> int:
    assert isinstance(value, int) and not isinstance(value, bool)
    return value
