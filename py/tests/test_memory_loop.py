"""Real-SQLite contracts for the first persistent MemoWeft memory loop."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import cast

import pytest

from memoweft.llm import ChatMessage
from memoweft.store import open_db, user_version
from memoweft.types import ContentType, EvidenceLink
from memoweft.world import (
    ClaimSpan,
    Entity,
    EventFacet,
    EventParticipant,
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
)
from memoweft.world.loop import (
    CognitionTransitionIntent,
    EvidenceConflictError,
    EvidenceRecord,
    MemoryLoop,
    MemoryLoopError,
    MemoryLoopIntegrityError,
    ProductClaimSlice,
    ReviewStateError,
)
from memoweft.world.identity_store import PersistentIdentityAuthority, ReviewedIdentityBinding
from memoweft.world.model import StructuredClaim
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


def test_memory_loop_can_share_the_v3_main_store_connection() -> None:
    db = open_db(":memory:")
    try:
        loop = MemoryLoop(db, _graph())
        assert loop.connection is db
        assert user_version(db) == 4
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
        legacy.execute("PRAGMA user_version = 0")
    finally:
        legacy.close()

    with MemoryLoop(path, _graph()) as upgraded:
        assert user_version(upgraded.connection) == 4
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
