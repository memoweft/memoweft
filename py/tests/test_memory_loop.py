"""Real-SQLite contracts for the first persistent MemoWeft memory loop."""
from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal, cast

import pytest

from memoweft.confidence import compute_confidence, derive_cred_status
from memoweft.llm import ChatMessage
from memoweft.store import SCHEMA_VERSION, open_db, user_version
from memoweft.types import ConfidenceInputs, ContentType, EvidenceLink, FormedBy
from memoweft.world import (
    ClaimSpan,
    Entity,
    EventFacet,
    EventParticipant,
    EvolutionStep,
    FormationSourceTrace,
    FormationTrace,
    MemoryTarget,
    MemoryWorldGraph,
    PersonalWorld,
    Perspective,
    Relationship,
    WorldCognition,
    WorldDelta,
    WorldEvent,
    WorldEvolutionPlan,
)
from memoweft.world.loop import (
    CognitionTransitionIntent,
    EvidenceConflictError,
    EvidenceRecord,
    MemoryLoop,
    MemoryLoopError,
    MemoryLoopIntegrityError,
    ProductClaimSlice,
    RecallEvidenceTrace,
    ReviewStateError,
    _json,
    _result_hash,
)
from memoweft.world.extractor import ConversationTurn
from memoweft.world.identity_review import IdentityAuthority
from memoweft.world.identity_store import PersistentIdentityAuthority, ReviewedIdentityBinding
from memoweft.world.model import StructuredClaim
from memoweft.world.turn_meaning import (
    build_accepted_world_object_handles,
    compile_product_turn,
    decode_turn_meaning,
)
from test_world_model_golden_nanjing import build_nanjing_world


class _Answerer:
    call_count = 0
    tier = None
    usage = None

    def __init__(self) -> None:
        self.messages: list[ChatMessage] = []

    def chat(self, messages: list[ChatMessage]) -> str:
        self.call_count += 1
        self.messages = messages
        return "Yun likes tea."


def _graph() -> MemoryWorldGraph:
    owner = Entity("person:yun", "world:yun", "person", "Yun")
    graph = MemoryWorldGraph(PersonalWorld("world:yun", owner.id))
    graph.add_entity(owner)
    return graph


def test_borrowed_connection_is_normalized_to_manual_transactions(tmp_path: Path) -> None:
    """注入的外部连接必须归一为手动事务控制：隐式事务会让 decide() 的
    BEGIN IMMEDIATE 报 "cannot start a transaction within a transaction"
    （评审发现：isolation_level 未归一）。"""
    conn = sqlite3.connect(tmp_path / "borrowed-default.sqlite3")
    try:
        loop = MemoryLoop(conn, _graph())
        assert conn.isolation_level is None  # 已归一，与 store.open_db 一致
        with pytest.raises(ReviewStateError):
            loop.decide("missing-review", "no-hash", "accept")
        loop.close()
    finally:
        conn.close()


def test_decide_on_borrowed_connection_rolls_back_only_its_savepoint(tmp_path: Path) -> None:
    """调用方事务打开时，decide() 失败只回滚自己的 SAVEPOINT，
    不回滚/破坏调用方外层事务（评审发现：ROLLBACK 副作用越界）。"""
    conn = sqlite3.connect(tmp_path / "borrowed-in-txn.sqlite3", isolation_level=None)
    try:
        conn.execute("CREATE TABLE caller_state (k TEXT PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO caller_state VALUES ('marker', 'alive')")
        conn.execute("BEGIN IMMEDIATE")  # 调用方外层事务
        loop = MemoryLoop(conn, _graph())
        with pytest.raises(ReviewStateError):
            loop.decide("missing-review", "no-hash", "accept")
        # 调用方标记仍在外层事务里，且外层事务仍可正常提交
        row = conn.execute("SELECT v FROM caller_state WHERE k = 'marker'").fetchone()
        assert row is not None and row[0] == "alive"
        conn.execute("COMMIT")
        row = conn.execute("SELECT v FROM caller_state WHERE k = 'marker'").fetchone()
        assert row is not None and row[0] == "alive"
        loop.close()
    finally:
        conn.close()


def _score(
    content_type: str,
    formed_by: str,
    support_count: int,
    contradict_count: int,
) -> tuple[int, str]:
    confidence = compute_confidence(
        ConfidenceInputs(
            cast(ContentType, content_type),
            cast(FormedBy, formed_by),
            support_count,
            contradict_count,
        )
    )
    return confidence, derive_cred_status(
        confidence,
        contradict_count,
        cast(ContentType, content_type),
        support_count=support_count,
    )


def _delta(*, cognition_id: str = "cog:tea", evidence_id: str = "e:tea") -> WorldDelta:
    content = "Yun likes tea"
    cognition = WorldCognition(
        cognition_id, "world:yun", MemoryTarget("entity", "person:yun"), content,
        "preference", "stated", 600, "limited", Perspective("entity", ("person:yun",)),
        sources=(EvidenceLink(evidence_id, "support"),),
    )
    trace = FormationTrace(
        cognition.id, False,
        (FormationSourceTrace(
            evidence_id, "support", "user_stated", "elaborate",
            ClaimSpan(0, len(content), "a" * 64, sha256(content.encode()).hexdigest()),
            None, None, "exact_user_claim", "formation.exact_user_claim",
        ),),
        "stated", 1, 1, 0,
    )
    return WorldDelta("world:yun", (evidence_id,), new_cognitions=(cognition,), formation_traces=(trace,))


def _lifecycle_evidence(
    evidence_id: str,
    content: str,
    *,
    occurred_at: str,
    recorded_at: str | None = None,
    legacy: bool = False,
    subject_id: str = "owner-test",
    allow_local_read: bool = True,
    allow_inference: bool = True,
) -> EvidenceRecord:
    metadata: dict[str, object] = {"occurred_at": occurred_at}
    if not legacy:
        metadata["system_evidence"] = {
            "id": evidence_id,
            "subjectId": subject_id,
            "sourceKind": "spoken",
            "hostId": "memory-loop-test",
            "originId": f"origin:{evidence_id}",
            "occurredAt": occurred_at,
            "recordedAt": recorded_at or occurred_at,
            "rawContent": content,
            "summary": content,
            "allowLocalRead": allow_local_read,
            "allowCloudRead": False,
            "allowInference": allow_inference,
            "correctsEvidenceId": None,
        }
    return EvidenceRecord(evidence_id, content, metadata=metadata)


def _accept_lifecycle_cognition(
    loop: MemoryLoop,
    *,
    cognition_id: str,
    content: str,
    content_type: ContentType,
    records: tuple[EvidenceRecord, ...],
    formed_by: FormedBy = "stated",
    relations: tuple[Literal["support", "contradict"], ...] | None = None,
    target_entity_id: str | None = None,
) -> WorldCognition:
    # Multiple exact Evidence records corroborate one direct claim without
    # inflating its formation confidence beyond one effective support.
    resolved_relations = relations or tuple("support" for _ in records)
    assert len(resolved_relations) == len(records)
    support_count = int("support" in resolved_relations)
    contradict_count = resolved_relations.count("contradict")
    confidence, cred_status = _score(
        content_type,
        formed_by,
        support_count,
        contradict_count,
    )
    cognition = WorldCognition(
        cognition_id,
        loop.view().graph.world.world_id,
        MemoryTarget(
            "entity", target_entity_id or loop.view().graph.world.owner_entity_id
        ),
        content,
        content_type,
        formed_by,
        confidence,
        cast(Any, cred_status),
        Perspective("entity", (loop.view().graph.world.owner_entity_id,)),
        tuple(
            EvidenceLink(record.id, relation)
            for record, relation in zip(records, resolved_relations, strict=True)
        ),
    )
    traces = tuple(
        FormationSourceTrace(
            record.id,
            relation,
            "assistant_proposed"
            if formed_by == "confirmed" or relation == "contradict"
            else "user_stated",
            "negate"
            if relation == "contradict"
            else "affirm"
            if formed_by == "confirmed"
            else "elaborate",
            ClaimSpan(
                0,
                len(content),
                sha256(content.encode("utf-8")).hexdigest(),
                sha256(content.encode("utf-8")).hexdigest(),
            ),
            "turn:asking-preceding"
            if formed_by == "confirmed" or relation == "contradict"
            else None,
            "b" * 64
            if formed_by == "confirmed" or relation == "contradict"
            else None,
            "user_negation"
            if relation == "contradict"
            else "assistant_confirmation"
            if formed_by == "confirmed"
            else "inference_grounding"
            if formed_by == "inferred"
            else "exact_user_claim",
            "formation.asking_fixture",
        )
        for record, relation in zip(records, resolved_relations, strict=True)
    )
    delta = WorldDelta(
        cognition.world_id,
        tuple(record.id for record in records),
        new_cognitions=(cognition,),
        formation_traces=(
            FormationTrace(
                cognition.id,
                formed_by == "inferred",
                traces,
                formed_by,
                resolved_relations.count("support"),
                support_count,
                contradict_count,
            ),
        ),
    )
    pending = loop.stage_addition(delta, records)
    loop.decide(pending.id, pending.result_hash, "accept")
    return cognition


def _json_data(value: Any) -> object:
    """Match the loop's dataclass -> canonical JSON projection in test fixtures."""

    return json.loads(json.dumps(asdict(value), ensure_ascii=False))


def _typed_evaluation_correction(
    loop: MemoryLoop,
) -> tuple[
    WorldEvolutionPlan,
    EvidenceRecord,
    dict[str, object],
    WorldCognition,
    WorldCognition,
]:
    evidence_id = "e:typed-correction"
    occurred_at = "2026-08-13T09:00:00Z"
    content = "李华支持星港项目，我觉得这段支持很不可靠。"
    claim_text = "我觉得这段支持很不可靠"
    claim_start = content.index(claim_text)
    claim_end = claim_start + len(claim_text)
    value_text = "很不可靠"
    value_start = content.index(value_text)
    value_end = value_start + len(value_text)
    prior = loop.view().graph.cognitions["cog:relationship-evaluation:prior"]
    assert prior.structured_claim is not None
    confidence, cred_status = _score("fact", "stated", 1, 0)
    successor = WorldCognition(
        "cog:relationship-evaluation:successor",
        prior.world_id,
        prior.target,
        claim_text,
        prior.content_type,
        "stated",
        confidence,
        cast(Any, cred_status),
        prior.perspective,
        (EvidenceLink(evidence_id, "support"),),
        scope=prior.scope,
        structured_claim=StructuredClaim(
            "evaluation",
            value=value_text,
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    trace = FormationTrace(
        successor.id,
        False,
        (
            FormationSourceTrace(
                evidence_id,
                "support",
                "user_stated",
                "elaborate",
                ClaimSpan(
                    claim_start,
                    claim_end,
                    sha256(content.encode("utf-8")).hexdigest(),
                    sha256(claim_text.encode("utf-8")).hexdigest(),
                ),
                None,
                None,
                "exact_user_claim",
                "product.evaluation.exact_user_claim",
            ),
        ),
        "stated",
        1,
        1,
        0,
    )
    step = EvolutionStep(
        "evolution:typed-evaluation-correction",
        "cognition_change",
        "corrects",
        prior.target,
        (prior.id,),
        (successor.id,),
        occurred_at,
        (evidence_id,),
    )
    plan = WorldEvolutionPlan(
        WorldDelta(
            prior.world_id,
            (evidence_id,),
            new_cognitions=(successor,),
            formation_traces=(trace,),
        ),
        (step,),
    )
    evidence = EvidenceRecord(
        evidence_id,
        content,
        metadata={
            "conversation_id": "session:typed-correction",
            "occurred_at": occurred_at,
            "continuity_scope": "session:typed-correction",
        },
    )
    before = cast(dict[str, object], _json_data(prior))
    after = cast(dict[str, object], _json_data(successor))
    formation = cast(dict[str, object], _json_data(trace))
    evolution_step = cast(dict[str, object], _json_data(step))
    candidate_after = {
        **after,
        "evidence": [{"evidenceId": evidence_id, "text": content}],
    }
    review_payload: dict[str, object] = {
        "runId": "memory-run-typed-correction",
        "kind": "adapter-typed-natural-correction",
        "autoApply": True,
        "operationId": "operation:typed-correction",
        "sessionId": "session:typed-correction",
        "currentEvidenceId": evidence_id,
        "adapterRequestHash": "sha256:" + "a" * 64,
        "createdAt": occurred_at,
        "baseWorldHash": loop.view().snapshot_hash,
        "productDisplay": {
            "currentEvidenceId": evidence_id,
            "candidateMemory": {
                "entities": [],
                "relationships": [],
                "events": [],
                "cognitions": [candidate_after],
            },
            "evidence": [{"evidenceId": evidence_id, "text": content}],
            "formation": [formation],
            "evolutionSteps": [evolution_step],
            "cognitionReplacements": [{
                "priorCognitionId": prior.id,
                "successorCognitionId": successor.id,
                "relation": "corrects",
                "evidenceId": evidence_id,
                "before": before,
                "after": after,
            }],
            "cognitionEvidenceChanges": [],
            "transitionIntents": [],
            "meaning": {
                "act": "assertion",
                "claims": [
                    {
                        "kind": "relationship",
                        "disposition": "assert",
                        "text": "李华支持星港项目",
                        "start": 0,
                        "end": len("李华支持星港项目"),
                    },
                    {
                        "kind": "evaluation",
                        "disposition": "correction",
                        "text": claim_text,
                        "start": claim_start,
                        "end": claim_end,
                        "value": {
                            "text": value_text,
                            "start": value_start,
                            "end": value_end,
                        },
                    },
                ],
            },
        },
    }
    return plan, evidence, review_payload, prior, successor


def _typed_correction_loop(path: Path) -> MemoryLoop:
    graph = _graph()
    graph.add_entity(Entity("person:lihua", "world:yun", "person", "李华"))
    graph.add_entity(Entity("project:xinggang", "world:yun", "organization", "星港项目"))
    relationship = Relationship(
        "relationship:lihua-supports-xinggang",
        "world:yun",
        "person:lihua",
        "project:xinggang",
        "支持",
        False,
        "active",
    )
    graph.add_relationship(relationship)
    graph.add_cognition(WorldCognition(
        "cog:relationship-evaluation:prior",
        "world:yun",
        MemoryTarget("relationship", relationship.id),
        "我觉得这段支持很可靠",
        "fact",
        "stated",
        760,
        "limited",
        Perspective("entity", (graph.world.owner_entity_id,)),
        (EvidenceLink("e:typed-prior", "support"),),
        scope="李华支持星港项目",
        structured_claim=StructuredClaim(
            "evaluation",
            value="很可靠",
            polarity="assert",
            epistemic_status="asserted",
        ),
    ))
    return MemoryLoop(path, graph)


def _product_bundle_delta(
    *,
    evidence_id: str = "e:product",
    cognition_id: str = "cog:project-current",
) -> WorldDelta:
    content = "晨星项目已经启动"
    cognition = WorldCognition(
        cognition_id,
        "world:yun",
        MemoryTarget("entity", "project:morningstar"),
        content,
        "project",
        "stated",
        600,
        "limited",
        Perspective("entity", ("person:yun",)),
        sources=(EvidenceLink(evidence_id, "support"),),
        structured_claim=StructuredClaim("attribute", "状态", "已经启动"),
    )
    trace = FormationTrace(
        cognition.id,
        False,
        (FormationSourceTrace(
            evidence_id, "support", "user_stated", "elaborate",
            ClaimSpan(0, len(content), sha256(content.encode()).hexdigest(), sha256(content.encode()).hexdigest()),
            None, None, "exact_user_claim", "formation.exact_user_claim",
        ),),
        "stated", 1, 1, 0,
    )
    artifact = Entity("object:morningstar-brief", "world:yun", "document", "晨星简报")
    relationship = Relationship(
        "relationship:morningstar-brief", "world:yun", "project:morningstar", artifact.id, "has_document"
    )
    event = WorldEvent(
        "event:morningstar-start", "world:yun", "project_started", content,
        "2026-08-12T09:00:00+00:00",
        participants=(EventParticipant("project:morningstar"),),
        related_entity_ids=(artifact.id,),
        relationship_ids=(relationship.id,),
        evidence_ids=(evidence_id,),
    )
    return WorldDelta(
        "world:yun",
        (evidence_id,),
        new_entities=(artifact,),
        new_relationships=(relationship,),
        new_events=(event,),
        new_cognitions=(cognition,),
        formation_traces=(trace,),
    )


def _product_bundle_graph() -> MemoryWorldGraph:
    graph = _graph()
    graph.add_entity(Entity("project:morningstar", "world:yun", "project", "晨星"))
    graph.add_cognition(WorldCognition(
        "cog:project-prior", "world:yun", MemoryTarget("entity", "project:morningstar"),
        "晨星项目尚未启动", "project", "stated", 600, "limited",
        Perspective("entity", ("person:yun",)),
        structured_claim=StructuredClaim("attribute", "状态", "尚未启动"),
    ))
    return graph


def _product_cognition_update_bundle(
    *,
    evidence_id: str = "e:relationship-evaluation-opposite",
) -> tuple[
    MemoryWorldGraph,
    WorldDelta,
    EvidenceRecord,
    EvolutionStep,
    WorldCognition,
]:
    """One valid product cognition update rooted in an accepted Relationship."""

    graph = _graph()
    graph.add_entity(Entity("person:lihua", "world:yun", "person", "李华"))
    graph.add_entity(Entity("project:xinggang", "world:yun", "project", "星港项目"))
    relationship = Relationship(
        "relationship:lihua-supports-xinggang",
        "world:yun",
        "person:lihua",
        "project:xinggang",
        "支持",
    )
    graph.add_relationship(relationship)
    prior = WorldCognition(
        "cog:lihua-supports-xinggang-reliable",
        "world:yun",
        MemoryTarget("relationship", relationship.id),
        "我觉得李华支持星港项目很可靠。",
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", ("person:yun",)),
        sources=(EvidenceLink("e:relationship-evaluation-initial", "support"),),
        structured_claim=StructuredClaim(
            "evaluation",
            value="很可靠",
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    graph.add_cognition(prior)
    occurred_at = "2026-08-13T09:00:00+00:00"
    evidence = EvidenceRecord(
        evidence_id,
        "李华支持星港项目，我不认同这段支持很可靠。",
        metadata={"occurred_at": occurred_at},
    )
    confidence, status = _score("fact", "stated", 1, 1)
    updated = replace(
        prior,
        confidence=confidence,
        cred_status=status,  # type: ignore[arg-type]
        sources=prior.sources + (EvidenceLink(evidence.id, "contradict"),),
    )
    step = EvolutionStep(
        "evolution:lihua-supports-xinggang:contradicts",
        "cognition_change",
        "contradicts",
        prior.target,
        (prior.id,),
        (prior.id,),
        occurred_at,
        (evidence.id,),
    )
    return graph, WorldDelta("world:yun", (evidence.id,)), evidence, step, updated


def _seed_product_cognition_update_source(loop: MemoryLoop) -> None:
    """Install provenance paired with the accepted initial-graph fixture."""

    record = EvidenceRecord(
        "e:relationship-evaluation-initial",
        "我觉得李华支持星港项目很可靠。",
    )
    payload = {
        "id": record.id,
        "content": record.content,
        "role": record.role,
        "metadata": None,
    }
    loop.connection.execute(  # noqa: SLF001 - initial snapshot provenance fixture
        "INSERT INTO evidence_ledger(id, content, payload_json) VALUES (?, ?, ?)",
        (
            record.id,
            record.content,
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ),
    )


def _accept_relationship_provenance(
    loop: MemoryLoop,
    relationship: Relationship,
    *,
    session_id: str,
    evidence_id: str,
) -> WorldCognition:
    """Accept one Relationship plus its same-session formal source cognition."""

    source = loop.view().graph.entities[relationship.source_entity_id]
    target = loop.view().graph.entities[relationship.target_entity_id]
    content = f"{source.canonical_name} {relationship.relation_type} {target.canonical_name}."
    cognition = WorldCognition(
        f"cog:provenance:{relationship.id}",
        relationship.world_id,
        MemoryTarget("relationship", relationship.id),
        content,
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", (loop.view().graph.world.owner_entity_id,)),
        sources=(EvidenceLink(evidence_id, "support"),),
        structured_claim=StructuredClaim(
            "relationship_statement",
            value=relationship.relation_type,
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    content_hash = sha256(content.encode("utf-8")).hexdigest()
    trace = FormationTrace(
        cognition.id,
        False,
        (
            FormationSourceTrace(
                evidence_id,
                "support",
                "user_stated",
                "elaborate",
                ClaimSpan(0, len(content), content_hash, content_hash),
                None,
                None,
                "exact_user_claim",
                "product.relationship.exact_user_claim",
            ),
        ),
        "stated",
        1,
        1,
        0,
    )
    evidence = EvidenceRecord(
        evidence_id,
        content,
        metadata={
            "conversation_id": session_id,
            "occurred_at": "2026-08-13T10:00:00Z",
            "continuity_scope": session_id,
        },
    )
    pending = loop.stage_addition(
        WorldDelta(
            relationship.world_id,
            (evidence_id,),
            new_relationships=(relationship,),
            new_cognitions=(cognition,),
            formation_traces=(trace,),
        ),
        (evidence,),
    )
    loop.decide(pending.id, pending.result_hash, "accept")
    return cognition


def _accept_event_provenance(
    loop: MemoryLoop,
    event: WorldEvent,
    *,
    session_id: str,
    evidence_id: str,
) -> WorldCognition:
    """Accept one Event plus its same-session formal source cognition."""

    cognition = WorldCognition(
        f"cog:provenance:{event.id}",
        event.world_id,
        MemoryTarget("event", event.id),
        event.summary,
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", (loop.view().graph.world.owner_entity_id,)),
        sources=(EvidenceLink(evidence_id, "support"),),
        structured_claim=StructuredClaim(
            "event_statement",
            predicate=event.event_type,
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    content_hash = sha256(event.summary.encode("utf-8")).hexdigest()
    trace = FormationTrace(
        cognition.id,
        False,
        (
            FormationSourceTrace(
                evidence_id,
                "support",
                "user_stated",
                "elaborate",
                ClaimSpan(0, len(event.summary), content_hash, content_hash),
                None,
                None,
                "exact_user_claim",
                "product.event.exact_user_claim",
            ),
        ),
        "stated",
        1,
        1,
        0,
    )
    evidence = EvidenceRecord(
        evidence_id,
        event.summary,
        metadata={
            "conversation_id": session_id,
            "occurred_at": "2026-08-13T10:00:00Z",
            "continuity_scope": session_id,
        },
    )
    pending = loop.stage_addition(
        WorldDelta(
            event.world_id,
            (evidence_id,),
            new_events=(event,),
            new_cognitions=(cognition,),
            formation_traces=(trace,),
        ),
        (evidence,),
    )
    loop.decide(pending.id, pending.result_hash, "accept")
    return cognition


def _relationship_security_graph() -> tuple[
    MemoryWorldGraph,
    Relationship,
    Relationship,
]:
    graph = _graph()
    for entity in (
        Entity("person:lihua", "world:yun", "person", "李华"),
        Entity("project:xinggang", "world:yun", "project", "星港项目"),
        Entity("person:wangqiang", "world:yun", "person", "王强"),
        Entity("project:beichen", "world:yun", "project", "北辰项目"),
    ):
        graph.add_entity(entity)
    first = Relationship(
        "relationship:lihua-supports-xinggang",
        "world:yun",
        "person:lihua",
        "project:xinggang",
        "支持",
    )
    second = Relationship(
        "relationship:wangqiang-supports-beichen",
        "world:yun",
        "person:wangqiang",
        "project:beichen",
        "支持",
    )
    return graph, first, second


def _indirect_relationship_bundle(
    loop: MemoryLoop,
    relationship: Relationship | WorldEvent,
    *,
    session_id: str,
    evidence_id: str,
) -> tuple[WorldDelta, EvidenceRecord, dict[str, object]]:
    """Build the adapter's signed v3 projection from the real trusted compiler."""

    is_event = isinstance(relationship, WorldEvent)
    content = "我觉得那次会议很有意义。" if is_event else "我觉得这段关系很重要。"
    view = loop.view()
    object_handles = build_accepted_world_object_handles(
        view.graph,
        () if is_event else (cast(Relationship, relationship),),
        (cast(WorldEvent, relationship),) if is_event else (),
        world_hash=view.snapshot_hash,
    )
    assert len(object_handles) == 1
    handle = object_handles[0].handle
    reference = "那次会议" if is_event else "这段关系"
    value = "很有意义" if is_event else "很重要"
    raw_meaning = {
        "act": "assertion",
        "mentions": [],
        "claims": [{
            "id": "claim:indirect-relationship-evaluation",
            "kind": "evaluation",
            "subject": None,
            "text": content,
            "start": 0,
            "end": len(content),
            "value": {
                "text": value,
                "start": content.index(value),
                "end": content.index(value) + len(value),
            },
            "predicate": None,
            "occurred_at": None,
            "normalized_occurred_at": None,
            "relationship_direction": None,
            "relationship_symmetric": False,
            "event_owner_participates": False,
            "event_subject_role": None,
            "event_related_roles": [],
            "evaluation_target_claim": None,
            "object_reference": {
                "text": reference,
                "start": content.index(reference),
                "end": content.index(reference) + len(reference),
            },
            "polarity": "affirm",
            "epistemic_status": "stated",
            "disposition": "assert",
            "related_mentions": [],
            "accepted_entity_handles": [],
            "accepted_object_handles": [handle],
            "prior_cognition_handles": [],
        }],
    }
    meaning = decode_turn_meaning(
        json.dumps(raw_meaning, ensure_ascii=False),
        content,
    )
    current_turn = ConversationTurn(
        evidence_id,
        session_id,
        "user",
        content,
        "2026-08-13T10:01:00Z",
    )
    plan = compile_product_turn(
        proposal=meaning,
        current_user_turn=current_turn,
        world_id=view.graph.world.world_id,
        owner_entity_id=view.graph.world.owner_entity_id,
        base_graph=view.graph,
        handles=(),
        identity_view=IdentityAuthority(view.graph).view(),
        operation_key=f"operation:{evidence_id}",
        accepted_object_handles=object_handles,
    )
    assert plan.state == "candidate"
    assert plan.delta is not None
    assert plan.claim_bundle is not None
    cognition = plan.delta.new_cognitions[0]
    trace = plan.delta.formation_traces[0]
    candidate_cognition = cast(dict[str, object], _json_data(cognition))
    candidate_cognition["evidence"] = [{
        "evidenceId": evidence_id,
        "text": content,
    }]
    claim_bundle = plan.claim_bundle.to_data()
    claims = cast(list[dict[str, object]], claim_bundle["claims"])
    object_descriptor: dict[str, object]
    if is_event:
        event = cast(WorldEvent, relationship)
        object_descriptor = {
            "kind": "event",
            "eventId": event.id,
            "participantEntityIds": [item.entity_id for item in event.participants],
            "objectEntityIds": list(event.related_entity_ids),
            "ownerParticipates": any(
                item.entity_id == view.graph.world.owner_entity_id
                for item in event.participants
            ),
            "eventType": event.event_type,
            "occurredAt": event.occurred_at,
        }
    else:
        edge = cast(Relationship, relationship)
        object_descriptor = {
            "kind": "relationship",
            "relationshipId": edge.id,
            "sourceEntityId": edge.source_entity_id,
            "targetEntityId": edge.target_entity_id,
            "relationType": edge.relation_type,
            "bidirectional": edge.bidirectional,
        }
    claims[0].update({
        "object": object_descriptor,
        "perspective": {
            "kind": "entity",
            "holderEntityIds": [view.graph.world.owner_entity_id],
        },
        "evidence": {
            "evidenceId": evidence_id,
            "span": {"start": 0, "end": len(content)},
            "valueSpan": {
                "start": content.index(value),
                "end": content.index(value) + len(value),
            },
        },
        "structuredStatus": "candidate",
        "writeState": "candidate",
    })
    preview = plan.delta.apply_to(
        view.graph,
        loop._evidence_ids() | {evidence_id},  # noqa: SLF001 - exact adapter preview
    )
    display_world = {
        "world": _json_data(preview.world),
        "entities": [
            _json_data(item)
            for item in sorted(preview.entities.values(), key=lambda item: item.id)
        ],
        "relationships": [
            _json_data(item)
            for item in sorted(
                preview.relationships.values(),
                key=lambda item: item.id,
            )
        ],
        "events": [
            _json_data(item)
            for item in sorted(preview.events.values(), key=lambda item: item.id)
        ],
        "cognitions": [
            _json_data(item)
            for item in sorted(
                preview.cognitions.values(),
                key=lambda item: item.id,
            )
        ],
    }
    display = {
        "title": "跨轮 Event 评价" if is_event else "跨轮 Relationship 评价",
        "previewWorldHash": "sha256:"
        + sha256(_json(display_world).encode("utf-8")).hexdigest(),
        "candidateMemory": {
            "entities": [],
            "relationships": [],
            "events": [],
            "cognitions": [candidate_cognition],
        },
        "evidence": [{"evidenceId": evidence_id, "text": content}],
        "formation": [cast(dict[str, object], _json_data(trace))],
        "unresolvedReferences": [],
        "semanticUncertainties": [],
        "meaning": {
            "act": meaning.act,
            "mention": None,
            "statement": None,
            "mentions": [],
            "claims": [cast(dict[str, object], _json_data(meaning.claims[0]))],
            "code": plan.code,
        },
        "identityBindings": [],
        "target": object_descriptor,
        "statementKind": "evaluation",
        "ownerPerspective": {
            "kind": "entity",
            "entityIds": [view.graph.world.owner_entity_id],
        },
        "claims": claim_bundle,
        "transitionIntents": [],
        "evolutionSteps": [],
        "cognitionEvidenceChanges": [],
        "cognitionReplacements": [],
        "currentEvidenceId": evidence_id,
    }
    review_payload: dict[str, object] = {
        "runId": f"memory-run:{evidence_id}",
        "kind": "product-bundle",
        "autoApply": True,
        "operationId": f"operation:{evidence_id}",
        "sessionId": session_id,
        "currentEvidenceId": evidence_id,
        "adapterRequestHash": "sha256:" + "a" * 64,
        "createdAt": "2026-08-13T10:01:00Z",
        "baseWorldHash": view.snapshot_hash,
        "productDisplay": display,
    }
    evidence = EvidenceRecord(
        evidence_id,
        content,
        metadata={
            "conversation_id": session_id,
            "occurred_at": current_turn.occurred_at,
            "continuity_scope": session_id,
        },
    )
    return plan.delta, evidence, review_payload


def _rehash_stored_product_bundle(
    payload: dict[str, object],
    review_payload: dict[str, object],
) -> str:
    """Reproduce a product-bundle signature without semantic revalidation."""

    parts: list[object] = [
        "product_bundle",
        _json(payload["delta"]),
        _json(payload["evidence"]),
        _json(payload["identity_bindings"]),
        _json(payload["transition_intents"]),
    ]
    if "evolution_steps" in payload:
        parts.append(_json(payload["evolution_steps"]))
    if "cognition_updates" in payload:
        parts.append(_json(payload["cognition_updates"]))
    parts.extend((
        _json(payload["claim_slices"]),
        _json(payload["always_include_entity_ids"]),
        _json(payload["always_include_identity_binding_indices"]),
        review_payload,
    ))
    return _result_hash(*parts)


def _claim_selection_bundle() -> tuple[WorldDelta, EvidenceRecord, tuple[ReviewedIdentityBinding, ...], tuple[ProductClaimSlice, ...]]:
    """Two offered claims share a required focal entity and identity binding."""
    content = "晨星已启动，晨星主色深蓝"
    evidence = EvidenceRecord(
        "e:claim-selection",
        content,
        metadata={
            "conversation_id": "conversation:selection",
            "occurred_at": "2026-08-12T10:00:00+00:00",
            "continuity_scope": "session:selection",
        },
    )
    phase = WorldCognition(
        "cog:phase", "world:yun", MemoryTarget("entity", "project:morningstar"),
        "晨星已启动", "project", "stated", 600, "limited",
        Perspective("entity", ("person:yun",)),
        sources=(EvidenceLink(evidence.id, "support"),),
        structured_claim=StructuredClaim("attribute", "状态", "已启动"),
    )
    color = WorldCognition(
        "cog:color", "world:yun", MemoryTarget("entity", "project:morningstar"),
        "晨星主色深蓝", "project", "stated", 600, "limited",
        Perspective("entity", ("person:yun",)),
        sources=(EvidenceLink(evidence.id, "support"),),
        structured_claim=StructuredClaim("attribute", "主色", "深蓝"),
    )
    source_hash = sha256(content.encode()).hexdigest()
    traces = tuple(
        FormationTrace(
            cognition.id, False,
            (FormationSourceTrace(
                evidence.id, "support", "user_stated", "elaborate",
                ClaimSpan(content.index(cognition.content), content.index(cognition.content) + len(cognition.content), source_hash, sha256(cognition.content.encode()).hexdigest()),
                None, None, "exact_user_claim", "formation.exact_user_claim",
            ),),
            "stated", 1, 1, 0,
        )
        for cognition in (phase, color)
    )
    delta = WorldDelta(
        "world:yun", (evidence.id,),
        new_entities=(Entity("project:morningstar", "world:yun", "project", "晨星"),),
        new_cognitions=(phase, color),
        formation_traces=traces,
    )
    bindings: tuple[ReviewedIdentityBinding, ...] = (
        ReviewedIdentityBinding(
            "project:morningstar", evidence.id, "conversation:selection",
            "2026-08-12T10:00:00+00:00", 0, 2, "project", "session:selection",
        ),
    )
    slices = (
        ProductClaimSlice("claim:phase", cognition_ids=(phase.id,)),
        ProductClaimSlice("claim:color", cognition_ids=(color.id,), depends_on_claim_ids=("claim:phase",)),
    )
    return delta, evidence, bindings, slices


def _loop(tmp_path: Path) -> MemoryLoop:
    return MemoryLoop(tmp_path / "memory.sqlite", _graph())


def _cognition(
    cognition_id: str,
    content: str,
    *,
    target: MemoryTarget | None = None,
    perspective: Perspective | None = None,
    content_type: ContentType = "fact",
    scope: str | None = None,
) -> WorldCognition:
    return WorldCognition(
        cognition_id,
        "world:yun",
        target or MemoryTarget("entity", "person:yun"),
        content,
        content_type,
        "inferred",
        440,
        "low",
        perspective or Perspective("entity", ("person:yun",)),
        scope=scope,
    )


def _accept_addition(loop: MemoryLoop) -> str:
    review = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
    loop.decide(review.id, review.result_hash, "accept")
    return "cog:tea"


def test_pending_reject_accept_and_reopen_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    loop = MemoryLoop(path, _graph())
    pending = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
    assert loop.view().revision == 0 and not loop.view().graph.cognitions
    rejected = loop.decide(pending.id, pending.result_hash, "reject")
    assert rejected.revision == 0 and not rejected.graph.cognitions
    accepted = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
    view = loop.decide(accepted.id, accepted.result_hash, "accept")
    assert view.revision == 1 and set(view.graph.cognitions) == {"cog:tea"}
    loop.close()
    with MemoryLoop(path, _graph()) as reopened:
        assert reopened.view().revision == 1
        assert reopened.view().graph.cognitions["cog:tea"].content == "Yun likes tea"


def test_decision_receipt_preserves_original_accept_outcome_after_later_world_change(tmp_path: Path) -> None:
    path = tmp_path / "decision-receipt-accept.sqlite"
    loop = MemoryLoop(path, _graph())
    first = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
    first_view = loop.decide(first.id, first.result_hash, "accept")
    first_receipt = loop.decision_receipt(first.id)
    assert first_receipt is not None
    assert first_receipt.offered_result_hash == first.result_hash
    assert first_receipt.effective_decision == "accept"
    assert first_receipt.world_revision == first_view.revision == 1
    assert first_receipt.snapshot_hash == first_view.snapshot_hash

    later = loop.stage_addition(
        _delta(cognition_id="cog:coffee", evidence_id="e:coffee"),
        (EvidenceRecord("e:coffee", "I like coffee."),),
    )
    later_view = loop.decide(later.id, later.result_hash, "accept")
    assert later_view.revision == 2
    assert later_view.snapshot_hash != first_receipt.snapshot_hash
    loop.close()

    with MemoryLoop(path, _graph()) as reopened:
        replay = reopened.decision_receipt(first.id)
        assert replay == first_receipt
        assert reopened.view().revision == 2
        assert reopened.view().snapshot_hash != replay.snapshot_hash


def test_decision_receipt_records_reject_with_unchanged_world_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "decision-receipt-reject.sqlite"
    with MemoryLoop(path, _graph()) as loop:
        before = loop.view()
        pending = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
        rejected = loop.decide(pending.id, pending.result_hash, "reject")
        receipt = loop.decision_receipt(pending.id)
        assert receipt is not None
        assert receipt.effective_decision == "reject"
        assert receipt.offered_result_hash == pending.result_hash
        assert receipt.world_revision == rejected.revision == before.revision == 0
        assert receipt.snapshot_hash == rejected.snapshot_hash == before.snapshot_hash

    with MemoryLoop(path, _graph()) as reopened:
        receipt = reopened.decision_receipt(pending.id)
        assert receipt is not None
        assert receipt.effective_decision == "reject"
        assert receipt.world_revision == 0


def test_decision_receipt_tamper_or_inconsistent_proposal_fails_closed(tmp_path: Path) -> None:
    with MemoryLoop(tmp_path / "decision-receipt-integrity.sqlite", _graph()) as loop:
        pending = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
        loop.decide(pending.id, pending.result_hash, "accept")
        original = loop.decision_receipt(pending.id)
        assert original is not None
        loop.connection.execute(  # noqa: SLF001 - exercise fail-closed durable read
            "UPDATE proposal_decision_receipts SET snapshot_hash = ? WHERE proposal_id = ?",
            ("sha256:tampered", pending.id),
        )
        with pytest.raises(MemoryLoopIntegrityError, match="decision receipt hash mismatch"):
            loop.decision_receipt(pending.id)

        # Restore the original, integrity-bound terminal fact, then prove a
        # proposal status that contradicts it cannot be silently projected as
        # a terminal receipt.
        loop.connection.execute(
            "UPDATE proposal_decision_receipts SET offered_result_hash = ?, effective_decision = ?, "
            "world_revision = ?, snapshot_hash = ?, decided_at = ?, receipt_hash = ? WHERE proposal_id = ?",
            (
                original.offered_result_hash,
                original.effective_decision,
                original.world_revision,
                original.snapshot_hash,
                original.decided_at,
                original.receipt_hash,
                pending.id,
            ),
        )
        loop.connection.execute("UPDATE proposals SET status = 'pending' WHERE id = ?", (pending.id,))
        with pytest.raises(MemoryLoopIntegrityError, match="decision receipt disagrees"):
            loop.decision_receipt(pending.id)


def test_decision_receipt_rejects_tampered_accepted_base_revision(tmp_path: Path) -> None:
    """A receipt is not trustworthy when its proposal's committed revision moved."""
    with MemoryLoop(tmp_path / "decision-receipt-base-revision.sqlite", _graph()) as loop:
        pending = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
        loop.decide(pending.id, pending.result_hash, "accept")
        receipt = loop.decision_receipt(pending.id)
        assert receipt is not None and receipt.world_revision == pending.base_revision + 1

        loop.connection.execute(  # noqa: SLF001 - exercise receipt/proposal integrity boundary
            "UPDATE proposals SET base_revision = ? WHERE id = ?",
            (pending.base_revision + 1, pending.id),
        )
        with pytest.raises(MemoryLoopIntegrityError, match="invalid world revision"):
            loop.decision_receipt(pending.id)


def test_memory_loop_can_share_the_v3_main_store_connection() -> None:
    db = open_db(":memory:")
    try:
        loop = MemoryLoop(db, _graph())
        assert loop.connection is db
        assert user_version(db) == SCHEMA_VERSION
        assert loop.view().graph.world.world_id == "world:yun"
        loop.close()
        assert db.execute("SELECT revision FROM memory_state WHERE singleton = 1").fetchone()[0] == 0
    finally:
        db.close()


def test_product_bundle_accepts_connected_world_and_transition_then_reopens(tmp_path: Path) -> None:
    path = tmp_path / "product-bundle.sqlite"
    loop = MemoryLoop(path, _product_bundle_graph())
    evidence = EvidenceRecord("e:product", "晨星项目已经启动")
    review = loop.stage_product_bundle(
        _product_bundle_delta(),
        (evidence,),
        {"display": {"title": "晨星更新", "statementKind": "attribute"}},
        transition_intents=(
            CognitionTransitionIntent(
                "cog:project-prior", "cog:project-current", "corrects", "attribute"
            ),
        ),
    )
    assert review.kind == "product_bundle"
    row = loop.connection.execute(
        "SELECT payload_json, review_payload_json FROM proposals WHERE id = ?", (review.id,)
    ).fetchone()
    assert row is not None
    payload = json.loads(row[0])
    assert set(payload) == {
        "delta",
        "evidence",
        "identity_bindings",
        "transition_intents",
        "claim_slices",
        "always_include_entity_ids",
        "always_include_identity_binding_indices",
    }
    assert payload["transition_intents"] == [{
        "prior_cognition_id": "cog:project-prior",
        "successor_cognition_id": "cog:project-current",
        "reason": "corrects",
        "statement_kind": "attribute",
    }]
    assert json.loads(row[1]) == {"display": {"title": "晨星更新", "statementKind": "attribute"}}

    accepted = loop.decide(review.id, review.result_hash, "accept")
    assert accepted.revision == 1
    assert "object:morningstar-brief" in accepted.graph.entities
    assert "relationship:morningstar-brief" in accepted.graph.relationships
    assert "event:morningstar-start" in accepted.graph.events
    assert accepted.superseded_cognition_ids == frozenset({"cog:project-prior"})
    assert [item.id for item in accepted.current_cognitions] == ["cog:project-current"]
    assert accepted.graph.cognitions["cog:project-current"].structured_claim == StructuredClaim(
        "attribute", "状态", "已经启动"
    )
    assert [(item.prior_cognition_id, item.replacement_cognition_id, item.reason) for item in accepted.transitions] == [
        ("cog:project-prior", "cog:project-current", "corrects")
    ]
    assert loop.connection.execute("SELECT content FROM evidence_ledger WHERE id = 'e:product'").fetchone()[0] == "晨星项目已经启动"
    loop.close()

    with MemoryLoop(path, _product_bundle_graph()) as reopened:
        view = reopened.view()
        assert view.revision == 1
        assert view.superseded_cognition_ids == frozenset({"cog:project-prior"})
        assert view.graph.cognitions["cog:project-current"].structured_claim == StructuredClaim(
            "attribute", "状态", "已经启动"
        )
        assert len(view.transitions) == 1


def test_product_bundle_reject_and_hash_tamper_leave_world_unchanged(tmp_path: Path) -> None:
    with MemoryLoop(tmp_path / "product-bundle-reject.sqlite", _product_bundle_graph()) as loop:
        rejected = loop.stage_product_bundle(_product_bundle_delta(), (EvidenceRecord("e:product", "晨星项目已经启动"),))
        view = loop.decide(rejected.id, rejected.result_hash, "reject")
        assert view.revision == 0
        assert "object:morningstar-brief" not in view.graph.entities
        assert not view.transitions
        assert loop.connection.execute("SELECT 1 FROM evidence_ledger WHERE id = 'e:product'").fetchone() is None

        tampered = loop.stage_product_bundle(
            _product_bundle_delta(evidence_id="e:tampered", cognition_id="cog:tampered"),
            (EvidenceRecord("e:tampered", "晨星项目已经启动"),),
            {"display": "reviewed"},
        )
        loop.connection.execute(  # noqa: SLF001 - hash covers the display payload
            "UPDATE proposals SET review_payload_json = ? WHERE id = ?",
            (json.dumps({"display": "changed"}), tampered.id),
        )
        with pytest.raises(MemoryLoopIntegrityError, match="cannot be reconstructed|result hash"):
            loop.decide(tampered.id, tampered.result_hash, "accept")
        assert loop.view().revision == 0
        assert loop.connection.execute("SELECT 1 FROM evidence_ledger WHERE id = 'e:tampered'").fetchone() is None
        assert loop.connection.execute("SELECT status FROM proposals WHERE id = ?", (tampered.id,)).fetchone()[0] == "pending"


def test_product_bundle_stale_base_world_hash_is_rejected_before_any_staging_write(
    tmp_path: Path,
) -> None:
    with MemoryLoop(
        tmp_path / "product-bundle-stale-base-hash.sqlite",
        _product_bundle_graph(),
    ) as loop:
        before = loop.view()
        state_before = tuple(loop.connection.execute(
            "SELECT revision, snapshot_json, snapshot_hash "
            "FROM memory_state WHERE singleton = 1"
        ).fetchone())
        review_payload = {
            "kind": "product-bundle",
            "baseWorldHash": before.snapshot_hash + "-stale",
        }

        with pytest.raises(MemoryLoopError, match="base World hash is stale"):
            loop.stage_product_bundle(
                _product_bundle_delta(),
                (EvidenceRecord("e:product", "晨星项目已经启动"),),
                review_payload,
            )

        after = loop.view()
        assert after.revision == before.revision == 0
        assert after.snapshot_hash == before.snapshot_hash
        assert tuple(loop.connection.execute(
            "SELECT revision, snapshot_json, snapshot_hash "
            "FROM memory_state WHERE singleton = 1"
        ).fetchone()) == state_before
        assert loop.connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
        assert loop.connection.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 0
        assert loop.connection.execute(
            "SELECT COUNT(*) FROM proposal_decision_receipts"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("status", "valid_to", "is_current"),
    (
        ("active", None, True),
        ("ended", None, False),
        ("active", "2000-01-01T00:00:00+00:00", False),
    ),
)
def test_product_bundle_new_cognition_targets_only_a_current_relationship(
    tmp_path: Path,
    status: str,
    valid_to: str | None,
    is_current: bool,
) -> None:
    graph = _graph()
    graph.add_entity(Entity("person:lin", "world:yun", "person", "Lin"))
    relationship = Relationship(
        "relationship:yun-lin",
        "world:yun",
        "person:yun",
        "person:lin",
        "friend",
        status=cast(Any, status),
        valid_to=valid_to,
    )
    graph.add_relationship(relationship)
    evidence_id = f"e:relationship-evaluation:{status}:{valid_to or 'open'}"
    cognition_id = f"cog:relationship-evaluation:{status}:{valid_to or 'open'}"
    content = "I think this relationship is reliable."
    cognition = WorldCognition(
        cognition_id,
        "world:yun",
        MemoryTarget("relationship", relationship.id),
        content,
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", ("person:yun",)),
        sources=(EvidenceLink(evidence_id, "support"),),
        structured_claim=StructuredClaim(
            "evaluation",
            value="reliable",
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    content_hash = sha256(content.encode("utf-8")).hexdigest()
    trace = FormationTrace(
        cognition.id,
        False,
        (
            FormationSourceTrace(
                evidence_id,
                "support",
                "user_stated",
                "elaborate",
                ClaimSpan(0, len(content), content_hash, content_hash),
                None,
                None,
                "exact_user_claim",
                "product.evaluation.exact_user_claim",
            ),
        ),
        "stated",
        1,
        1,
        0,
    )
    delta = WorldDelta(
        "world:yun",
        (evidence_id,),
        new_cognitions=(cognition,),
        formation_traces=(trace,),
    )
    evidence = EvidenceRecord(evidence_id, content)
    case_name = "current" if is_current else f"noncurrent-{status}-{bool(valid_to)}"

    with MemoryLoop(tmp_path / f"relationship-target-{case_name}.sqlite", graph) as loop:
        before = loop.view()
        if is_current:
            pending = loop.stage_product_bundle(delta, (evidence,))
            accepted = loop.decide(pending.id, pending.result_hash, "accept")
            assert accepted.revision == 1
            assert accepted.graph.cognitions[cognition.id] == cognition
            assert loop.connection.execute(
                "SELECT content FROM evidence_ledger WHERE id = ?",
                (evidence.id,),
            ).fetchone()[0] == content
            return

        with pytest.raises(
            MemoryLoopError,
            match="targets a non-current Relationship",
        ):
            loop.stage_product_bundle(delta, (evidence,))

        after = loop.view()
        assert after.revision == before.revision == 0
        assert after.snapshot_hash == before.snapshot_hash
        assert cognition.id not in after.graph.cognitions
        assert loop.connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
        assert loop.connection.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 0
        assert loop.connection.execute(
            "SELECT COUNT(*) FROM proposal_decision_receipts"
        ).fetchone()[0] == 0


def test_pending_product_bundle_stays_stale_and_zero_write_after_world_advances(
    tmp_path: Path,
) -> None:
    with MemoryLoop(
        tmp_path / "product-bundle-stale-after-world-advance.sqlite",
        _product_bundle_graph(),
    ) as loop:
        product_before = loop.view()
        pending = loop.stage_product_bundle(
            _product_bundle_delta(),
            (EvidenceRecord("e:product", "晨星项目已经启动"),),
            {
                "kind": "product-bundle",
                "baseWorldHash": product_before.snapshot_hash,
            },
        )
        other = loop.stage_addition(
            _delta(),
            (EvidenceRecord("e:tea", "I like tea."),),
        )
        advanced = loop.decide(other.id, other.result_hash, "accept")
        assert advanced.revision == 1
        state_before_failed_accept = tuple(loop.connection.execute(
            "SELECT revision, snapshot_json, snapshot_hash "
            "FROM memory_state WHERE singleton = 1"
        ).fetchone())
        evidence_before_failed_accept = [
            tuple(row)
            for row in loop.connection.execute(
                "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
            ).fetchall()
        ]

        with pytest.raises(ReviewStateError, match="stale review"):
            loop.decide(pending.id, pending.result_hash, "accept")

        assert tuple(loop.connection.execute(
            "SELECT revision, snapshot_json, snapshot_hash "
            "FROM memory_state WHERE singleton = 1"
        ).fetchone()) == state_before_failed_accept
        assert [
            tuple(row)
            for row in loop.connection.execute(
                "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
            ).fetchall()
        ] == evidence_before_failed_accept
        assert "object:morningstar-brief" not in loop.view().graph.entities
        assert "cog:project-current" not in loop.view().graph.cognitions
        assert loop.connection.execute(
            "SELECT 1 FROM evidence_ledger WHERE id = 'e:product'"
        ).fetchone() is None
        assert loop.connection.execute(
            "SELECT status FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()[0] == "pending"
        assert loop.decision_receipt(pending.id) is None


@pytest.mark.parametrize(
    "tamper",
    (
        "claims_object_target",
        "candidate_memory_target",
        "formation",
        "evidence",
    ),
)
def test_rehashed_indirect_relationship_display_fork_is_zero_write(
    tmp_path: Path,
    tamper: str,
) -> None:
    session_id = "session:indirect-display-integrity"
    graph, selected, other = _relationship_security_graph()
    with MemoryLoop(
        tmp_path / f"indirect-display-integrity-{tamper}.sqlite",
        graph,
    ) as loop:
        _accept_relationship_provenance(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:relationship:selected",
        )
        _accept_relationship_provenance(
            loop,
            other,
            session_id="session:other",
            evidence_id="e:relationship:other",
        )
        delta, evidence, review_payload = _indirect_relationship_bundle(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:relationship:indirect-evaluation",
        )
        pending = loop.stage_product_bundle(
            delta,
            (evidence,),
            review_payload=review_payload,
        )
        row = loop.connection.execute(
            "SELECT payload_json, review_payload_json FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()
        assert row is not None
        stored_payload = cast(dict[str, object], json.loads(row["payload_json"]))
        stored_review = cast(
            dict[str, object],
            json.loads(row["review_payload_json"]),
        )
        display = cast(dict[str, object], stored_review["productDisplay"])
        if tamper == "claims_object_target":
            claims = cast(dict[str, object], display["claims"])
            resolutions = cast(
                list[dict[str, object]],
                claims["claim_resolutions"],
            )
            resolutions[0].update({
                "target_id": other.id,
                "source_entity_id": other.source_entity_id,
                "target_entity_id": other.target_entity_id,
                "relation_type": other.relation_type,
                "bidirectional": other.bidirectional,
            })
            other_handle = build_accepted_world_object_handles(
                loop.view().graph,
                (other,),
                world_hash=loop.view().snapshot_hash,
            )[0]
            raw_claims = cast(list[dict[str, object]], claims["claims"])
            raw_claims[0]["accepted_object_handles"] = [other_handle.handle]
        elif tamper == "candidate_memory_target":
            candidate_memory = cast(
                dict[str, object],
                display["candidateMemory"],
            )
            candidate_cognitions = cast(
                list[dict[str, object]],
                candidate_memory["cognitions"],
            )
            candidate_target = cast(
                dict[str, object],
                candidate_cognitions[0]["target"],
            )
            candidate_target["id"] = other.id
        elif tamper == "formation":
            formation = cast(list[dict[str, object]], display["formation"])
            formation[0]["cognition_id"] = "cog:forged-display-target"
        else:
            signed_evidence = cast(
                list[dict[str, object]],
                display["evidence"],
            )
            signed_evidence[0]["text"] = "forged display Evidence"

        rehashed = _rehash_stored_product_bundle(stored_payload, stored_review)
        loop.connection.execute(
            "UPDATE proposals SET review_payload_json = ?, result_hash = ? WHERE id = ?",
            (_json(stored_review), rehashed, pending.id),
        )
        before = loop.view()

        with pytest.raises(
            MemoryLoopIntegrityError,
            match="indirect World object evaluation contract is invalid",
        ):
            loop.decide(pending.id, rehashed, "accept")

        after = loop.view()
        assert after.revision == before.revision
        assert after.snapshot_hash == before.snapshot_hash
        assert after.graph == before.graph
        assert delta.new_cognitions[0].id not in after.graph.cognitions
        assert loop.connection.execute(
            "SELECT 1 FROM evidence_ledger WHERE id = ?",
            (evidence.id,),
        ).fetchone() is None
        assert loop.connection.execute(
            "SELECT status FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()[0] == "pending"
        assert loop.decision_receipt(pending.id) is None


def test_rehashed_indirect_relationship_shape_downgrade_is_zero_write(
    tmp_path: Path,
) -> None:
    """A rehashed delta cannot escape the closed indirect-object contract."""

    session_id = "session:indirect-shape-downgrade"
    graph, selected, other = _relationship_security_graph()
    with MemoryLoop(
        tmp_path / "indirect-shape-downgrade.sqlite",
        graph,
    ) as loop:
        _accept_relationship_provenance(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:relationship:shape-selected",
        )
        _accept_relationship_provenance(
            loop,
            other,
            session_id="session:shape-other",
            evidence_id="e:relationship:shape-other",
        )
        delta, evidence, review_payload = _indirect_relationship_bundle(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:relationship:shape-indirect-evaluation",
        )
        pending = loop.stage_product_bundle(
            delta,
            (evidence,),
            review_payload=review_payload,
        )
        row = loop.connection.execute(
            "SELECT payload_json, review_payload_json FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()
        assert row is not None
        stored_payload = cast(dict[str, object], json.loads(row["payload_json"]))
        stored_review = cast(
            dict[str, object],
            json.loads(row["review_payload_json"]),
        )

        delta_data = cast(dict[str, object], stored_payload["delta"])
        new_cognitions = cast(
            list[dict[str, object]],
            delta_data["new_cognitions"],
        )
        cast(dict[str, object], new_cognitions[0]["target"])["id"] = other.id
        cast(list[dict[str, object]], delta_data["new_entities"]).append(cast(
            dict[str, object],
            _json_data(Entity(
                "object:actual-world-target-decoy",
                "world:yun",
                "object",
                "干扰对象",
            )),
        ))
        new_entities = cast(list[dict[str, object]], delta_data["new_entities"])
        new_entities.append(cast(
            dict[str, object],
            _json_data(Entity(
                "object:shape-downgrade-decoy",
                "world:yun",
                "object",
                "干扰对象",
            )),
        ))

        display = cast(dict[str, object], stored_review["productDisplay"])
        claims_bundle = cast(dict[str, object], display["claims"])
        raw_claims = cast(list[dict[str, object]], claims_bundle["claims"])
        raw_claims[0]["accepted_object_handles"] = []
        meaning = cast(dict[str, object], display["meaning"])
        meaning_claims = cast(list[dict[str, object]], meaning["claims"])
        meaning_claims[0]["accepted_object_handles"] = []

        rehashed = _rehash_stored_product_bundle(stored_payload, stored_review)
        loop.connection.execute(
            "UPDATE proposals SET payload_json = ?, review_payload_json = ?, "
            "result_hash = ? WHERE id = ?",
            (_json(stored_payload), _json(stored_review), rehashed, pending.id),
        )
        before = loop.view()
        before_counts = tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone())

        with pytest.raises(
            MemoryLoopIntegrityError,
            match="indirect World object evaluation contract is invalid",
        ):
            loop.decide(pending.id, rehashed, "accept")

        after = loop.view()
        assert after.revision == before.revision
        assert after.snapshot_hash == before.snapshot_hash
        assert after.graph == before.graph
        assert "object:shape-downgrade-decoy" not in after.graph.entities
        assert delta.new_cognitions[0].id not in after.graph.cognitions
        assert tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone()) == before_counts
        assert loop.connection.execute(
            "SELECT 1 FROM evidence_ledger WHERE id = ?",
            (evidence.id,),
        ).fetchone() is None
        assert loop.connection.execute(
            "SELECT status FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()[0] == "pending"
        assert loop.decision_receipt(pending.id) is None


def test_rehashed_indirect_relationship_marker_removal_cannot_hide_actual_world_target(
    tmp_path: Path,
) -> None:
    """The applied Relationship evaluation selects its contract, not display hints."""

    session_id = "session:indirect-actual-world-target"
    graph, selected, other = _relationship_security_graph()
    with MemoryLoop(
        tmp_path / "indirect-actual-world-target.sqlite",
        graph,
    ) as loop:
        _accept_relationship_provenance(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:relationship:actual-selected",
        )
        _accept_relationship_provenance(
            loop,
            other,
            session_id="session:actual-other",
            evidence_id="e:relationship:actual-other",
        )
        delta, evidence, review_payload = _indirect_relationship_bundle(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:relationship:actual-indirect-evaluation",
        )
        pending = loop.stage_product_bundle(
            delta,
            (evidence,),
            review_payload=review_payload,
        )
        row = loop.connection.execute(
            "SELECT payload_json, review_payload_json FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()
        assert row is not None
        stored_payload = cast(dict[str, object], json.loads(row["payload_json"]))
        stored_review = cast(
            dict[str, object],
            json.loads(row["review_payload_json"]),
        )

        # Point the actual World mutation at a different current Relationship,
        # then remove every optional display marker that previously selected
        # the closed object-reference validator.  Re-signing the internally
        # inconsistent row must not turn those hints into write authority.
        delta_data = cast(dict[str, object], stored_payload["delta"])
        new_cognitions = cast(
            list[dict[str, object]],
            delta_data["new_cognitions"],
        )
        cast(dict[str, object], new_cognitions[0]["target"])["id"] = other.id

        display = cast(dict[str, object], stored_review["productDisplay"])
        claims_bundle = cast(dict[str, object], display["claims"])
        raw_claim = cast(list[dict[str, object]], claims_bundle["claims"])[0]
        raw_claim.pop("accepted_object_handles")
        raw_claim["object_reference"] = None
        raw_claim["subject_mention_index"] = 0
        resolution = cast(
            list[dict[str, object]],
            claims_bundle["claim_resolutions"],
        )[0]
        resolution["subject_entity_id"] = selected.source_entity_id

        meaning = cast(dict[str, object], display["meaning"])
        meaning_claim = cast(list[dict[str, object]], meaning["claims"])[0]
        meaning_claim.pop("accepted_object_handles")
        meaning_claim["object_reference"] = None
        meaning_claim["subject_mention_index"] = 0

        rehashed = _rehash_stored_product_bundle(stored_payload, stored_review)
        loop.connection.execute(
            "UPDATE proposals SET payload_json = ?, review_payload_json = ?, "
            "result_hash = ? WHERE id = ?",
            (_json(stored_payload), _json(stored_review), rehashed, pending.id),
        )
        before = loop.view()
        before_counts = tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone())

        with pytest.raises(
            MemoryLoopIntegrityError,
            match="indirect World object evaluation contract is invalid",
        ):
            loop.decide(pending.id, rehashed, "accept")

        after = loop.view()
        assert after.revision == before.revision
        assert after.snapshot_hash == before.snapshot_hash
        assert after.graph == before.graph
        assert delta.new_cognitions[0].id not in after.graph.cognitions
        assert tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone()) == before_counts
        assert loop.connection.execute(
            "SELECT status FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()[0] == "pending"
        assert loop.decision_receipt(pending.id) is None


def _event_security_graph() -> tuple[MemoryWorldGraph, WorldEvent, WorldEvent]:
    graph = _graph()
    graph.add_entity(Entity("person:lihua", "world:yun", "person", "李华"))
    graph.add_entity(Entity("place:xinggang", "world:yun", "place", "星港"))
    first = WorldEvent(
        "event:xinggang-meeting",
        "world:yun",
        "occurrence",
        "昨天我和李华在星港开会。",
        "2026-08-12T12:00:00+08:00",
        (
            EventParticipant("person:yun", "owner"),
            EventParticipant("person:lihua", "focus"),
        ),
        ("place:xinggang",),
        facets=(EventFacet("predicate", "开会"),),
        evidence_ids=("e:event:selected",),
    )
    second = WorldEvent(
        "event:xinggang-review",
        "world:yun",
        "occurrence",
        "今天我和李华在星港复盘。",
        "2026-08-13T12:00:00+08:00",
        (
            EventParticipant("person:yun", "owner"),
            EventParticipant("person:lihua", "focus"),
        ),
        ("place:xinggang",),
        facets=(EventFacet("predicate", "复盘"),),
        evidence_ids=("e:event:other",),
    )
    return graph, first, second


@pytest.mark.parametrize(
    "tamper",
    (
        "claims_object_target",
        "claim_resolution",
        "candidate_memory_target",
        "world_delta_target",
    ),
)
def test_rehashed_indirect_event_target_fork_is_zero_write(
    tmp_path: Path,
    tamper: str,
) -> None:
    session_id = "session:indirect-event-integrity"
    graph, selected, other = _event_security_graph()
    with MemoryLoop(
        tmp_path / f"indirect-event-integrity-{tamper}.sqlite",
        graph,
    ) as loop:
        _accept_event_provenance(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:event:selected",
        )
        _accept_event_provenance(
            loop,
            other,
            session_id="session:other",
            evidence_id="e:event:other",
        )
        delta, evidence, review_payload = _indirect_relationship_bundle(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:event:indirect-evaluation",
        )
        pending = loop.stage_product_bundle(
            delta,
            (evidence,),
            review_payload=review_payload,
        )
        row = loop.connection.execute(
            "SELECT payload_json, review_payload_json FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()
        assert row is not None
        stored_payload = cast(dict[str, object], json.loads(row["payload_json"]))
        stored_review = cast(
            dict[str, object],
            json.loads(row["review_payload_json"]),
        )
        display = cast(dict[str, object], stored_review["productDisplay"])
        claims_bundle = cast(dict[str, object], display["claims"])
        if tamper == "claims_object_target":
            raw_claim = cast(list[dict[str, object]], claims_bundle["claims"])[0]
            cast(dict[str, object], raw_claim["object"])["eventId"] = other.id
        elif tamper == "claim_resolution":
            resolution = cast(
                list[dict[str, object]],
                claims_bundle["claim_resolutions"],
            )[0]
            resolution.update({
                "target_id": other.id,
                "participant_entity_ids": [
                    item.entity_id for item in other.participants
                ],
                "object_entity_ids": list(other.related_entity_ids),
                "owner_participates": True,
                "event_type": other.event_type,
                "occurred_at": other.occurred_at,
            })
        elif tamper == "candidate_memory_target":
            candidate_memory = cast(dict[str, object], display["candidateMemory"])
            cognition = cast(
                list[dict[str, object]],
                candidate_memory["cognitions"],
            )[0]
            cast(dict[str, object], cognition["target"])["id"] = other.id
        else:
            delta_data = cast(dict[str, object], stored_payload["delta"])
            cognition = cast(
                list[dict[str, object]],
                delta_data["new_cognitions"],
            )[0]
            cast(dict[str, object], cognition["target"])["id"] = other.id

        rehashed = _rehash_stored_product_bundle(stored_payload, stored_review)
        loop.connection.execute(
            "UPDATE proposals SET payload_json = ?, review_payload_json = ?, "
            "result_hash = ? WHERE id = ?",
            (_json(stored_payload), _json(stored_review), rehashed, pending.id),
        )
        before = loop.view()
        before_counts = tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone())

        with pytest.raises(
            MemoryLoopIntegrityError,
            match="indirect World object evaluation contract is invalid",
        ):
            loop.decide(pending.id, rehashed, "accept")

        after = loop.view()
        assert after.revision == before.revision
        assert after.snapshot_hash == before.snapshot_hash
        assert after.graph == before.graph
        assert delta.new_cognitions[0].id not in after.graph.cognitions
        assert tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone()) == before_counts
        assert loop.connection.execute(
            "SELECT 1 FROM evidence_ledger WHERE id = ?",
            (evidence.id,),
        ).fetchone() is None
        assert loop.connection.execute(
            "SELECT status FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()[0] == "pending"
        assert loop.decision_receipt(pending.id) is None


@pytest.mark.parametrize("eligible_count", (0, 2))
def test_indirect_relationship_stage_requires_exactly_one_same_session_eligible_target(
    tmp_path: Path,
    eligible_count: int,
) -> None:
    session_id = f"session:stage-eligibility:{eligible_count}"
    graph, selected, other = _relationship_security_graph()
    with MemoryLoop(
        tmp_path / f"indirect-stage-eligibility-{eligible_count}.sqlite",
        graph,
    ) as loop:
        selected_session = session_id if eligible_count == 2 else "session:other:first"
        other_session = session_id if eligible_count == 2 else "session:other:second"
        _accept_relationship_provenance(
            loop,
            selected,
            session_id=selected_session,
            evidence_id="e:stage-eligibility:selected",
        )
        _accept_relationship_provenance(
            loop,
            other,
            session_id=other_session,
            evidence_id="e:stage-eligibility:other",
        )
        delta, evidence, review_payload = _indirect_relationship_bundle(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:stage-eligibility:evaluation",
        )
        before = loop.view()
        counts_before = tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM proposals), "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone())

        with pytest.raises(
            MemoryLoopIntegrityError,
            match="same-session|eligible Relationship",
        ):
            loop.stage_product_bundle(
                delta,
                (evidence,),
                review_payload=review_payload,
            )

        assert loop.view() == before
        assert tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM proposals), "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone()) == counts_before
        assert loop.connection.execute(
            "SELECT 1 FROM evidence_ledger WHERE id = ?",
            (evidence.id,),
        ).fetchone() is None


@pytest.mark.parametrize("eligible_count_at_accept", (0, 2))
def test_indirect_relationship_accept_rechecks_same_session_eligibility(
    tmp_path: Path,
    eligible_count_at_accept: int,
) -> None:
    session_id = f"session:accept-eligibility:{eligible_count_at_accept}"
    graph, selected, other = _relationship_security_graph()
    selected_evidence_id = "e:accept-eligibility:selected"
    other_evidence_id = "e:accept-eligibility:other"
    with MemoryLoop(
        tmp_path / f"indirect-accept-eligibility-{eligible_count_at_accept}.sqlite",
        graph,
    ) as loop:
        _accept_relationship_provenance(
            loop,
            selected,
            session_id=session_id,
            evidence_id=selected_evidence_id,
        )
        _accept_relationship_provenance(
            loop,
            other,
            session_id="session:other",
            evidence_id=other_evidence_id,
        )
        delta, evidence, review_payload = _indirect_relationship_bundle(
            loop,
            selected,
            session_id=session_id,
            evidence_id="e:accept-eligibility:evaluation",
        )
        pending = loop.stage_product_bundle(
            delta,
            (evidence,),
            review_payload=review_payload,
        )

        provenance_id = (
            selected_evidence_id
            if eligible_count_at_accept == 0
            else other_evidence_id
        )
        row = loop.connection.execute(
            "SELECT payload_json FROM evidence_ledger WHERE id = ?",
            (provenance_id,),
        ).fetchone()
        assert row is not None
        provenance = cast(dict[str, object], json.loads(row["payload_json"]))
        metadata = cast(dict[str, object], provenance["metadata"])
        replacement_session = (
            "session:no-longer-eligible"
            if eligible_count_at_accept == 0
            else session_id
        )
        metadata["conversation_id"] = replacement_session
        metadata["continuity_scope"] = replacement_session
        loop.connection.execute(
            "UPDATE evidence_ledger SET payload_json = ? WHERE id = ?",
            (_json(provenance), provenance_id),
        )
        before = loop.view()
        evidence_before = [
            tuple(item)
            for item in loop.connection.execute(
                "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
            ).fetchall()
        ]

        with pytest.raises(
            MemoryLoopIntegrityError,
            match="same-session|eligible Relationship",
        ):
            loop.decide(pending.id, pending.result_hash, "accept")

        assert loop.view() == before
        assert [
            tuple(item)
            for item in loop.connection.execute(
                "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
            ).fetchall()
        ] == evidence_before
        assert delta.new_cognitions[0].id not in loop.view().graph.cognitions
        assert loop.connection.execute(
            "SELECT status FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()[0] == "pending"
        assert loop.connection.execute(
            "SELECT 1 FROM evidence_ledger WHERE id = ?",
            (evidence.id,),
        ).fetchone() is None
        assert loop.decision_receipt(pending.id) is None


def test_pending_product_bundle_reopens_with_exact_hash_then_accepts(tmp_path: Path) -> None:
    path = tmp_path / "product-bundle-pending.sqlite"
    loop = MemoryLoop(path, _product_bundle_graph())
    pending = loop.stage_product_bundle(
        _product_bundle_delta(),
        (EvidenceRecord("e:product", "晨星项目已经启动"),),
        {"display": {"title": "pending product bundle"}},
        transition_intents=(
            CognitionTransitionIntent("cog:project-prior", "cog:project-current", "corrects", "attribute"),
        ),
    )
    loop.close()

    with MemoryLoop(path, _product_bundle_graph()) as reopened:
        review = reopened.view().pending_reviews[0]
        assert review.kind == "product_bundle"
        assert review.id == pending.id
        assert review.result_hash == pending.result_hash
        accepted = reopened.decide(review.id, review.result_hash, "accept")
        assert accepted.revision == 1
        assert accepted.superseded_cognition_ids == frozenset({"cog:project-prior"})


def test_product_bundle_transition_failure_rolls_back_every_write(tmp_path: Path) -> None:
    with MemoryLoop(tmp_path / "product-bundle-atomic.sqlite", _product_bundle_graph()) as loop:
        pending = loop.stage_product_bundle(
            _product_bundle_delta(),
            (EvidenceRecord("e:product", "晨星项目已经启动"),),
            transition_intents=(
                CognitionTransitionIntent("cog:project-prior", "cog:project-current", "corrects", "attribute"),
            ),
        )
        loop.connection.executescript(  # noqa: SLF001 - force a mid-transaction SQLite failure
            """
            CREATE TRIGGER fail_product_bundle_transition
            BEFORE INSERT ON cognition_transitions
            WHEN NEW.prior_cognition_id = 'cog:project-prior'
            BEGIN
                SELECT RAISE(ABORT, 'forced product bundle transition failure');
            END;
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="forced product bundle transition failure"):
            loop.decide(pending.id, pending.result_hash, "accept")
        view = loop.view()
        assert view.revision == 0
        assert "object:morningstar-brief" not in view.graph.entities
        assert "cog:project-current" not in view.graph.cognitions
        assert not view.transitions
        assert loop.connection.execute("SELECT 1 FROM evidence_ledger WHERE id = 'e:product'").fetchone() is None
        assert loop.connection.execute("SELECT status FROM proposals WHERE id = ?", (pending.id,)).fetchone()[0] == "pending"
        assert loop.decision_receipt(pending.id) is None


def test_product_bundle_relationship_successor_failure_rolls_back_world_evidence_evolution_and_receipt(
    tmp_path: Path,
) -> None:
    """A successor edge is part of the one product-bundle transaction."""

    graph = _graph()
    graph.add_entity(Entity("organization:old", "world:yun", "organization", "Old Org"))
    graph.add_entity(Entity("project:old", "world:yun", "project", "Old Project"))
    predecessor = Relationship(
        "relationship:ended", "world:yun", "organization:old", "project:old", "supports",
        status="ended", valid_to="2026-08-12T00:00:00+00:00",
    )
    graph.add_relationship(predecessor)
    successor = Relationship(
        "relationship:successor", "world:yun", "organization:old", "project:old", "supports",
        valid_from="2026-08-13T00:00:00+00:00",
    )
    delta = WorldDelta("world:yun", ("e:successor",), new_relationships=(successor,))
    evidence = EvidenceRecord(
        "e:successor",
        "Old Org supports Old Project again.",
        metadata={"occurred_at": "2026-08-13T00:00:00+00:00"},
    )
    step = EvolutionStep(
        "evolution:relationship-successor", "relationship_successor", "reestablished",
        MemoryTarget("relationship", successor.id), (predecessor.id,), (successor.id,),
        "2026-08-13T00:00:00+00:00", (evidence.id,),
    )
    with MemoryLoop(tmp_path / "product-bundle-successor-atomic.sqlite", graph) as loop:
        pending = loop.stage_product_bundle(delta, (evidence,), evolution_steps=(step,))
        loop.connection.executescript(  # noqa: SLF001 - fault after world/evidence work, before commit
            """
            CREATE TRIGGER fail_successor_terminal_status
            BEFORE UPDATE OF status ON proposals
            WHEN NEW.id = '""" + pending.id + """' AND NEW.status = 'accept'
            BEGIN
                SELECT RAISE(ABORT, 'forced successor terminal-status failure');
            END;
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="forced successor terminal-status failure"):
            loop.decide(pending.id, pending.result_hash, "accept")

        view = loop.view()
        assert view.revision == 0
        assert set(view.graph.relationships) == {predecessor.id}
        assert view.evolution_steps == ()
        assert loop.connection.execute("SELECT 1 FROM evidence_ledger WHERE id = ?", (evidence.id,)).fetchone() is None
        assert loop.connection.execute("SELECT status FROM proposals WHERE id = ?", (pending.id,)).fetchone()[0] == "pending"
        assert loop.connection.execute("SELECT 1 FROM proposal_decision_receipts WHERE proposal_id = ?", (pending.id,)).fetchone() is None


def test_product_bundle_updates_one_relationship_evaluation_with_contradiction_then_reaffirmation(
    tmp_path: Path,
) -> None:
    """Current cognition keeps its identity while product Evidence accumulates."""

    path = tmp_path / "product-bundle-cognition-update.sqlite"
    graph = _graph()
    graph.add_entity(Entity("person:lihua", "world:yun", "person", "李华"))
    graph.add_entity(Entity("project:xinggang", "world:yun", "project", "星港项目"))
    relationship = Relationship(
        "relationship:lihua-supports-xinggang",
        "world:yun",
        "person:lihua",
        "project:xinggang",
        "支持",
    )
    graph.add_relationship(relationship)

    initial_text = "我觉得李华支持星港项目很可靠。"
    prior = WorldCognition(
        "cog:lihua-supports-xinggang-reliable",
        "world:yun",
        MemoryTarget("relationship", relationship.id),
        initial_text,
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", ("person:yun",)),
        sources=(EvidenceLink("e:reliable", "support"),),
        structured_claim=StructuredClaim(
            "evaluation",
            value="很可靠",
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    initial_hash = sha256(initial_text.encode("utf-8")).hexdigest()
    initial_delta = WorldDelta(
        "world:yun",
        ("e:reliable",),
        new_cognitions=(prior,),
        formation_traces=(
            FormationTrace(
                prior.id,
                False,
                (
                    FormationSourceTrace(
                        "e:reliable",
                        "support",
                        "user_stated",
                        "elaborate",
                        ClaimSpan(0, len(initial_text), initial_hash, initial_hash),
                        None,
                        None,
                        "exact_user_claim",
                        "formation.exact_user_claim",
                    ),
                ),
                "stated",
                1,
                1,
                0,
            ),
        ),
    )

    loop = MemoryLoop(path, graph)
    initial = loop.stage_addition(
        initial_delta,
        (EvidenceRecord("e:reliable", initial_text),),
    )
    loop.decide(initial.id, initial.result_hash, "accept")

    contradict_time = "2026-08-13T09:00:00+00:00"
    contradict_evidence = EvidenceRecord(
        "e:not-reliable",
        "李华支持星港项目，我不认同这段支持很可靠。",
        metadata={"occurred_at": contradict_time},
    )
    confidence, status = _score("fact", "stated", 1, 1)
    contradicted = replace(
        prior,
        confidence=confidence,
        cred_status=status,  # type: ignore[arg-type]
        sources=prior.sources + (
            EvidenceLink(contradict_evidence.id, "contradict"),
        ),
    )
    contradiction = EvolutionStep(
        "evolution:lihua-supports-xinggang:contradicts",
        "cognition_change",
        "contradicts",
        prior.target,
        (prior.id,),
        (prior.id,),
        contradict_time,
        (contradict_evidence.id,),
    )
    pending = loop.stage_product_bundle(
        WorldDelta("world:yun", (contradict_evidence.id,)),
        (contradict_evidence,),
        evolution_steps=(contradiction,),
        cognition_updates=(contradicted,),
    )
    contradicted_view = loop.decide(pending.id, pending.result_hash, "accept")

    assert contradicted_view.revision == 2
    assert set(contradicted_view.graph.cognitions) == {prior.id}
    assert contradicted_view.graph.cognitions[prior.id] == contradicted
    assert contradicted_view.superseded_cognition_ids == frozenset()
    assert contradicted_view.transitions == ()
    assert [item.step.relation for item in contradicted_view.evolution_steps] == [
        "contradicts"
    ]
    recall_after_contradiction = loop.recall(
        "李华支持星港项目这件事可靠吗？",
        resolved_entity_ids=("person:lihua", "project:xinggang"),
    )
    assert recall_after_contradiction.current_cognition_ids == (prior.id,)
    assert {
        (item.evidence_id, item.relation)
        for item in recall_after_contradiction.provenance
    } == {
        ("e:reliable", "support"),
        ("e:not-reliable", "contradict"),
    }
    loop.close()

    with MemoryLoop(path, graph) as reopened:
        restored = reopened.view()
        assert restored.revision == 2
        assert restored.graph.cognitions[prior.id] == contradicted
        assert reopened.recall(
            "李华支持星港项目这件事可靠吗？",
            resolved_entity_ids=("person:lihua", "project:xinggang"),
        ) == recall_after_contradiction

        reaffirm_time = "2026-08-13T10:00:00+00:00"
        reaffirm_evidence = EvidenceRecord(
            "e:reliable-again",
            "李华支持星港项目，我还是觉得这段支持很可靠。",
            metadata={"occurred_at": reaffirm_time},
        )
        confidence, status = _score("fact", "stated", 2, 1)
        reaffirmed = replace(
            contradicted,
            confidence=confidence,
            cred_status=status,  # type: ignore[arg-type]
            sources=contradicted.sources + (
                EvidenceLink(reaffirm_evidence.id, "support"),
            ),
        )
        reaffirmation = EvolutionStep(
            "evolution:lihua-supports-xinggang:reaffirms",
            "cognition_change",
            "reaffirms",
            prior.target,
            (prior.id,),
            (prior.id,),
            reaffirm_time,
            (reaffirm_evidence.id,),
        )
        reaffirm_pending = reopened.stage_product_bundle(
            WorldDelta("world:yun", (reaffirm_evidence.id,)),
            (reaffirm_evidence,),
            evolution_steps=(reaffirmation,),
            cognition_updates=(reaffirmed,),
        )
        reaffirmed_view = reopened.decide(
            reaffirm_pending.id,
            reaffirm_pending.result_hash,
            "accept",
        )

        assert reaffirmed_view.revision == 3
        assert reaffirmed_view.graph.cognitions[prior.id] == reaffirmed
        assert reaffirmed_view.superseded_cognition_ids == frozenset()
        assert reaffirmed_view.transitions == ()
        assert [item.step.relation for item in reaffirmed_view.evolution_steps] == [
            "contradicts",
            "reaffirms",
        ]

    with MemoryLoop(path, graph) as reopened_again:
        current = reopened_again.view()
        assert current.revision == 3
        assert current.graph.cognitions[prior.id] == reaffirmed
        assert current.superseded_cognition_ids == frozenset()
        assert current.transitions == ()


@pytest.mark.parametrize("tampered_field", ("source", "confidence", "structured_claim"))
@pytest.mark.parametrize("rehash_tampered_payload", (False, True))
def test_product_cognition_update_payload_and_hash_tampering_never_writes(
    tmp_path: Path,
    tampered_field: str,
    rehash_tampered_payload: bool,
) -> None:
    """v6 binds cognition updates to the offer and still revalidates on accept."""

    graph, delta, evidence, step, updated = _product_cognition_update_bundle(
        evidence_id=f"e:tampered-{tampered_field}-{rehash_tampered_payload}"
    )
    with MemoryLoop(
        tmp_path / f"cognition-update-tamper-{tampered_field}-{rehash_tampered_payload}.sqlite",
        graph,
    ) as loop:
        _seed_product_cognition_update_source(loop)
        before = loop.view()
        pending = loop.stage_product_bundle(
            delta,
            (evidence,),
            evolution_steps=(step,),
            cognition_updates=(updated,),
        )
        row = loop.connection.execute(
            "SELECT payload_json, review_payload_json FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        if tampered_field == "source":
            payload["cognition_updates"][0]["sources"][-1]["relation"] = "support"
        elif tampered_field == "confidence":
            payload["cognition_updates"][0]["confidence"] += 1
        else:
            payload["cognition_updates"][0]["structured_claim"]["value"] = "很专业"
        stored_payload = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        offered_hash = pending.result_hash
        if rehash_tampered_payload:
            offered_hash = loop._recompute_review_result_hash(  # noqa: SLF001 - adversarial payload fixture
                "product_bundle",
                payload,
                None if row[1] is None else json.loads(row[1]),
            )
            loop.connection.execute(  # noqa: SLF001 - simulate payload plus stored-hash tampering
                "UPDATE proposals SET payload_json = ?, result_hash = ? WHERE id = ?",
                (stored_payload, offered_hash, pending.id),
            )
        else:
            loop.connection.execute(  # noqa: SLF001 - hash must detect payload-only tampering
                "UPDATE proposals SET payload_json = ? WHERE id = ?",
                (stored_payload, pending.id),
            )

        with pytest.raises((MemoryLoopError, MemoryLoopIntegrityError)):
            loop.decide(pending.id, offered_hash, "accept")

        after = loop.view()
        assert after.revision == before.revision == 0
        assert after.snapshot_hash == before.snapshot_hash
        assert after.graph.cognitions[updated.id] == graph.cognitions[updated.id]
        assert after.evolution_steps == ()
        assert loop.connection.execute(
            "SELECT 1 FROM evidence_ledger WHERE id = ?",
            (evidence.id,),
        ).fetchone() is None
        assert loop.connection.execute(
            "SELECT status FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()[0] == "pending"
        assert loop.connection.execute(
            "SELECT 1 FROM proposal_decision_receipts WHERE proposal_id = ?",
            (pending.id,),
        ).fetchone() is None


@pytest.mark.parametrize("missing_part", ("step", "update"))
def test_product_cognition_update_requires_one_matching_step_and_update(
    tmp_path: Path,
    missing_part: str,
) -> None:
    graph, delta, evidence, step, updated = _product_cognition_update_bundle(
        evidence_id=f"e:missing-{missing_part}"
    )
    with MemoryLoop(tmp_path / f"cognition-update-missing-{missing_part}.sqlite", graph) as loop:
        with pytest.raises(MemoryLoopError, match="product bundle evolution is invalid|require evolution steps"):
            loop.stage_product_bundle(
                delta,
                (evidence,),
                evolution_steps=() if missing_part == "step" else (step,),
                cognition_updates=(updated,) if missing_part == "step" else (),
            )

        view = loop.view()
        assert view.revision == 0
        assert view.graph.cognitions[updated.id] == graph.cognitions[updated.id]
        assert view.evolution_steps == ()
        assert loop.connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
        assert loop.connection.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 0


def test_product_cognition_update_cannot_be_mixed_with_claim_selection(tmp_path: Path) -> None:
    graph, delta, evidence, step, updated = _product_cognition_update_bundle()
    with MemoryLoop(tmp_path / "cognition-update-selection.sqlite", graph) as loop:
        _seed_product_cognition_update_source(loop)
        with pytest.raises(ValueError, match="product evolution does not support claim selection"):
            loop.stage_product_bundle(
                delta,
                (evidence,),
                evolution_steps=(step,),
                cognition_updates=(updated,),
                claim_slices=(ProductClaimSlice("claim:evaluation-update"),),
            )

        assert loop.view().revision == 0
        assert loop.connection.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
        assert loop.connection.execute(
            "SELECT 1 FROM evidence_ledger WHERE id = ?",
            (evidence.id,),
        ).fetchone() is None


def test_product_cognition_update_receipt_failure_rolls_back_world_evidence_status_and_receipt(
    tmp_path: Path,
) -> None:
    """The same-ID snapshot update is not committed before its immutable receipt."""

    graph, delta, evidence, step, updated = _product_cognition_update_bundle()
    prior = graph.cognitions[updated.id]
    with MemoryLoop(tmp_path / "cognition-update-receipt-rollback.sqlite", graph) as loop:
        _seed_product_cognition_update_source(loop)
        pending = loop.stage_product_bundle(
            delta,
            (evidence,),
            evolution_steps=(step,),
            cognition_updates=(updated,),
        )
        loop.connection.executescript(  # noqa: SLF001 - fault at the last durable write before COMMIT
            """
            CREATE TRIGGER fail_cognition_update_receipt
            BEFORE INSERT ON proposal_decision_receipts
            WHEN NEW.proposal_id = '""" + pending.id + """'
            BEGIN
                SELECT RAISE(ABORT, 'forced cognition update receipt failure');
            END;
            """
        )

        with pytest.raises(sqlite3.IntegrityError, match="forced cognition update receipt failure"):
            loop.decide(pending.id, pending.result_hash, "accept")

        view = loop.view()
        assert view.revision == 0
        assert view.graph.cognitions[prior.id] == prior
        assert view.evolution_steps == ()
        assert loop.connection.execute(
            "SELECT 1 FROM evidence_ledger WHERE id = ?",
            (evidence.id,),
        ).fetchone() is None
        assert loop.connection.execute(
            "SELECT status FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()[0] == "pending"
        assert loop.connection.execute(
            "SELECT 1 FROM proposal_decision_receipts WHERE proposal_id = ?",
            (pending.id,),
        ).fetchone() is None


def test_product_bundle_claim_selection_applies_only_selected_closed_subset(tmp_path: Path) -> None:
    loop = MemoryLoop(tmp_path / "claim-selection.sqlite", _graph())
    delta, evidence, bindings, slices = _claim_selection_bundle()
    pending = loop.stage_product_bundle(
        delta,
        (evidence,),
        {"display": {"offeredClaims": ["claim:phase", "claim:color"]}},
        identity_bindings=bindings,
        claim_slices=slices,
        always_include_entity_ids=("project:morningstar",),
        always_include_identity_binding_indices=(0,),
    )
    accepted = loop.decide(
        pending.id, pending.result_hash, "accept", selected_claim_ids=("claim:phase",)
    )
    assert accepted.revision == 1
    assert set(accepted.graph.cognitions) == {"cog:phase"}
    assert "project:morningstar" in accepted.graph.entities
    assert accepted.decision_receipt is not None
    assert accepted.decision_receipt.selected_claim_ids == ("claim:phase",)
    assert accepted.decision_receipt.applied_claim_ids == ("claim:phase",)
    assert accepted.decision_receipt.decision == "accept"
    row = loop.connection.execute(
        "SELECT payload_json, review_payload_json FROM proposals WHERE id = ?", (pending.id,)
    ).fetchone()
    assert row is not None
    stored = json.loads(row[0])
    assert loop._recompute_review_result_hash(  # noqa: SLF001 - hash compatibility contract
        "product_bundle", stored, json.loads(row[1])
    ) == pending.result_hash
    assert stored["selection"] == {
        "version": 1,
        "decision": "accept",
        "selected_claim_ids": ["claim:phase"],
        "applied_claim_ids": ["claim:phase"],
        "final_hash": accepted.decision_receipt.final_hash,
    }
    identity = PersistentIdentityAuthority(loop.connection).view()
    assert len(identity.review_envelopes) == 1
    assert identity.review_envelopes[0].review_payload["memory_review_id"] == pending.id
    assert identity.review_envelopes[0].review_payload["memory_result_hash"] == (
        accepted.decision_receipt.final_hash
    )
    loop.close()


def test_claim_selection_dependency_restart_empty_and_invalid_requests(tmp_path: Path) -> None:
    path = tmp_path / "claim-selection-restart.sqlite"
    loop = MemoryLoop(path, _graph())
    delta, evidence, bindings, slices = _claim_selection_bundle()
    pending = loop.stage_product_bundle(
        delta, (evidence,), identity_bindings=bindings, claim_slices=slices,
        always_include_entity_ids=("project:morningstar",),
        always_include_identity_binding_indices=(0,),
    )
    loop.close()
    with MemoryLoop(path, _graph()) as reopened:
        accepted = reopened.decide(
            pending.id, pending.result_hash, "accept", selected_claim_ids=("claim:color",)
        )
        assert accepted.revision == 1
        assert set(accepted.graph.cognitions) == {"cog:phase", "cog:color"}
        assert accepted.decision_receipt is not None
        assert accepted.decision_receipt.selected_claim_ids == ("claim:color",)
        assert accepted.decision_receipt.applied_claim_ids == ("claim:phase", "claim:color")

    with MemoryLoop(tmp_path / "claim-selection-full.sqlite", _graph()) as full_loop:
        delta, evidence, bindings, slices = _claim_selection_bundle()
        full = full_loop.stage_product_bundle(
            delta, (evidence,), identity_bindings=bindings, claim_slices=slices,
            always_include_entity_ids=("project:morningstar",),
            always_include_identity_binding_indices=(0,),
        )
        accepted_full = full_loop.decide(full.id, full.result_hash, "accept")
        assert set(accepted_full.graph.cognitions) == {"cog:phase", "cog:color"}
        assert accepted_full.decision_receipt is not None
        assert accepted_full.decision_receipt.selected_claim_ids == ("claim:phase", "claim:color")
        assert accepted_full.decision_receipt.applied_claim_ids == ("claim:phase", "claim:color")

    with MemoryLoop(tmp_path / "claim-selection-empty.sqlite", _graph()) as empty_loop:
        delta, evidence, bindings, slices = _claim_selection_bundle()
        empty = empty_loop.stage_product_bundle(
            delta, (evidence,), identity_bindings=bindings, claim_slices=slices,
            always_include_entity_ids=("project:morningstar",),
            always_include_identity_binding_indices=(0,),
        )
        rejected = empty_loop.decide(empty.id, empty.result_hash, "accept", selected_claim_ids=())
        assert rejected.revision == 0
        assert rejected.decision_receipt is not None
        assert rejected.decision_receipt.decision == "reject"
        assert rejected.decision_receipt.applied_claim_ids == ()
        assert empty_loop.connection.execute("SELECT status FROM proposals WHERE id = ?", (empty.id,)).fetchone()[0] == "reject"

    with MemoryLoop(tmp_path / "claim-selection-invalid.sqlite", _graph()) as invalid_loop:
        delta, evidence, bindings, slices = _claim_selection_bundle()
        invalid = invalid_loop.stage_product_bundle(
            delta, (evidence,), identity_bindings=bindings, claim_slices=slices,
            always_include_entity_ids=("project:morningstar",),
            always_include_identity_binding_indices=(0,),
        )
        with pytest.raises(ValueError, match="unknown claim"):
            invalid_loop.decide(invalid.id, invalid.result_hash, "accept", selected_claim_ids=("claim:unknown",))
        with pytest.raises(ValueError, match="duplicates"):
            invalid_loop.decide(invalid.id, invalid.result_hash, "accept", selected_claim_ids=("claim:phase", "claim:phase"))
        assert invalid_loop.view().revision == 0
        assert invalid_loop.connection.execute("SELECT status FROM proposals WHERE id = ?", (invalid.id,)).fetchone()[0] == "pending"


def test_claim_selection_payload_tamper_fails_before_any_write(tmp_path: Path) -> None:
    with MemoryLoop(tmp_path / "claim-selection-tamper.sqlite", _graph()) as loop:
        delta, evidence, bindings, slices = _claim_selection_bundle()
        pending = loop.stage_product_bundle(
            delta, (evidence,), identity_bindings=bindings, claim_slices=slices,
            always_include_entity_ids=("project:morningstar",),
            always_include_identity_binding_indices=(0,),
        )
        row = loop.connection.execute("SELECT payload_json FROM proposals WHERE id = ?", (pending.id,)).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        payload["claim_slices"][0]["cognition_ids"] = ["cog:color"]
        loop.connection.execute(
            "UPDATE proposals SET payload_json = ? WHERE id = ?", (json.dumps(payload), pending.id)
        )
        with pytest.raises(MemoryLoopIntegrityError, match="cannot be reconstructed|result hash"):
            loop.decide(pending.id, pending.result_hash, "accept", selected_claim_ids=("claim:phase",))
        assert loop.view().revision == 0
        assert loop.connection.execute("SELECT 1 FROM evidence_ledger WHERE id = ?", (evidence.id,)).fetchone() is None


def test_product_bundle_rejects_noncurrent_or_incompatible_transition_intents(tmp_path: Path) -> None:
    graph = _product_bundle_graph()
    with MemoryLoop(tmp_path / "product-bundle-invalid.sqlite", graph) as loop:
        evidence = EvidenceRecord("e:product", "晨星项目已经启动")
        invalid_cases = (
            (CognitionTransitionIntent("cog:missing", "cog:project-current", "corrects", "attribute"), "not accepted"),
            (CognitionTransitionIntent("cog:project-prior", "cog:missing", "corrects", "attribute"), "not a new bundle"),
            (CognitionTransitionIntent("cog:project-prior", "cog:project-current", "corrects", "evaluation"), "statement_kind"),
        )
        for intent, message in invalid_cases:
            with pytest.raises(MemoryLoopError, match=message):
                loop.stage_product_bundle(_product_bundle_delta(), (evidence,), transition_intents=(intent,))

        accepted = loop.stage_product_bundle(
            _product_bundle_delta(),
            (evidence,),
            transition_intents=(CognitionTransitionIntent("cog:project-prior", "cog:project-current", "corrects", "attribute"),),
        )
        loop.decide(accepted.id, accepted.result_hash, "accept")
        again_full = _product_bundle_delta(evidence_id="e:again", cognition_id="cog:again")
        again = WorldDelta(
            "world:yun",
            ("e:again",),
            new_cognitions=again_full.new_cognitions,
            formation_traces=again_full.formation_traces,
        )
        with pytest.raises(MemoryLoopError, match="already historical"):
            loop.stage_product_bundle(
                again,
                (EvidenceRecord("e:again", "晨星项目已经启动"),),
                transition_intents=(CognitionTransitionIntent("cog:project-prior", "cog:again", "corrects", "attribute"),),
            )


def test_legacy_memory_loop_database_upgrades_into_the_v3_main_store(tmp_path: Path) -> None:
    path = tmp_path / "legacy-world.sqlite"
    with MemoryLoop(path, _graph()) as loop:
        _accept_addition(loop)

    legacy = sqlite3.connect(path, isolation_level=None)
    try:
        for table in (
            "world_event_evidence",
            "world_event",
            "retraction",
            "cognition_target",
            "relationship_evidence",
            "relationship",
            "entity",
            "memory_world_job",
            "boundary_evidence_content",
            "identity_state",
            "semantic_resolution",
            "interaction_context",
            "management_log",
            "evidence_retraction",
            "cognition_evidence",
            "cognition",
            "event_evidence",
            "event",
            "evidence",
        ):
            legacy.execute(f'DROP TABLE "{table}"')
        legacy.execute("PRAGMA application_id = 0")
        legacy.execute("PRAGMA user_version = 0")
    finally:
        legacy.close()

    with MemoryLoop(path, _graph()) as upgraded:
        assert user_version(upgraded.connection) == SCHEMA_VERSION
        assert upgraded.view().revision == 1
        assert upgraded.view().graph.cognitions["cog:tea"].content == "Yun likes tea"
        tables = {
            row[0]
            for row in upgraded.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert {"evidence", "event", "cognition", "identity_state", "memory_state"} <= tables


def test_decision_is_single_use_and_stale_review_cannot_pollute(tmp_path: Path) -> None:
    with _loop(tmp_path) as loop:
        first = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
        second = loop.stage_addition(_delta(cognition_id="cog:coffee", evidence_id="e:coffee"), (EvidenceRecord("e:coffee", "I like coffee."),))
        with pytest.raises(ReviewStateError, match="hash mismatch"):
            loop.decide(first.id, "sha256:not-the-reviewed-result", "accept")
        assert loop.view().revision == 0
        loop.decide(first.id, first.result_hash, "accept")
        with pytest.raises(ReviewStateError, match="stale review"):
            loop.decide(second.id, second.result_hash, "accept")
        with pytest.raises(ReviewStateError, match="already decided"):
            loop.decide(first.id, first.result_hash, "accept")
        assert set(loop.view().graph.cognitions) == {"cog:tea"}


def test_stale_review_can_be_rejected_without_mutating_world_or_evidence_and_stays_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "stale-reject.sqlite"
    loop = MemoryLoop(path, _graph())
    accepted = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
    stale = loop.stage_addition(
        _delta(cognition_id="cog:coffee", evidence_id="e:coffee"),
        (EvidenceRecord("e:coffee", "I like coffee."),),
    )
    loop.decide(accepted.id, accepted.result_hash, "accept")

    with pytest.raises(ReviewStateError, match="stale review"):
        loop.decide(stale.id, stale.result_hash, "accept")

    state_before = loop._conn.execute(  # noqa: SLF001
        "SELECT revision, snapshot_json, snapshot_hash FROM memory_state WHERE singleton = 1"
    ).fetchone()
    evidence_before = loop._conn.execute(  # noqa: SLF001
        "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
    ).fetchall()
    view = loop.decide(stale.id, stale.result_hash, "reject")
    state_after = loop._conn.execute(  # noqa: SLF001
        "SELECT revision, snapshot_json, snapshot_hash FROM memory_state WHERE singleton = 1"
    ).fetchone()
    evidence_after = loop._conn.execute(  # noqa: SLF001
        "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
    ).fetchall()

    assert tuple(state_after) == tuple(state_before)
    assert [tuple(row) for row in evidence_after] == [tuple(row) for row in evidence_before]
    assert view.revision == 1
    assert view.snapshot_hash == state_before[2]
    assert set(view.graph.cognitions) == {"cog:tea"}
    assert not view.pending_reviews
    assert loop._conn.execute(  # noqa: SLF001
        "SELECT status FROM proposals WHERE id = ?", (stale.id,)
    ).fetchone()["status"] == "reject"
    with pytest.raises(ReviewStateError, match="already decided"):
        loop.decide(stale.id, stale.result_hash, "accept")
    loop.close()

    with MemoryLoop(path, _graph()) as reopened:
        reopened_view = reopened.view()
        assert reopened_view.revision == 1
        assert reopened_view.snapshot_hash == state_before[2]
        assert set(reopened_view.graph.cognitions) == {"cog:tea"}
        assert not reopened_view.pending_reviews
        assert reopened._conn.execute(  # noqa: SLF001
            "SELECT status FROM proposals WHERE id = ?", (stale.id,)
        ).fetchone()["status"] == "reject"
        assert reopened._conn.execute(  # noqa: SLF001
            "SELECT 1 FROM evidence_ledger WHERE id = 'e:coffee'"
        ).fetchone() is None


def test_evidence_id_conflict_and_failed_accept_are_atomic(tmp_path: Path) -> None:
    with _loop(tmp_path) as loop:
        _accept_addition(loop)
        with pytest.raises(EvidenceConflictError):
            loop.stage_addition(_delta(cognition_id="cog:other"), (EvidenceRecord("e:tea", "Different words."),))
        pending = loop.stage_addition(_delta(cognition_id="cog:more", evidence_id="e:more"), (EvidenceRecord("e:more", "More tea."),))
        # Simulate a damaged/stale staged payload.  The accepting transaction
        # must roll back its ledger write and revision update together.
        loop._conn.execute("UPDATE proposals SET payload_json = ? WHERE id = ?", (json.dumps({"evidence": []}), pending.id))  # noqa: SLF001
        with pytest.raises((KeyError, MemoryLoopIntegrityError)):
            loop.decide(pending.id, pending.result_hash, "accept")
        assert loop.view().revision == 1 and "cog:more" not in loop.view().graph.cognitions


def test_evidence_id_reuse_requires_exact_full_provenance_not_only_matching_content(
    tmp_path: Path,
) -> None:
    with _loop(tmp_path) as loop:
        original = EvidenceRecord(
            "e:tea",
            "I like tea.",
            metadata={"system_evidence": {"hostId": "host-a", "allowInference": True}},
        )
        accepted = loop.stage_addition(_delta(), (original,))
        loop.decide(accepted.id, accepted.result_hash, "accept")
        before = loop.view()
        ledger_before = loop.connection.execute(
            "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
        ).fetchall()

        conflicting = EvidenceRecord(
            original.id,
            original.content,
            metadata={"system_evidence": {"hostId": "host-b", "allowInference": True}},
        )
        with pytest.raises(EvidenceConflictError, match="evidence id conflict"):
            loop.stage_addition(
                _delta(cognition_id="cog:other"),
                (conflicting,),
            )

        assert loop.view() == before
        assert [tuple(row) for row in loop.connection.execute(
            "SELECT id, content, payload_json FROM evidence_ledger ORDER BY id"
        ).fetchall()] == [tuple(row) for row in ledger_before]


def test_recall_no_memory_and_answer_context_are_evidence_bounded(tmp_path: Path) -> None:
    with _loop(tmp_path) as loop:
        answerer = _Answerer()
        assert loop.ask("what is the weather", answerer).status == "no_memory"
        assert answerer.call_count == 0
        _accept_addition(loop)
        answer = loop.ask("What does Yun like?", answerer)
        assert answer.status == "answered" and answer.answer == "Yun likes tea."
        assert answerer.call_count == 1
        assert [item.id for item in answer.evidence_context] == ["e:tea"]
        prompt = answerer.messages[-1].content
        assert "content=Yun likes tea" in prompt
        assert "cognition:cog:tea --support--> e:tea" in prompt
        assert "I like tea." not in prompt


def test_recall_marks_pre_envelope_evidence_as_legacy_without_backfilling_it(
    tmp_path: Path,
) -> None:
    """Old accepted Evidence stays readable but never gains invented provenance."""

    with _loop(tmp_path) as loop:
        _accept_addition(loop)
        before = loop.connection.execute(
            "SELECT payload_json FROM evidence_ledger WHERE id = 'e:tea'"
        ).fetchone()[0]

        answer = loop.ask("What does Yun like?")

        assert answer.status == "recalled"
        assert answer.evidence_traces == (
            RecallEvidenceTrace(
                "cognition",
                "cog:tea",
                "current",
                "e:tea",
                "support",
                "legacy",
            ),
        )
        assert loop.connection.execute(
            "SELECT payload_json FROM evidence_ledger WHERE id = 'e:tea'"
        ).fetchone()[0] == before


def test_recall_fails_closed_on_a_malformed_stored_system_evidence_envelope(
    tmp_path: Path,
) -> None:
    with _loop(tmp_path) as loop:
        review = loop.stage_addition(
            _delta(),
            (
                EvidenceRecord(
                    "e:tea",
                    "I like tea.",
                    metadata={
                        "system_evidence": {
                            "id": "e:tea",
                            "rawContent": "I like tea.",
                        }
                    },
                ),
            ),
        )
        loop.decide(review.id, review.result_hash, "accept")
        before = loop.view()
        ledger_before = loop.connection.execute(
            "SELECT payload_json FROM evidence_ledger WHERE id = 'e:tea'"
        ).fetchone()[0]

        with pytest.raises(
            MemoryLoopIntegrityError,
            match="invalid system Evidence envelope",
        ):
            loop.ask("What does Yun like?")

        assert loop.view() == before
        assert loop.connection.execute(
            "SELECT payload_json FROM evidence_ledger WHERE id = 'e:tea'"
        ).fetchone()[0] == ledger_before


def test_correction_reject_accept_history_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    loop = MemoryLoop(path, _graph())
    prior = _accept_addition(loop)
    rejected = loop.stage_correction(prior, "Yun does not like tea", EvidenceRecord("e:correction", "Actually I do not like tea."))
    loop.decide(rejected.id, rejected.result_hash, "reject")
    assert tuple(item.id for item in loop.view().current_cognitions) == (prior,)
    accepted = loop.stage_correction(prior, "Yun does not like tea", EvidenceRecord("e:correction", "Actually I do not like tea."))
    view = loop.decide(accepted.id, accepted.result_hash, "accept")
    assert prior in view.graph.cognitions and prior in view.superseded_cognition_ids
    assert len(view.current_cognitions) == 1
    assert view.current_cognitions[0].content == "Yun does not like tea"
    assert loop.ask("Does Yun like tea?").recalled_cognitions[0].content == "Yun does not like tea"
    loop.close()
    with MemoryLoop(path, _graph()) as reopened:
        assert prior in reopened.view().superseded_cognition_ids
        assert len(reopened.view().transitions) == 1


def test_correction_bundle_accepts_two_priors_as_one_replacement_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "bundle.sqlite"
    graph = _graph()
    graph.add_cognition(_cognition("cog:cat-name", "我的猫叫豆包"))
    graph.add_cognition(_cognition("cog:cat-real", "豆包是我的猫"))
    loop = MemoryLoop(path, graph)
    review = loop.stage_correction_bundle(
        ("cog:cat-name", "cog:cat-real"),
        "豆包其实是AI，不是我的猫",
        EvidenceRecord("e:cat-correction", "豆包其实是AI，不是我的猫"),
    )
    row = loop._conn.execute("SELECT payload_json FROM proposals WHERE id = ?", (review.id,)).fetchone()  # noqa: SLF001
    assert row is not None
    payload = cast(dict[str, object], json.loads(cast(str, row["payload_json"])))
    assert payload["prior_cognition_ids"] == ["cog:cat-name", "cog:cat-real"]
    assert "prior_cognition_id" not in payload

    view = loop.decide(review.id, review.result_hash, "accept")
    assert view.revision == 1
    assert view.superseded_cognition_ids == frozenset(("cog:cat-name", "cog:cat-real"))
    assert len(view.graph.cognitions) == 3
    assert len(view.current_cognitions) == 1
    replacement = view.current_cognitions[0]
    assert (
        replacement.content,
        replacement.formed_by,
        replacement.confidence,
        replacement.cred_status,
        replacement.sources,
    ) == (
        "豆包其实是AI，不是我的猫",
        "stated",
        600,
        "limited",
        (EvidenceLink("e:cat-correction", "support"),),
    )
    assert {item.prior_cognition_id for item in view.transitions} == {"cog:cat-name", "cog:cat-real"}
    assert {item.replacement_cognition_id for item in view.transitions} == {replacement.id}
    assert {item.revision for item in view.transitions} == {1}
    loop.close()

    with MemoryLoop(path, _graph()) as reopened:
        reopened_view = reopened.view()
        assert reopened_view.revision == 1
        assert reopened_view.superseded_cognition_ids == frozenset(("cog:cat-name", "cog:cat-real"))
        assert [item.content for item in reopened_view.current_cognitions] == ["豆包其实是AI，不是我的猫"]
        assert len(reopened_view.transitions) == 2


def test_correction_bundle_reject_has_no_world_evidence_or_history_effect(tmp_path: Path) -> None:
    graph = _graph()
    graph.add_cognition(_cognition("cog:one", "旧认知一"))
    graph.add_cognition(_cognition("cog:two", "旧认知二"))
    with MemoryLoop(tmp_path / "bundle-reject.sqlite", graph) as loop:
        review = loop.stage_correction_bundle(
            ("cog:one", "cog:two"), "当前纠正", EvidenceRecord("e:bundle-reject", "当前纠正")
        )
        view = loop.decide(review.id, review.result_hash, "reject")
        assert view.revision == 0
        assert set(view.graph.cognitions) == {"cog:one", "cog:two"}
        assert {item.id for item in view.current_cognitions} == {"cog:one", "cog:two"}
        assert not view.superseded_cognition_ids and not view.transitions
        assert loop._conn.execute("SELECT 1 FROM evidence_ledger WHERE id = 'e:bundle-reject'").fetchone() is None  # noqa: SLF001


def test_correction_bundle_rejects_invalid_or_incoherent_prior_sets(tmp_path: Path) -> None:
    graph = _graph()
    base = _cognition("cog:base", "基础认知")
    same = _cognition("cog:same", "同槽认知")
    variants = (
        (replace(base, id="cog:target", target=MemoryTarget("world", "world:yun")), "target"),
        (replace(base, id="cog:perspective", perspective=Perspective("system")), "perspective"),
        (replace(base, id="cog:type", content_type="preference"), "content_type"),
        (replace(base, id="cog:scope", scope="只限过去"), "scope"),
    )
    for cognition in (base, same, *(item[0] for item in variants)):
        graph.add_cognition(cognition)

    with MemoryLoop(tmp_path / "bundle-invalid.sqlite", graph) as loop:
        evidence = EvidenceRecord("e:invalid-bundle", "当前纠正")
        with pytest.raises(MemoryLoopError, match="between 1 and 4"):
            loop.stage_correction_bundle((), "当前纠正", evidence)
        with pytest.raises(MemoryLoopError, match="between 1 and 4"):
            loop.stage_correction_bundle(
                ("cog:base", "cog:same", "cog:target", "cog:perspective", "cog:type"), "当前纠正", evidence
            )
        with pytest.raises(MemoryLoopError, match="duplicate"):
            loop.stage_correction_bundle(("cog:base", "cog:base"), "当前纠正", evidence)
        with pytest.raises(MemoryLoopError, match="unknown prior cognition"):
            loop.stage_correction_bundle(("cog:base", "cog:missing"), "当前纠正", evidence)
        for variant, dimension in variants:
            with pytest.raises(MemoryLoopError, match=dimension):
                loop.stage_correction_bundle(("cog:base", variant.id), "当前纠正", evidence)


def test_correction_bundle_rejects_a_superseded_prior_and_old_single_api_stays_compatible(tmp_path: Path) -> None:
    graph = _graph()
    graph.add_cognition(_cognition("cog:one", "旧认知一"))
    graph.add_cognition(_cognition("cog:two", "旧认知二"))
    with MemoryLoop(tmp_path / "bundle-superseded.sqlite", graph) as loop:
        single = loop.stage_correction("cog:one", "第一条纠正", EvidenceRecord("e:single", "第一条纠正"))
        view = loop.decide(single.id, single.result_hash, "accept")
        assert [item.content for item in view.current_cognitions] == ["旧认知二", "第一条纠正"]
        with pytest.raises(MemoryLoopError, match="already superseded"):
            loop.stage_correction_bundle(
                ("cog:one", "cog:two"), "第二条纠正", EvidenceRecord("e:bundle-after", "第二条纠正")
            )


def test_legacy_single_prior_correction_payload_is_still_accepted(tmp_path: Path) -> None:
    graph = _graph()
    graph.add_cognition(_cognition("cog:legacy", "旧认知"))
    with MemoryLoop(tmp_path / "legacy-correction.sqlite", graph) as loop:
        review = loop.stage_correction("cog:legacy", "新认知", EvidenceRecord("e:legacy", "新认知"))
        row = loop._conn.execute("SELECT payload_json FROM proposals WHERE id = ?", (review.id,)).fetchone()  # noqa: SLF001
        assert row is not None
        payload = cast(dict[str, object], json.loads(cast(str, row["payload_json"])))
        prior_ids = cast(list[str], payload.pop("prior_cognition_ids"))
        payload["prior_cognition_id"] = prior_ids[0]
        loop._conn.execute(  # noqa: SLF001
            "UPDATE proposals SET payload_json = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), review.id)
        )
        view = loop.decide(review.id, review.result_hash, "accept")
        assert [item.content for item in view.current_cognitions] == ["新认知"]
        assert len(view.transitions) == 1


def test_stale_correction_bundle_rolls_back_without_inserting_current_evidence(tmp_path: Path) -> None:
    graph = _graph()
    graph.add_cognition(_cognition("cog:one", "旧认知一"))
    graph.add_cognition(_cognition("cog:two", "旧认知二"))
    with MemoryLoop(tmp_path / "bundle-stale.sqlite", graph) as loop:
        bundle = loop.stage_correction_bundle(
            ("cog:one", "cog:two"), "当前纠正", EvidenceRecord("e:stale-bundle", "当前纠正")
        )
        other = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
        loop.decide(other.id, other.result_hash, "accept")

        with pytest.raises(ReviewStateError, match="stale review"):
            loop.decide(bundle.id, bundle.result_hash, "accept")
        view = loop.view()
        assert view.revision == 1
        assert not view.superseded_cognition_ids and not view.transitions
        assert {item.id for item in view.current_cognitions} == {"cog:one", "cog:two", "cog:tea"}
        assert loop._conn.execute("SELECT 1 FROM evidence_ledger WHERE id = 'e:stale-bundle'").fetchone() is None  # noqa: SLF001


def test_correction_bundle_transition_failure_rolls_back_replacement_and_evidence(tmp_path: Path) -> None:
    graph = _graph()
    graph.add_cognition(_cognition("cog:one", "旧认知一"))
    graph.add_cognition(_cognition("cog:two", "旧认知二"))
    with MemoryLoop(tmp_path / "bundle-atomic.sqlite", graph) as loop:
        review = loop.stage_correction_bundle(
            ("cog:one", "cog:two"), "当前纠正", EvidenceRecord("e:atomic-bundle", "当前纠正")
        )
        loop._conn.executescript(  # noqa: SLF001
            """
            CREATE TRIGGER fail_second_bundle_transition
            BEFORE INSERT ON cognition_transitions
            WHEN NEW.prior_cognition_id = 'cog:two'
            BEGIN
                SELECT RAISE(ABORT, 'forced bundle transition failure');
            END;
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="forced bundle transition failure"):
            loop.decide(review.id, review.result_hash, "accept")
        view = loop.view()
        assert view.revision == 0
        assert set(view.graph.cognitions) == {"cog:one", "cog:two"}
        assert not view.superseded_cognition_ids and not view.transitions
        assert loop._conn.execute("SELECT 1 FROM evidence_ledger WHERE id = 'e:atomic-bundle'").fetchone() is None  # noqa: SLF001


def test_snapshot_tamper_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    loop = MemoryLoop(path, _graph())
    _accept_addition(loop)
    loop._conn.execute("UPDATE memory_state SET snapshot_json = ? WHERE singleton = 1", ("{}",))  # noqa: SLF001
    with pytest.raises(MemoryLoopIntegrityError, match="hash mismatch"):
        loop.view()
    loop.close()
    with pytest.raises(MemoryLoopIntegrityError, match="hash mismatch"):
        MemoryLoop(path, _graph())


def test_chinese_query_reconstructs_cat_entity_and_current_cognition(tmp_path: Path) -> None:
    graph = _graph()
    graph.add_entity(Entity("pet:doubao", "world:yun", "pet", "豆包"))
    content = "豆包害怕吸尘器"
    cognition = WorldCognition(
        "cog:doubao-fear", "world:yun", MemoryTarget("entity", "pet:doubao"), content,
        "preference", "stated", 600, "limited", Perspective("entity", ("person:yun",)),
        sources=(EvidenceLink("e:doubao", "support"),),
    )
    trace = FormationTrace(
        cognition.id, False,
        (FormationSourceTrace("e:doubao", "support", "user_stated", "elaborate", ClaimSpan(0, len(content), "a" * 64, sha256(content.encode()).hexdigest()), None, None, "exact_user_claim", "formation.exact_user_claim"),),
        "stated", 1, 1, 0,
    )
    with MemoryLoop(tmp_path / "cat.sqlite", graph) as loop:
        review = loop.stage_addition(WorldDelta("world:yun", ("e:doubao",), new_cognitions=(cognition,), formation_traces=(trace,)), (EvidenceRecord("e:doubao", "豆包很怕吸尘器。"),))
        loop.decide(review.id, review.result_hash, "accept")
        answer = loop.ask("豆包害怕什么")
        assert answer.status == "recalled"
        assert [item.canonical_name for item in answer.recalled_entities] == ["豆包"]
        assert [item.content for item in answer.recalled_cognitions] == ["豆包害怕吸尘器"]


def test_recall_requires_an_explicit_entity_name_or_strong_lexical_overlap(tmp_path: Path) -> None:
    graph = _graph()
    graph.add_entity(Entity("pet:doubao", "world:yun", "pet", "豆包"))
    content = "豆包是我的猫"
    cognition = WorldCognition(
        "cog:doubao-cat", "world:yun", MemoryTarget("entity", "pet:doubao"), content,
        "fact", "stated", 600, "limited", Perspective("entity", ("person:yun",)),
        sources=(EvidenceLink("e:doubao-cat", "support"),),
    )
    trace = FormationTrace(
        cognition.id, False,
        (FormationSourceTrace("e:doubao-cat", "support", "user_stated", "elaborate", ClaimSpan(0, len(content), "a" * 64, sha256(content.encode()).hexdigest()), None, None, "exact_user_claim", "formation.exact_user_claim"),),
        "stated", 1, 1, 0,
    )
    with MemoryLoop(tmp_path / "cat-relevance.sqlite", graph) as loop:
        review = loop.stage_addition(
            WorldDelta("world:yun", ("e:doubao-cat",), new_cognitions=(cognition,), formation_traces=(trace,)),
            (EvidenceRecord("e:doubao-cat", "豆包是我的猫。"),),
        )
        loop.decide(review.id, review.result_hash, "accept")

        assert loop.ask("二五的猫").status == "no_memory"
        assert loop.ask("我有一只叫二五的猫，她很喜欢钻被窝").status == "no_memory"

        explicit = loop.ask("豆包怎么了")
        assert explicit.status == "recalled"
        assert [item.canonical_name for item in explicit.recalled_entities] == ["豆包"]
        assert [item.content for item in explicit.recalled_cognitions] == [content]


def test_romance_recall_does_not_leak_an_unnamed_cat_or_owner_sibling_memory(tmp_path: Path) -> None:
    graph = _graph()
    graph.add_entity(Entity("pet:erwu", "world:yun", "pet", "二五"))
    cat_memory = _cognition(
        "cog:erwu-smoking",
        "二五不喜欢我抽",
        target=MemoryTarget("entity", "pet:erwu"),
    )
    swimming_memory = _cognition(
        "cog:swimming",
        "我一般每个周六都会去游泳",
        scope="exercise",
    )
    romance_memory = _cognition(
        "cog:romance",
        "我也不知道她喜不喜欢我，因为她还说之前给一个不认识的网友买过衣服",
        scope="romantic_interest",
    )
    for cognition in (cat_memory, swimming_memory):
        graph.add_cognition(cognition)

    with MemoryLoop(tmp_path / "romance-first-turn.sqlite", graph) as loop:
        first_turn = loop.ask("我有一个喜欢的女生，她给我点了一份三文鱼刺身")
        assert first_turn.status == "no_memory"

    graph.add_cognition(romance_memory)
    with MemoryLoop(tmp_path / "romance-follow-up.sqlite", graph) as loop:
        follow_up = loop.ask("感觉她喜欢我，但是又不喜欢我，她喜欢稳定，我也不知道该怎么办")
        assert follow_up.status == "recalled"
        assert [item.content for item in follow_up.recalled_cognitions] == [romance_memory.content]
        assert "pet:erwu" not in {item.id for item in follow_up.recalled_entities}

        named_cat = loop.ask("二五为什么不喜欢我抽烟")
        assert named_cat.status == "recalled"
        assert [item.content for item in named_cat.recalled_cognitions] == [cat_memory.content]


def test_cognition_content_name_can_anchor_age_recall_without_becoming_an_entity_alias(tmp_path: Path) -> None:
    graph = _graph()
    pet = Entity("pet:kitten", "world:yun", "pet", "小猫")
    graph.add_entity(pet)
    age_memory = _cognition(
        "cog:kitten-age",
        "她叫二五，是一只小母猫，今年都3岁了",
        target=MemoryTarget("entity", pet.id),
    )
    smoking_memory = _cognition(
        "cog:kitten-smoking",
        "她叫二五，但她不喜欢我抽烟",
        target=MemoryTarget("entity", pet.id),
    )
    den_memory = _cognition(
        "cog:kitten-den",
        "她叫二五，也很喜欢钻被窝",
        target=MemoryTarget("entity", pet.id),
    )
    swimming_memory = _cognition("cog:owner-swimming", "我一般每个周六都会去游泳", scope="exercise")
    romance_memory = _cognition(
        "cog:owner-romance",
        "我也不知道她喜不喜欢我，因为她给一个不认识的网友买过衣服",
        scope="romantic_interest",
    )
    for cognition in (age_memory, smoking_memory, den_memory, swimming_memory, romance_memory):
        graph.add_cognition(cognition)

    with MemoryLoop(tmp_path / "content-name-age.sqlite", graph) as loop:
        answer = loop.ask("二五多大了")
        assert answer.status == "recalled"
        assert [item.id for item in answer.recalled_entities] == [pet.id]
        assert [item.content for item in answer.recalled_cognitions] == [age_memory.content]
        assert answer.recalled_entities[0].canonical_name == "小猫"
        assert answer.recalled_entities[0].aliases == ()


def test_pending_rejected_and_superseded_cognitions_do_not_enter_recalled_cognitions(tmp_path: Path) -> None:
    with _loop(tmp_path) as loop:
        pending = loop.stage_addition(_delta(), (EvidenceRecord("e:tea", "I like tea."),))
        assert loop.ask("What does Yun like about tea?").recalled_cognitions == ()
        loop.decide(pending.id, pending.result_hash, "reject")
        assert loop.ask("What does Yun like about tea?").recalled_cognitions == ()

        prior = _accept_addition(loop)
        correction = loop.stage_correction(
            prior,
            "Yun does not like tea",
            EvidenceRecord("e:tea-correction", "Actually I do not like tea."),
        )
        replacement = loop.decide(correction.id, correction.result_hash, "accept").current_cognitions[0]
        recalled = loop.ask("Does Yun like tea?")
        assert [item.id for item in recalled.recalled_cognitions] == [replacement.id]
        assert prior not in {item.id for item in recalled.recalled_cognitions}


def test_nanjing_why_query_returns_event_relationship_facets_and_prompt_context(tmp_path: Path) -> None:
    graph = _graph()
    friend = Entity("person:lin", "world:yun", "person", "Lin")
    graph.add_entity(friend)
    relationship = Relationship("relationship:yun-lin", "world:yun", "person:yun", friend.id, "friend")
    graph.add_relationship(relationship)
    event = WorldEvent(
        "event:nanjing", "world:yun", "interpersonal_conflict", "南京旅行时两人因行程安排争执", "2026-05-01",
        participants=(EventParticipant("person:yun"), EventParticipant(friend.id)), relationship_ids=(relationship.id,),
        facets=(EventFacet("cause", "Yun想要明确行程，Lin想临时决定"), EventFacet("destination", "南京")),
    )
    graph.add_event(event)
    graph.add_cognition(WorldCognition(
        "cog:nanjing-conflict", "world:yun", MemoryTarget("event", event.id), "争执源于两人对行程安排的不同偏好",
        "fact", "stated", 600, "limited", Perspective("entity", ("person:yun",)),
    ))
    answerer = _Answerer()
    with MemoryLoop(tmp_path / "nanjing.sqlite", graph) as loop:
        answer = loop.ask("为什么南京吵架", answerer)
        assert answer.status == "answered"
        assert [item.id for item in answer.recalled_events] == [event.id]
        assert [item.id for item in answer.recalled_relationships] == [relationship.id]
        assert [facet.value for facet in answer.recalled_events[0].facets] == ["Yun想要明确行程，Lin想临时决定", "南京"]
        prompt = answerer.messages[-1].content
        assert "cause=Yun想要明确行程，Lin想临时决定" in prompt
        assert "争执源于两人对行程安排的不同偏好" in prompt


def test_event_provenance_is_hydrated_after_anchor_selection_but_raw_text_is_not_prompted(
    tmp_path: Path,
) -> None:
    path = tmp_path / "event-provenance.sqlite"
    graph = _graph()
    friend = Entity("person:friend", "world:yun", "person", "Friend_X")
    trip = Entity("activity:nanjing", "world:yun", "activity", "Nanjing trip")
    place = Entity("place:nanjing", "world:yun", "place", "Nanjing")
    relationship = Relationship(
        "relationship:friend",
        "world:yun",
        "person:yun",
        friend.id,
        "friend",
        True,
    )
    event = WorldEvent(
        "event:nanjing-conflict",
        "world:yun",
        "interpersonal_conflict",
        "Yun and Friend_X argued while planning the Nanjing trip.",
        "2026-08-06T12:00:00+08:00",
        (EventParticipant("person:yun"), EventParticipant(friend.id)),
        (trip.id, place.id),
        (relationship.id,),
        (
            EventFacet("cause", "They wanted different levels of advance planning."),
            EventFacet("position", "Preferred flexibility.", "person:yun"),
            EventFacet("position", "Preferred an itinerary.", friend.id),
        ),
        ("turn:event-one", "turn:event-two"),
    )
    delta = WorldDelta(
        "world:yun",
        event.evidence_ids,
        (friend, trip, place),
        (relationship,),
        (event,),
    )
    records = (
        EvidenceRecord("turn:event-one", "RAW_TRANSCRIPT_SENTINEL one"),
        EvidenceRecord("turn:event-two", "RAW_TRANSCRIPT_SENTINEL two"),
    )
    with MemoryLoop(path, graph) as loop:
        pending = loop.stage_addition(delta, records)
        loop.decide(pending.id, pending.result_hash, "accept")
        before = loop.view()
        before_counts = tuple(
            loop.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "memory_state",
                "evidence_ledger",
                "proposals",
                "cognition_transitions",
            )
        )
        answerer = _Answerer()
        answer = loop.ask(
            "Do you remember why she and I argued about the Nanjing trip?",
            answerer,
        )
        after = loop.view()
        assert before == after
        assert tuple(
            loop.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "memory_state",
                "evidence_ledger",
                "proposals",
                "cognition_transitions",
            )
        ) == before_counts
        assert answer.status == "answered"
        assert [item.id for item in answer.recalled_events] == [event.id]
        assert {item.id for item in answer.evidence_context} == set(event.evidence_ids)
        prompt = answerer.messages[-1].content
        assert "They wanted different levels of advance planning." in prompt
        assert "position[about=Yun]=Preferred flexibility." in prompt
        assert "position[about=Friend_X]=Preferred an itinerary." in prompt
        assert "turn:event-one" in prompt and "turn:event-two" in prompt
        assert "RAW_TRANSCRIPT_SENTINEL" not in prompt

    with MemoryLoop(path, _graph()) as reopened:
        result = reopened.recall(
            "Do you remember why she and I argued about the Nanjing trip?"
        )
        assert result.status == "resolved"
        assert result.event_ids == (event.id,)
        assert set(result.evidence_ids) == set(event.evidence_ids)


def test_complete_nanjing_reconstruction_is_identical_after_sqlite_reopen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "complete-nanjing.sqlite"
    graph = build_nanjing_world()
    query = "Do you remember why she and I argued about the Nanjing trip?"
    with MemoryLoop(path, graph) as loop:
        before = loop.recall(query)
    with MemoryLoop(path, _graph()) as reopened:
        after = reopened.recall(query)

    assert before == after
    assert after.status == "resolved"
    assert after.primary_anchor is not None
    assert after.primary_anchor.target.id == "event:nanjing-conflict"
    assert set(after.entity_ids) == {
        "person:user",
        "person:friend-x",
        "activity:nanjing-trip",
        "place:nanjing",
    }


@pytest.mark.parametrize(
    ("now", "expected_expired", "expected_reason"),
    (
        ("2026-08-05T00:00:00Z", False, "below_effective_confidence"),
        ("2026-08-10T00:00:00Z", True, "expired"),
    ),
)
def test_recall_projects_available_lifecycle_and_filters_transient_cognition_without_writes(
    tmp_path: Path,
    now: str,
    expected_expired: bool,
    expected_reason: str,
) -> None:
    path = tmp_path / f"recall-decay-{expected_reason}.sqlite"
    clock = lambda: now  # noqa: E731 - compact immutable clock fixture
    with MemoryLoop(path, _graph(), recall_clock=clock) as loop:
        state_text = "Yun is temporarily exhausted."
        state_record = _lifecycle_evidence(
            "e:lifecycle-state",
            state_text,
            occurred_at="2026-08-01T00:00:00Z",
        )
        state = _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:lifecycle-state",
            content=state_text,
            content_type="state",
            records=(state_record,),
        )
        preference_text = "Yun prefers tea."
        preference_record = _lifecycle_evidence(
            "e:lifecycle-preference",
            preference_text,
            occurred_at="2026-08-01T00:00:00Z",
        )
        preference = _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:lifecycle-preference",
            content=preference_text,
            content_type="preference",
            records=(preference_record,),
        )
        before = loop.view()
        before_rows = {
            table: tuple(tuple(row) for row in loop.connection.execute(f"SELECT * FROM {table}"))
            for table in (
                "memory_state",
                "evidence_ledger",
                "proposals",
                "cognition_transitions",
                "proposal_decision_receipts",
            )
        }

        answer = loop.ask(
            "Does Yun prefer tea while temporarily exhausted?",
            resolved_entity_ids=(before.graph.world.owner_entity_id,),
        )

        assert [item.id for item in answer.recalled_cognitions] == [preference.id]
        lifecycle = {item.cognition_id: item for item in answer.cognition_lifecycles}
        projected_state = lifecycle[state.id]
        assert projected_state.time_authority_status == "available"
        assert projected_state.corroborating_evidence_ids == (state_record.id,)
        assert projected_state.last_corroborated_at == "2026-08-01T00:00:00Z"
        assert projected_state.stored_confidence == state.confidence
        assert projected_state.effective_confidence is not None
        assert projected_state.effective_confidence < 80
        assert projected_state.is_expired is expected_expired
        assert projected_state.is_current is (not expected_expired)
        assert projected_state.recall_eligible is False
        assert projected_state.exclusion_reason == expected_reason
        projected_preference = lifecycle[preference.id]
        assert projected_preference.effective_confidence == preference.confidence
        assert projected_preference.active_salience == preference.confidence
        assert projected_preference.recall_eligible is True
        assert projected_preference.exclusion_reason is None
        assert loop.view() == before
        assert {
            table: tuple(tuple(row) for row in loop.connection.execute(f"SELECT * FROM {table}"))
            for table in before_rows
        } == before_rows

    with MemoryLoop(path, _graph(), recall_clock=clock) as reopened:
        replay = reopened.ask(
            "Does Yun prefer tea while temporarily exhausted?",
            resolved_entity_ids=(reopened.view().graph.world.owner_entity_id,),
        )
        assert replay.cognition_lifecycles == answer.cognition_lifecycles
        assert replay.recalled_cognitions == answer.recalled_cognitions


def test_recall_uses_latest_exact_support_recorded_time_as_corroboration(
    tmp_path: Path,
) -> None:
    with MemoryLoop(
        tmp_path / "recall-latest-support.sqlite",
        _graph(),
        recall_clock=lambda: "2026-08-05T00:00:00Z",
    ) as loop:
        content = "Yun is temporarily exhausted."
        old = _lifecycle_evidence(
            "e:lifecycle-old-support",
            content,
            occurred_at="2026-08-01T00:00:00Z",
        )
        recent = _lifecycle_evidence(
            "e:lifecycle-recent-support",
            content,
            occurred_at="2026-08-04T00:00:00Z",
        )
        cognition = _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:lifecycle-refreshed-state",
            content=content,
            content_type="state",
            records=(old, recent),
        )
        before = loop.view()

        answer = loop.ask(
            "Is Yun temporarily exhausted?",
            resolved_entity_ids=(before.graph.world.owner_entity_id,),
        )

        assert [item.id for item in answer.recalled_cognitions] == [cognition.id]
        projected = answer.cognition_lifecycles[0]
        assert projected.corroborating_evidence_ids == (old.id, recent.id)
        assert projected.last_corroborated_at == "2026-08-04T00:00:00Z"
        assert projected.time_authority_status == "available"
        assert projected.effective_confidence is not None
        assert projected.effective_confidence >= 80
        assert projected.recall_eligible is True
        assert loop.view() == before


@pytest.mark.parametrize(
    ("case", "expected_status"),
    (
        ("legacy", "legacy"),
        ("missing", "missing"),
        ("mixed", "mixed"),
    ),
)
def test_recall_never_guesses_legacy_or_missing_corroboration_time(
    tmp_path: Path,
    case: str,
    expected_status: str,
) -> None:
    graph = _graph()
    content = "Yun is temporarily exhausted."
    missing = WorldCognition(
        "cog:lifecycle-unavailable",
        graph.world.world_id,
        MemoryTarget("entity", graph.world.owner_entity_id),
        content,
        "state",
        "stated",
        300,
        "low",
        Perspective("entity", (graph.world.owner_entity_id,)),
        (EvidenceLink("e:lifecycle-missing", "support"),),
    )
    if case == "missing":
        graph.add_cognition(missing)
    with MemoryLoop(
        tmp_path / f"recall-{case}-time.sqlite",
        graph,
        recall_clock=lambda: "2099-01-01T00:00:00Z",
    ) as loop:
        if case != "missing":
            legacy = _lifecycle_evidence(
                "e:lifecycle-legacy",
                content,
                occurred_at="2026-08-01T00:00:00Z",
                legacy=True,
            )
            records: tuple[EvidenceRecord, ...] = (legacy,)
            if case == "mixed":
                available = _lifecycle_evidence(
                    "e:lifecycle-available",
                    content,
                    occurred_at="2026-08-02T00:00:00Z",
                )
                records = (legacy, available)
            missing = _accept_lifecycle_cognition(
                loop,
                cognition_id=missing.id,
                content=content,
                content_type="state",
                records=records,
            )
        before = loop.view()

        answer = loop.ask(
            "Is Yun temporarily exhausted?",
            resolved_entity_ids=(before.graph.world.owner_entity_id,),
        )

        assert [item.id for item in answer.recalled_cognitions] == [missing.id]
        projected = answer.cognition_lifecycles[0]
        assert projected.time_authority_status == expected_status
        assert projected.last_corroborated_at is None
        assert projected.effective_confidence is None
        assert projected.is_current is None
        assert projected.is_expired is None
        assert projected.active_salience is None
        assert projected.recall_eligible is True
        assert projected.exclusion_reason is None
        assert loop.view() == before


@pytest.mark.parametrize(
    "now",
    ("not-a-time", "2026-07-31T23:59:59Z"),
)
def test_recall_fails_closed_on_invalid_or_pre_evidence_server_time(
    tmp_path: Path,
    now: str,
) -> None:
    with MemoryLoop(
        tmp_path / "recall-clock-integrity.sqlite",
        _graph(),
        recall_clock=lambda: now,
    ) as loop:
        content = "Yun is temporarily exhausted."
        record = _lifecycle_evidence(
            "e:lifecycle-clock",
            content,
            occurred_at="2026-08-01T00:00:00Z",
        )
        _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:lifecycle-clock",
            content=content,
            content_type="state",
            records=(record,),
        )
        before = loop.view()
        before_counts = tuple(
            loop.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "evidence_ledger",
                "proposals",
                "proposal_decision_receipts",
            )
        )

        with pytest.raises(MemoryLoopIntegrityError, match="lifecycle time"):
            loop.ask(
                "Is Yun temporarily exhausted?",
                resolved_entity_ids=(before.graph.world.owner_entity_id,),
            )

        assert loop.view() == before
        assert tuple(
            loop.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "evidence_ledger",
                "proposals",
                "proposal_decision_receipts",
            )
        ) == before_counts


def test_asking_projection_selects_one_best_recalled_hypothesis_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMOWEFT_LANG", "en")
    path = tmp_path / "asking-projection.sqlite"
    clock = lambda: "2026-08-01T12:00:00Z"  # noqa: E731 - immutable fixture
    with MemoryLoop(path, _graph(), recall_clock=clock) as loop:
        inferred_record = _lifecycle_evidence(
            "e:asking-inferred",
            "Yun has been staying up late.",
            occurred_at="2026-08-01T09:00:00Z",
        )
        _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:asking-inferred",
            content="Late nights may be making Yun tired.",
            content_type="hypothesis",
            records=(inferred_record,),
            formed_by="inferred",
        )
        confirmed_record = _lifecycle_evidence(
            "e:asking-confirmed",
            "Maybe tea is affecting Yun's sleep.",
            occurred_at="2026-08-01T10:00:00Z",
        )
        selected = _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:asking-confirmed",
            content="Tea may be affecting Yun's sleep.",
            content_type="hypothesis",
            records=(confirmed_record,),
            formed_by="confirmed",
        )
        before = loop.view()
        before_rows = {
            table: tuple(
                tuple(row)
                for row in loop.connection.execute(f"SELECT * FROM {table}")
            )
            for table in (
                "memory_state",
                "evidence_ledger",
                "proposals",
                "cognition_transitions",
                "proposal_decision_receipts",
            )
        }

        proposal = loop.propose_ask(
            "What should Yun clarify about sleep?",
            resolved_entity_ids=(before.graph.world.owner_entity_id,),
        )

        assert proposal is not None
        assert proposal.cognition_id == selected.id
        assert proposal.kind == "hypothesis"
        assert proposal.reason == "low_confidence"
        assert proposal.content == selected.content
        assert proposal.question == (
            'I noticed "Maybe tea is affecting Yun\'s sleep.", which got me '
            "wondering: Tea may be affecting Yun's sleep.. Is that right?"
        )
        assert [asdict(item) for item in proposal.support_evidence] == [
            {"id": confirmed_record.id, "summary": confirmed_record.content}
        ]
        assert proposal.contradict_evidence == ()
        assert proposal.stored_confidence == 280
        assert proposal.effective_confidence == 272
        assert proposal.cred_status == "candidate"
        assert loop.view() == before
        assert {
            table: tuple(
                tuple(row)
                for row in loop.connection.execute(f"SELECT * FROM {table}")
            )
            for table in before_rows
        } == before_rows

    with MemoryLoop(path, _graph(), recall_clock=clock) as reopened:
        replay = reopened.propose_ask(
            "What should Yun clarify about sleep?",
            resolved_entity_ids=(reopened.view().graph.world.owner_entity_id,),
        )
        assert replay == proposal


def test_asking_projection_prioritizes_explicit_conflict_as_a_distinct_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMOWEFT_LANG", "en")
    with MemoryLoop(
        tmp_path / "asking-conflict.sqlite",
        _graph(),
        recall_clock=lambda: "2026-08-01T12:00:00Z",
    ) as loop:
        hypothesis_record = _lifecycle_evidence(
            "e:asking-low-hypothesis",
            "Yun may prefer quiet mornings.",
            occurred_at="2026-08-01T08:00:00Z",
        )
        _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:asking-low-hypothesis",
            content="Yun may work best in quiet mornings.",
            content_type="hypothesis",
            records=(hypothesis_record,),
            formed_by="inferred",
        )
        support = _lifecycle_evidence(
            "e:asking-conflict-support",
            "Yun likes early mornings.",
            occurred_at="2026-08-01T09:00:00Z",
        )
        contradict = _lifecycle_evidence(
            "e:asking-conflict-contradict",
            "Yun usually sleeps until noon.",
            occurred_at="2026-08-01T10:00:00Z",
        )
        conflicted = _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:asking-conflict",
            content="Yun prefers early mornings.",
            content_type="preference",
            records=(support, contradict),
            formed_by="inferred",
            relations=("support", "contradict"),
        )
        before = loop.view()

        proposal = loop.propose_ask(
            "What should Yun clarify about mornings?",
            resolved_entity_ids=(before.graph.world.owner_entity_id,),
        )

        assert proposal is not None
        assert proposal.cognition_id == conflicted.id
        assert proposal.kind == "conflict"
        assert proposal.reason == "unresolved_conflict"
        assert proposal.question == (
            'About "Yun prefers early mornings." — on one hand "Yun likes early '
            'mornings.", but on the other hand "Yun usually sleeps until noon.". '
            "Which is it actually now?"
        )
        assert [item.id for item in proposal.support_evidence] == [support.id]
        assert [item.id for item in proposal.contradict_evidence] == [contradict.id]
        assert proposal.stored_confidence == 80
        assert proposal.effective_confidence == 80
        assert proposal.cred_status == "conflicted"
        assert loop.view() == before


def test_asking_projection_excludes_legacy_expired_and_unrelated_cognitions(
    tmp_path: Path,
) -> None:
    graph = _graph()
    friend = Entity("person:friend", "world:yun", "person", "Friend")
    graph.add_entity(friend)
    with MemoryLoop(
        tmp_path / "asking-ineligible.sqlite",
        graph,
        recall_clock=lambda: "2026-08-20T00:00:00Z",
    ) as loop:
        legacy = _lifecycle_evidence(
            "e:asking-legacy",
            "Yun may be avoiding crowds.",
            occurred_at="2026-08-19T00:00:00Z",
            legacy=True,
        )
        _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:asking-legacy",
            content="Yun may dislike crowds.",
            content_type="hypothesis",
            records=(legacy,),
            formed_by="inferred",
        )
        expired = _lifecycle_evidence(
            "e:asking-expired",
            "Yun stayed up late once.",
            occurred_at="2026-08-01T00:00:00Z",
        )
        _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:asking-expired",
            content="Late nights may be making Yun tired.",
            content_type="hypothesis",
            records=(expired,),
            formed_by="inferred",
        )
        unrelated = _lifecycle_evidence(
            "e:asking-unrelated",
            "Friend may be planning a move.",
            occurred_at="2026-08-19T00:00:00Z",
        )
        _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:asking-unrelated",
            content="Friend may move soon.",
            content_type="hypothesis",
            records=(unrelated,),
            formed_by="inferred",
            target_entity_id=friend.id,
        )
        before = loop.view()

        assert (
            loop.propose_ask(
                "What should Yun clarify?",
                resolved_entity_ids=(before.graph.world.owner_entity_id,),
            )
            is None
        )
        assert loop.view() == before


def test_asking_projection_fails_closed_on_permission_ineligible_evidence(
    tmp_path: Path,
) -> None:
    with MemoryLoop(
        tmp_path / "asking-permission.sqlite",
        _graph(),
        recall_clock=lambda: "2026-08-01T12:00:00Z",
    ) as loop:
        record = _lifecycle_evidence(
            "e:asking-permission",
            "Yun may prefer cycling.",
            occurred_at="2026-08-01T10:00:00Z",
            allow_inference=False,
        )
        _accept_lifecycle_cognition(
            loop,
            cognition_id="cog:asking-permission",
            content="Yun may enjoy cycling.",
            content_type="hypothesis",
            records=(record,),
            formed_by="inferred",
        )
        before = loop.view()

        with pytest.raises(MemoryLoopIntegrityError, match="does not match"):
            loop.propose_ask(
                "What should Yun clarify?",
                resolved_entity_ids=(before.graph.world.owner_entity_id,),
            )

        assert loop.view() == before


def test_ambiguous_experience_anchor_does_not_call_answerer_or_union_bundles(
    tmp_path: Path,
) -> None:
    graph = _graph()
    for item in (
        Entity("person:a", "world:yun", "person", "Friend A"),
        Entity("person:b", "world:yun", "person", "Friend B"),
        Entity("activity:nanjing", "world:yun", "activity", "Nanjing trip"),
        Entity("place:nanjing", "world:yun", "place", "Nanjing"),
    ):
        graph.add_entity(item)
    for suffix, friend_id in (("a", "person:a"), ("b", "person:b")):
        graph.add_event(
            WorldEvent(
                f"event:{suffix}",
                "world:yun",
                "interpersonal_conflict",
                "Yun argued with a friend about the Nanjing trip.",
                f"2026-08-0{1 if suffix == 'a' else 2}T12:00:00+08:00",
                (EventParticipant("person:yun"), EventParticipant(friend_id)),
                ("activity:nanjing", "place:nanjing"),
                facets=(EventFacet("cause", "Different travel styles"),),
                evidence_ids=(f"e:{suffix}",),
            )
        )
    with MemoryLoop(tmp_path / "ambiguous.sqlite", graph) as loop:
        answerer = _Answerer()
        answer = loop.ask(
            "Why did I argue with a friend about the Nanjing trip?", answerer
        )

    assert answer.status == "ambiguous"
    assert answerer.call_count == 0
    assert answer.recalled_entities == ()
    assert answer.recalled_events == ()
    assert answer.reconstruction is not None
    assert answer.reconstruction.reason_code == "ANCHOR_SCORE_TIE"


def test_typed_evaluation_correction_evolution_is_closed_and_replays_after_accept(
    tmp_path: Path,
) -> None:
    path = tmp_path / "typed-evaluation-correction.sqlite"
    with _typed_correction_loop(path) as loop:
        plan, evidence, review_payload, prior, successor = (
            _typed_evaluation_correction(loop)
        )
        pending = loop.stage_evolution(
            plan,
            (evidence,),
            review_payload=review_payload,
        )
        accepted = loop.decide(pending.id, pending.result_hash, "accept")

        assert prior.id in accepted.superseded_cognition_ids
        assert successor.id in {item.id for item in accepted.current_cognitions}
        assert accepted.graph.cognitions[prior.id] == prior
        assert accepted.graph.cognitions[successor.id] == successor
        row = loop.connection.execute(
            "SELECT payload_json, review_payload_json FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()
        assert row is not None
        stored_payload = cast(dict[str, object], json.loads(row["payload_json"]))
        stored_review_payload = cast(
            dict[str, object], json.loads(row["review_payload_json"])
        )
        # Rehash/recovery validation must remain valid after the prior becomes
        # historical; the exact accepted evolution+transition proves this is
        # the already-applied proposal, not permission to reuse a stale prior.
        assert loop._recompute_review_result_hash(  # noqa: SLF001
            "evolution",
            stored_payload,
            stored_review_payload,
        ) == pending.result_hash

    with MemoryLoop(path, _graph()) as reopened:
        receipt = reopened.decision_receipt(pending.id)
        assert receipt is not None
        assert receipt.effective_decision == "accept"
        assert prior.id in reopened.view().superseded_cognition_ids
        assert reopened._recompute_review_result_hash(  # noqa: SLF001
            "evolution",
            stored_payload,
            stored_review_payload,
        ) == pending.result_hash


@pytest.mark.parametrize("changed_kind", [False, True])
def test_generic_evolution_rejects_a_structured_correction_without_a_real_same_kind_change(
    tmp_path: Path,
    changed_kind: bool,
) -> None:
    with _typed_correction_loop(tmp_path / f"generic-{changed_kind}.sqlite") as loop:
        plan, evidence, _, prior, successor = _typed_evaluation_correction(loop)
        assert successor.structured_claim is not None
        invalid_claim = (
            StructuredClaim(
                "attribute",
                value=successor.structured_claim.value,
                polarity="assert",
                epistemic_status="asserted",
            )
            if changed_kind
            else prior.structured_claim
        )
        invalid_successor = replace(successor, structured_claim=invalid_claim)
        invalid_plan = WorldEvolutionPlan(
            replace(plan.delta, new_cognitions=(invalid_successor,)),
            plan.steps,
        )

        with pytest.raises(
            MemoryLoopIntegrityError,
            match="invalid proposition change",
        ):
            loop.stage_evolution(invalid_plan, (evidence,))
        assert loop.view().revision == 0
        assert loop.connection.execute(
            "SELECT COUNT(*) FROM proposals"
        ).fetchone()[0] == 0


def test_adapter_typed_correction_requires_a_different_evaluation_value(
    tmp_path: Path,
) -> None:
    """Changing only structured metadata cannot masquerade as a replacement."""

    with _typed_correction_loop(tmp_path / "typed-same-value.sqlite") as loop:
        plan, evidence, review_payload, prior, successor = (
            _typed_evaluation_correction(loop)
        )
        assert prior.structured_claim is not None
        assert successor.structured_claim is not None
        same_value_claim = replace(
            successor.structured_claim,
            value=prior.structured_claim.value,
            polarity="negate",
        )
        invalid_successor = replace(
            successor,
            structured_claim=same_value_claim,
        )
        invalid_plan = WorldEvolutionPlan(
            replace(plan.delta, new_cognitions=(invalid_successor,)),
            plan.steps,
        )

        with pytest.raises(
            MemoryLoopIntegrityError,
            match="successor evaluation shape",
        ):
            loop.stage_evolution(
                invalid_plan,
                (evidence,),
                review_payload=review_payload,
            )
        assert loop.view().revision == 0
        assert loop.connection.execute(
            "SELECT COUNT(*) FROM proposals"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("tamper", ["plan_value_reverted", "display_plan_fork"])
def test_rehashed_typed_correction_tamper_stays_pending_and_writes_nothing(
    tmp_path: Path,
    tamper: str,
) -> None:
    with _typed_correction_loop(tmp_path / f"tamper-{tamper}.sqlite") as loop:
        plan, evidence, review_payload, prior, _ = _typed_evaluation_correction(loop)
        assert prior.structured_claim is not None
        pending = loop.stage_evolution(
            plan,
            (evidence,),
            review_payload=review_payload,
        )
        row = loop.connection.execute(
            "SELECT payload_json, review_payload_json FROM proposals WHERE id = ?",
            (pending.id,),
        ).fetchone()
        assert row is not None
        stored_payload = cast(dict[str, object], json.loads(row["payload_json"]))
        stored_review = cast(dict[str, object], json.loads(row["review_payload_json"]))
        display = cast(dict[str, object], stored_review["productDisplay"])
        replacements = cast(list[dict[str, object]], display["cognitionReplacements"])
        if tamper == "plan_value_reverted":
            plan_data = cast(dict[str, object], stored_payload["plan"])
            delta_data = cast(dict[str, object], plan_data["delta"])
            cognitions = cast(list[dict[str, object]], delta_data["new_cognitions"])
            successor_claim = cast(dict[str, object], cognitions[0]["structured_claim"])
            successor_claim["value"] = prior.structured_claim.value
            candidate = cast(
                dict[str, object],
                cast(dict[str, object], display["candidateMemory"])["cognitions"][0],  # type: ignore[index]
            )
            cast(dict[str, object], candidate["structured_claim"])["value"] = (
                prior.structured_claim.value
            )
            cast(
                dict[str, object],
                cast(dict[str, object], replacements[0]["after"])["structured_claim"],
            )["value"] = prior.structured_claim.value
        else:
            cast(
                dict[str, object],
                cast(dict[str, object], replacements[0]["after"])["structured_claim"],
            )["value"] = "被伪造的展示值"

        rehashed = _result_hash(
            "evolution",
            _json(stored_payload["plan"]),
            _json(stored_payload["evidence"]),
            stored_review,
        )
        loop.connection.execute(
            "UPDATE proposals SET payload_json = ?, review_payload_json = ?, result_hash = ? WHERE id = ?",
            (_json(stored_payload), _json(stored_review), rehashed, pending.id),
        )
        before = loop.view()
        before_revision = loop.view().revision
        before_counts = tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM cognition_transitions), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone())

        with pytest.raises(MemoryLoopIntegrityError):
            loop.decide(pending.id, rehashed, "accept")

        assert loop.view().revision == before_revision
        assert loop.view().graph == before.graph
        assert tuple(loop.connection.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM evidence_ledger), "
            "(SELECT COUNT(*) FROM cognition_transitions), "
            "(SELECT COUNT(*) FROM proposal_decision_receipts)"
        ).fetchone()) == before_counts
        assert loop.connection.execute(
            "SELECT status FROM proposals WHERE id = ?", (pending.id,)
        ).fetchone()[0] == "pending"
