"""Replay retention stays bounded while exercising real RPC queries."""
from __future__ import annotations

import gc
import importlib.util
from pathlib import Path
import tracemalloc
from typing import Any

import pytest

from memoweft.integrations.dsh_bridge.protocol_v2 import DshRpcV2Server
from memoweft.integrations.dsh_bridge.replay import ResponseReplay
from test_dsh_trust_rpc import _message_boundary, _request


def test_repeated_source_reads_have_bounded_retained_heap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMOWEFT_SEMANTIC_ENABLED", "0")
    server = DshRpcV2Server()
    initialized = server.handle(_request("init", "initialize", {
        "session_id": "synthetic", "dsh_home": str(tmp_path),
        "user_id": "synthetic", "auto_route": False, "model_tier": "local",
    }))
    assert initialized["ok"] is True
    try:
        for i in range(16):
            boundary = _message_boundary(f"source-{i}", f"Synthetic {i}: " + "x" * 4096)
            assert server.handle(_request(f"ingest-{i}", "ingest_boundary", {"boundary": boundary}))["ok"] is True
        tracemalloc.start()
        def read_batch(start: int, end: int) -> int:
            for i in range(start, end):
                response: Any = server.handle(_request(f"read-{i}", "query_evidence", {"operation": "list"}))
                assert response["ok"] is True
                assert len(response["result"]["evidence"]) == 16
            del response
            gc.collect()
            return tracemalloc.get_traced_memory()[0]

        warm = read_batch(0, 80)
        after = read_batch(80, 320)
        print(f"retained Python bytes: warm={warm}, after={after}, delta={after - warm}")
        # The 8 MiB retained-response budget plus <= 4 MiB for query/module
        # allocations. Tripling the work after warmup must add < 2 MiB,
        # rather than retaining another full copy of every source per query.
        assert after - warm < 2 * 1024 * 1024, (warm, after)
        assert after < 12 * 1024 * 1024, after
    finally:
        tracemalloc.stop()
        server.runtime.shutdown()


def test_small_responses_also_plateau() -> None:
    server = DshRpcV2Server()
    tracemalloc.start()
    try:
        for i in range(256):
            assert server.handle(_request(str(i), "capabilities"))["ok"] is True
        gc.collect()
        warm = tracemalloc.get_traced_memory()[0]
        for i in range(256, 2048):
            assert server.handle(_request(str(i), "capabilities"))["ok"] is True
        gc.collect()
        after = tracemalloc.get_traced_memory()[0]
        assert after - warm < 512 * 1024, (warm, after)
    finally:
        tracemalloc.stop()


def test_replay_lru_detachment_size_and_clear() -> None:
    replay = ResponseReplay(max_entries=2, max_bytes=4096)
    original: dict[str, object] = {"nested": ["synthetic"]}
    replay.remember("first", "fp-1", original)
    original["nested"] = ["changed by caller"]
    assert replay.get("first") == ("fp-1", {"nested": ["synthetic"]})
    replay.remember("second", "fp-2", {})
    assert replay.get("first") is not None  # refresh recency
    replay.remember("third", "fp-3", {})
    assert replay.get("second") is None
    assert replay.get("first") is not None
    # Non-ASCII heap size, not a character-count or entry-count-only bound.
    replay.remember("oversize", "fp-big", {"text": "\U0001f9f6" * 4096})
    assert replay.get("oversize") is None
    replay.clear()
    assert replay.get("first") is None
    replay.remember("after-clear", "fp-new", {})
    assert replay.get("after-clear") == ("fp-new", {})

    sized = ResponseReplay(max_entries=128, max_bytes=4096)
    sized.remember("one", "fp-1", {"text": "a" * 1800})
    assert sized.get("one") is not None
    sized.remember("two", "fp-2", {"text": "b" * 1800})
    assert sized.get("one") is None
    assert sized.get("two") is not None


def test_evicted_boundary_replay_uses_durable_identity_and_keeps_conflict_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMOWEFT_SEMANTIC_ENABLED", "0")
    server = DshRpcV2Server()
    assert server.handle(_request("init", "initialize", {
        "session_id": "synthetic", "dsh_home": str(tmp_path),
        "user_id": "synthetic", "auto_route": False, "model_tier": "local",
    }))["ok"] is True
    try:
        request = _request("boundary", "ingest_boundary", {
            "boundary": _message_boundary("synthetic-message", "Synthetic source"),
        })
        first = server.handle(request)
        assert first["ok"] is True
        for i in range(256):
            assert server.handle(_request(f"health-{i}", "health"))["ok"] is True
        assert server.handle(request) == first
        assert server.handle(_request("boundary", "health"))["result_code"] == "request_id_conflict"
        response: Any = server.handle(_request("evidence", "query_evidence", {"operation": "list"}))
        assert len(response["result"]["evidence"]) == 1
        response["result"]["evidence"].clear()
        detached: Any = server.handle(_request("evidence", "query_evidence", {"operation": "list"}))
        assert len(detached["result"]["evidence"]) == 1
    finally:
        server.runtime.shutdown()


@pytest.mark.parametrize("raises", [False, True])
def test_memory_observer_cannot_replace_handler_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raises: bool,
) -> None:
    expected = {"synthetic": object()}
    failure = RuntimeError("synthetic handler failure")
    def handler(self: DshRpcV2Server, request: object) -> dict[str, object]:
        if raises:
            raise failure
        return expected

    monkeypatch.setattr(DshRpcV2Server, "handle", handler)
    # Opening a directory as the observer's output file fails on every OS.
    monkeypatch.setenv("BRIDGE_PROBE_OUTPUT", str(tmp_path))
    probe = Path(__file__).resolve().parents[2] / "bench/bridge-memory/sitecustomize.py"
    spec = importlib.util.spec_from_file_location("bridge_memory_observer_test", probe)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        assert DshRpcV2Server.handle is not handler
        server = DshRpcV2Server()
        request = {"request_id": "sample-synthetic"}
        if raises:
            with pytest.raises(RuntimeError) as caught:
                server.handle(request)
            assert caught.value is failure
        else:
            assert server.handle(request) is expected
    finally:
        tracemalloc.stop()
