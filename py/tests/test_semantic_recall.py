"""Synthetic candidate-boundary and derived-data lifecycle tests (no model)."""
from __future__ import annotations

from contextlib import closing
import importlib.util
import importlib
from pathlib import Path
import sqlite3
import struct
import threading
from time import monotonic, sleep
from typing import Any, Sequence
from unittest.mock import patch

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.semantic.config import SemanticConfig
from memoweft.semantic.model import DIMENSIONS, MODEL_ID, file_valid, prepare_files
from memoweft.semantic.runtime import SemanticCandidate, SemanticRuntime, configure_runtime, close_runtime
from memoweft.semantic import store
from memoweft.store.driver import open_db, user_version
from test_trust_command_service import _seed_cognition, _seed_revision, _service, _command

EVAL_PATH = Path(__file__).resolve().parents[2] / "bench/recall-quality/evaluate.py"
spec = importlib.util.spec_from_file_location("semantic_fixture", EVAL_PATH)
assert spec is not None and spec.loader is not None
fixture: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class ForcedProvider:
    def __init__(self, memories: Sequence[dict[str, Any]], config: SemanticConfig = SemanticConfig()) -> None:
        self.config = config
        self.items = tuple(SemanticCandidate("cognition", str(m["id"]), 1, store.content_hash(str(m["content"]))) for m in memories)

    def candidates(self, db: sqlite3.Connection, subject_id: str, query: str) -> Sequence[SemanticCandidate]:
        return self.items


def vector() -> bytes:
    return struct.pack("<" + "f" * DIMENSIONS, 1, *([0] * (DIMENSIONS - 1)))


@pytest.mark.parametrize("state", ["forgotten", "archived", "muted", "invalid", "local_denied", "cloud_denied", "expired", "future", "cross_evidence", "other_account"])
def test_forced_blocked_nomination_never_leaves_final_gate(tmp_path: Path, state: str) -> None:
    path = tmp_path / "synthetic.sqlite3"
    memories = [{"id": "blocked", "content": "我一直偏好蓝色手账。", "state": state}]
    fixture.seed(path, {"memories": memories})
    with closing(sqlite3.connect(path)) as db:
        before = db.total_changes
        snapshot = recall_world_snapshot(db, "fictional-owner", "我的记录本偏好是什么？",
            model_tier="cloud" if state == "cloud_denied" else "local", candidate_provider=ForcedProvider(memories))
        assert snapshot is not None and snapshot.selected_item_ids == ()
        assert db.total_changes == before


@pytest.mark.parametrize("state", ["forgotten", "archived", "muted", "invalid", "local_denied", "cloud_denied", "expired", "future", "cross_evidence", "other_account"])
def test_final_gate_alone_rejects_a_forced_unfiltered_row(tmp_path: Path, state: str) -> None:
    path = tmp_path / "last-gate.sqlite3"
    memories = [{"id": "blocked", "content": "我一直偏好蓝色手账。", "state": state}]
    fixture.seed(path, {"memories": memories})
    with closing(sqlite3.connect(path)) as db, patch("memoweft.integrations.hermes.recall._current_world_rows", return_value=[{"kind": "cognition", "id": "blocked", "content": memories[0]["content"], "confidence": 600}]):
        snapshot = recall_world_snapshot(db, "fictional-owner", "我的记录本偏好是什么？",
            model_tier="cloud" if state == "cloud_denied" else "local", candidate_provider=ForcedProvider(memories))
        assert snapshot is not None and snapshot.selected_item_ids == ()


def test_literal_order_cap_and_name_rules(tmp_path: Path) -> None:
    memories = [{"id": f"m-{i}", "content": "我喜欢蓝色手账。" if i == 0 else f"我收藏虚构品种{i}。"} for i in range(7)]
    path = tmp_path / "synthetic.sqlite3"
    fixture.seed(path, {"memories": memories})
    with closing(sqlite3.connect(path)) as db:
        baseline = recall_world_snapshot(db, "fictional-owner", "我喜欢什么手账？", candidate_provider=ForcedProvider([], SemanticConfig(enabled=False)))
        current = recall_world_snapshot(db, "fictional-owner", "我喜欢什么手账？", candidate_provider=ForcedProvider(memories))
        assert baseline is not None and current is not None
        assert current.selected_item_ids[:baseline.count] == baseline.selected_item_ids
        assert current.count == baseline.count + 3
    named = [{"id": "person-fact", "content": "樂是我的同事。"}, {"id": "unrelated", "content": "我收藏虚构邮票。"}]
    fixture.seed(tmp_path / "named.sqlite3", {"memories": named, "person_name": "樂"})
    with closing(sqlite3.connect(tmp_path / "named.sqlite3")) as db:
        result = recall_world_snapshot(db, "fictional-owner", "樂是谁？", candidate_provider=ForcedProvider(named))
        assert result is not None and result.selected_item_ids == (("cognition", "person-fact"),)


def test_candidates_cannot_revive_corrected_or_changed_content(tmp_path: Path) -> None:
    memories = [{"id": "old", "content": "我喜欢蓝色手账。"}, {"id": "new", "content": "我改为红色手账。", "predecessor": "old"}]
    path = tmp_path / "synthetic.sqlite3"
    fixture.seed(path, {"memories": memories})
    with closing(sqlite3.connect(path)) as db:
        result = recall_world_snapshot(db, "fictional-owner", "我现在用什么记录本？", candidate_provider=ForcedProvider(memories))
        assert result is not None
        assert ("cognition", "old") not in result.selected_item_ids
        assert ("cognition", "new") in result.selected_item_ids
        db.execute("UPDATE cognition SET content='合成的新内容' WHERE id='new'")
        db.commit()
        result = recall_world_snapshot(db, "fictional-owner", "完全换个说法？", candidate_provider=ForcedProvider(memories))
        assert result is not None and result.selected_item_ids == ()


def test_actual_forget_receipt_erases_side_table_and_cannot_restore(tmp_path: Path) -> None:
    path = tmp_path / "forget.sqlite3"
    with closing(open_db(str(path))) as db:
        _seed_cognition(db)
        _seed_revision(db)
        store.install(db)
        assert store.publish(db, "cognition", "cog-coffee", store.content_hash("用户喜欢喝咖啡"), vector())
        assert db.execute("SELECT count(*) FROM semantic_vector").fetchone()[0] == 1
    receipt = _service(path).submit_command(_command("semantic-forget", 1, "delete_world_item", "cognition", "cog-coffee"))
    assert receipt["result_state"] == "applied"
    with closing(open_db(str(path))) as db:
        assert db.execute("SELECT count(*) FROM semantic_vector").fetchone()[0] == 0
        assert not store.publish(db, "cognition", "cog-coffee", store.content_hash("用户喜欢喝咖啡"), vector())


@pytest.mark.parametrize("mutation", ["UPDATE cognition SET content='更改后的合成正文'", "UPDATE cognition SET invalid_at='2026-01-01'", "UPDATE cognition SET archived_at='2026-01-01'", "UPDATE cognition SET muted_at='2026-01-01'", "UPDATE evidence SET allow_local_read=0", "UPDATE evidence SET deleted_at='2026-01-01'", "DELETE FROM cognition_evidence", "DELETE FROM evidence", "DELETE FROM cognition"])
def test_vector_erasure_is_atomic_with_all_source_mutations(tmp_path: Path, mutation: str) -> None:
    with closing(open_db(str(tmp_path / "atomic.sqlite3"))) as db:
        _seed_cognition(db)
        store.install(db)
        assert store.publish(db, "cognition", "cog-coffee", store.content_hash("用户喜欢喝咖啡"), vector())
        db.execute("BEGIN")
        db.execute(mutation)
        assert db.execute("SELECT count(*) FROM semantic_vector").fetchone()[0] == 0
        db.rollback()
        assert db.execute("SELECT count(*) FROM semantic_vector").fetchone()[0] == 1


def test_extension_is_reentrant_optional_and_expiry_removes_bytes(tmp_path: Path) -> None:
    path = tmp_path / "extension.sqlite3"
    with closing(open_db(str(path))) as db:
        _seed_cognition(db)
        version = user_version(db)
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_vector'").fetchone()
        store.install(db)
        first = tuple(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        store.install(db)
        assert first == tuple(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name"))
        assert user_version(db) == version == 21
        assert store.publish(db, "cognition", "cog-coffee", store.content_hash("用户喜欢喝咖啡"), vector())
        db.execute("UPDATE semantic_vector SET expires_at='2000-01-01'")
        assert store.expire(db) == 1
        assert not db.execute("SELECT 1 FROM semantic_vector").fetchone()
    with closing(open_db(str(path))) as db:
        assert user_version(db) == 21


def test_lazy_start_failure_returns_immediately_without_download(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()
    def failing(folder: Path, stop: threading.Event) -> Any:
        entered.set()
        release.wait(2)
        raise ModuleNotFoundError("synthetic dependency")
    runtime = SemanticRuntime(tmp_path / "lazy.sqlite3", SemanticConfig(), failing)
    try:
        with closing(open_db(str(runtime.path))) as db:
            assert runtime.candidates(db, "owner", "合成问题") == ()
            assert entered.wait(1) and runtime.status()["state"] == "preparing"
            assert runtime.candidates(db, "owner", "合成问题") == ()
        release.set()
        assert runtime._thread is not None
        runtime._thread.join(2)
        assert runtime.status()["state"] == "unavailable"
        assert runtime.status()["reason"] == "dependency_missing"
    finally:
        release.set()
        runtime.close()


def test_bad_download_hash_is_rejected_and_partial_is_removed(tmp_path: Path) -> None:
    class Response:
        def __enter__(self) -> Response: return self
        def __exit__(self, *args: object) -> None: pass
        def raise_for_status(self) -> None: pass
        def iter_bytes(self, size: int) -> Sequence[bytes]: return [b"untrusted"]
    with patch("memoweft.semantic.model.httpx.Client.stream", return_value=Response()), patch("memoweft.semantic.model.FILES", {"synthetic.onnx": (9, "0" * 64)}):
        with pytest.raises(ValueError, match="hash mismatch"):
            prepare_files(tmp_path, threading.Event())
    assert list(tmp_path.iterdir()) == []
    assert not file_valid(tmp_path / "missing", 0, "0" * 64)


def test_forget_during_encoding_cannot_publish_stale_vector(tmp_path: Path) -> None:
    class FakeVector:
        def astype(self, dtype: str) -> FakeVector: return self
        def tobytes(self) -> bytes: return vector()
    with closing(open_db(str(tmp_path / "race.sqlite3"))) as db:
        store.install(db)
        _seed_cognition(db)
        class ForgettingEncoder:
            def encode(self, texts: Sequence[str], *, query: bool = False) -> list[FakeVector]:
                db.execute("UPDATE evidence SET deleted_at='2026-01-01'")
                return [FakeVector() for text in texts]
        runtime = SemanticRuntime(tmp_path / "race.sqlite3", SemanticConfig())
        assert runtime.process_batch(db, ForgettingEncoder()) == 0
        assert not db.execute("SELECT 1 FROM semantic_vector").fetchone()
        runtime.close()


def test_corrected_predecessor_vector_is_deleted_in_same_transaction(tmp_path: Path) -> None:
    with closing(open_db(str(tmp_path / "correction.sqlite3"))) as db:
        store.install(db)
        _seed_cognition(db)
        _seed_cognition(db, "replacement", "e-new", "用户改为喝茶")
        assert store.publish(db, "cognition", "cog-coffee", store.content_hash("用户喜欢喝咖啡"), vector())
        db.execute("INSERT INTO cognition_transitions VALUES('t','cog-coffee','replacement','corrects',1)")
        assert not db.execute("SELECT 1 FROM semantic_vector WHERE item_id='cog-coffee'").fetchone()
        assert store.eligible_record(db, "cognition", "cog-coffee") is None


def test_expiry_cleanup_keeps_working_with_semantic_switch_off(tmp_path: Path) -> None:
    path = tmp_path / "expiry.sqlite3"
    with closing(open_db(str(path))) as db:
        store.install(db)
        _seed_cognition(db)
        assert store.publish(db, "cognition", "cog-coffee", store.content_hash("用户喜欢喝咖啡"), vector())
        db.execute("UPDATE semantic_vector SET expires_at='2000-01-01'")
    configure_runtime(path, SemanticConfig(enabled=False))
    try:
        deadline = monotonic() + 2
        with closing(sqlite3.connect(path)) as db:
            while db.execute("SELECT 1 FROM semantic_vector").fetchone() and monotonic() < deadline:
                sleep(0.01)
            assert not db.execute("SELECT 1 FROM semantic_vector").fetchone()
    finally:
        close_runtime(path)


def test_disable_and_provider_failure_restore_identical_literal_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "fallback.sqlite3"
    memories = [{"id": "literal", "content": "我喜欢蓝色手账。"}]
    fixture.seed(path, {"memories": memories})
    class FailedProvider(ForcedProvider):
        def candidates(self, db: sqlite3.Connection, subject_id: str, query: str) -> Sequence[SemanticCandidate]:
            raise TimeoutError("synthetic inference failure")
    with closing(sqlite3.connect(path)) as db:
        baseline = recall_world_snapshot(db, "fictional-owner", "我喜欢什么手账？", candidate_provider=ForcedProvider([], SemanticConfig(enabled=False)))
        failed = recall_world_snapshot(db, "fictional-owner", "我喜欢什么手账？", candidate_provider=FailedProvider(memories))
        assert baseline == failed and baseline is not None


def test_download_offline_failure_removes_partial_and_does_not_touch_world(tmp_path: Path) -> None:
    import httpx
    with patch("memoweft.semantic.model.httpx.Client.stream", side_effect=httpx.ConnectError("synthetic offline")), patch("memoweft.semantic.model.FILES", {"synthetic.onnx": (9, "0" * 64)}):
        with pytest.raises(httpx.ConnectError):
            prepare_files(tmp_path, threading.Event())
    assert list(tmp_path.iterdir()) == []


def test_account_matrix_isolation_invalidation_and_model_hash_contract(tmp_path: Path) -> None:
    np: Any = importlib.import_module("numpy")
    path = tmp_path / "matrix.sqlite3"
    class FixedEncoder:
        def encode(self, texts: Sequence[str], *, query: bool = False) -> Any:
            return np.array([[1, *([0] * (DIMENSIONS - 1))] for text in texts], dtype="<f4")
    runtime = SemanticRuntime(path, SemanticConfig())
    runtime._encoder = FixedEncoder()
    runtime._state = "ready"
    # Mark this synthetic runtime started without running a loader.
    runtime._thread = threading.Thread(target=lambda: None)
    runtime._thread.start()
    runtime._thread.join()
    with closing(open_db(str(path))) as db:
        store.install(db)
        _seed_cognition(db)
        assert store.publish(db, "cognition", "cog-coffee", store.content_hash("用户喜欢喝咖啡"), vector())
        db.execute("INSERT INTO semantic_vector VALUES('other','cognition','cross','synthetic',?,384,?,NULL)", (MODEL_ID, vector()))
        assert [r.item_id for r in runtime.candidates(db, "owner", "合成问句")] == ["cog-coffee"]
        assert [r.item_id for r in runtime.candidates(db, "other", "合成问句")] == ["cross"]
        assert set(runtime._matrices) == {"owner", "other"}
        db.execute("UPDATE evidence SET deleted_at='2026-01-01'")
        assert runtime.candidates(db, "owner", "合成问句") == ()
        assert runtime._matrices["owner"][1] == []
        db.execute("UPDATE semantic_vector SET model_id='different-model'")
        assert runtime.candidates(db, "other", "合成问句") == ()
    runtime.close()


def test_batched_backfill_resumes_without_encoding_existing_rows(tmp_path: Path) -> None:
    np: Any = importlib.import_module("numpy")
    encoded: list[str] = []
    class CountingEncoder:
        def encode(self, texts: Sequence[str], *, query: bool = False) -> Any:
            encoded.extend(texts)
            return np.array([[1, *([0] * (DIMENSIONS - 1))] for text in texts], dtype="<f4")
    path = tmp_path / "resume.sqlite3"
    fixture.seed(path, {"memories": [{"id": f"m-{i}", "content": f"合成记忆{i}"} for i in range(40)]})
    with closing(open_db(str(path))) as db:
        store.install(db)
        first = SemanticRuntime(path, SemanticConfig(batch_size=16))
        store.enqueue_backfill(db, "cognition", "", 16)
        assert first.process_batch(db, CountingEncoder()) == 16
        first.close()
        assert len(encoded) == 16
    resumed = SemanticRuntime(path, SemanticConfig(batch_size=16), lambda folder, stop: CountingEncoder())
    try:
        assert resumed.start()
        deadline = monotonic() + 3
        while not resumed.backfill_complete and monotonic() < deadline:
            sleep(0.01)
        assert resumed.backfill_complete and resumed.indexed_count == 40
        assert len(encoded) == 40 and len(set(encoded)) == 40
    finally:
        resumed.close()
