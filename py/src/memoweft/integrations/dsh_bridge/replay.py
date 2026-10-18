"""Bounded, process-local response replay; durable identities live in Core."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import sys


# Four times the host's 32 concurrent requests covers short transport replays.
# Count alone is insufficient: a source-list/export response can be megabytes.
REPLAY_MAX_ENTRIES = 128
REPLAY_MAX_BYTES = 8 * 1024 * 1024


def _retained_size(value: object, budget: int) -> int:
    """Conservative Python heap size, stopping once a response cannot fit.

    Count shared children again rather than undercounting retained allocations.
    Responses are JSON-native acyclic values, as required by the RPC envelope.
    """
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        for key, item in value.items():
            size += _retained_size(key, budget - size)
            if size > budget:
                return size
            size += _retained_size(item, budget - size)
            if size > budget:
                return size
    elif isinstance(value, (list, tuple)):
        for item in value:
            size += _retained_size(item, budget - size)
            if size > budget:
                return size
    return size


class ResponseReplay:
    def __init__(self, *, max_entries: int = REPLAY_MAX_ENTRIES,
                 max_bytes: int = REPLAY_MAX_BYTES) -> None:
        self._entries: OrderedDict[str, tuple[str, dict[str, object], int]] = OrderedDict()
        self._bytes = 0
        self._max_entries, self._max_bytes = max_entries, max_bytes

    def get(self, request_id: str) -> tuple[str, dict[str, object]] | None:
        entry = self._entries.get(request_id)
        if entry is None:
            return None
        self._entries.move_to_end(request_id)
        return entry[0], entry[1]

    def remember(self, request_id: str, fingerprint: str, response: dict[str, object]) -> None:
        # Include entry/container bookkeeping, not just serialized text bytes.
        size = 256 + _retained_size((request_id, fingerprint, response), self._max_bytes)
        if size > self._max_bytes:
            return
        while self._entries and (len(self._entries) >= self._max_entries
                                 or self._bytes + size > self._max_bytes):
            _, removed = self._entries.popitem(last=False)
            self._bytes -= removed[2]
        self._entries[request_id] = fingerprint, deepcopy(response), size
        self._bytes += size

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0
