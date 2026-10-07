from __future__ import annotations

from dataclasses import replace

import pytest

from memoweft.types import EvidenceLink
from memoweft.world import (
    AcceptedEntityReference,
    Entity,
    EntityAliasProposal,
    EntityCandidate,
    EntityIdentityValidationError,
    EntityReferenceResolution,
    EntityReferenceResolver,
    EventFacet,
    EventParticipant,
    MemoryTarget,
    MemoryWorldGraph,
    PersonalWorld,
    Perspective,
    ReferenceMention,
    Relationship,
    WorldCognition,
    WorldEvent,
)


def _world() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld("world:test", "person:owner"))
    for entity in (
        Entity("person:owner", "world:test", "person", "Owner"),
        Entity("person:mother", "world:test", "person", "Mother", ("妈妈",)),
        Entity("person:nanjing-friend", "world:test", "person", "Friend A", ("小林",)),
        Entity("person:other-friend", "world:test", "person", "Friend B", ("小周",)),
        Entity("activity:nanjing-trip", "world:test", "activity", "Nanjing trip", ("南京旅游",)),
        Entity("place:nanjing", "world:test", "place", "Nanjing", ("南京",)),
    ):
        graph.add_entity(entity)
    graph.validate_owner()
    graph.add_relationship(
        Relationship(
            "relationship:owner-mother",
            "world:test",
            "person:owner",
            "person:mother",
            "child_of",
        )
    )
    for entity_id in ("person:nanjing-friend", "person:other-friend"):
        graph.add_relationship(
            Relationship(
                f"relationship:owner-{entity_id.rsplit(':', 1)[1]}",
                "world:test",
                "person:owner",
                entity_id,
                "friend",
                True,
            )
        )
    graph.add_event(
        WorldEvent(
            "event:nanjing-trip",
            "world:test",
            "trip",
            "Owner and Friend A travelled to Nanjing.",
            "2026-07-01T08:00:00+08:00",
            participants=(
                EventParticipant("person:owner", "traveler"),
                EventParticipant("person:nanjing-friend", "traveler"),
            ),
            related_entity_ids=("activity:nanjing-trip", "place:nanjing"),
            relationship_ids=("relationship:owner-nanjing-friend",),
            evidence_ids=("e:trip",),
        )
    )
    return graph


def _mention(
    text: str,
    evidence_id: str,
    conversation_id: str,
    *,
    kind_hint: str | None = None,
    continuity_id: str | None = "continuity:test",
    occurred_at: str = "2026-08-10T10:00:00+08:00",
) -> ReferenceMention:
    return ReferenceMention(
        text=text,
        evidence_id=evidence_id,
        conversation_id=conversation_id,
        occurred_at=occurred_at,
        source_role="user",
        kind_hint=kind_hint,
        continuity_id=continuity_id,
    )


def _accepted(
    entity_id: str,
    mention: str,
    evidence_id: str,
    conversation_id: str,
    *,
    continuity_id: str | None = "continuity:test",
    occurred_at: str = "2026-08-10T09:00:00+08:00",
) -> AcceptedEntityReference:
    return AcceptedEntityReference(
        entity_id=entity_id,
        mention=mention,
        evidence_id=evidence_id,
        conversation_id=conversation_id,
        occurred_at=occurred_at,
        source_role="user",
        continuity_id=continuity_id,
    )


def _cognition(
    cognition_id: str = "cognition:owner",
    *,
    target: MemoryTarget | None = None,
    perspective: Perspective | None = None,
) -> WorldCognition:
    return WorldCognition(
        cognition_id,
        "world:test",
        target or MemoryTarget("entity", "person:owner"),
        "The owner prefers reliable identity resolution.",
        "preference",
        "stated",
        600,
        "limited",
        perspective or Perspective("entity", ("person:owner",)),
    )


def test_owner_relative_alias_proposes_an_evidence_bound_non_mutating_preview() -> None:
    base = _world()
    resolver = EntityReferenceResolver()

    resolution = resolver.resolve(base, _mention("我妈", "e:session-2", "session:2"))

    assert resolution.state == "resolved"
    assert resolution.entity_id == "person:mother"
    assert resolution.basis == "owner-relative-alias"
    assert resolution.alias_proposal == EntityAliasProposal(
        "person:mother",
        "我妈",
        ("e:session-2",),
        "owner-relative-alias",
    )

    preview = resolution.alias_proposal.preview(base, (resolution.mention,))
    assert base.entities["person:mother"].aliases == ("妈妈",)
    assert preview.entities["person:mother"].aliases == ("妈妈", "我妈")
    assert preview.relationships == base.relationships
    assert preview.events == base.events


def test_alias_preview_rejects_ineligible_evidence_and_cross_entity_collision() -> None:
    base = _world()
    base.add_entity(Entity("person:other", "world:test", "person", "Another", ("我妈",)))
    proposal = EntityAliasProposal(
        "person:mother",
        "我妈",
        ("e:identity",),
        "owner-relative-alias",
    )
    before = base.entities.copy()

    with pytest.raises(EntityIdentityValidationError) as raised:
        proposal.preview(base, (_mention("我妈", "e:other", "session:2"),))

    assert raised.value.issues == (
        "evidence_id.not_eligible:e:identity",
        "alias.conflicts_with:person:other",
    )
    assert base.entities == before


def test_exact_alias_collision_is_ambiguous_instead_of_ranked_aggressively() -> None:
    base = _world()
    base.add_entity(Entity("person:other-mother", "world:test", "person", "Other", ("妈妈",)))

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("妈妈", "e:collision", "session:collision"),
    )

    assert resolution.state == "ambiguous"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "OWNER_RELATIVE_ALIAS_COLLISION"
    assert resolution.candidate_entity_ids == ("person:mother", "person:other-mother")


def test_pronoun_uses_only_the_latest_accepted_user_evidence_group() -> None:
    base = _world()
    history = (
        _accepted(
            "person:other-friend",
            "小周",
            "e:old",
            "session:old",
            occurred_at="2026-08-10T08:00:00+08:00",
        ),
        _accepted("person:nanjing-friend", "小林", "e:latest", "session:one"),
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("她", "e:pronoun", "session:two", kind_hint="person"),
        history,
    )

    assert resolution.state == "resolved"
    assert resolution.entity_id == "person:nanjing-friend"
    assert resolution.basis == "recent-user-context"
    assert resolution.candidates[0].reasons == (
        "accepted-user-reference:session:one:e:latest",
    )
    assert resolution.alias_proposal is None


@pytest.mark.parametrize("pronoun", ("它", "it"))
def test_animal_pronoun_surface_resolves_only_an_accepted_animal(
    pronoun: str,
) -> None:
    base = _world()
    base.add_entity(Entity("animal:cat", "world:test", "animal", "Cat"))
    history = (
        _accepted("animal:cat", "Cat", "e:cat", "session:one"),
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention(pronoun, "e:pronoun", "session:two"),
        history,
    )

    assert resolution.state == "resolved"
    assert resolution.entity_id == "animal:cat"


@pytest.mark.parametrize(
    ("entity_id", "pronoun"),
    (
        ("person:nanjing-friend", "它"),
        ("person:nanjing-friend", "it"),
        ("animal:cat", "她"),
        ("animal:cat", "he"),
    ),
)
def test_pronoun_surface_kind_rejects_an_incompatible_latest_entity(
    entity_id: str,
    pronoun: str,
) -> None:
    base = _world()
    base.add_entity(Entity("animal:cat", "world:test", "animal", "Cat"))
    history = (
        _accepted(entity_id, "prior", "e:prior", "session:one"),
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention(pronoun, "e:pronoun", "session:two"),
        history,
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "NO_ACCEPTED_USER_REFERENCE_CONTEXT"


def test_explicit_kind_hint_conflicting_with_pronoun_surface_fails_closed() -> None:
    base = _world()
    base.add_entity(Entity("animal:cat", "world:test", "animal", "Cat"))
    history = (
        _accepted("animal:cat", "Cat", "e:cat", "session:one"),
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("it", "e:pronoun", "session:two", kind_hint="person"),
        history,
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "PRONOUN_KIND_CONFLICT"


def test_two_entities_in_latest_user_evidence_keep_a_pronoun_ambiguous() -> None:
    base = _world()
    history = (
        _accepted("person:nanjing-friend", "小林", "e:latest", "session:one"),
        _accepted("person:other-friend", "小周", "e:latest", "session:one"),
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("她", "e:pronoun", "session:two", kind_hint="person"),
        history,
    )

    assert resolution.state == "ambiguous"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "RECENT_USER_CONTEXT_AMBIGUOUS"
    assert resolution.candidate_entity_ids == (
        "person:nanjing-friend",
        "person:other-friend",
    )


def test_non_adjacent_bindings_from_the_latest_evidence_cannot_hide_ambiguity() -> None:
    base = _world()
    history = (
        _accepted("person:nanjing-friend", "小林", "e:latest", "session:one"),
        _accepted(
            "person:mother",
            "妈妈",
            "e:older",
            "session:one",
            occurred_at="2026-08-10T08:00:00+08:00",
        ),
        _accepted("person:other-friend", "小周", "e:latest", "session:one"),
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("她", "e:pronoun", "session:two", kind_hint="person"),
        history,
    )

    assert resolution.state == "ambiguous"
    assert resolution.candidate_entity_ids == (
        "person:nanjing-friend",
        "person:other-friend",
    )


def test_structured_description_uses_relationship_and_event_context() -> None:
    base = _world()

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个南京旅游的朋友", "e:descriptor", "session:later"),
    )

    assert resolution.state == "resolved"
    assert resolution.entity_id == "person:nanjing-friend"
    assert resolution.basis == "structured-description"
    assert resolution.candidate_entity_ids == (
        "person:nanjing-friend",
        "person:other-friend",
    )
    assert resolution.candidates[0].score > resolution.candidates[1].score


def test_structured_description_with_two_equally_supported_friends_is_ambiguous() -> None:
    base = _world()
    event = base.events["event:nanjing-trip"]
    base.events[event.id] = replace(
        event,
        participants=(*event.participants, EventParticipant("person:other-friend", "traveler")),
        relationship_ids=(*event.relationship_ids, "relationship:owner-other-friend"),
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个南京旅游的朋友", "e:descriptor", "session:later"),
    )

    assert resolution.state == "ambiguous"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "STRUCTURED_DESCRIPTION_AMBIGUOUS"
    assert resolution.candidates[0].score == resolution.candidates[1].score


def test_composite_descriptor_requires_every_named_graph_anchor() -> None:
    base = _world()
    event = base.events["event:nanjing-trip"]
    base.events[event.id] = replace(
        event,
        related_entity_ids=("place:nanjing",),
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个南京旅游的朋友", "e:descriptor", "session:later"),
    )

    assert resolution.state != "resolved"
    assert resolution.entity_id is None


def test_descriptor_event_type_and_anchor_must_cooccur_in_one_event() -> None:
    base = _world()
    base.add_entity(Entity("place:shanghai", "world:test", "place", "Shanghai", ("上海",)))
    base.add_event(
        WorldEvent(
            "event:shanghai-conflict",
            "world:test",
            "interpersonal_conflict",
            "Friend A argued in Shanghai.",
            "2026-07-02T08:00:00+08:00",
            participants=(EventParticipant("person:nanjing-friend"),),
            related_entity_ids=("place:shanghai",),
            relationship_ids=("relationship:owner-nanjing-friend",),
            evidence_ids=("e:conflict",),
        )
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个在南京吵架的朋友", "e:descriptor", "session:later"),
    )

    assert resolution.state != "resolved"
    assert resolution.entity_id is None


def test_multiple_event_type_terms_cannot_be_joined_across_unlinked_events() -> None:
    base = _world()
    base.add_event(
        WorldEvent(
            "event:nanjing-conflict",
            "world:test",
            "interpersonal_conflict",
            "Friend A argued during a different Nanjing visit.",
            "2026-08-01T08:00:00+08:00",
            participants=(EventParticipant("person:nanjing-friend"),),
            related_entity_ids=("place:nanjing",),
            relationship_ids=("relationship:owner-nanjing-friend",),
            evidence_ids=("e:later-conflict",),
        )
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个在南京旅游期间吵架的朋友", "e:descriptor", "session:later"),
    )

    assert resolution.state != "resolved"
    assert resolution.entity_id is None


@pytest.mark.parametrize(
    "text",
    (
        "和小周一起南京旅游的朋友",
        "my friend Friend B from the trip",
        "my friend C++ from the trip",
    ),
)
def test_embedded_known_entity_requires_role_binding_instead_of_retargeting(
    text: str,
) -> None:
    base = _world()
    base.add_entity(Entity("person:cpp", "world:test", "person", "C++"))
    base.add_relationship(
        Relationship(
            "relationship:owner-cpp",
            "world:test",
            "person:owner",
            "person:cpp",
            "friend",
            True,
        )
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention(text, "e:descriptor", "session:later"),
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "EMBEDDED_ENTITY_ROLE_REQUIRES_BINDING"


def test_retracted_family_relationship_cannot_support_owner_relative_resolution() -> None:
    base = _world()
    relationship = base.relationships["relationship:owner-mother"]
    base.relationships[relationship.id] = replace(
        relationship,
        status="retracted",
        valid_to="2020-01-01T00:00:00+08:00",
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("我妈", "e:new", "session:new"),
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None


def test_retracted_reverse_family_relationship_is_filtered_symmetrically() -> None:
    base = _world()
    relationship = base.relationships["relationship:owner-mother"]
    base.relationships[relationship.id] = replace(
        relationship,
        source_entity_id="person:mother",
        target_entity_id="person:owner",
        relation_type="mother_of",
        status="superseded",
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("我妈", "e:new", "session:new"),
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None


@pytest.mark.parametrize(
    ("valid_from", "valid_to"),
    (
        (None, "2026-08-10T09:00:00+08:00"),
        ("2026-08-10T11:00:00+08:00", None),
    ),
    ids=("expired", "not-yet-active"),
)
def test_out_of_interval_family_relationship_cannot_support_resolution(
    valid_from: str | None,
    valid_to: str | None,
) -> None:
    base = _world()
    relationship = base.relationships["relationship:owner-mother"]
    base.relationships[relationship.id] = replace(
        relationship,
        status="active",
        valid_from=valid_from,
        valid_to=valid_to,
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("我妈", "e:new", "session:new"),
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None


def test_owner_relative_family_requires_a_person_or_agent_entity() -> None:
    base = _world()
    base.relationships.pop("relationship:owner-mother")
    base.entities.pop("person:mother")
    base.add_entity(Entity("place:mother", "world:test", "place", "Mother", ("妈妈",)))
    base.add_relationship(
        Relationship(
            "relationship:owner-place-mother",
            "world:test",
            "person:owner",
            "place:mother",
            "child_of",
        )
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("我妈", "e:new", "session:new"),
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None


def test_human_relationship_descriptor_cannot_resolve_a_place_entity() -> None:
    base = _world()
    base.events.clear()
    base.relationships.pop("relationship:owner-nanjing-friend")
    base.relationships.pop("relationship:owner-other-friend")
    base.add_entity(Entity("place:friend", "world:test", "place", "Remote location"))
    base.add_relationship(
        Relationship(
            "relationship:owner-place-friend",
            "world:test",
            "person:owner",
            "place:friend",
            "friend",
        )
    )
    base.add_event(
        WorldEvent(
            "event:place-trip",
            "world:test",
            "trip",
            "Malformed accepted graph fixture.",
            "2026-07-01T08:00:00+08:00",
            participants=(EventParticipant("place:friend"),),
            related_entity_ids=("activity:nanjing-trip", "place:nanjing"),
            relationship_ids=("relationship:owner-place-friend",),
            evidence_ids=("e:trip",),
        )
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个南京旅游的朋友", "e:new", "session:new"),
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None


@pytest.mark.parametrize(
    ("source_entity_id", "target_entity_id", "bidirectional", "expected_entity_id"),
    (
        ("person:owner", "animal:pet", False, "animal:pet"),
        ("animal:pet", "person:owner", False, None),
        ("animal:pet", "person:owner", True, None),
    ),
    ids=("owner-owns-pet", "pet-owns-owner", "reverse-owns-marked-bidirectional"),
)
def test_directed_owns_descriptor_requires_owner_to_be_the_source(
    source_entity_id: str,
    target_entity_id: str,
    bidirectional: bool,
    expected_entity_id: str | None,
) -> None:
    base = _world()
    base.add_entity(Entity("animal:pet", "world:test", "animal", "Cat"))
    base.add_relationship(
        Relationship(
            "relationship:pet-owner",
            "world:test",
            source_entity_id,
            target_entity_id,
            "owns",
            bidirectional,
        )
    )
    base.add_event(
        WorldEvent(
            "event:pet-nanjing-trip",
            "world:test",
            "trip",
            "The cat travelled to Nanjing.",
            "2026-08-01T08:00:00+08:00",
            participants=(EventParticipant("animal:pet"),),
            related_entity_ids=("activity:nanjing-trip", "place:nanjing"),
            relationship_ids=("relationship:pet-owner",),
            evidence_ids=("e:pet-trip",),
        )
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个南京旅游的宠物", "e:new", "session:new"),
    )

    assert resolution.entity_id == expected_entity_id
    assert (resolution.state == "resolved") is (expected_entity_id is not None)


def test_gendered_pronoun_conflicting_with_structural_parent_stays_unresolved() -> None:
    base = _world()
    history = (
        _accepted("person:mother", "妈妈", "e:prior", "session:one"),
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("他", "e:new", "session:two", kind_hint="person"),
        history,
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None


def test_public_resolution_dto_rejects_a_resolved_state_without_a_target() -> None:
    mention = _mention("小林", "e:new", "session:new")

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolution(
            mention=mention,
            state="resolved",
            basis="canonical",
            candidates=(),
        )

    assert raised.value.issues == (
        "resolution.resolved.candidates.empty",
        "resolution.resolved.entity_id.invalid",
    )


def test_public_resolution_dto_fails_closed_for_malformed_nested_values() -> None:
    mention = _mention("小林", "e:new", "session:new")

    with pytest.raises(EntityIdentityValidationError) as candidates_error:
        EntityReferenceResolution(
            mention=mention,
            state="resolved",
            basis="canonical",
            candidates=("bad",),  # type: ignore[arg-type]
            entity_id="person:nanjing-friend",
        )
    with pytest.raises(EntityIdentityValidationError) as scalar_candidates_error:
        EntityReferenceResolution(
            mention=mention,
            state="resolved",
            basis="canonical",
            candidates=42,  # type: ignore[arg-type]
            entity_id="person:nanjing-friend",
        )
    with pytest.raises(EntityIdentityValidationError) as alias_error:
        EntityReferenceResolution(
            mention=mention,
            state="resolved",
            basis="canonical",
            candidates=(
                EntityCandidate(
                    "person:nanjing-friend",
                    100,
                    ("canonical:小林",),
                ),
            ),
            entity_id="person:nanjing-friend",
            alias_proposal="bad",  # type: ignore[arg-type]
        )

    assert candidates_error.value.issues == ("resolution.candidates.invalid",)
    assert scalar_candidates_error.value.issues == ("resolution.candidates.invalid",)
    assert alias_error.value.issues == ("resolution.alias_proposal.invalid",)


@pytest.mark.parametrize("accepted_history", (None, 42, object(), "not-history"))
def test_public_resolver_rejects_non_sequence_history_with_a_controlled_error(
    accepted_history: object,
) -> None:
    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(
            _world(),
            _mention("她", "e:new", "session:new", kind_hint="person"),
            accepted_history,  # type: ignore[arg-type]
        )

    assert raised.value.issues == ("accepted_history.not_sequence",)


def test_public_resolver_preflights_malformed_entity_identity_fields() -> None:
    bad_kind = _world()
    bad_kind.add_entity(
        Entity(
            "person:bad-kind",
            "world:test",
            None,  # type: ignore[arg-type]
            "Bad kind",
        )
    )
    bad_alias = _world()
    bad_alias.add_entity(
        Entity(
            "person:bad-alias",
            "world:test",
            "person",
            "Bad alias",
            (None,),  # type: ignore[arg-type]
        )
    )

    with pytest.raises(EntityIdentityValidationError) as kind_error:
        EntityReferenceResolver().resolve(
            bad_kind,
            _mention("未知", "e:new-kind", "session:new"),
        )
    with pytest.raises(EntityIdentityValidationError) as alias_error:
        EntityReferenceResolver().resolve(
            bad_alias,
            _mention("未知", "e:new-alias", "session:new"),
        )

    assert kind_error.value.issues == ("base.entities[6].kind.invalid",)
    assert alias_error.value.issues == ("base.entities[6].aliases[0].invalid",)


@pytest.mark.parametrize(
    ("field_name", "value", "expected_issue"),
    (
        ("world_id", "world:other", "base.relationships[0].world_id.invalid"),
        (
            "source_entity_id",
            "person:missing",
            "base.relationships[0].source_entity_id.unknown",
        ),
        (
            "target_entity_id",
            "person:missing",
            "base.relationships[0].target_entity_id.unknown",
        ),
        ("relation_type", None, "base.relationships[0].relation_type.invalid"),
        ("bidirectional", "yes", "base.relationships[0].bidirectional.invalid"),
        ("status", [], "base.relationships[0].status.invalid"),
        ("valid_from", 42, "base.relationships[0].valid_from.invalid"),
        ("valid_to", object(), "base.relationships[0].valid_to.invalid"),
    ),
)
def test_public_resolver_preflights_relationship_fields_used_by_resolution(
    field_name: str,
    value: object,
    expected_issue: str,
) -> None:
    base = _world()
    relationship = base.relationships["relationship:owner-mother"]
    base.relationships[relationship.id] = replace(
        relationship,
        **{field_name: value},  # type: ignore[arg-type]
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(
            base,
            _mention("未知", "e:new", "session:new"),
        )

    assert expected_issue in raised.value.issues


@pytest.mark.parametrize(
    ("field_name", "value", "expected_issue"),
    (
        ("world", object(), "base.world.type.invalid"),
        ("entities", [], "base.entities.not_dict"),
        ("relationships", [], "base.relationships.not_dict"),
        ("events", [], "base.events.not_dict"),
        ("cognitions", [], "base.cognitions.not_dict"),
    ),
)
def test_public_resolver_preflights_every_graph_container_runtime_type(
    field_name: str,
    value: object,
    expected_issue: str,
) -> None:
    base = _world()
    setattr(base, field_name, value)

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(base, _mention("未知", "e:new", "session:new"))

    assert expected_issue in raised.value.issues


def test_public_resolver_converts_an_invalid_base_type_to_a_controlled_error() -> None:
    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(
            object(),  # type: ignore[arg-type]
            _mention("未知", "e:new", "session:new"),
        )

    assert raised.value.issues == ("base.type.invalid",)


def test_alias_preview_converts_an_invalid_base_type_to_a_controlled_error() -> None:
    proposal = EntityAliasProposal(
        "person:mother",
        "我妈",
        ("e:identity",),
        "owner-relative-alias",
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        proposal.preview(
            object(),  # type: ignore[arg-type]
            (_mention("我妈", "e:identity", "session:new"),),
        )

    assert raised.value.issues == ("base.type.invalid",)


@pytest.mark.parametrize("collection_name", ("entities", "relationships", "events", "cognitions"))
def test_public_resolver_preflights_every_record_dictionary_key(
    collection_name: str,
) -> None:
    base = _world()
    if collection_name == "cognitions":
        cognition = _cognition()
        base.add_cognition(cognition)

    collection = getattr(base, collection_name)
    records = list(collection.items())
    record_id, record = records[0]
    collection.clear()
    collection[f"wrong-key:{record_id}"] = record
    collection.update(records[1:])

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(base, _mention("未知", "e:new", "session:new"))

    assert raised.value.issues == (f"base.{collection_name}[0].key.invalid",)


@pytest.mark.parametrize(
    ("field_name", "value", "expected_issue"),
    (
        ("participants", [], "base.events[0].participants.not_tuple"),
        ("related_entity_ids", [], "base.events[0].related_entity_ids.not_tuple"),
        ("relationship_ids", [], "base.events[0].relationship_ids.not_tuple"),
        ("facets", [], "base.events[0].facets.not_tuple"),
        ("evidence_ids", [], "base.events[0].evidence_ids.not_tuple"),
    ),
)
def test_public_resolver_preflights_event_tuple_runtime_shapes(
    field_name: str,
    value: object,
    expected_issue: str,
) -> None:
    base = _world()
    event = base.events["event:nanjing-trip"]
    base.events[event.id] = replace(
        event,
        **{field_name: value},  # type: ignore[arg-type]
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(base, _mention("未知", "e:new", "session:new"))

    assert raised.value.issues == (expected_issue,)


@pytest.mark.parametrize(
    ("field_name", "value", "expected_issue"),
    (
        (
            "participants",
            (EventParticipant("person:missing"),),
            "base.events[0].participants[0].entity_id.unknown",
        ),
        (
            "related_entity_ids",
            ("person:missing",),
            "base.events[0].related_entity_ids[0].unknown",
        ),
        (
            "relationship_ids",
            ("relationship:missing",),
            "base.events[0].relationship_ids[0].unknown",
        ),
        (
            "facets",
            (EventFacet("outcome", "unknown", "person:missing"),),
            "base.events[0].facets[0].about_entity_id.unknown",
        ),
    ),
)
def test_public_resolver_preflights_all_event_referential_edges(
    field_name: str,
    value: object,
    expected_issue: str,
) -> None:
    base = _world()
    event = base.events["event:nanjing-trip"]
    base.events[event.id] = replace(
        event,
        **{field_name: value},  # type: ignore[arg-type]
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(base, _mention("未知", "e:new", "session:new"))

    assert raised.value.issues == (expected_issue,)


@pytest.mark.parametrize(
    ("field_name", "value", "expected_issue"),
    (
        ("participants", (object(),), "base.events[0].participants[0].type.invalid"),
        ("facets", (object(),), "base.events[0].facets[0].type.invalid"),
    ),
)
def test_public_resolver_preflights_event_member_runtime_shapes(
    field_name: str,
    value: object,
    expected_issue: str,
) -> None:
    base = _world()
    event = base.events["event:nanjing-trip"]
    base.events[event.id] = replace(
        event,
        **{field_name: value},  # type: ignore[arg-type]
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(base, _mention("未知", "e:new", "session:new"))

    assert raised.value.issues == (expected_issue,)


@pytest.mark.parametrize(
    ("target", "expected_issue"),
    (
        (MemoryTarget("world", "world:missing"), "base.cognitions[0].target.id.unknown"),
        (MemoryTarget("entity", "person:missing"), "base.cognitions[0].target.id.unknown"),
        (
            MemoryTarget("relationship", "relationship:missing"),
            "base.cognitions[0].target.id.unknown",
        ),
        (MemoryTarget("event", "event:missing"), "base.cognitions[0].target.id.unknown"),
    ),
)
def test_public_resolver_preflights_all_cognition_target_edges(
    target: MemoryTarget,
    expected_issue: str,
) -> None:
    base = _world()
    cognition = _cognition()
    base.cognitions[cognition.id] = replace(cognition, target=target)

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(base, _mention("未知", "e:new", "session:new"))

    assert raised.value.issues == (expected_issue,)


@pytest.mark.parametrize(
    ("field_name", "value", "expected_issue"),
    (
        ("target", None, "base.cognitions[0].target.type.invalid"),
        ("perspective", None, "base.cognitions[0].perspective.type.invalid"),
        ("sources", [], "base.cognitions[0].sources.not_tuple"),
    ),
)
def test_public_resolver_preflights_cognition_runtime_member_shapes(
    field_name: str,
    value: object,
    expected_issue: str,
) -> None:
    base = _world()
    cognition = _cognition()
    base.cognitions[cognition.id] = replace(
        cognition,
        **{field_name: value},  # type: ignore[arg-type]
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(base, _mention("未知", "e:new", "session:new"))

    assert raised.value.issues == (expected_issue,)


@pytest.mark.parametrize(
    ("field_name", "value", "expected_issue"),
    (
        ("content_type", [], "base.cognitions[0].content_type.invalid"),
        ("formed_by", [], "base.cognitions[0].formed_by.invalid"),
        ("cred_status", [], "base.cognitions[0].cred_status.invalid"),
        (
            "target",
            MemoryTarget([], "person:owner"),  # type: ignore[arg-type]
            "base.cognitions[0].target.kind.invalid",
        ),
        (
            "perspective",
            Perspective([], ()),  # type: ignore[arg-type]
            "base.cognitions[0].perspective.kind.invalid",
        ),
        (
            "sources",
            (EvidenceLink("e:source", []),),  # type: ignore[arg-type]
            "base.cognitions[0].sources[0].relation.invalid",
        ),
    ),
)
def test_public_resolver_contains_unhashable_cognition_runtime_fields(
    field_name: str,
    value: object,
    expected_issue: str,
) -> None:
    base = _world()
    cognition = _cognition()
    base.cognitions[cognition.id] = replace(
        cognition,
        **{field_name: value},  # type: ignore[arg-type]
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(base, _mention("未知", "e:new", "session:new"))

    assert raised.value.issues == (expected_issue,)


def test_public_resolver_preflights_cognition_perspective_holder_runtime_shape_and_edge() -> None:
    holder_not_tuple = Perspective("entity", ["person:owner"])  # type: ignore[arg-type]
    dangling_holder = Perspective("entity", ("person:missing",))

    for perspective, expected_issue in (
        (
            holder_not_tuple,
            "base.cognitions[0].perspective.holder_entity_ids.not_tuple",
        ),
        (
            dangling_holder,
            "base.cognitions[0].perspective.holder_entity_ids[0].unknown",
        ),
    ):
        base = _world()
        cognition = _cognition()
        base.cognitions[cognition.id] = replace(cognition, perspective=perspective)

        with pytest.raises(EntityIdentityValidationError) as raised:
            EntityReferenceResolver().resolve(
                base,
                _mention("未知", "e:new", "session:new"),
            )

        assert raised.value.issues == (expected_issue,)


def test_public_resolver_rejects_cross_kind_object_id_collisions() -> None:
    base = _world()
    relationship = base.relationships.pop("relationship:owner-mother")
    base.relationships["person:mother"] = replace(relationship, id="person:mother")

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(base, _mention("未知", "e:new", "session:new"))

    assert "base.relationships[2].id.cross_kind_collision" in raised.value.issues


@pytest.mark.parametrize("label", ("朋友", "friend", "同事", "colleague", "同学", "classmate", "宠物", "pet", "喜欢的人", "romantic interest"))
@pytest.mark.parametrize("identity_field", ("canonical_name", "aliases"))
def test_role_only_exact_labels_cannot_resolve_a_unique_entity(
    label: str,
    identity_field: str,
) -> None:
    base = _world()
    entity = Entity(
        "person:role-only",
        "world:test",
        "person",
        label if identity_field == "canonical_name" else "Specific person",
        () if identity_field == "canonical_name" else (label,),
    )
    base.add_entity(entity)

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention(label, "e:role-only", "session:new"),
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "ROLE_ONLY_REFERENCE_REQUIRES_BINDING"


def test_same_evidence_id_cannot_claim_conflicting_history_metadata() -> None:
    history = (
        _accepted(
            "person:nanjing-friend",
            "小林",
            "e:same",
            "session:one",
            occurred_at="2026-08-10T08:00:00+08:00",
        ),
        _accepted(
            "person:other-friend",
            "小周",
            "e:same",
            "session:other",
            occurred_at="2026-08-10T09:00:00+08:00",
        ),
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(
            _world(),
            _mention("她", "e:new", "session:new", kind_hint="person"),
            history,
        )

    assert raised.value.issues == (
        "accepted_history[1].evidence_metadata.conflict",
    )


def test_one_weak_generic_friend_candidate_stays_unresolved() -> None:
    base = _world()
    base.events.clear()

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个朋友", "e:weak", "session:later"),
    )

    assert resolution.state == "ambiguous"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "STRUCTURED_DESCRIPTION_AMBIGUOUS"


def test_unknown_mention_projects_to_the_existing_unresolved_sidecar() -> None:
    mention = _mention("上周在车站遇到的那个人", "e:unknown", "session:new")

    resolution = EntityReferenceResolver().resolve(_world(), mention)

    assert resolution.state == "unresolved"
    assert resolution.candidates == ()
    unresolved = resolution.as_unresolved_reference()
    assert unresolved is not None
    assert unresolved.mention == mention.text
    assert unresolved.evidence_ids == ("e:unknown",)


def test_assistant_text_cannot_be_constructed_as_identity_evidence() -> None:
    with pytest.raises(EntityIdentityValidationError) as raised:
        ReferenceMention(
            "小林",
            "turn:assistant",
            "session:one",
            "2026-08-10T10:00:00+08:00",
            source_role="assistant",  # type: ignore[arg-type]
        )

    assert raised.value.issues == ("source_role.ineligible",)


def test_unknown_entity_in_accepted_history_fails_closed() -> None:
    history = (_accepted("person:missing", "某人", "e:old", "session:old"),)

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(
            _world(),
            _mention("她", "e:new", "session:new", kind_hint="person"),
            history,
        )

    assert raised.value.issues == ("accepted_history[0].entity_id.unknown",)


def test_candidate_retrieval_is_deterministic() -> None:
    base = _world()
    mention = _mention("那个南京旅游的朋友", "e:descriptor", "session:later")
    resolver = EntityReferenceResolver()

    first = resolver.retrieve_candidates(base, mention)
    second = resolver.retrieve_candidates(base, mention)

    assert first == second


def test_owner_relative_form_cannot_be_stolen_by_an_unrelated_exact_alias() -> None:
    base = _world()
    base.add_entity(Entity("person:other", "world:test", "person", "Another", ("我妈",)))

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("我妈", "e:collision", "session:new"),
    )

    assert resolution.state == "ambiguous"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "OWNER_RELATIVE_ALIAS_COLLISION"
    assert set(resolution.candidate_entity_ids) == {"person:mother", "person:other"}


def test_pronoun_semantics_precede_an_entity_named_like_the_pronoun() -> None:
    base = _world()
    base.add_entity(Entity("work:pronoun", "world:test", "work", "她"))
    history = (
        _accepted("person:nanjing-friend", "小林", "e:prior", "session:one"),
    )

    resolved = EntityReferenceResolver().resolve(
        base,
        _mention("她", "e:new", "session:two", kind_hint="person"),
        history,
    )
    unresolved = EntityReferenceResolver().resolve(
        base,
        _mention(
            "她",
            "e:no-context",
            "session:other",
            kind_hint="person",
            continuity_id="continuity:other",
        ),
        history,
    )

    assert resolved.entity_id == "person:nanjing-friend"
    assert unresolved.state == "unresolved"
    assert unresolved.candidates == ()


def test_recent_context_uses_time_not_caller_sequence_order() -> None:
    base = _world()
    history = (
        _accepted("person:nanjing-friend", "小林", "e:latest-a", "session:one"),
        _accepted("person:other-friend", "小周", "e:latest-b", "session:one"),
        _accepted(
            "person:mother",
            "妈妈",
            "e:old",
            "session:old",
            occurred_at="2026-08-10T08:00:00+08:00",
        ),
    )
    mention = _mention("她", "e:new", "session:new", kind_hint="person")

    forward = EntityReferenceResolver().resolve(base, mention, history)
    reverse = EntityReferenceResolver().resolve(base, mention, tuple(reversed(history)))

    assert forward == reverse
    assert forward.state == "ambiguous"
    assert set(forward.candidate_entity_ids) == {
        "person:nanjing-friend",
        "person:other-friend",
    }


def test_future_reference_cannot_bind_a_current_mention() -> None:
    history = (
        _accepted(
            "person:nanjing-friend",
            "小林",
            "e:future",
            "session:future",
            occurred_at="2026-08-10T11:00:00+08:00",
        ),
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        EntityReferenceResolver().resolve(
            _world(),
            _mention("她", "e:now", "session:now", kind_hint="person"),
            history,
        )

    assert raised.value.issues == ("accepted_history[0].occurred_at.not_prior",)


def test_cross_session_pronoun_requires_an_explicit_shared_continuity_window() -> None:
    history = (
        _accepted(
            "person:nanjing-friend",
            "小林",
            "e:prior",
            "session:one",
            continuity_id="continuity:one",
        ),
    )

    resolution = EntityReferenceResolver().resolve(
        _world(),
        _mention(
            "她",
            "e:new",
            "session:two",
            kind_hint="person",
            continuity_id="continuity:two",
        ),
        history,
    )

    assert resolution.state == "unresolved"
    assert resolution.uncertainty_code == "NO_ACCEPTED_USER_REFERENCE_CONTEXT"


def test_possessive_descriptor_does_not_resolve_the_anchor_as_referent() -> None:
    resolution = EntityReferenceResolver().resolve(
        _world(),
        _mention("小林的朋友", "e:descriptor", "session:new"),
    )

    assert resolution.state == "unresolved"
    assert resolution.entity_id is None
    assert resolution.uncertainty_code == "POSSESSIVE_REFERENCE_REQUIRES_ROLE_BINDING"


def test_duplicate_relationship_records_do_not_manufacture_resolution() -> None:
    base = _world()
    base.events.clear()
    base.relationships.pop("relationship:owner-other-friend")
    base.add_relationship(
        Relationship(
            "relationship:owner-nanjing-friend-duplicate",
            "world:test",
            "person:owner",
            "person:nanjing-friend",
            "friend",
            True,
        )
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个朋友", "e:generic", "session:new"),
    )

    assert resolution.state == "unresolved"
    assert resolution.candidates[0].score == 1
    assert resolution.candidates[0].reasons == ("owner-relationship:friend",)


def test_repeated_trip_records_do_not_break_a_semantic_tie() -> None:
    base = _world()
    event = base.events["event:nanjing-trip"]
    base.events[event.id] = replace(
        event,
        participants=(*event.participants, EventParticipant("person:other-friend", "traveler")),
        relationship_ids=(*event.relationship_ids, "relationship:owner-other-friend"),
    )
    base.add_event(
        replace(
            event,
            id="event:nanjing-trip-repeat",
            summary="A second accepted expression of the same trip context.",
        )
    )

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("那个南京旅游的朋友", "e:descriptor", "session:new"),
    )

    assert resolution.state == "ambiguous"
    assert resolution.candidates[0].score == resolution.candidates[1].score


def test_exact_label_collision_is_global_even_with_an_explicit_kind_hint() -> None:
    base = _world()
    base.add_entity(Entity("person:dog-name", "world:test", "person", "狗"))
    base.add_entity(Entity("animal:dog", "world:test", "animal", "狗"))

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("狗", "e:collision", "session:new", kind_hint="animal"),
    )

    assert resolution.state == "ambiguous"
    assert resolution.uncertainty_code == "EXACT_LABEL_COLLISION"
    assert set(resolution.candidate_entity_ids) == {"person:dog-name", "animal:dog"}


def test_exact_identity_normalization_keeps_meaningful_punctuation_distinct() -> None:
    base = _world()
    base.add_entity(Entity("work:cpp", "world:test", "work", "C++"))
    base.add_entity(Entity("work:csharp", "world:test", "work", "C#"))

    resolution = EntityReferenceResolver().resolve(
        base,
        _mention("C++", "e:exact", "session:new"),
    )

    assert resolution.state == "resolved"
    assert resolution.entity_id == "work:cpp"


def test_alias_preview_revalidates_target_relationship_and_exact_evidence_span() -> None:
    base = _world()
    unsupported = EntityAliasProposal(
        "person:nanjing-friend",
        "我妈",
        ("e:identity",),
        "owner-relative-alias",
    )
    mismatched = EntityAliasProposal(
        "person:mother",
        "我妈",
        ("e:weather",),
        "owner-relative-alias",
    )

    with pytest.raises(EntityIdentityValidationError) as unsupported_error:
        unsupported.preview(
            base,
            (_mention("我妈", "e:identity", "session:new"),),
        )
    with pytest.raises(EntityIdentityValidationError) as mismatch_error:
        mismatched.preview(
            base,
            (_mention("今天天气不错", "e:weather", "session:new"),),
        )

    assert "alias.owner_relative_target.unsupported" in unsupported_error.value.issues
    assert mismatch_error.value.issues == ("evidence_mention.mismatch:e:weather",)


def test_alias_preview_handles_malformed_public_inputs_without_raw_exceptions() -> None:
    base = _world()

    with pytest.raises(EntityIdentityValidationError) as alias_error:
        EntityAliasProposal(
            "person:mother",
            None,  # type: ignore[arg-type]
            ("e:identity",),
            "owner-relative-alias",
        )
    with pytest.raises(EntityIdentityValidationError) as evidence_error:
        EntityAliasProposal(
            "person:mother",
            "我妈",
            42,  # type: ignore[arg-type]
            "owner-relative-alias",
        )
    with pytest.raises(TypeError, match="verified_user_mentions must be a collection"):
        EntityAliasProposal(
            "person:mother",
            "我妈",
            ("e:identity",),
            "owner-relative-alias",
        ).preview(base, 42)  # type: ignore[arg-type]

    assert alias_error.value.issues == ("alias.invalid",)
    assert evidence_error.value.issues == ("evidence_ids.not_tuple",)


def test_resolution_basis_and_alias_proposal_must_match_the_primary_reason() -> None:
    base = _world()
    exact = EntityReferenceResolver().resolve(
        base,
        _mention("小林", "e:exact", "session:new"),
    )
    proposal = EntityAliasProposal(
        "person:nanjing-friend",
        "我妈",
        ("e:exact",),
        "owner-relative-alias",
    )

    with pytest.raises(EntityIdentityValidationError) as basis_error:
        replace(exact, basis="structured-description")
    with pytest.raises(EntityIdentityValidationError) as proposal_error:
        replace(exact, alias_proposal=proposal)

    assert basis_error.value.issues == (
        "resolution.resolved.basis.not_supported",
    )
    assert proposal_error.value.issues == (
        "resolution.resolved.alias_proposal.unexpected",
    )


@pytest.mark.parametrize(
    ("mention_text", "forged_reason"),
    (
        ("Friend A", "canonical:Friend B"),
        ("小林", "alias:小周"),
    ),
)
def test_exact_resolution_reason_payload_must_match_the_mention(
    mention_text: str,
    forged_reason: str,
) -> None:
    resolution = EntityReferenceResolver().resolve(
        _world(),
        _mention(mention_text, "e:exact", "session:new"),
    )
    forged_candidate = EntityCandidate(
        resolution.entity_id or "missing",
        100,
        (forged_reason,),
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        replace(resolution, candidates=(forged_candidate,))

    assert raised.value.issues == (
        "resolution.resolved.exact_reason.mention_mismatch",
    )


def test_structured_resolution_dto_requires_all_structure_derived_from_mention() -> None:
    resolution = EntityReferenceResolver().resolve(
        _world(),
        _mention("那个南京旅游的朋友", "e:descriptor", "session:new"),
    )
    forged_primary = EntityCandidate(
        resolution.entity_id or "missing",
        100,
        ("owner-relationship:friend",),
    )

    with pytest.raises(EntityIdentityValidationError) as raised:
        replace(resolution, candidates=(forged_primary,))

    assert raised.value.issues == (
        "resolution.resolved.structured.reason.not_derived",
    )
