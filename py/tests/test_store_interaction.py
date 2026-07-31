"""interaction_context + semantic_resolution store 内部行为单测(注入固定 clock)。"""

from __future__ import annotations

from datetime import datetime, timezone

from memoweft.store import open_db
from memoweft.store.interaction_context import SqliteInteractionContextStore
from memoweft.store.semantic_resolution import SqliteSemanticResolutionStore
from memoweft.types import InteractionContextInput, SemanticResolutionInput, VisibleTurn


def _fixed() -> datetime:
    return datetime(2026, 3, 4, 5, 6, 7, 123_000, tzinfo=timezone.utc)


def test_interaction_context_record_idempotent_and_roundtrip() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteInteractionContextStore(db, clock=_fixed)
        turns = [
            VisibleTurn(role="assistant", content="你喜欢爬山吧?"),
            VisibleTurn(role="user", content="是的"),
        ]
        a = store.record(
            InteractionContextInput(
                subject_id="owner", conversation_id="c1", episode_id="e1", context=turns
            )
        )
        assert a.created_at == "2026-03-04T05:06:07.123Z"  # 注入 clock
        # 幂等只在同 subject + conversation + episode 生效；相同文本跨会话/用户/episode 必须不互相吞掉。
        same = store.record(
            InteractionContextInput(
                subject_id="owner", conversation_id="c1", episode_id="e1", context=turns
            )
        )
        other_conversation = store.record(
            InteractionContextInput(
                subject_id="owner", conversation_id="c2", episode_id="e2", context=turns
            )
        )
        other_subject = store.record(
            InteractionContextInput(
                subject_id="other", conversation_id="c1", episode_id="e3", context=turns
            )
        )
        assert a.id == same.id
        assert other_conversation.id != a.id
        assert other_subject.id != a.id
        other_episode = store.record(
            InteractionContextInput(
                subject_id="owner",
                conversation_id="c1",
                episode_id="e-next",
                context=turns,
            )
        )
        assert other_episode.id != a.id
        assert len(store.all("owner")) == 3
        assert len(store.all("other")) == 1
        # roundtrip:get 回读 context 结构一致
        got = store.get(a.id)
        assert got is not None and got.context == turns
        assert {c.id for c in store.by_conversation("c1")} == {
            a.id,
            other_subject.id,
            other_episode.id,
        }
    finally:
        db.close()


def test_semantic_resolution_put_of_evidence_for_ids() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteSemanticResolutionStore(db, clock=_fixed)
        r = store.put(
            SemanticResolutionInput(
                evidence_id="ev1",
                resolved_content="用户确认喜欢 X",
                resolver_version="consolidate@v7",
                response_act="affirm",
                proposition_origin="assistant_proposed",
            )
        )
        assert r.created_at == "2026-03-04T05:06:07.123Z"
        assert store.of_evidence("ev1") is not None
        assert store.of_evidence("nope") is None
        got = store.get(r.id)
        assert got is not None
        assert (
            got.response_act == "affirm"
            and got.proposition_origin == "assistant_proposed"
        )
        # 未提供的解析维度 → None
        assert (
            got.prompt_act is None
            and got.assertion_strength is None
            and got.required_context is None
        )
        store.put(
            SemanticResolutionInput(
                evidence_id="ev2", resolved_content="x", resolver_version="v"
            )
        )
        assert len(store.for_evidence_ids(["ev1", "ev2"])) == 2
        assert store.for_evidence_ids([]) == []
        assert store.remove_by_evidence_ids(["ev1"]) == 1
        assert store.of_evidence("ev1") is None
    finally:
        db.close()
