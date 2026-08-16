"""SQLite review/transaction contracts for Stage 4 world evolution."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from typing import cast

import pytest

from memoweft.confidence import compute_confidence, derive_cred_status
from memoweft.types import ConfidenceInputs, EvidenceLink
from memoweft.world import (
    ClaimSpan,
    Entity,
    EventParticipant,
    EvolutionStep,
    FormationSourceTrace,
    FormationTrace,
    MemoryTarget,
    MemoryWorldGraph,
    PersonalWorld,
    PersistentIdentityAuthority,
    Perspective,
    Relationship,
    StructuredClaim,
    WorldCognition,
    WorldDelta,
    WorldEvent,
    WorldEvolutionPlan,
    project_relationship_state,
    relationship_event_chain,
)
from memoweft.world.loop import EvidenceRecord, MemoryLoop, MemoryLoopIntegrityError


WORLD_ID = "world:yun"
OWNER_ID = "person:yun"
FRIEND_ID = "person:friend-x"
RELATIONSHIP_ID = "relationship:yun-friend-x"


def _score(content_type: str, formed_by: str, support: int, contradict: int) -> tuple[int, str]:
    confidence = compute_confidence(
        ConfidenceInputs(content_type, formed_by, support, contradict)  # type: ignore[arg-type]
    )
    return confidence, derive_cred_status(
        confidence,
        contradict,
        content_type,  # type: ignore[arg-type]
        support_count=support,
    )


def _cognition(
    cognition_id: str,
    target: MemoryTarget,
    content: str,
    evidence_id: str,
    *,
    content_type: str = "state",
    scope: str | None = "relationship_state",
) -> WorldCognition:
    confidence, status = _score(content_type, "stated", 1, 0)
    return WorldCognition(
        cognition_id,
        WORLD_ID,
        target,
        content,
        content_type,  # type: ignore[arg-type]
        "stated",
        confidence,
        status,  # type: ignore[arg-type]
        Perspective("entity", (OWNER_ID,)),
        sources=(EvidenceLink(evidence_id, "support"),),
        scope=scope,
    )


def _trace(cognition: WorldCognition, evidence_id: str) -> FormationTrace:
    return FormationTrace(
        cognition.id,
        False,
        (
            FormationSourceTrace(
                evidence_id,
                "support",
                "user_stated",
                "elaborate",
                ClaimSpan(
                    0,
                    len(cognition.content),
                    "a" * 64,
                    sha256(cognition.content.encode("utf-8")).hexdigest(),
                ),
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
    )


def _event(event_id: str, event_type: str, occurred_at: str, evidence_id: str) -> WorldEvent:
    return WorldEvent(
        event_id,
        WORLD_ID,
        event_type,
        event_type.replace("_", " "),
        occurred_at,
        participants=(
            EventParticipant(OWNER_ID, "participant"),
            EventParticipant(FRIEND_ID, "participant"),
        ),
        relationship_ids=(RELATIONSHIP_ID,),
        evidence_ids=(evidence_id,),
    )


def _relationship_graph() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld(WORLD_ID, OWNER_ID))
    graph.add_entity(Entity(OWNER_ID, WORLD_ID, "person", "Yun"))
    graph.add_entity(Entity(FRIEND_ID, WORLD_ID, "person", "Friend_X"))
    graph.add_relationship(
        Relationship(
            RELATIONSHIP_ID,
            WORLD_ID,
            OWNER_ID,
            FRIEND_ID,
            "friend",
            bidirectional=True,
        )
    )
    graph.add_event(
        _event(
            "event:argument",
            "interpersonal_conflict",
            "2026-06-01T09:00:00+08:00",
            "e:argument",
        )
    )
    graph.add_cognition(
        _cognition(
            "cog:relationship-strained:initial",
            MemoryTarget("relationship", RELATIONSHIP_ID),
            "The friendship is strained.",
            "e:argument",
        )
    )
    return graph


def _plan(
    predecessor_event_id: str,
    predecessor_state_id: str,
    event_id: str,
    event_type: str,
    occurred_at: str,
    evidence_id: str,
    successor_state_id: str,
    state: str,
    event_relation: str,
) -> WorldEvolutionPlan:
    event = _event(event_id, event_type, occurred_at, evidence_id)
    state_cognition = _cognition(
        successor_state_id,
        MemoryTarget("relationship", RELATIONSHIP_ID),
        f"The friendship is {state}.",
        evidence_id,
    )
    return WorldEvolutionPlan(
        WorldDelta(
            WORLD_ID,
            (evidence_id,),
            new_events=(event,),
            new_cognitions=(state_cognition,),
            formation_traces=(_trace(state_cognition, evidence_id),),
        ),
        (
            EvolutionStep(
                f"evolution:{event_id}:link",
                "event_link",
                event_relation,  # type: ignore[arg-type]
                MemoryTarget("relationship", RELATIONSHIP_ID),
                (predecessor_event_id,),
                (event_id,),
                occurred_at,
                (evidence_id,),
            ),
            EvolutionStep(
                f"evolution:{event_id}:state",
                "relationship_state",
                state,  # type: ignore[arg-type]
                MemoryTarget("relationship", RELATIONSHIP_ID),
                (predecessor_state_id,),
                (successor_state_id,),
                occurred_at,
                (evidence_id,),
            ),
        ),
    )


def test_long_relationship_sequence_reject_accept_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite"
    loop = MemoryLoop(path, _relationship_graph())
    sequence = (
        (
            "event:apology:one",
            "apology",
            "2026-06-02T09:00:00+08:00",
            "e:apology:one",
            "cog:relationship-repairing:one",
            "repairing",
            "responds_to",
        ),
        (
            "event:repair:one",
            "relationship_repair",
            "2026-06-03T09:00:00+08:00",
            "e:repair:one",
            "cog:relationship-repaired:one",
            "repaired",
            "repairs",
        ),
        (
            "event:setback",
            "relationship_setback",
            "2026-06-04T09:00:00+08:00",
            "e:setback",
            "cog:relationship-strained:setback",
            "strained",
            "causes",
        ),
        (
            "event:clarification",
            "clarification",
            "2026-06-05T09:00:00+08:00",
            "e:clarification",
            "cog:relationship-repairing:two",
            "repairing",
            "responds_to",
        ),
        (
            "event:repair:two",
            "relationship_repair",
            "2026-06-06T09:00:00+08:00",
            "e:repair:two",
            "cog:relationship-repaired:two",
            "repaired",
            "repairs",
        ),
    )
    predecessor_event = "event:argument"
    predecessor_state = "cog:relationship-strained:initial"
    for index, (
        event_id,
        event_type,
        occurred_at,
        evidence_id,
        state_id,
        state,
        event_relation,
    ) in enumerate(sequence):
        plan = _plan(
            predecessor_event,
            predecessor_state,
            event_id,
            event_type,
            occurred_at,
            evidence_id,
            state_id,
            state,
            event_relation,
        )
        if index == 1:
            rejected = loop.stage_evolution(
                plan,
                (EvidenceRecord(evidence_id, f"rejected {event_type}"),),
            )
            rejected_view = loop.decide(rejected.id, rejected.result_hash, "reject")
            assert rejected_view.revision == 1
            assert event_id not in rejected_view.graph.events
        pending = loop.stage_evolution(
            plan,
            (EvidenceRecord(evidence_id, f"accepted {event_type}"),),
            {"summary": f"{event_type} -> {state}"},
        )
        assert pending.kind == "evolution"
        view = loop.decide(pending.id, pending.result_hash, "accept")
        assert view.revision == index + 1
        predecessor_event = event_id
        predecessor_state = state_id

    assert len(view.evolution_steps) == 10
    assert len(view.transitions) == 5
    assert {item.reason for item in view.transitions} == {"repairing", "repaired", "strained"}
    assert [
        event.id for event in relationship_event_chain(view.graph, view.evolution_steps, RELATIONSHIP_ID)
    ] == ["event:argument", *(item[0] for item in sequence)]
    projection = project_relationship_state(view.graph, view.evolution_steps, RELATIONSHIP_ID)
    assert projection is not None
    assert (projection.state, projection.cognition_id, projection.revision) == (
        "repaired",
        "cog:relationship-repaired:two",
        5,
    )
    assert {item.id for item in view.current_cognitions} == {"cog:relationship-repaired:two"}
    recall_query = "What happened after the interpersonal conflict with Friend_X?"
    recall_before_reopen = loop.recall(recall_query)
    assert recall_before_reopen.status == "resolved"
    assert recall_before_reopen.event_ids == (
        "event:argument",
        *(item[0] for item in sequence),
    )
    assert recall_before_reopen.relationship_states[0].state == "repaired"
    assert recall_before_reopen.current_cognition_ids == (
        "cog:relationship-repaired:two",
    )
    assert "cog:relationship-strained:initial" in recall_before_reopen.historical_cognition_ids
    loop.close()

    with MemoryLoop(path, _relationship_graph()) as reopened:
        restored = reopened.view()
        assert restored.revision == 5
        assert len(restored.evolution_steps) == 10
        assert len(restored.transitions) == 5
        assert [
            event.id
            for event in relationship_event_chain(
                restored.graph,
                restored.evolution_steps,
                RELATIONSHIP_ID,
            )
        ] == ["event:argument", *(item[0] for item in sequence)]
        assert reopened.recall(recall_query) == recall_before_reopen


def test_stored_evolution_payload_tamper_rolls_back_without_evidence(tmp_path: Path) -> None:
    loop = MemoryLoop(tmp_path / "tamper.sqlite", _relationship_graph())
    plan = _plan(
        "event:argument",
        "cog:relationship-strained:initial",
        "event:apology",
        "apology",
        "2026-06-02T09:00:00+08:00",
        "e:apology",
        "cog:relationship-repairing",
        "repairing",
        "responds_to",
    )
    pending = loop.stage_evolution(plan, (EvidenceRecord("e:apology", "Friend apologised."),))
    row = loop.connection.execute(
        "SELECT payload_json FROM proposals WHERE id = ?", (pending.id,)
    ).fetchone()
    assert row is not None
    payload = cast(dict[str, object], json.loads(cast(str, row[0])))
    plan_data = cast(dict[str, object], payload["plan"])
    steps = cast(list[dict[str, object]], plan_data["steps"])
    steps[0]["effective_at"] = "2026-07-01T09:00:00+08:00"
    loop.connection.execute(
        "UPDATE proposals SET payload_json = ? WHERE id = ?",
        (json.dumps(payload, ensure_ascii=False), pending.id),
    )

    with pytest.raises(MemoryLoopIntegrityError, match="result hash"):
        loop.decide(pending.id, pending.result_hash, "accept")

    assert loop.view().revision == 0
    assert "event:apology" not in loop.view().graph.events
    assert loop.connection.execute(
        "SELECT 1 FROM evidence_ledger WHERE id = 'e:apology'"
    ).fetchone() is None
    assert loop.connection.execute(
        "SELECT status FROM proposals WHERE id = ?", (pending.id,)
    ).fetchone()[0] == "pending"


def test_unknown_review_kind_fails_closed_before_world_mutation(tmp_path: Path) -> None:
    loop = MemoryLoop(tmp_path / "unknown.sqlite", _relationship_graph())
    plan = _plan(
        "event:argument",
        "cog:relationship-strained:initial",
        "event:apology",
        "apology",
        "2026-06-02T09:00:00+08:00",
        "e:apology",
        "cog:relationship-repairing",
        "repairing",
        "responds_to",
    )
    pending = loop.stage_evolution(plan, (EvidenceRecord("e:apology", "Friend apologised."),))
    loop.connection.execute(
        "UPDATE proposals SET kind = 'future-kind' WHERE id = ?",
        (pending.id,),
    )

    with pytest.raises(MemoryLoopIntegrityError, match="unsupported review kind"):
        loop.decide(pending.id, pending.result_hash, "accept")

    assert loop.view().revision == 0
    assert "event:apology" not in loop.view().graph.events


def test_nanjing_scope_narrowing_persists_as_a_true_transition(tmp_path: Path) -> None:
    path = tmp_path / "nanjing.sqlite"
    graph = _relationship_graph()
    prior = _cognition(
        "cog:friend-planning-style",
        MemoryTarget("entity", FRIEND_ID),
        "Friend_X always needs a fully planned itinerary.",
        "e:planning",
        content_type="fact",
        scope=None,
    )
    graph.add_cognition(prior)
    successor = _cognition(
        "cog:friend-planning-style:nanjing",
        prior.target,
        "Friend_X wanted a plan on that first Nanjing trip because they were nervous.",
        "e:nanjing-correction",
        content_type="fact",
        scope="that_nanjing_trip",
    )
    plan = WorldEvolutionPlan(
        WorldDelta(
            WORLD_ID,
            ("e:nanjing-correction",),
            new_cognitions=(successor,),
            formation_traces=(_trace(successor, "e:nanjing-correction"),),
        ),
        (
            EvolutionStep(
                "evolution:nanjing:narrow-planning-style",
                "cognition_change",
                "narrows",
                prior.target,
                (prior.id,),
                (successor.id,),
                "2026-06-04T09:00:00+08:00",
                ("e:nanjing-correction",),
            ),
        ),
    )
    loop = MemoryLoop(path, graph)
    pending = loop.stage_evolution(
        plan,
        (
            EvidenceRecord(
                "e:nanjing-correction",
                "That was our first Nanjing trip; Friend_X was nervous, not always rigid.",
            ),
        ),
    )
    view = loop.decide(pending.id, pending.result_hash, "accept")

    assert view.superseded_cognition_ids == frozenset({prior.id})
    assert any(item.id == prior.id for item in view.graph.cognitions.values())
    assert view.graph.cognitions[successor.id].scope == "that_nanjing_trip"
    assert [(item.prior_cognition_id, item.replacement_cognition_id, item.reason) for item in view.transitions] == [
        (prior.id, successor.id, "narrows")
    ]
    recall_query = "What changed about Friend_X on that first Nanjing trip?"
    recall_before_reopen = loop.recall(recall_query)
    assert recall_before_reopen.status == "resolved"
    assert successor.id in recall_before_reopen.current_cognition_ids
    assert prior.id in recall_before_reopen.historical_cognition_ids
    assert [item.relation for item in recall_before_reopen.cognition_lineage] == [
        "narrows"
    ]
    loop.close()

    with MemoryLoop(path, graph) as reopened:
        restored = reopened.view()
        assert restored.superseded_cognition_ids == frozenset({prior.id})
        assert restored.graph.cognitions[successor.id].scope == "that_nanjing_trip"
        assert reopened.recall(recall_query) == recall_before_reopen


def test_structured_evaluation_correction_is_one_typed_successor_with_exact_trace(
    tmp_path: Path,
) -> None:
    """A structured value replacement persists through the evolution authority."""

    path = tmp_path / "structured-evaluation-correction.sqlite"
    graph = MemoryWorldGraph(PersonalWorld(WORLD_ID, OWNER_ID))
    graph.add_entity(Entity(OWNER_ID, WORLD_ID, "person", "Yun"))
    graph.add_entity(Entity(FRIEND_ID, WORLD_ID, "person", "Friend_X"))
    graph.add_relationship(
        Relationship(
            RELATIONSHIP_ID,
            WORLD_ID,
            OWNER_ID,
            FRIEND_ID,
            "supports",
        )
    )
    target = MemoryTarget("relationship", RELATIONSHIP_ID)
    prior = WorldCognition(
        "cog:relationship-reliability:reliable",
        WORLD_ID,
        target,
        "I think this support is reliable.",
        "fact",
        "stated",
        600,
        "limited",
        Perspective("entity", (OWNER_ID,)),
        (EvidenceLink("e:reliable", "support"),),
        structured_claim=StructuredClaim(
            "evaluation",
            value="reliable",
            polarity="assert",
            epistemic_status="asserted",
        ),
    )
    graph.add_cognition(prior)
    evidence_id = "e:not-reliable-correction"
    evidence_text = "Correction: I think this support is not reliable."
    claim_text = "I think this support is not reliable"
    claim_start = evidence_text.index(claim_text)
    successor = WorldCognition(
        "cog:relationship-reliability:not-reliable",
        WORLD_ID,
        target,
        claim_text,
        "fact",
        "stated",
        600,
        "limited",
        prior.perspective,
        (EvidenceLink(evidence_id, "support"),),
        structured_claim=StructuredClaim(
            "evaluation",
            value="not reliable",
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
                    claim_start + len(claim_text),
                    sha256(evidence_text.encode("utf-8")).hexdigest(),
                    sha256(claim_text.encode("utf-8")).hexdigest(),
                ),
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
    )
    step = EvolutionStep(
        "evolution:relationship-reliability:corrected",
        "cognition_change",
        "corrects",
        target,
        (prior.id,),
        (successor.id,),
        "2026-08-13T09:00:00Z",
        (evidence_id,),
    )
    plan = WorldEvolutionPlan(
        WorldDelta(
            WORLD_ID,
            (evidence_id,),
            new_cognitions=(successor,),
            formation_traces=(trace,),
        ),
        (step,),
    )
    loop = MemoryLoop(path, graph)
    pending = loop.stage_evolution(
        plan,
        (EvidenceRecord(evidence_id, evidence_text),),
    )
    assert pending.kind == "evolution"
    stored = loop.connection.execute(
        "SELECT payload_json FROM proposals WHERE id = ?",
        (pending.id,),
    ).fetchone()
    assert stored is not None
    stored_plan = json.loads(stored[0])["plan"]
    assert stored_plan["cognition_updates"] == []
    assert len(stored_plan["delta"]["new_cognitions"]) == 1
    assert len(stored_plan["delta"]["formation_traces"]) == 1
    assert stored_plan["steps"] == [
        {
            "id": step.id,
            "kind": "cognition_change",
            "relation": "corrects",
            "subject": {"kind": "relationship", "id": RELATIONSHIP_ID},
            "predecessor_ids": [prior.id],
            "successor_ids": [successor.id],
            "effective_at": "2026-08-13T09:00:00Z",
            "evidence_ids": [evidence_id],
        }
    ]

    view = loop.decide(pending.id, pending.result_hash, "accept")
    assert view.revision == 1
    assert view.superseded_cognition_ids == frozenset({prior.id})
    assert {item.id for item in view.current_cognitions} == {successor.id}
    assert view.graph.cognitions[prior.id] == prior
    assert view.graph.cognitions[successor.id] == successor
    assert [
        (item.prior_cognition_id, item.replacement_cognition_id, item.reason)
        for item in view.transitions
    ] == [(prior.id, successor.id, "corrects")]
    recall_before_reopen = loop.recall(
        "Is Friend_X's support reliable now?",
        resolved_entity_ids=(OWNER_ID, FRIEND_ID),
    )
    assert recall_before_reopen.status == "resolved"
    assert recall_before_reopen.current_cognition_ids == (successor.id,)
    assert recall_before_reopen.historical_cognition_ids == (prior.id,)
    assert [
        (item.prior_cognition_id, item.successor_cognition_id, item.relation)
        for item in recall_before_reopen.cognition_lineage
    ] == [(prior.id, successor.id, "corrects")]
    assert {
        (item.subject_id, item.evidence_id, item.relation)
        for item in recall_before_reopen.provenance
    } == {
        (prior.id, "e:reliable", "support"),
        (successor.id, evidence_id, "support"),
    }
    loop.close()

    with MemoryLoop(path, graph) as reopened:
        restored = reopened.view()
        assert restored.revision == 1
        assert restored.graph.cognitions[prior.id] == prior
        assert restored.graph.cognitions[successor.id] == successor
        assert restored.superseded_cognition_ids == frozenset({prior.id})
        assert reopened.recall(
            "Is Friend_X's support reliable now?",
            resolved_entity_ids=(OWNER_ID, FRIEND_ID),
        ) == recall_before_reopen


def test_contradiction_persists_same_id_without_supersession(tmp_path: Path) -> None:
    graph = MemoryWorldGraph(PersonalWorld(WORLD_ID, OWNER_ID))
    graph.add_entity(Entity(OWNER_ID, WORLD_ID, "person", "Yun"))
    loop = MemoryLoop(tmp_path / "contradiction.sqlite", graph)
    prior = _cognition(
        "cog:likes-crowds",
        MemoryTarget("entity", OWNER_ID),
        "Yun likes crowded places.",
        "e:crowds",
        content_type="fact",
        scope=None,
    )
    addition = WorldDelta(
        WORLD_ID,
        ("e:crowds",),
        new_cognitions=(prior,),
        formation_traces=(_trace(prior, "e:crowds"),),
    )
    addition_review = loop.stage_addition(
        addition,
        (EvidenceRecord("e:crowds", "I like crowded places."),),
    )
    loop.decide(addition_review.id, addition_review.result_hash, "accept")
    confidence, status = _score(prior.content_type, prior.formed_by, 1, 1)
    updated = replace(
        prior,
        confidence=confidence,
        cred_status=status,  # type: ignore[arg-type]
        sources=prior.sources + (EvidenceLink("e:not-crowds", "contradict"),),
    )
    plan = WorldEvolutionPlan(
        WorldDelta(WORLD_ID, ("e:not-crowds",)),
        (
            EvolutionStep(
                "evolution:crowds:contradiction",
                "cognition_change",
                "contradicts",
                prior.target,
                (prior.id,),
                (prior.id,),
                "2026-06-07T09:00:00+08:00",
                ("e:not-crowds",),
            ),
        ),
        cognition_updates=(updated,),
    )
    pending = loop.stage_evolution(
        plan,
        (EvidenceRecord("e:not-crowds", "Actually, I avoid crowded places."),),
    )
    view = loop.decide(pending.id, pending.result_hash, "accept")

    assert set(view.graph.cognitions) == {prior.id}
    assert view.graph.cognitions[prior.id] == updated
    assert view.transitions == ()
    assert view.superseded_cognition_ids == frozenset()
    assert tuple(item.step.relation for item in view.evolution_steps) == ("contradicts",)
    recall_query = "Does Yun like crowded places?"
    recall_before_reopen = loop.recall(recall_query)
    assert recall_before_reopen.status == "resolved"
    assert recall_before_reopen.current_cognition_ids == (prior.id,)
    assert {item.relation for item in recall_before_reopen.provenance} == {
        "support",
        "contradict",
    }

    loop.close()
    with MemoryLoop(tmp_path / "contradiction.sqlite", graph) as reopened:
        restored = reopened.view()
        assert restored.graph.cognitions[prior.id] == updated
        assert restored.transitions == ()
        assert reopened.recall(recall_query) == recall_before_reopen


def test_evolution_and_identity_graph_commit_on_the_same_connection(tmp_path: Path) -> None:
    loop = MemoryLoop(tmp_path / "identity-sync.sqlite", _relationship_graph())
    authority = PersistentIdentityAuthority(loop.connection)
    plan = _plan(
        "event:argument",
        "cog:relationship-strained:initial",
        "event:apology",
        "apology",
        "2026-06-02T09:00:00+08:00",
        "e:apology",
        "cog:relationship-repairing",
        "repairing",
        "responds_to",
    )
    pending = loop.stage_evolution(
        plan,
        (EvidenceRecord("e:apology", "Friend_X apologised."),),
    )

    view = loop.decide(pending.id, pending.result_hash, "accept")
    identity_view = authority.view()
    stored = loop.connection.execute(
        "SELECT memory_revision, memory_snapshot_hash FROM identity_state WHERE singleton = 1"
    ).fetchone()

    assert stored is not None
    assert tuple(stored) == (1, view.snapshot_hash)
    assert {item.id for item in identity_view.graph.events} == {
        "event:argument",
        "event:apology",
    }
    assert {item.id for item in identity_view.graph.cognitions} == {
        "cog:relationship-strained:initial",
        "cog:relationship-repairing",
    }
