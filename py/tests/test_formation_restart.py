"""FX-18: real lifetimes and the existing atomic formation/checkpoint path."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from memoweft.integrations.hermes.worker_lifetime import PREFIX, acquire_lifetime, owner_is_gone
from memoweft.integrations.hermes.world_worker import WorldJobStore, WorldJobWorker, WorldJobResult
from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from test_hermes_world_worker import (
    MutableClock, _initialize_database, _insert_job, _job, SequenceProcessor,
)


def fixture(path: Path, attempts: int = 0):
    clock = MutableClock()
    _initialize_database(path)
    _insert_job(path, clock, attempts=attempts)
    return clock, WorldJobStore(path, clock=clock)


def dead_owner(path: Path) -> str:
    # The process actually holds an OS lock, then is forcibly terminated.
    code = """
import sys, time
from pathlib import Path
from uuid import uuid4
from memoweft.integrations.hermes.worker_lifetime import PREFIX, acquire_lifetime
owner = PREFIX + uuid4().hex
lock = acquire_lifetime(Path(sys.argv[1]), owner)
print(owner, flush=True)
time.sleep(60)
"""
    child = subprocess.Popen([sys._base_executable, "-c", code, str(path)], stdout=subprocess.PIPE,
                             text=True, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
    try:
        owner = child.stdout.readline().strip()
        assert not owner_is_gone(path, owner)
    finally:
        child.kill()
        child.wait(timeout=5)
        child.stdout.close()
    assert owner_is_gone(path, owner)
    return owner


@pytest.mark.parametrize("attempts", [0, 3])
def test_dead_lifetime_reclaimed_without_expiry_or_failed_attempt(tmp_path: Path, attempts: int):
    path = tmp_path / "memory.sqlite3"
    clock, store = fixture(path, attempts)
    claim = store.claim_one(dead_owner(path))
    assert claim is not None
    store.mark_dispatch_started(claim)
    worker = WorldJobWorker(path, clock=clock, retry_interrupted_inference=True,
                           processor=SequenceProcessor(WorldJobResult.no_change("resumed")))
    try:
        assert worker.run_until_quiescent() == 1  # clock has not moved at all
        assert _job(path)["state"] == "no_change"
        assert _job(path)["attempts"] == attempts + 1
        assert not store.heartbeat(claim)
        assert not store.settle(claim, WorldJobResult.no_change("stale"))
    finally:
        worker.shutdown()


def test_current_and_other_live_lifetimes_untouched(tmp_path: Path):
    path = tmp_path / "memory.sqlite3"
    clock, store = fixture(path)
    first = WorldJobWorker(path, clock=clock)
    second = WorldJobWorker(path, clock=clock)
    try:
        claim = store.claim_one(first.worker_id)
        assert claim is not None
        assert store.recover_interrupted() == ()
        assert second.run_until_quiescent() == 0
        clock.advance(120)
        assert store.heartbeat(claim)
        clock.advance(240)  # longer than original 300-second lease
        assert store.recover_interrupted() == ()
        assert store.heartbeat(claim)
        assert _job(path)["claim_owner"] == first.worker_id
    finally:
        first.shutdown()
        second.shutdown()


@pytest.mark.parametrize("checkpoint", ["none", "saved", "partial-apply"])
def test_restart_model_and_atomic_apply_once(tmp_path: Path, checkpoint: str, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "memory.sqlite3"
    clock, store = fixture(path)
    claim = store.claim_one(dead_owner(path))
    assert claim is not None
    store.mark_dispatch_started(claim)
    raw = "user Evidence evidence-1"
    payload = {"content": json.dumps({"schema_version": 1, "result": "one_cognition", "cognition": {
        "target": "owner_self", "statement_kind": "preference", "proposition": raw,
        "supports": [{"evidence_id": "evidence-1", "start": 0, "end": len(raw)}]}})}
    calls = []
    def route(*args, **kwargs):
        calls.append(True)
        return payload
    processor = HermesBatchAdapterProcessor(str(path), route, clock=clock)
    if checkpoint != "none":
        with sqlite3.connect(path, isolation_level=None) as db:
            processor._persist_checkpoint(db, claim, payload)
    if checkpoint == "partial-apply":
        import memoweft.integrations.hermes.batch_adapter as adapter
        def fail_after_world_rows(*args):
            raise RuntimeError("exit during atomic apply")
        with monkeypatch.context() as patch:
            patch.setattr(adapter, "persist_terminal_outcome_in_transaction", fail_after_world_rows)
            with pytest.raises(RuntimeError, match="exit during atomic apply"):
                processor.process(claim)
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
        assert _job(path)["model_result_json"] is not None
    worker = WorldJobWorker(path, clock=clock, processor=processor, retry_interrupted_inference=True)
    try:
        assert worker.run_until_quiescent() == 1
        assert _job(path)["state"] == "applied"
        assert len(calls) == (1 if checkpoint == "none" else 0)
        # Apply commits World, revision, provenance and terminal together. An
        # exit after that commit cannot reclaim any part of the finished job.
        assert store.recover_interrupted() == ()
        assert worker.run_until_quiescent() == 0
        assert not store.settle(claim, WorldJobResult.no_change("stale"))
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM memory_world_job").fetchone()[0] == 1
            assert db.execute("SELECT revision FROM memory_state").fetchone()[0] == 1
    finally:
        worker.shutdown()


def test_generic_unknown_dispatch_keeps_at_most_once_and_unknown_owner_keeps_lease(tmp_path: Path):
    path = tmp_path / "memory.sqlite3"
    _, store = fixture(path)
    claim = store.claim_one("external-holder")
    assert store.recover_interrupted(retry_inference=True) == ()
    assert store.heartbeat(claim)
    store.recover_interrupted(owner="external-holder")
    claim = store.claim_one(dead_owner(path))
    store.mark_dispatch_started(claim)
    assert store.recover_interrupted() == ()
    assert _job(path)["state"] == "dead"
    assert _job(path)["last_error_type"] == "dispatch_outcome_unknown"


def test_upgrade_legacy_pid_owner_is_conservative(tmp_path: Path):
    path = tmp_path / "memory.sqlite3"
    fixture(path)
    child = subprocess.Popen([sys._base_executable, "-c", "import time; time.sleep(60)", str(path)])
    owner = f"memoweft-world:{child.pid}:" + "a" * 32
    try:
        assert not owner_is_gone(path, owner)
    finally:
        child.kill()
        child.wait(timeout=5)
    assert owner_is_gone(path, owner)
    assert not owner_is_gone(path, f"memoweft-world:{os.getpid()}:" + "b" * 32)


def test_shutdown_database_contention_remains_bounded(tmp_path: Path):
    import time
    path = tmp_path / "memory.sqlite3"
    clock, store = fixture(path)
    worker = WorldJobWorker(path, clock=clock)
    claim = store.claim_one(worker.worker_id)
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        worker.shutdown(timeout=0)
        assert time.monotonic() - started < 0.7
        assert not owner_is_gone(path, worker.worker_id)
    finally:
        db.execute("ROLLBACK")
        db.close()
        worker.shutdown(timeout=0)
    assert owner_is_gone(path, worker.worker_id)
    assert not store.heartbeat(claim)
    assert _job(path)["attempts"] == 0
