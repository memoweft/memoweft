"""Focused contract tests for the pure in-memory Stage 2 identity authority."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, cast

import pytest

from memoweft.world.entity_resolution import (
    EntityIdentityValidationError,
    EntityReferenceResolver,
)
from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.identity_review import (
    BindingAssignment,
    EntityIdentityDelta,
    EntityReferenceLocator,
    EntityReferenceRewrite,
    IdentityAuthority,
    IdentityEvidence,
    IdentityResolutionContext,
    IdentityReviewStateError,
    IdentityReviewValidationError,
    SplitSuccessor,
    VerifiedReferenceMention,
)
from memoweft.world.model import (
    Entity,
    EventFacet,
    EventParticipant,
    MemoryTarget,
    PersonalWorld,
    Perspective,
    Relationship,
    WorldCognition,
    WorldEvent,
)


def _graph(reverse: bool = False) -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld("w", "owner"))
    entities = (
        Entity("owner", "w", "person", "Owner"),
        Entity("a", "w", "person", "Ana"),
        Entity("b", "w", "person", "Annie"),
        Entity("other", "w", "person", "Other"),
    )
    for entity in reversed(entities) if reverse else entities:
        graph.add_entity(entity)
    graph.add_relationship(Relationship("rel", "w", "a", "other", "knows"))
    graph.add_event(
        WorldEvent(
            "event", "w", "meeting", "narrative is immutable", "2026-01-01T00:00:00+00:00",
            participants=(EventParticipant("a", "speaker"), EventParticipant("other", "listener")),
            related_entity_ids=("a", "other"), facets=(EventFacet("about", "immutable", "a"),),
        )
    )
    graph.add_cognition(
        WorldCognition(
            "cog", "w", MemoryTarget("entity", "a"), "narrative", "fact", "stated", 90,
            "stable", Perspective("joint", ("a", "other")),
        )
    )
    return graph


def _authority() -> tuple[IdentityAuthority, VerifiedReferenceMention, VerifiedReferenceMention, VerifiedReferenceMention]:
    authority = IdentityAuthority(_graph())
    authority.register_evidence(IdentityEvidence("e:a", "w", "c", "2026-01-01T00:00:00+00:00", "user", "Ana"))
    authority.register_evidence(IdentityEvidence("e:b", "w", "c", "2026-01-02T00:00:00+00:00", "user", "Annie"))
    authority.register_evidence(IdentityEvidence("e:next", "w", "c", "2026-03-01T00:00:00+00:00", "user", "she"))
    return authority, authority.issue_verified_mention("e:a", 0, 3), authority.issue_verified_mention("e:b", 0, 5), authority.issue_verified_mention("e:next", 0, 3)


def _accept(
    authority: IdentityAuthority,
    delta: EntityIdentityDelta,
    decided_at: str = "2026-02-01T00:00:00+00:00",
) -> None:
    review = authority.stage(delta, {"reviewer": "owner", "reason": "exact-user-evidence"})
    authority.decide(review.review_id, review.result_hash, "accept", decided_at)


def test_canonical_hash_is_insertion_order_independent_and_input_is_isolated() -> None:
    left = IdentityAuthority(_graph())
    right = IdentityAuthority(_graph(reverse=True))
    assert left.view().graph.graph_hash == right.view().graph.graph_hash
    original = _graph()
    authority = IdentityAuthority(original)
    original.entities.clear()
    assert len(authority.view().graph.entities) == 4


def test_unicode_exact_span_user_only_and_closed_constructor() -> None:
    authority = IdentityAuthority(_graph())
    content = "前缀😀小林后缀"
    authority.register_evidence(IdentityEvidence("e", "w", "c", "2026-01-01T00:00:00+00:00", "user", content))
    mention = authority.issue_verified_mention("e", 3, 5)
    assert mention.text == "小林"
    assert mention.claim_span.start_codepoint == 3
    with pytest.raises(TypeError):
        VerifiedReferenceMention()
    with pytest.raises(IdentityReviewValidationError, match="source_role.ineligible"):
        authority.register_evidence(IdentityEvidence("bad", "w", "c", "2026-01-01T00:00:00+00:00", "assistant", "no"))


def test_stage_is_immutable_reject_is_zero_mutation_and_hash_is_tamper_protected() -> None:
    authority, mention, _, _ = _authority()
    before = authority.view()
    review = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {"x": 1})
    assert authority.view().graph.graph_hash == before.graph.graph_hash
    authority.decide(review.review_id, review.result_hash, "reject", "2026-02-01T00:00:00+00:00")
    after = authority.view()
    assert (after.revision, after.graph.graph_hash, after.bindings, after.transitions, after.redirects, after.tombstones) == (before.revision, before.graph.graph_hash, before.bindings, before.transitions, before.redirects, before.tombstones)
    review = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {"x": 2})
    with pytest.raises(IdentityReviewStateError, match="RESULT_HASH_MISMATCH"):
        authority.decide(review.review_id, "0" * 64, "accept", "2026-02-01T00:00:00+00:00")
    _accept(authority, EntityIdentityDelta.bind("w", "a", mention))
    stale = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {"x": 3})
    _accept(authority, EntityIdentityDelta.bind("w", "a", mention))
    with pytest.raises(IdentityReviewStateError, match="STALE_BASE"):
        authority.decide(stale.review_id, stale.result_hash, "accept", "2026-02-01T00:00:00+00:00")


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_returned_decision_is_detached_from_the_audit_ledger(
    decision: Literal["accept", "reject"],
) -> None:
    authority, mention, _, _ = _authority()
    review = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {})
    outcome = authority.decide(
        review.review_id,
        review.result_hash,
        decision,
        "2026-02-01T00:00:00+00:00",
    )

    object.__setattr__(outcome, "status", "pending")

    assert authority.view().decisions[0].status == (
        "accepted" if decision == "accept" else "rejected"
    )


def test_one_verified_atom_has_at_most_one_current_entity_target() -> None:
    authority, mention, _, _ = _authority()
    _accept(authority, EntityIdentityDelta.bind("w", "a", mention))
    _accept(authority, EntityIdentityDelta.bind("w", "a", mention))

    assert len(authority.view().bindings) == 1
    with pytest.raises(
        IdentityReviewValidationError,
        match="binding.atom.target_conflict",
    ):
        authority.stage(EntityIdentityDelta.bind("w", "b", mention), {})


def test_decision_time_is_causal_and_backdated_context_fails_closed() -> None:
    authority, mention, _, _ = _authority()
    review = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {})
    with pytest.raises(
        IdentityReviewValidationError,
        match="decided_at.before_evidence",
    ):
        authority.decide(
            review.review_id,
            review.result_hash,
            "accept",
            "2025-12-31T00:00:00+00:00",
        )
    authority.decide(
        review.review_id,
        review.result_hash,
        "accept",
        "2026-02-01T00:00:00+00:00",
    )
    authority.register_evidence(
        IdentityEvidence(
            "e:backdated-current",
            "w",
            "c",
            "2026-01-15T00:00:00+00:00",
            "user",
            "she",
        )
    )
    backdated = authority.issue_verified_mention("e:backdated-current", 0, 3)

    with pytest.raises(
        IdentityReviewValidationError,
        match="context.current_mention.before_authority_revision",
    ):
        authority.resolution_context(backdated)


def test_decided_review_envelopes_are_retained_and_publicly_detached() -> None:
    authority, a_mention, b_mention, _ = _authority()
    accepted = authority.stage(EntityIdentityDelta.bind("w", "a", a_mention), {"a": 1})
    authority.decide(
        accepted.review_id,
        accepted.result_hash,
        "accept",
        "2026-02-01T00:00:00+00:00",
    )
    rejected = authority.stage(EntityIdentityDelta.bind("w", "b", b_mention), {"b": 2})
    authority.decide(
        rejected.review_id,
        rejected.result_hash,
        "reject",
        "2026-02-01T00:00:00+00:00",
    )

    view = authority.view()
    assert {item.review_id for item in view.review_envelopes} == {
        accepted.review_id,
        rejected.review_id,
    }
    assert {item.review_id for item in view.decisions} == {
        accepted.review_id,
        rejected.review_id,
    }
    object.__setattr__(view.review_envelopes[0], "review_payload", {"forged": True})
    assert all(
        item.review_payload != {"forged": True}
        for item in authority.view().review_envelopes
    )


def test_pending_status_tamper_and_malformed_context_fail_closed() -> None:
    authority, mention, _, _ = _authority()
    review = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {})
    object.__setattr__(authority._pending[review.review_id], "status", "accepted")
    with pytest.raises(IdentityReviewStateError, match="REVIEW_STATUS_INVALID"):
        authority.decide(
            review.review_id,
            review.result_hash,
            "accept",
            "2026-02-01T00:00:00+00:00",
        )
    assert authority.view().revision == 0

    malformed = object.__new__(IdentityResolutionContext)
    with pytest.raises(IdentityReviewStateError, match="CONTEXT_SEAL_INVALID"):
        malformed.resolver_inputs()
    with pytest.raises(IdentityReviewStateError, match="CONTEXT_SEAL_INVALID"):
        EntityReferenceResolver().resolve_context(malformed)


def test_remaining_malformed_public_and_stored_values_fail_closed() -> None:
    authority, mention, _, next_mention = _authority()
    for kind_hint in ([], object()):
        with pytest.raises(IdentityReviewValidationError, match="kind_hint.invalid"):
            authority.issue_verified_mention(
                "e:a",
                0,
                3,
                kind_hint=cast(Any, kind_hint),
            )

    with pytest.raises(IdentityReviewValidationError, match="review_id.invalid"):
        authority.decide(
            cast(Any, []),
            "0" * 64,
            "accept",
            "2026-02-01T00:00:00+00:00",
        )
    assert authority.resolution_context(next_mention).verify(cast(Any, object())) is False

    review = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {})
    object.__setattr__(
        authority._pending[review.review_id].delta,
        "successors",
        (object(),),
    )
    with pytest.raises(IdentityReviewStateError, match="RESULT_HASH_MISMATCH"):
        authority.decide(
            review.review_id,
            review.result_hash,
            "accept",
            "2026-02-01T00:00:00+00:00",
        )


def test_merge_rewrites_every_entity_reference_surface_and_rejects_collapse() -> None:
    authority, a_mention, _, _ = _authority()
    _accept(authority, EntityIdentityDelta.merge("w", "b", ("a",), (a_mention,)))
    graph = authority.view().graph
    assert "a" not in {item.id for item in graph.entities}
    assert graph.relationships[0].source_entity_id == "b"
    event = graph.events[0]
    assert event.participants[0].entity_id == event.related_entity_ids[0] == event.facets[0].about_entity_id == "b"
    assert graph.cognitions[0].target.id == graph.cognitions[0].perspective.holder_entity_ids[0] == "b"
    collapse = IdentityAuthority(_graph())
    collapse.register_evidence(IdentityEvidence("e", "w", "c", "2026-01-01T00:00:00+00:00", "user", "Ana"))
    mention = collapse.issue_verified_mention("e", 0, 3)
    with pytest.raises(IdentityReviewValidationError, match="cardinality_collapse"):
        collapse.stage(EntityIdentityDelta.merge("w", "other", ("a",), (mention,)), {})


def test_split_requires_full_partition_fresh_ids_and_can_unresolve_old_binding() -> None:
    authority, a_mention, b_mention, next_mention = _authority()
    _accept(authority, EntityIdentityDelta.bind("w", "a", a_mention))
    locators = []
    for surface, object_id, index in (
        ("relationship.source", "rel", 0), ("event.participant", "event", 0),
        ("event.related", "event", 0), ("event.facet_about", "event", 0),
        ("cognition.target", "cog", 0), ("cognition.perspective", "cog", 0),
    ):
        typed_surface = cast(Literal["relationship.source", "event.participant", "event.related", "event.facet_about", "cognition.target", "cognition.perspective"], surface)
        locators.append(EntityReferenceRewrite(EntityReferenceLocator(typed_surface, object_id, index), "a", "a:one"))
    one = SplitSuccessor(Entity("a:one", "w", "person", "Ana", ()), (a_mention,))
    two = SplitSuccessor(Entity("a:two", "w", "person", "Annie", ()), (b_mention,))
    binding = authority.view().bindings[0]
    delta = EntityIdentityDelta.split("w", "a", (one, two), tuple(locators), (BindingAssignment(binding.binding_id, None),))
    _accept(authority, delta)
    context = authority.resolution_context(next_mention)
    assert all(item.entity_id != "a" for item in context.to_accepted_entity_references())
    assert authority.view().tombstones[0].successors == ("a:one", "a:two")
    with pytest.raises(IdentityReviewValidationError, match="partition.incomplete"):
        IdentityAuthority(_graph()).stage(EntityIdentityDelta.split("w", "a", (one, two), (), ()), {})


def test_context_one_call_projects_only_verified_accepted_history() -> None:
    authority, a_mention, _, next_mention = _authority()
    pending = authority.stage(EntityIdentityDelta.bind("w", "a", a_mention), {})
    assert not authority.resolution_context(next_mention).to_accepted_entity_references()
    authority.decide(pending.review_id, pending.result_hash, "reject", "2026-02-01T00:00:00+00:00")
    assert not authority.resolution_context(next_mention).to_accepted_entity_references()
    _accept(authority, EntityIdentityDelta.bind("w", "a", a_mention))
    inputs = authority.resolution_context(next_mention).resolver_inputs(_graph())
    assert inputs.current_mention.text == "she"
    assert tuple(item.entity_id for item in inputs.accepted_history) == ("a",)
    with pytest.raises(IdentityReviewStateError, match="CONTEXT_BASE_MISMATCH"):
        stale_context = authority.resolution_context(next_mention)
        changed = _graph(reverse=True)
        changed.entities["a"] = replace(changed.entities["a"], aliases=("different",))
        stale_context.resolver_inputs(changed)


def test_sealed_context_is_the_authoritative_resolver_entry_point() -> None:
    authority, a_mention, _, next_mention = _authority()
    _accept(authority, EntityIdentityDelta.bind("w", "a", a_mention))

    resolution = EntityReferenceResolver().resolve_context(
        authority.resolution_context(next_mention)
    )

    assert resolution.state == "resolved"
    assert resolution.entity_id == "a"
    assert resolution.basis == "recent-user-context"
    with pytest.raises(EntityIdentityValidationError, match="context.type.invalid"):
        EntityReferenceResolver().resolve_context(cast(Any, object()))


def test_decision_is_single_use_and_stored_envelope_tampering_fails_closed() -> None:
    authority, mention, _, _ = _authority()
    review = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {"stable": True})
    object.__setattr__(review, "review_payload", {"stable": False})
    authority.decide(
        review.review_id,
        review.result_hash,
        "accept",
        "2026-02-01T00:00:00+00:00",
    )
    with pytest.raises(IdentityReviewStateError, match="REVIEW_ALREADY_DECIDED"):
        authority.decide(
            review.review_id,
            review.result_hash,
            "reject",
            "2026-02-01T00:00:00+00:00",
        )

    stored = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {"stable": True})
    object.__setattr__(
        authority._pending[stored.review_id],
        "review_payload",
        {"stable": False},
    )
    with pytest.raises(IdentityReviewStateError, match="RESULT_HASH_MISMATCH"):
        authority.decide(
            stored.review_id,
            stored.result_hash,
            "accept",
            "2026-02-01T00:00:00+00:00",
        )


def test_malformed_graph_fails_with_stable_domain_error_not_attribute_error() -> None:
    graph = _graph()
    graph.events["event"] = replace(graph.events["event"], participants=("not-a-participant",))  # type: ignore[arg-type]
    with pytest.raises(
        IdentityReviewValidationError,
        match=r"graph\.events\[0\]\.participants\[0\]\.type\.invalid",
    ):
        IdentityAuthority(graph)


def test_view_snapshot_and_resolver_base_are_deeply_detached() -> None:
    authority, mention, _, next_mention = _authority()
    _accept(authority, EntityIdentityDelta.bind("w", "a", mention))
    before = authority.view()
    view = authority.view()
    object.__setattr__(view.graph.entities[1], "canonical_name", "tampered")
    object.__setattr__(view.graph.events[0].participants[0], "entity_id", "tampered")
    object.__setattr__(view.graph.cognitions[0].target, "id", "tampered")
    object.__setattr__(view.graph.cognitions[0].perspective, "holder_entity_ids", ("tampered",))
    after = authority.view()
    assert (after.revision, after.graph.graph_hash) == (before.revision, before.graph.graph_hash)

    detached = after.graph.to_graph()
    inputs = authority.resolution_context(next_mention).resolver_inputs(detached)
    assert inputs.base.world.world_id == "w"
    detached.entities["a"] = replace(detached.entities["a"], canonical_name="detached")
    assert authority.view().graph.graph_hash == before.graph.graph_hash


@pytest.mark.parametrize(
    "field,value",
    [
        ("occurred_at", "2030-01-01T00:00:00+00:00"),
        ("conversation_id", "other-conversation"),
        ("continuity_scope", "other-continuity"),
        ("kind_hint", "other-kind"),
        ("atom_hash", "0" * 64),
    ],
)
def test_mutated_public_mention_is_rejected_while_exact_reissue_keeps_old_pending_valid(
    field: str, value: str | None
) -> None:
    authority, mention, _, _ = _authority()
    pending = authority.stage(EntityIdentityDelta.bind("w", "a", mention), {"stable": True})
    exact_again = authority.issue_verified_mention("e:a", 0, 3)
    assert exact_again == mention
    authority.decide(pending.review_id, pending.result_hash, "accept", "2026-02-01T00:00:00+00:00")

    object.__setattr__(mention, field, value)
    with pytest.raises(IdentityReviewValidationError, match=r"delta\.mentions\[0\]\.unverified"):
        authority.stage(EntityIdentityDelta.bind("w", "a", mention), {})
    with pytest.raises(IdentityReviewValidationError, match="context.current_mention.unverified"):
        authority.resolution_context(mention)


def test_merge_pending_sidecars_are_complete_and_every_sidecar_tamper_fails_closed() -> None:
    authority, mention, _, _ = _authority()
    authority._graph.add_relationship(Relationship("rel-target", "w", "other", "a", "knows"))
    pending = authority.stage(EntityIdentityDelta.merge("w", "b", ("a",), (mention,)), {})
    surfaces = {rewrite.locator.surface for rewrite in pending.preview_transition.rewrite_manifest}
    assert surfaces == {
        "relationship.source", "relationship.target", "event.participant", "event.related",
        "event.facet_about", "cognition.target", "cognition.perspective",
    }
    assert pending.preview_bindings and pending.preview_redirects and pending.preview_tombstones

    for field, replacement in (
        ("preview_bindings", ()),
        ("preview_redirects", ()),
        ("preview_tombstones", ()),
        ("preview_transition", replace(pending.preview_transition, rewrite_manifest=())),
    ):
        tampered_authority, tampered_mention, _, _ = _authority()
        tampered_authority._graph.add_relationship(Relationship("rel-target", "w", "other", "a", "knows"))
        review = tampered_authority.stage(EntityIdentityDelta.merge("w", "b", ("a",), (tampered_mention,)), {})
        object.__setattr__(tampered_authority._pending[review.review_id], field, replacement)
        with pytest.raises(IdentityReviewStateError, match="RESULT_HASH_MISMATCH"):
            tampered_authority.decide(review.review_id, review.result_hash, "accept", "2026-02-01T00:00:00+00:00")

    authority.decide(pending.review_id, pending.result_hash, "accept", "2026-02-01T00:00:00+00:00")
    accepted = authority.view()
    transition = accepted.transitions[-1]
    assert transition.rewrite_manifest == pending.preview_transition.rewrite_manifest
    assert accepted.tombstones[-1].retired_entity.id == "a"
    assert accepted.redirects[-1].review_id == pending.review_id
    assert all(binding.result_hash == pending.result_hash for binding in accepted.bindings)


def test_alias_normalization_duplicate_external_collision_and_resolver_ready_snapshot() -> None:
    authority = IdentityAuthority(_graph())
    authority.register_evidence(IdentityEvidence("alias", "w", "c", "2026-01-01T00:00:00+00:00", "user", "Ava"))
    mention = authority.issue_verified_mention("alias", 0, 3)
    with pytest.raises(IdentityReviewValidationError, match="alias.value.duplicate"):
        authority.stage(EntityIdentityDelta.alias("w", "a", " ＡＮＡ ", (mention,)), {})
    with pytest.raises(IdentityReviewValidationError, match="alias.conflicts_with:b"):
        authority.stage(EntityIdentityDelta.alias("w", "a", "AnNiE", (mention,)), {})
    _accept(authority, EntityIdentityDelta.alias("w", "a", "Ava", (mention,)))
    authority.register_evidence(
        IdentityEvidence(
            "alias:current",
            "w",
            "c",
            "2026-03-01T00:00:00+00:00",
            "user",
            "Ava",
        )
    )
    current = authority.issue_verified_mention("alias:current", 0, 3)
    snapshot_base = authority.view().graph.to_graph()
    assert authority.resolution_context(current).resolver_inputs(snapshot_base).base.entities["a"].aliases[-1] == "Ava"


@pytest.mark.parametrize(
    "mutate, action",
    [
        (lambda graph: object.__setattr__(graph, "entities", []), lambda graph: IdentityAuthority(graph)),
        (lambda graph: object.__setattr__(graph, "relationships", []), lambda graph: IdentityAuthority(graph)),
        (lambda graph: object.__setattr__(graph, "events", []), lambda graph: IdentityAuthority(graph)),
        (lambda graph: object.__setattr__(graph, "cognitions", []), lambda graph: IdentityAuthority(graph)),
        (lambda graph: graph.events.__setitem__("event", replace(graph.events["event"], related_entity_ids=["a"])), lambda graph: IdentityAuthority(graph)),
        (lambda graph: graph.events.__setitem__("event", replace(graph.events["event"], participants=[[]])), lambda graph: IdentityAuthority(graph)),
        (lambda graph: graph.events.__setitem__("event", replace(graph.events["event"], facets=[[]])), lambda graph: IdentityAuthority(graph)),
        (lambda graph: graph.cognitions.__setitem__("cog", replace(graph.cognitions["cog"], perspective=Perspective("joint", cast(Any, ["a", []])))), lambda graph: IdentityAuthority(graph)),
    ],
)
def test_malformed_graph_shapes_raise_only_identity_review_validation_error(mutate: object, action: object) -> None:
    graph = _graph()
    cast(Any, mutate)(graph)
    with pytest.raises(IdentityReviewValidationError):
        cast(Any, action)(graph)


@pytest.mark.parametrize(
    "field,value",
    [
        ("mentions", []),
        ("absorbed_entity_ids", []),
        ("successors", []),
        ("rewrites", []),
        ("binding_assignments", []),
    ],
)
def test_malformed_delta_containers_and_locator_raise_only_domain_error(field: str, value: object) -> None:
    authority, mention, _, _ = _authority()
    delta = EntityIdentityDelta.bind("w", "a", mention)
    object.__setattr__(delta, field, value)
    with pytest.raises(IdentityReviewValidationError):
        authority.stage(delta, {})


def test_malformed_split_locator_raises_only_domain_error() -> None:
    authority, _, _, _ = _authority()
    delta = EntityIdentityDelta.split(
        "w", "a", (),
        (EntityReferenceRewrite(EntityReferenceLocator(cast(Any, "not-a-surface"), "x"), "a", "b"),),
        (),
    )
    with pytest.raises(IdentityReviewValidationError):
        authority.stage(delta, {})


def test_unhashable_public_values_raise_only_domain_errors() -> None:
    authority, mention, _, _ = _authority()
    object.__setattr__(mention, "atom_hash", [])
    with pytest.raises(IdentityReviewValidationError):
        authority.stage(EntityIdentityDelta.bind("w", "a", mention), {})

    evidence = IdentityEvidence(
        "e:bad",
        "w",
        "c",
        "2026-01-01T00:00:00+00:00",
        "user",
        "bad",
    )
    object.__setattr__(evidence, "id", [])
    with pytest.raises(IdentityReviewValidationError):
        authority.register_evidence(evidence)

    malformed_rewrite_values: tuple[tuple[str, object], ...] = (
        ("surface", []),
        ("replacement_entity_id", []),
    )
    for field, value in malformed_rewrite_values:
        locator = EntityReferenceLocator("relationship.source", "rel")
        rewrite = EntityReferenceRewrite(locator, "a", "successor")
        target = locator if field == "surface" else rewrite
        object.__setattr__(target, field, value)
        delta = EntityIdentityDelta.split("w", "a", (), (rewrite,), ())
        with pytest.raises(IdentityReviewValidationError):
            authority.stage(delta, {})


def test_split_binding_partition_rejects_duplicate_missing_unknown_and_removes_old_references() -> None:
    authority, a_mention, b_mention, next_mention = _authority()
    _accept(authority, EntityIdentityDelta.bind("w", "a", a_mention))
    binding_id = authority.view().bindings[0].binding_id
    rewrites = tuple(
        EntityReferenceRewrite(EntityReferenceLocator(cast(Any, surface), object_id, index), "a", "a:one")
        for surface, object_id, index in (
            ("relationship.source", "rel", 0), ("event.participant", "event", 0),
            ("event.related", "event", 0), ("event.facet_about", "event", 0),
            ("cognition.target", "cog", 0), ("cognition.perspective", "cog", 0),
        )
    )
    successors = (
        SplitSuccessor(Entity("a:one", "w", "person", "Ana"), (a_mention,)),
        SplitSuccessor(Entity("a:two", "w", "person", "Annie"), (b_mention,)),
    )
    for assignments, code in (
        ((BindingAssignment(binding_id, None), BindingAssignment(binding_id, None)), "duplicate"),
        ((), "partition.incomplete"),
        ((BindingAssignment("unknown", None),), "partition.incomplete"),
    ):
        with pytest.raises(IdentityReviewValidationError, match=code):
            authority.stage(EntityIdentityDelta.split("w", "a", successors, rewrites, assignments), {})
    _accept(authority, EntityIdentityDelta.split("w", "a", successors, rewrites, (BindingAssignment(binding_id, None),)))
    graph = authority.view().graph
    assert "a" not in {entity.id for entity in graph.entities}
    assert all("a" not in (relationship.source_entity_id, relationship.target_entity_id) for relationship in graph.relationships)
    assert all("a" not in event.related_entity_ids for event in graph.events)
    assert all(participant.entity_id != "a" for event in graph.events for participant in event.participants)
    assert all(facet.about_entity_id != "a" for event in graph.events for facet in event.facets)
    assert all(cognition.target.id != "a" and "a" not in cognition.perspective.holder_entity_ids for cognition in graph.cognitions)
    history = authority.resolution_context(next_mention).to_accepted_entity_references()
    assert {item.entity_id for item in history} == {"a:one", "a:two"}


def test_split_source_that_is_existing_redirect_target_is_rejected() -> None:
    authority, a_mention, b_mention, _ = _authority()
    _accept(authority, EntityIdentityDelta.merge("w", "b", ("a",), (a_mention,)))
    successors = (
        SplitSuccessor(Entity("b:one", "w", "person", "Ana"), (a_mention,)),
        SplitSuccessor(Entity("b:two", "w", "person", "Annie"), (b_mention,)),
    )
    with pytest.raises(IdentityReviewValidationError, match="split.source.redirect_target"):
        authority.stage(EntityIdentityDelta.split("w", "b", successors, (), ()), {})


def test_split_successor_cannot_reuse_a_retired_entity_id() -> None:
    authority, a_mention, _, _ = _authority()
    _accept(authority, EntityIdentityDelta.merge("w", "b", ("a",), (a_mention,)))
    for evidence in (
        IdentityEvidence(
            "e:retired",
            "w",
            "c",
            "2026-01-03T00:00:00+00:00",
            "user",
            "Alex",
        ),
        IdentityEvidence(
            "e:fresh",
            "w",
            "c",
            "2026-01-04T00:00:00+00:00",
            "user",
            "Taylor",
        ),
    ):
        authority.register_evidence(evidence)
    retired_support = authority.issue_verified_mention("e:retired", 0, 4)
    fresh_support = authority.issue_verified_mention("e:fresh", 0, 6)
    rewrites = tuple(
        EntityReferenceRewrite(
            EntityReferenceLocator(cast(Any, surface), object_id, index),
            "other",
            "a",
        )
        for surface, object_id, index in (
            ("relationship.target", "rel", 0),
            ("event.participant", "event", 1),
            ("event.related", "event", 1),
            ("cognition.perspective", "cog", 1),
        )
    )
    successors = (
        SplitSuccessor(Entity("a", "w", "person", "Alex"), (retired_support,)),
        SplitSuccessor(
            Entity("other:new", "w", "person", "Taylor"),
            (fresh_support,),
        ),
    )

    with pytest.raises(IdentityReviewValidationError, match="id.retired"):
        authority.stage(
            EntityIdentityDelta.split("w", "other", successors, rewrites, ()),
            {},
        )


def test_context_requires_strict_prior_and_same_conversation_or_continuity_and_has_detached_base_hash() -> None:
    authority = IdentityAuthority(_graph())
    for evidence in (
        IdentityEvidence("prior", "w", "c", "2026-01-01T00:00:00+00:00", "user", "Ana", "scope"),
        IdentityEvidence("same-time", "w", "c", "2026-01-02T00:00:00+00:00", "user", "Same", "scope"),
        IdentityEvidence("foreign", "w", "foreign", "2026-01-01T00:00:00+00:00", "user", "Foreign"),
        IdentityEvidence("continued", "w", "foreign", "2026-01-01T00:00:00+00:00", "user", "Continued", "scope"),
        IdentityEvidence("current", "w", "c", "2026-01-02T00:00:00+00:00", "user", "Now", "scope"),
    ):
        authority.register_evidence(evidence)
    mentions = {key: authority.issue_verified_mention(key, 0, len(label)) for key, label in {"prior": "Ana", "same-time": "Same", "foreign": "Foreign", "continued": "Continued", "current": "Now"}.items()}
    for evidence_id, entity_id in (
        ("prior", "a"),
        ("foreign", "other"),
        ("continued", "b"),
    ):
        _accept(
            authority,
            EntityIdentityDelta.bind("w", entity_id, mentions[evidence_id]),
            "2026-01-01T12:00:00+00:00",
        )
    context = authority.resolution_context(mentions["current"])
    inputs = context.resolver_inputs()
    assert context.graph_hash == authority.view().graph.graph_hash
    assert {item.evidence_id for item in inputs.accepted_history} == {"prior", "continued"}
    inputs.base.entities.clear()
    assert authority.view().graph.graph_hash == context.graph_hash
    _accept(authority, EntityIdentityDelta.bind("w", "a", mentions["current"]))
    with pytest.raises(IdentityReviewStateError, match="CONTEXT_SEAL_INVALID"):
        context.resolver_inputs()
