"""Stage 1 contract tests for create-only world deltas."""
from __future__ import annotations

from dataclasses import asdict, replace
from hashlib import sha256

import pytest

from memoweft.types import EvidenceLink
from memoweft.world import (
    ClaimSpan,
    Entity,
    EventFacet,
    EventParticipant,
    FormationContentBinding,
    FormationSourceTrace,
    FormationTrace,
    MemoryTarget,
    MemoryWorldGraph,
    PersonalWorld,
    Perspective,
    Relationship,
    SemanticUncertainty,
    StructuredClaim,
    UnresolvedReference,
    WorldCognition,
    WorldDelta,
    WorldDeltaValidationError,
    WorldEvent,
)


class _TupleSubclass(tuple[object, ...]):
    """An iterable that must still fail the top-level exact-tuple preflight."""


def _base() -> MemoryWorldGraph:
    owner = Entity("person:example", "world:example", "person", "Casey")
    graph = MemoryWorldGraph(PersonalWorld("world:example", owner.id))
    graph.add_entity(owner)
    return graph


def _source_trace(
    source: EvidenceLink = EvidenceLink("e:nanjing", "support"),
    *,
    decision: str = "exact_user_claim",
    preceding_assistant_turn_id: str | None = None,
    preceding_assistant_content_sha256: str | None = None,
    claim_span: ClaimSpan | None = None,
) -> FormationSourceTrace:
    return FormationSourceTrace(
        evidence_id=source.evidence_id,
        relation=source.relation,
        proposition_origin_proposal="user_stated",
        response_act_proposal="elaborate",
        claim_span=claim_span or ClaimSpan(0, 1, "a" * 64, "b" * 64),
        preceding_assistant_turn_id=preceding_assistant_turn_id,
        preceding_assistant_content_sha256=preceding_assistant_content_sha256,
        local_origin_decision=decision,  # type: ignore[arg-type]
        decision_code="formation.exact_user_claim",
    )


def _formation_trace(
    cognition: WorldCognition,
    *,
    sources: tuple[FormationSourceTrace, ...] | None = None,
    model_inferred_proposal: bool = False,
    derived_formed_by: str = "stated",
    effective_support_count: int = 1,
    content_bindings: tuple[FormationContentBinding, ...] = (),
) -> FormationTrace:
    trace_sources = sources if sources is not None else tuple(_source_trace(source) for source in cognition.sources)
    return FormationTrace(
        cognition_id=cognition.id,
        model_inferred_proposal=model_inferred_proposal,
        sources=trace_sources,
        derived_formed_by=derived_formed_by,  # type: ignore[arg-type]
        raw_support_count=sum(source.relation == "support" for source in trace_sources),
        effective_support_count=effective_support_count,
        contradict_count=sum(source.relation == "contradict" for source in trace_sources),
        content_bindings=content_bindings,
    )


def _content_binding(
    about_entity_id: str,
    *,
    evidence_id: str = "e:nanjing",
    start_codepoint: int = 0,
    end_codepoint: int = 1,
    claim_span: ClaimSpan | None = None,
) -> FormationContentBinding:
    return FormationContentBinding(
        semantic_role="relationship_side",
        about_entity_id=about_entity_id,
        evidence_id=evidence_id,
        claim_span=claim_span or ClaimSpan(start_codepoint, end_codepoint, "a" * 64, "b" * 64),
    )


def _inferred_relationship_delta(
    bindings: tuple[FormationContentBinding, ...] | None = None,
    *,
    cognition_sources: tuple[EvidenceLink, ...] = (EvidenceLink("e:nanjing", "support"),),
    trace_sources: tuple[FormationSourceTrace, ...] | None = None,
) -> WorldDelta:
    template = _nanjing_delta()
    relationship = template.new_relationships[0]
    owner_content = "I need a clear itinerary."
    other_content = "I want room to improvise."
    owner_span = ClaimSpan(0, len(owner_content), "a" * 64, sha256(owner_content.encode("utf-8")).hexdigest())
    other_span = ClaimSpan(
        len(owner_content) + 1,
        len(owner_content) + 1 + len(other_content),
        "a" * 64,
        sha256(other_content.encode("utf-8")).hexdigest(),
    )
    resolved_bindings = bindings if bindings is not None else (
        _content_binding("person:example", claim_span=owner_span),
        _content_binding("person:lin", claim_span=other_span),
    )
    owner_cognition = WorldCognition(
        "cog:owner-side", "world:example", MemoryTarget("entity", "person:example"), owner_content,
        "fact", "stated", 600, "limited", Perspective("entity", ("person:example",)),
        sources=(EvidenceLink("e:nanjing", "support"),),
    )
    other_cognition = WorldCognition(
        "cog:other-side", "world:example", MemoryTarget("entity", "person:lin"), other_content,
        "fact", "stated", 600, "limited", Perspective("entity", ("person:example",)),
        sources=(EvidenceLink("e:nanjing", "support"),),
    )
    cognition = replace(
        template.new_cognitions[0],
        target=MemoryTarget("relationship", relationship.id),
        content=(
            f"owner-side: {owner_content}\n"
            f"other-side: {other_content}\n"
            "relationship inference: scoped contrast/conflict"
        ),
        formed_by="inferred",
        confidence=200,
        cred_status="candidate",
        sources=cognition_sources,
    )
    sources = trace_sources if trace_sources is not None else tuple(
        _source_trace(source, decision="inference_grounding") for source in cognition_sources
    )
    trace = _formation_trace(
        cognition,
        sources=sources,
        model_inferred_proposal=True,
        derived_formed_by="inferred",
        content_bindings=resolved_bindings,
    )
    owner_trace = _formation_trace(
        owner_cognition,
        sources=(replace(_source_trace(), claim_span=owner_span),),
    )
    other_trace = _formation_trace(
        other_cognition,
        sources=(replace(_source_trace(), claim_span=other_span),),
    )
    return replace(
        template,
        source_evidence_ids=tuple(source.evidence_id for source in cognition_sources),
        new_cognitions=(owner_cognition, other_cognition, cognition),
        formation_traces=(owner_trace, other_trace, trace),
    )


def _conflict_relationship_projection_delta(*, event_type: str = "interpersonal_conflict") -> WorldDelta:
    """A complete two-sided conflict that requires a local relationship projection."""
    template = _inferred_relationship_delta()
    scope = "planning"
    event = replace(template.new_events[0], event_type=event_type)
    cognitions = (
        replace(template.new_cognitions[0], scope=scope),
        replace(template.new_cognitions[1], scope=scope),
        replace(
            template.new_cognitions[2],
            content_type="hypothesis",
            perspective=Perspective("system"),
            scope=scope,
        ),
    )
    return replace(template, new_events=(event,), new_cognitions=cognitions)


def _direct_relationship_cognition_delta(template: WorldDelta) -> WorldDelta:
    """Replace the relationship inference with a legal generic direct cognition."""
    relationship_cognition = replace(
        template.new_cognitions[2],
        content="We discussed the relationship directly.",
        content_type="fact",
        formed_by="stated",
        confidence=600,
        cred_status="limited",
        perspective=Perspective("entity", ("person:example",)),
        sources=(EvidenceLink("e:nanjing", "support"),),
    )
    relationship_trace = _formation_trace(relationship_cognition)
    return replace(
        template,
        new_cognitions=(*template.new_cognitions[:2], relationship_cognition),
        formation_traces=(*template.formation_traces[:2], relationship_trace),
    )


def _nanjing_delta() -> WorldDelta:
    friend = Entity("person:lin", "world:example", "person", "Lin")
    relationship = Relationship("relationship:example-lin", "world:example", "person:example", friend.id, "friend")
    event = WorldEvent(
        "event:nanjing", "world:example", "trip", "Casey and Lin visited Nanjing", "2026-05-01",
        participants=(EventParticipant("person:example"), EventParticipant(friend.id)),
        relationship_ids=(relationship.id,), evidence_ids=("e:nanjing",),
    )
    cognition = WorldCognition(
        "cog:nanjing", "world:example", MemoryTarget("event", event.id), "The Nanjing trip was meaningful",
        "fact", "stated", 600, "limited", Perspective("entity", ("person:example",)),
        sources=(EvidenceLink("e:nanjing", "support"),),
    )
    return WorldDelta(
        world_id="world:example",
        source_evidence_ids=("e:nanjing",),
        new_entities=(friend,),
        new_relationships=(relationship,),
        new_events=(event,),
        new_cognitions=(cognition,),
        formation_traces=(_formation_trace(cognition),),
        unresolved_references=(UnresolvedReference("the old restaurant", ("e:nanjing",)),),
        semantic_uncertainties=(SemanticUncertainty("whether the trip was planned", ("e:nanjing",)),),
    )


def _snapshot(
    graph: MemoryWorldGraph,
) -> tuple[dict[str, Entity], dict[str, Relationship], dict[str, WorldEvent], dict[str, WorldCognition]]:
    return (graph.entities.copy(), graph.relationships.copy(), graph.events.copy(), graph.cognitions.copy())


def test_apply_valid_nanjing_delta_builds_new_graph_without_mutating_base() -> None:
    base = _base()
    before = _snapshot(base)

    result = _nanjing_delta().apply_to(base, {"e:nanjing"})

    assert result is not base
    assert set(result.entities) == {"person:example", "person:lin"}
    assert set(result.relationships) == {"relationship:example-lin"}
    assert set(result.events) == {"event:nanjing"}
    assert set(result.cognitions) == {"cog:nanjing"}
    assert _snapshot(base) == before
    assert result.entities is not base.entities


def test_structured_claim_is_optional_hashable_and_serializable_without_changing_legacy_cognition() -> None:
    legacy = _nanjing_delta().new_cognitions[0]
    claim = StructuredClaim("attribute", predicate="hair_style", value="long", polarity="assert")
    structured = replace(legacy, structured_claim=claim)

    assert legacy.structured_claim is None
    assert hash(claim) == hash(StructuredClaim("attribute", predicate="hair_style", value="long", polarity="assert"))
    assert asdict(structured)["structured_claim"] == {
        "statement_kind": "attribute",
        "predicate": "hair_style",
        "value": "long",
        "polarity": "assert",
        "epistemic_status": "asserted",
    }


@pytest.mark.parametrize(
    ("structured_claim", "perspective", "expected_issue"),
    [
        ("not-a-claim", Perspective("entity", ("person:example",)), "cognition[0].structured_claim.invalid_type"),
        (
            StructuredClaim("unknown"),  # type: ignore[arg-type]
            Perspective("entity", ("person:example",)),
            "cognition[0].structured_claim.statement_kind.invalid",
        ),
        (
            StructuredClaim("attribute", predicate=""),
            Perspective("entity", ("person:example",)),
            "cognition[0].structured_claim.predicate.invalid",
        ),
        (
            StructuredClaim("attribute", polarity="maybe"),  # type: ignore[arg-type]
            Perspective("entity", ("person:example",)),
            "cognition[0].structured_claim.polarity.invalid",
        ),
        (
            StructuredClaim("attribute", epistemic_status="model_guess"),  # type: ignore[arg-type]
            Perspective("entity", ("person:example",)),
            "cognition[0].structured_claim.epistemic_status.invalid",
        ),
        (
            StructuredClaim("evaluation", predicate="character", value="unreliable"),
            Perspective("system"),
            "cognition[0].structured_claim.evaluation.perspective.invalid",
        ),
    ],
)
def test_structured_claim_validation_fails_closed_for_malformed_or_perspective_free_evaluation(
    structured_claim: object,
    perspective: Perspective,
    expected_issue: str,
) -> None:
    base = _base()
    before = _snapshot(base)
    cognition = replace(
        _nanjing_delta().new_cognitions[0],
        structured_claim=structured_claim,  # type: ignore[arg-type]
        perspective=perspective,
    )
    delta = replace(_nanjing_delta(), new_cognitions=(cognition,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.apply_to(base, {"e:nanjing"})

    assert expected_issue in raised.value.issues
    assert _snapshot(base) == before


def test_structured_evaluation_with_owner_perspective_remains_valid() -> None:
    cognition = replace(
        _nanjing_delta().new_cognitions[0],
        target=MemoryTarget("entity", "person:lin"),
        structured_claim=StructuredClaim("evaluation", predicate="character", value="kind"),
    )
    delta = replace(_nanjing_delta(), new_cognitions=(cognition,))

    result = delta.apply_to(_base(), {"e:nanjing"})

    assert result.cognitions[cognition.id].structured_claim == cognition.structured_claim


def test_rejected_delta_never_calls_apply_or_changes_base(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _base()
    before = _snapshot(base)
    called = False

    def forbid_add(_: Entity) -> None:
        nonlocal called
        called = True
        raise AssertionError("apply must not begin for an invalid delta")

    monkeypatch.setattr(MemoryWorldGraph, "add_entity", forbid_add)
    invalid = replace(_nanjing_delta(), new_events=(replace(_nanjing_delta().new_events[0], evidence_ids=("e:forbidden",)),))

    with pytest.raises(WorldDeltaValidationError) as raised:
        invalid.apply_to(base, {"e:nanjing"})

    assert "event[0].evidence_ids[0].not_eligible" in raised.value.issues
    assert not called
    assert _snapshot(base) == before


@pytest.mark.parametrize(
    "field_name",
    [
        "source_evidence_ids",
        "new_entities",
        "new_relationships",
        "new_events",
        "new_cognitions",
        "formation_traces",
        "unresolved_references",
        "semantic_uncertainties",
    ],
)
@pytest.mark.parametrize("invalid_value", [None, [], _TupleSubclass()])
def test_top_level_collections_fail_closed_before_any_cross_record_work(
    field_name: str, invalid_value: object
) -> None:
    delta = replace(_nanjing_delta(), **{field_name: invalid_value})  # type: ignore[arg-type]
    base = _base()
    before = _snapshot(base)
    expected_issue = f"{field_name}.not_tuple"

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    assert validated.value.issues == (expected_issue,)
    assert applied.value.issues == (expected_issue,)
    assert _snapshot(base) == before


@pytest.mark.parametrize(
    ("delta", "expected_issue"),
    [
        (replace(_nanjing_delta(), source_evidence_ids=("e:invented",)), "source_evidence_ids[0].not_eligible"),
        (replace(_nanjing_delta(), new_relationships=(Relationship("rel:bad", "world:example", "person:example", "person:missing", "friend"),)), "relationship[0].target_entity_id.dangling"),
        (replace(_nanjing_delta(), new_entities=(Entity("person:example", "world:example", "person", "Duplicate"),)), "entity[0].id.conflicts_with_base"),
        (replace(_nanjing_delta(), new_cognitions=(replace(_nanjing_delta().new_cognitions[0], confidence=True),)), "cognition[0].confidence.invalid"),
    ],
)
def test_invalid_deltas_fail_closed_without_partial_writes(delta: WorldDelta, expected_issue: str) -> None:
    base = _base()
    before = _snapshot(base)

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.apply_to(base, {"e:nanjing"})

    assert expected_issue in raised.value.issues
    assert _snapshot(base) == before


def test_runtime_literal_and_evidence_relation_are_validated_without_partial_writes() -> None:
    base = _base()
    before = _snapshot(base)
    bad_cognition = replace(
        _nanjing_delta().new_cognitions[0],
        content_type="not-a-content-type",  # type: ignore[arg-type]
        perspective=Perspective("entity", ("person:example",)),
        sources=(EvidenceLink("e:nanjing", "not-a-relation"),),  # type: ignore[arg-type]
    )
    delta = replace(_nanjing_delta(), new_cognitions=(bad_cognition,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.apply_to(base, {"e:nanjing"})

    assert "cognition[0].content_type.invalid" in raised.value.issues
    assert "cognition[0].sources[0].relation.invalid" in raised.value.issues
    assert _snapshot(base) == before


def test_allowlist_and_base_owner_are_independently_validated() -> None:
    delta = _nanjing_delta()
    owner_missing = MemoryWorldGraph(PersonalWorld("world:example", "person:missing"))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(owner_missing, ("e:nanjing", 7, ""))  # type: ignore[arg-type]

    assert "eligible_evidence_ids[1].invalid" in raised.value.issues
    assert "eligible_evidence_ids[2].invalid" in raised.value.issues
    assert "base.owner_entity_id.dangling" in raised.value.issues


@pytest.mark.parametrize(
    ("delta", "expected_issue"),
    [
        (replace(_nanjing_delta(), source_evidence_ids=()), "source_evidence_ids.empty"),
        (replace(_nanjing_delta(), new_events=(replace(_nanjing_delta().new_events[0], evidence_ids=()),)), "event[0].evidence_ids.empty"),
        (replace(_nanjing_delta(), new_cognitions=(replace(_nanjing_delta().new_cognitions[0], sources=()),)), "cognition[0].sources.empty"),
        (replace(_nanjing_delta(), unresolved_references=(UnresolvedReference("old restaurant", ()),)), "unresolved_reference[0].evidence_ids.empty"),
        (replace(_nanjing_delta(), semantic_uncertainties=(SemanticUncertainty("timing", ()),)), "semantic_uncertainty[0].evidence_ids.empty"),
    ],
)
def test_required_provenance_never_accepts_an_empty_collection(delta: WorldDelta, expected_issue: str) -> None:
    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert expected_issue in raised.value.issues


def test_top_level_provenance_rejects_repeated_evidence_ids() -> None:
    delta = replace(_nanjing_delta(), source_evidence_ids=("e:nanjing", "e:nanjing"))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "source_evidence_ids[1].duplicate" in raised.value.issues


@pytest.mark.parametrize(
    ("sources", "expected_issue"),
    [
        (
            (
                EvidenceLink("e:nanjing", "support"),
                EvidenceLink("e:nanjing", "support"),
            ),
            "cognition[0].sources[1].evidence_id.duplicate",
        ),
        (
            (
                EvidenceLink("e:nanjing", "support"),
                EvidenceLink("e:nanjing", "contradict"),
            ),
            "cognition[0].sources[1].evidence_id.duplicate",
        ),
        ((EvidenceLink("e:nanjing", "contradict"),), "cognition[0].sources.support.empty"),
    ],
)
def test_cognition_provenance_rejects_ambiguous_or_non_supporting_sources(
    sources: tuple[EvidenceLink, ...], expected_issue: str
) -> None:
    cognition = replace(_nanjing_delta().new_cognitions[0], sources=sources)
    delta = replace(_nanjing_delta(), new_cognitions=(cognition,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert expected_issue in raised.value.issues


def test_cognition_provenance_rejects_more_than_four_distinct_sources() -> None:
    evidence_ids = tuple(f"e:source-{index}" for index in range(5))
    cognition = replace(
        _nanjing_delta().new_cognitions[0],
        sources=tuple(EvidenceLink(evidence_id, "support") for evidence_id in evidence_ids),
    )
    delta = replace(_nanjing_delta(), new_cognitions=(cognition,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing", *evidence_ids})

    assert "cognition[0].sources.too_many" in raised.value.issues


def test_runtime_record_shape_fields_fail_closed_before_apply() -> None:
    base = _base()
    before = _snapshot(base)
    template = _nanjing_delta()
    event = replace(
        template.new_events[0],
        event_type=1,  # type: ignore[arg-type]
        summary=None,  # type: ignore[arg-type]
        occurred_at=object(),  # type: ignore[arg-type]
        participants=(EventParticipant("person:example", role=7),),  # type: ignore[arg-type]
        facets=(EventFacet(7, None),),  # type: ignore[arg-type]
    )
    cognition = replace(
        template.new_cognitions[0],
        content=9,  # type: ignore[arg-type]
        scope=7,  # type: ignore[arg-type]
        valid_at=7,  # type: ignore[arg-type]
        invalid_at=7,  # type: ignore[arg-type]
        sources=[EvidenceLink("e:nanjing", "support")],  # type: ignore[arg-type]
    )
    delta = replace(
        template,
        new_entities=(Entity("person:lin", "world:example", 7, None, aliases=[]),),  # type: ignore[arg-type]
        new_relationships=(Relationship("relationship:example-lin", "world:example", "person:example", "person:lin", 7),),  # type: ignore[arg-type]
        new_events=(event,),
        new_cognitions=(cognition,),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.apply_to(base, {"e:nanjing"})

    assert {
        "entity[0].kind.invalid",
        "entity[0].canonical_name.invalid",
        "entity[0].aliases.not_tuple",
        "relationship[0].relation_type.invalid",
        "event[0].event_type.invalid",
        "event[0].summary.invalid",
        "event[0].occurred_at.invalid",
        "event[0].participants[0].role.invalid",
        "event[0].facets[0].key.invalid",
        "event[0].facets[0].value.invalid",
        "cognition[0].content.invalid",
        "cognition[0].scope.invalid",
        "cognition[0].valid_at.invalid",
        "cognition[0].invalid_at.invalid",
        "cognition[0].sources.not_tuple",
        "cognition[0].sources.empty",
    }.issubset(raised.value.issues)
    assert _snapshot(base) == before


def test_object_namespaces_cannot_be_collapsed_into_entities() -> None:
    base = _base()
    collapsed = WorldDelta(
        world_id="world:example",
        source_evidence_ids=("e:nanjing",),
        new_entities=(Entity("event:nanjing-conflict", "world:example", "event", "Conflict"),),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        collapsed.apply_to(base, {"e:nanjing"})

    assert "entity[0].kind.not_entity" in raised.value.issues
    assert "entity[0].id.namespace" in raised.value.issues
    assert "event:nanjing-conflict" not in base.entities


def test_formation_trace_is_keyword_only_without_rebinding_existing_issue_fields() -> None:
    unresolved = (UnresolvedReference("朋友", ("e:nanjing",)),)
    uncertain = (SemanticUncertainty("身份未解析", ("e:nanjing",)),)

    delta = WorldDelta("world:example", ("e:nanjing",), (), (), (), (), unresolved, uncertain)

    assert delta.formation_traces == ()
    assert delta.unresolved_references == unresolved
    assert delta.semantic_uncertainties == uncertain


def test_formation_trace_content_bindings_is_keyword_only_with_empty_default() -> None:
    trace = FormationTrace("cog:legacy", False, (), "stated", 0, 0, 0)

    assert trace.content_bindings == ()


@pytest.mark.parametrize(
    ("formation_traces", "expected_issue"),
    [
        ((), "cognition[0].formation_trace.missing"),
        (
            (
                _formation_trace(_nanjing_delta().new_cognitions[0]),
                replace(_formation_trace(_nanjing_delta().new_cognitions[0]), cognition_id="cog:extra"),
            ),
            "formation_trace[1].cognition_id.not_new",
        ),
        (
            (
                _formation_trace(_nanjing_delta().new_cognitions[0]),
                _formation_trace(_nanjing_delta().new_cognitions[0]),
            ),
            "formation_trace[1].cognition_id.duplicate",
        ),
    ],
)
def test_formation_trace_is_a_required_one_to_one_sidecar(
    formation_traces: tuple[FormationTrace, ...], expected_issue: str
) -> None:
    delta = replace(_nanjing_delta(), formation_traces=formation_traces)

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert expected_issue in raised.value.issues


def test_formation_trace_sources_must_match_cognition_sources_in_order() -> None:
    cognition = _nanjing_delta().new_cognitions[0]
    trace = _formation_trace(cognition, sources=(replace(_source_trace(), evidence_id="e:other"),))
    delta = replace(_nanjing_delta(), formation_traces=(trace,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing", "e:other"})

    assert "formation_trace[0].sources[0].evidence_id.mismatch" in raised.value.issues


def test_formation_trace_rejects_bad_span_and_hashes() -> None:
    cognition = _nanjing_delta().new_cognitions[0]
    bad_span = ClaimSpan(-1, -1, "A" * 64, "short")
    trace = _formation_trace(cognition, sources=(replace(_source_trace(), claim_span=bad_span),))
    delta = replace(_nanjing_delta(), formation_traces=(trace,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert {
        "formation_trace[0].sources[0].claim_span.range.invalid",
        "formation_trace[0].sources[0].claim_span.source_content_sha256.invalid",
        "formation_trace[0].sources[0].claim_span.claim_sha256.invalid",
    }.issubset(raised.value.issues)


def test_formation_trace_rejects_fake_preceding_assistant_context() -> None:
    cognition = _nanjing_delta().new_cognitions[0]
    source = _source_trace(
        decision="assistant_confirmation",
        preceding_assistant_turn_id="",
        preceding_assistant_content_sha256="z" * 64,
    )
    trace = _formation_trace(cognition, sources=(source,), derived_formed_by="confirmed")
    delta = replace(
        _nanjing_delta(),
        new_cognitions=(replace(cognition, formed_by="confirmed", confidence=400, cred_status="limited"),),
        formation_traces=(trace,),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert {
        "formation_trace[0].sources[0].preceding_assistant_turn_id.invalid",
        "formation_trace[0].sources[0].preceding_assistant_content_sha256.invalid",
    }.issubset(raised.value.issues)


def test_non_inferred_formation_trace_cannot_promote_unverified_support() -> None:
    cognition = _nanjing_delta().new_cognitions[0]
    trace = _formation_trace(cognition, sources=(_source_trace(decision="unverified"),))
    delta = replace(_nanjing_delta(), formation_traces=(trace,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "formation_trace[0].derived_formed_by.unverified_support" in raised.value.issues


def test_non_inferred_formation_trace_cannot_promote_user_negation_carrier() -> None:
    cognition = _nanjing_delta().new_cognitions[0]
    source = _source_trace(
        decision="user_negation",
        preceding_assistant_turn_id="turn:assistant-previous",
        preceding_assistant_content_sha256="c" * 64,
    )
    trace = _formation_trace(cognition, sources=(source,))
    delta = replace(_nanjing_delta(), formation_traces=(trace,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "formation_trace[0].derived_formed_by.unverified_support" in raised.value.issues


def test_formation_trace_derived_formed_by_must_follow_proposal_and_cognition() -> None:
    cognition = _nanjing_delta().new_cognitions[0]
    trace = _formation_trace(
        cognition,
        sources=(_source_trace(decision="inference_grounding"),),
        model_inferred_proposal=True,
        derived_formed_by="stated",
    )
    delta = replace(_nanjing_delta(), formation_traces=(trace,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "formation_trace[0].derived_formed_by.mismatch" in raised.value.issues


def test_inferred_formation_requires_inference_grounding_support() -> None:
    cognition = replace(
        _nanjing_delta().new_cognitions[0],
        formed_by="inferred",
        confidence=200,
        cred_status="candidate",
    )
    trace = _formation_trace(
        cognition,
        sources=(_source_trace(decision="exact_user_claim"),),
        model_inferred_proposal=True,
        derived_formed_by="inferred",
    )
    delta = replace(_nanjing_delta(), new_cognitions=(cognition,), formation_traces=(trace,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "formation_trace[0].sources[0].inferred_support.invalid_decision" in raised.value.issues


def test_non_inferred_formation_rejects_inference_grounding_support() -> None:
    cognition = _nanjing_delta().new_cognitions[0]
    trace = _formation_trace(cognition, sources=(_source_trace(decision="inference_grounding"),))
    delta = replace(_nanjing_delta(), formation_traces=(trace,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "formation_trace[0].sources[0].non_inferred_support.invalid_decision" in raised.value.issues


def test_non_inferred_formation_rejects_mixed_direct_and_confirmation_support() -> None:
    sources = (
        _source_trace(),
        _source_trace(
            EvidenceLink("e:confirm", "support"),
            decision="assistant_confirmation",
            preceding_assistant_turn_id="turn:assistant",
            preceding_assistant_content_sha256="c" * 64,
        ),
    )
    cognition = replace(
        _nanjing_delta().new_cognitions[0],
        sources=tuple(EvidenceLink(source.evidence_id, source.relation) for source in sources),
    )
    trace = _formation_trace(cognition, sources=sources)
    delta = replace(
        _nanjing_delta(),
        source_evidence_ids=("e:nanjing", "e:confirm"),
        new_cognitions=(cognition,),
        formation_traces=(trace,),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing", "e:confirm"})

    assert "formation_trace[0].support_decisions.mixed" in raised.value.issues


@pytest.mark.parametrize(
    ("decision", "preceding_id", "preceding_hash", "expected_issue"),
    [
        ("unverified", None, None, "formation_trace[0].sources[1].contradict.unverified"),
        (
            "assistant_confirmation",
            "turn:assistant",
            "c" * 64,
            "formation_trace[0].sources[1].contradict.assistant_confirmation",
        ),
    ],
)
def test_formation_trace_rejects_untrusted_contradict_decisions(
    decision: str,
    preceding_id: str | None,
    preceding_hash: str | None,
    expected_issue: str,
) -> None:
    sources = (
        _source_trace(decision="inference_grounding"),
        _source_trace(
            EvidenceLink("e:contradict", "contradict"),
            decision=decision,
            preceding_assistant_turn_id=preceding_id,
            preceding_assistant_content_sha256=preceding_hash,
        ),
    )
    cognition = replace(
        _nanjing_delta().new_cognitions[0],
        formed_by="inferred",
        confidence=80,
        cred_status="conflicted",
        sources=tuple(EvidenceLink(source.evidence_id, source.relation) for source in sources),
    )
    trace = _formation_trace(
        cognition,
        sources=sources,
        model_inferred_proposal=True,
        derived_formed_by="inferred",
    )
    delta = replace(
        _nanjing_delta(),
        source_evidence_ids=("e:nanjing", "e:contradict"),
        new_cognitions=(cognition,),
        formation_traces=(trace,),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing", "e:contradict"})

    assert expected_issue in raised.value.issues


def test_inferred_formation_allows_multiple_groundings_and_contextual_negation_contradict() -> None:
    sources = (
        _source_trace(decision="inference_grounding"),
        _source_trace(EvidenceLink("e:second", "support"), decision="inference_grounding"),
        _source_trace(
            EvidenceLink("e:contradict", "contradict"),
            decision="user_negation",
            preceding_assistant_turn_id="turn:assistant",
            preceding_assistant_content_sha256="c" * 64,
        ),
    )
    cognition = replace(
        _nanjing_delta().new_cognitions[0],
        formed_by="inferred",
        confidence=80,
        cred_status="conflicted",
        sources=tuple(EvidenceLink(source.evidence_id, source.relation) for source in sources),
    )
    trace = _formation_trace(
        cognition,
        sources=sources,
        model_inferred_proposal=True,
        derived_formed_by="inferred",
    )
    delta = replace(
        _nanjing_delta(),
        source_evidence_ids=("e:nanjing", "e:second", "e:contradict"),
        new_cognitions=(cognition,),
        formation_traces=(trace,),
    )

    delta.validate_against(_base(), {"e:nanjing", "e:second", "e:contradict"})


def test_four_supports_have_one_effective_support_and_do_not_reach_stable() -> None:
    evidence_ids = tuple(f"e:source-{index}" for index in range(4))
    cognition = replace(
        _nanjing_delta().new_cognitions[0],
        sources=tuple(EvidenceLink(evidence_id, "support") for evidence_id in evidence_ids),
        confidence=600,
        cred_status="limited",
    )
    trace = _formation_trace(cognition)
    delta = replace(
        _nanjing_delta(),
        source_evidence_ids=("e:nanjing", *evidence_ids),
        new_cognitions=(cognition,),
        formation_traces=(trace,),
    )

    delta.validate_against(_base(), {"e:nanjing", *evidence_ids})

    inflated = replace(
        delta,
        new_cognitions=(replace(cognition, confidence=720, cred_status="stable"),),
    )
    with pytest.raises(WorldDeltaValidationError) as raised:
        inflated.validate_against(_base(), {"e:nanjing", *evidence_ids})

    assert {
        "formation_trace[0].confidence.mismatch",
        "formation_trace[0].cred_status.mismatch",
    }.issubset(raised.value.issues)


def test_apply_to_validates_but_does_not_store_formation_trace_on_graph() -> None:
    result = _nanjing_delta().apply_to(_base(), {"e:nanjing"})

    assert not hasattr(result, "formation_traces")


def test_inferred_relationship_uses_two_direct_sides_without_inflating_support() -> None:
    delta = _inferred_relationship_delta()

    delta.validate_against(_base(), {"e:nanjing"})

    trace = delta.formation_traces[2]
    assert trace.raw_support_count == 1
    assert trace.effective_support_count == 1
    assert delta.new_cognitions[2].confidence == 200
    assert delta.new_cognitions[2].cred_status == "candidate"
    assert delta.new_cognitions[2].content == (
        "owner-side: I need a clear itinerary.\n"
        "other-side: I want room to improvise.\n"
        "relationship inference: scoped contrast/conflict"
    )


def test_conflict_relationship_projection_passes_and_applies() -> None:
    delta = _conflict_relationship_projection_delta()
    base = _base()

    delta.validate_against(base, {"e:nanjing"})
    result = delta.apply_to(base, {"e:nanjing"})

    assert set(result.cognitions) == {"cog:owner-side", "cog:other-side", "cog:nanjing"}


def test_conflict_relationship_projection_rejects_wrong_direct_only_target() -> None:
    delta = _direct_relationship_cognition_delta(_conflict_relationship_projection_delta())

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "event[0].conflict_relationship_projection.target_cognition.missing" in raised.value.issues


def test_conflict_relationship_projection_rejects_missing_target_in_validate_and_apply() -> None:
    template = _conflict_relationship_projection_delta()
    delta = replace(template, new_cognitions=template.new_cognitions[:2], formation_traces=template.formation_traces[:2])
    base = _base()

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    expected_issue = "event[0].conflict_relationship_projection.target_cognition.missing"
    assert expected_issue in validated.value.issues
    assert expected_issue in applied.value.issues


def test_conflict_relationship_projection_allows_correct_projection_plus_direct_relationship_cognition() -> None:
    template = _conflict_relationship_projection_delta()
    direct_template = _direct_relationship_cognition_delta(template)
    cognition = replace(direct_template.new_cognitions[2], id="cog:relationship-direct-extra")
    trace = replace(direct_template.formation_traces[2], cognition_id=cognition.id)
    delta = replace(
        template,
        new_cognitions=(*template.new_cognitions, cognition),
        formation_traces=(*template.formation_traces, trace),
    )

    delta.validate_against(_base(), {"e:nanjing"})
    result = delta.apply_to(_base(), {"e:nanjing"})

    assert set(result.cognitions) == {
        "cog:owner-side",
        "cog:other-side",
        "cog:nanjing",
        "cog:relationship-direct-extra",
    }


def test_conflict_relationship_projection_rejects_two_qualifying_projections() -> None:
    template = _conflict_relationship_projection_delta()
    cognition = replace(template.new_cognitions[2], id="cog:relationship-extra")
    trace = replace(template.formation_traces[2], cognition_id=cognition.id)
    delta = replace(
        template,
        new_cognitions=(*template.new_cognitions, cognition),
        formation_traces=(*template.formation_traces, trace),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "event[0].conflict_relationship_projection.target_cognition.count.invalid" in raised.value.issues


@pytest.mark.parametrize("extra_scope", [None, "budget"])
def test_conflict_relationship_projection_rejects_a_second_inferred_proposal_with_invalid_scope(
    extra_scope: str | None,
) -> None:
    template = _conflict_relationship_projection_delta()
    cognition = replace(template.new_cognitions[2], id="cog:relationship-inferred-extra", scope=extra_scope)
    trace = replace(template.formation_traces[2], cognition_id=cognition.id)
    delta = replace(
        template,
        new_cognitions=(*template.new_cognitions, cognition),
        formation_traces=(*template.formation_traces, trace),
    )
    base = _base()

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    expected_issue = "event[0].conflict_relationship_projection.target_cognition.count.invalid"
    assert expected_issue in validated.value.issues
    assert expected_issue in applied.value.issues


def test_conflict_relationship_projection_rejects_two_wrong_targets() -> None:
    template = _direct_relationship_cognition_delta(_conflict_relationship_projection_delta())
    cognition = replace(template.new_cognitions[2], id="cog:relationship-wrong-extra")
    trace = replace(template.formation_traces[2], cognition_id=cognition.id)
    delta = replace(
        template,
        new_cognitions=(*template.new_cognitions, cognition),
        formation_traces=(*template.formation_traces, trace),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "event[0].conflict_relationship_projection.target_cognition.missing" in raised.value.issues


def test_conflict_relationship_projection_requires_independent_direct_evidence_in_validate_and_apply() -> None:
    template = _conflict_relationship_projection_delta()
    first_source = template.formation_traces[0].sources[0]
    second_trace = replace(template.formation_traces[1], sources=(replace(template.formation_traces[1].sources[0], claim_span=first_source.claim_span),))
    delta = replace(template, formation_traces=(template.formation_traces[0], second_trace, template.formation_traces[2]))
    base = _base()

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    expected_issue = "event[0].conflict_relationship_projection.direct_evidence.independent.required"
    assert expected_issue in validated.value.issues
    assert expected_issue in applied.value.issues


@pytest.mark.parametrize("event_type", ["interpersonal conflict", "人际冲突", "争执", "吵架"])
def test_conflict_event_type_aliases_reject_only_wrong_direct_target(event_type: str) -> None:
    delta = _direct_relationship_cognition_delta(_conflict_relationship_projection_delta(event_type=event_type))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "event[0].conflict_relationship_projection.target_cognition.missing" in raised.value.issues


@pytest.mark.parametrize("event_type", ["INTERPERSONAL---CONFLICT", "人际冲突", "争执", "吵架"])
def test_conflict_event_type_aliases_accept_a_qualified_projection(event_type: str) -> None:
    delta = _conflict_relationship_projection_delta(event_type=event_type)

    delta.validate_against(_base(), {"e:nanjing"})


@pytest.mark.parametrize(
    ("scopes", "expected_issue"),
    [
        ((None, "planning"), "event[0].conflict_relationship_projection.direct_scope.required"),
        (("planning", "budget"), "event[0].conflict_relationship_projection.direct_scope.mismatch"),
    ],
)
def test_conflict_relationship_projection_rejects_missing_or_mismatched_direct_scope(
    scopes: tuple[str | None, str | None], expected_issue: str
) -> None:
    template = _conflict_relationship_projection_delta()
    delta = replace(
        template,
        new_cognitions=(
            replace(template.new_cognitions[0], scope=scopes[0]),
            replace(template.new_cognitions[1], scope=scopes[1]),
            template.new_cognitions[2],
        ),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert expected_issue in raised.value.issues


def test_conflict_relationship_projection_rejects_inference_targeting_an_unlinked_relationship() -> None:
    template = _conflict_relationship_projection_delta()
    unlinked = Relationship(
        "relationship:example-lin-unlinked", "world:example", "person:example", "person:lin", "friend"
    )
    cognition = replace(template.new_cognitions[2], target=MemoryTarget("relationship", unlinked.id))
    delta = replace(template, new_relationships=(*template.new_relationships, unlinked), new_cognitions=(*template.new_cognitions[:2], cognition))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "event[0].conflict_relationship_projection.target_cognition.missing" in raised.value.issues


def test_non_conflict_event_keeps_a_direct_relationship_cognition_legal() -> None:
    delta = _direct_relationship_cognition_delta(_inferred_relationship_delta())

    delta.validate_against(_base(), {"e:nanjing"})


def test_conflict_with_one_missing_endpoint_direct_does_not_trigger_projection() -> None:
    template = _direct_relationship_cognition_delta(_conflict_relationship_projection_delta())
    delta = replace(
        template,
        new_cognitions=(template.new_cognitions[0], template.new_cognitions[2]),
        formation_traces=(template.formation_traces[0], template.formation_traces[2]),
    )

    delta.validate_against(_base(), {"e:nanjing"})


@pytest.mark.parametrize(
    ("bindings", "cognition_sources", "trace_sources", "expected_issue"),
    [
        (("not-a-binding",), (EvidenceLink("e:nanjing", "support"),), None, "formation_trace[2].content_bindings[0].invalid_type"),
        ((replace(_content_binding("person:example"), semantic_role="other"),), (EvidenceLink("e:nanjing", "support"),), None, "formation_trace[2].content_bindings[0].semantic_role.invalid"),  # type: ignore[arg-type]
        ((_content_binding(""),), (EvidenceLink("e:nanjing", "support"),), None, "formation_trace[2].content_bindings[0].about_entity_id.invalid"),
        ((replace(_content_binding("person:example"), evidence_id=""),), (EvidenceLink("e:nanjing", "support"),), None, "formation_trace[2].content_bindings[0].evidence_id.invalid"),
        ((replace(_content_binding("person:example"), claim_span=ClaimSpan(1, 1, "a" * 64, "b" * 64)),), (EvidenceLink("e:nanjing", "support"),), None, "formation_trace[2].content_bindings[0].claim_span.range.invalid"),
        ((_content_binding("person:example"), _content_binding("person:example", start_codepoint=2, end_codepoint=3)), (EvidenceLink("e:nanjing", "support"),), None, "formation_trace[2].content_bindings[1].about_entity_id.duplicate"),
        ((_content_binding("person:example"), _content_binding("person:lin")), (EvidenceLink("e:nanjing", "support"),), None, "formation_trace[2].content_bindings[1].claim_span.duplicate"),
        ((_content_binding("person:example", evidence_id="e:other"),), (EvidenceLink("e:nanjing", "support"),), None, "formation_trace[2].content_bindings[0].evidence_id.not_cognition_support"),
        ((_content_binding("person:example"),), (EvidenceLink("e:nanjing", "support"),), (_source_trace(EvidenceLink("e:other", "support"), decision="inference_grounding"),), "formation_trace[2].content_bindings[0].evidence_id.not_formation_support"),
    ],
)
def test_content_binding_validator_rejects_tampering_and_non_supporting_evidence(
    bindings: tuple[FormationContentBinding, ...],
    cognition_sources: tuple[EvidenceLink, ...],
    trace_sources: tuple[FormationSourceTrace, ...] | None,
    expected_issue: str,
) -> None:
    delta = _inferred_relationship_delta(bindings, cognition_sources=cognition_sources, trace_sources=trace_sources)

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing", "e:other"})

    assert expected_issue in raised.value.issues


@pytest.mark.parametrize(
    ("bindings", "expected_issue"),
    [
        ((), "formation_trace[2].content_bindings.count.invalid"),
        ((_content_binding("person:example"),), "formation_trace[2].content_bindings.count.invalid"),
        (
            (
                _content_binding("person:example"),
                _content_binding("person:lin", start_codepoint=2, end_codepoint=3),
                _content_binding("person:extra", start_codepoint=4, end_codepoint=5),
            ),
            "formation_trace[2].content_bindings.count.invalid",
        ),
    ],
)
def test_inferred_relationship_requires_exactly_two_content_bindings(
    bindings: tuple[FormationContentBinding, ...], expected_issue: str
) -> None:
    delta = _inferred_relationship_delta(bindings)

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert expected_issue in raised.value.issues


def test_content_bindings_remain_limited_to_inferred_relationship_cognitions() -> None:
    template = _nanjing_delta()
    trace = replace(template.formation_traces[0], content_bindings=(_content_binding("person:example"),))
    delta = replace(template, formation_traces=(trace,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert {
        "formation_trace[0].content_bindings.model_inferred_proposal.required",
        "formation_trace[0].content_bindings.target.relationship.required",
    }.issubset(raised.value.issues)


def test_inferred_relationship_bindings_must_cover_exactly_its_endpoints_with_distinct_spans() -> None:
    template = _inferred_relationship_delta()
    bindings = template.formation_traces[2].content_bindings
    delta = replace(
        template,
        formation_traces=(
            template.formation_traces[0],
            template.formation_traces[1],
            replace(
                template.formation_traces[2],
                content_bindings=(
                    replace(bindings[0], about_entity_id="person:other"),
                    replace(bindings[1], claim_span=bindings[0].claim_span),
                ),
            ),
        ),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert {
        "formation_trace[2].content_bindings.endpoints.mismatch",
        "formation_trace[2].content_bindings.claim_span.same_identity",
    }.issubset(raised.value.issues)


def test_inferred_relationship_requires_one_matching_direct_cognition_per_binding() -> None:
    template = _inferred_relationship_delta()
    duplicate = replace(template.new_cognitions[0], id="cog:owner-side-duplicate")
    duplicate_trace = replace(template.formation_traces[0], cognition_id=duplicate.id)
    delta = replace(
        template,
        new_cognitions=(*template.new_cognitions, duplicate),
        formation_traces=(*template.formation_traces, duplicate_trace),
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "formation_trace[2].content_bindings[0].direct_match.count.invalid" in raised.value.issues


@pytest.mark.parametrize(
    "mutation",
    ["wrong_perspective", "wrong_content"],
)
def test_inferred_relationship_rejects_non_direct_or_tampered_side_cognition(mutation: str) -> None:
    template = _inferred_relationship_delta()
    owner = template.new_cognitions[0]
    if mutation == "wrong_perspective":
        owner = replace(owner, perspective=Perspective("system"))
    else:
        owner = replace(owner, content="Tampered side")
    delta = replace(template, new_cognitions=(owner, *template.new_cognitions[1:]))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "formation_trace[2].content_bindings[0].direct_match.count.invalid" in raised.value.issues


def test_inferred_relationship_rejects_tampered_projection() -> None:
    template = _inferred_relationship_delta()
    inference = replace(template.new_cognitions[2], content="model supplied relationship prose")
    delta = replace(template, new_cognitions=(*template.new_cognitions[:2], inference))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "formation_trace[2].content.projection.mismatch" in raised.value.issues


def test_inferred_relationship_rejects_owner_outside_relationship() -> None:
    template = _inferred_relationship_delta()
    other = Entity("person:mei", "world:example", "person", "Mei")
    relationship = replace(template.new_relationships[0], source_entity_id="person:lin", target_entity_id=other.id)
    delta = replace(template, new_entities=(*template.new_entities, other), new_relationships=(relationship,))

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing"})

    assert "formation_trace[2].content_bindings.owner.not_endpoint" in raised.value.issues


def test_inferred_relationship_self_loop_is_rejected_without_raw_exception_or_apply() -> None:
    template = _inferred_relationship_delta()
    bindings = template.formation_traces[2].content_bindings
    relationship = replace(template.new_relationships[0], source_entity_id="person:example", target_entity_id="person:example")
    trace = replace(
        template.formation_traces[2],
        content_bindings=(bindings[0], replace(bindings[1], about_entity_id="person:example")),
    )
    delta = replace(template, new_relationships=(relationship,), formation_traces=(*template.formation_traces[:2], trace))
    base = _base()
    before = _snapshot(base)

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    expected_issue = "formation_trace[2].content_bindings.relationship.endpoints.not_distinct"
    assert expected_issue in validated.value.issues
    assert expected_issue in applied.value.issues
    assert all("person:example" not in issue and "raw" not in issue for issue in validated.value.issues)
    assert _snapshot(base) == before


def test_inferred_relationship_grounding_span_cannot_reuse_a_direct_side_span() -> None:
    template = _inferred_relationship_delta()
    inference_trace = template.formation_traces[2]
    reused_source = replace(inference_trace.sources[0], claim_span=template.formation_traces[0].sources[0].claim_span)
    delta = replace(template, formation_traces=(*template.formation_traces[:2], replace(inference_trace, sources=(reused_source,))))
    base = _base()
    before = _snapshot(base)

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    expected_issue = "formation_trace[2].content_bindings.inference_support.claim_span.identity.reused"
    assert expected_issue in validated.value.issues
    assert expected_issue in applied.value.issues
    assert all("person:example" not in issue and "raw" not in issue for issue in validated.value.issues)
    assert _snapshot(base) == before


def test_inferred_relationship_requires_exactly_one_inference_grounding_support() -> None:
    sources = (
        _source_trace(EvidenceLink("e:nanjing", "support"), decision="inference_grounding"),
        _source_trace(EvidenceLink("e:other", "support"), decision="inference_grounding"),
    )
    delta = _inferred_relationship_delta(
        cognition_sources=(EvidenceLink("e:nanjing", "support"), EvidenceLink("e:other", "support")),
        trace_sources=sources,
    )

    with pytest.raises(WorldDeltaValidationError) as raised:
        delta.validate_against(_base(), {"e:nanjing", "e:other"})

    assert "formation_trace[2].content_bindings.inference_support.invalid" in raised.value.issues


@pytest.mark.parametrize(
    ("binding", "expected_issue"),
    [
        (replace(_inferred_relationship_delta().formation_traces[2].content_bindings[0], claim_span="bad"), "formation_trace[2].content_bindings[0].claim_span.invalid_type"),  # type: ignore[arg-type]
        (replace(_inferred_relationship_delta().formation_traces[2].content_bindings[0], claim_span=replace(_inferred_relationship_delta().formation_traces[2].content_bindings[0].claim_span, start_codepoint=[])), "formation_trace[2].content_bindings[0].claim_span.range.invalid"),  # type: ignore[arg-type]
        (replace(_inferred_relationship_delta().formation_traces[2].content_bindings[0], about_entity_id=["x"]), "formation_trace[2].content_bindings[0].about_entity_id.invalid"),  # type: ignore[arg-type]
        (replace(_inferred_relationship_delta().formation_traces[2].content_bindings[0], evidence_id=["e"]), "formation_trace[2].content_bindings[0].evidence_id.invalid"),  # type: ignore[arg-type]
    ],
)
def test_malformed_content_bindings_fail_closed_before_cross_record_validation(
    binding: FormationContentBinding, expected_issue: str
) -> None:
    template = _inferred_relationship_delta()
    trace = replace(template.formation_traces[2], content_bindings=(binding, template.formation_traces[2].content_bindings[1]))
    delta = replace(template, formation_traces=(*template.formation_traces[:2], trace))
    base = _base()
    before = _snapshot(base)

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    assert expected_issue in validated.value.issues
    assert expected_issue in applied.value.issues
    assert all("person:example" not in issue and "raw" not in issue for issue in validated.value.issues)
    assert _snapshot(base) == before


def test_malformed_relationship_endpoint_fails_closed_before_cross_record_set_operations() -> None:
    template = _inferred_relationship_delta()
    relationship = replace(template.new_relationships[0], source_entity_id=[])  # type: ignore[arg-type]
    delta = replace(template, new_relationships=(relationship,))
    base = _base()
    before = _snapshot(base)

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    assert "formation_trace[2].content_bindings.relationship.endpoints.invalid" in validated.value.issues
    assert "formation_trace[2].content_bindings.relationship.endpoints.invalid" in applied.value.issues
    assert _snapshot(base) == before


def test_malformed_inference_support_evidence_fails_closed_before_identity_comparison() -> None:
    template = _inferred_relationship_delta()
    inference_trace = template.formation_traces[2]
    source = replace(inference_trace.sources[0], evidence_id=["e"])  # type: ignore[arg-type]
    delta = replace(template, formation_traces=(*template.formation_traces[:2], replace(inference_trace, sources=(source,))))
    base = _base()
    before = _snapshot(base)

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    assert "formation_trace[2].content_bindings.inference_support.invalid" in validated.value.issues
    assert "formation_trace[2].content_bindings.inference_support.invalid" in applied.value.issues
    assert _snapshot(base) == before


def test_malformed_trace_cognition_id_fails_closed_before_cross_record_lookup() -> None:
    template = _inferred_relationship_delta()
    traces = list(template.formation_traces)
    traces[2] = replace(traces[2], cognition_id=["cog:bad"])  # type: ignore[arg-type]
    delta = replace(template, formation_traces=tuple(traces))
    base = _base()
    before = _snapshot(base)

    with pytest.raises(WorldDeltaValidationError) as validated:
        delta.validate_against(base, {"e:nanjing"})
    with pytest.raises(WorldDeltaValidationError) as applied:
        delta.apply_to(base, {"e:nanjing"})

    assert "formation_trace[2].cognition_id.invalid" in validated.value.issues
    assert "formation_trace[2].cognition_id.invalid" in applied.value.issues
    assert all("cog:bad" not in issue and "raw" not in issue for issue in validated.value.issues)
    assert _snapshot(base) == before


def test_inferred_relationship_resolves_target_from_base_or_new_relationships() -> None:
    template = _inferred_relationship_delta()
    base = _base()
    base.add_entity(template.new_entities[0])
    base.add_relationship(template.new_relationships[0])
    delta = replace(template, new_entities=(), new_relationships=())

    delta.validate_against(base, {"e:nanjing"})


def test_content_binding_is_a_nonpersistent_audit_sidecar_without_segment_id() -> None:
    delta = _inferred_relationship_delta()
    result = delta.apply_to(_base(), {"e:nanjing"})

    assert not hasattr(result, "formation_traces")
    assert "segment_id" not in asdict(delta.formation_traces[0])
