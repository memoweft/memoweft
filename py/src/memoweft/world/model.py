"""MemoWeft Next personal-memory-world domain model.

The TypeScript 1.x model treats ``subject_id`` as the semantic center of memory.
This module starts the next architecture with a different rule:

    the user owns the world, but is not the subject of every memory.

World entities, relationships, events and cognitions are separate first-class
objects.  Perspective (who holds a belief) and provenance (which evidence
supports it) are intentionally orthogonal.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from ..types import ContentType, CredStatus, EvidenceLink, FormedBy

EntityKind = str
TargetKind = Literal["world", "entity", "relationship", "event"]
PerspectiveKind = Literal["entity", "joint", "system"]


@dataclass(frozen=True, slots=True)
class PersonalWorld:
    """One person's memory world / namespace.

    ``world_id`` replaces the old semantic use of ``subject_id``.  Every object
    belongs to this world, while its actual semantic target may be any entity,
    relationship or event inside it.
    """

    world_id: str
    owner_entity_id: str


@dataclass(frozen=True, slots=True)
class Entity:
    """A persistent thing that can own local memory.

    ``kind`` is deliberately open-ended in Next.  Person, agent, project,
    organization, place and device are expected common values, but the storage
    contract should not require a major migration to add another kind.
    """

    id: str
    world_id: str
    kind: EntityKind
    canonical_name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Relationship:
    """A first-class relationship object, not merely a graph edge.

    Because it has its own id, events and cognitions can target the relationship
    itself (for example, "travel-planning differences are a recurring friction
    point between the user and Friend_X").
    """

    id: str
    world_id: str
    source_entity_id: str
    target_entity_id: str
    relation_type: str
    bidirectional: bool = False
    status: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None


@dataclass(frozen=True, slots=True)
class EventParticipant:
    entity_id: str
    role: Optional[str] = None


@dataclass(frozen=True, slots=True)
class EventFacet:
    """Queryable semantic detail kept alongside the event narrative.

    The event remains narratively expressive, while facets preserve details a
    later query may need to reconstruct: cause, position, outcome, destination,
    and so on.  ``about_entity_id`` lets two participants hold different
    positions without flattening them into one summary.
    """

    key: str
    value: str
    about_entity_id: Optional[str] = None


@dataclass(frozen=True, slots=True)
class WorldEvent:
    """A lived-world event that can be used as a future recall anchor."""

    id: str
    world_id: str
    event_type: str
    summary: str
    occurred_at: str
    participants: tuple[EventParticipant, ...] = ()
    related_entity_ids: tuple[str, ...] = ()
    relationship_ids: tuple[str, ...] = ()
    facets: tuple[EventFacet, ...] = ()
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryTarget:
    """What a cognition is *about*.

    This is intentionally separate from perspective and provenance.
    """

    kind: TargetKind
    id: str


@dataclass(frozen=True, slots=True)
class Perspective:
    """Who holds/interprets a cognition.

    ``entity`` means one world entity's perspective; ``joint`` is a shared
    interpretation formed by multiple participants; ``system`` is an explicitly
    system-level interpretation and has no holder entity.  None of these fields
    are evidence by themselves.
    """

    kind: PerspectiveKind
    holder_entity_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind == "entity" and len(self.holder_entity_ids) != 1:
            raise ValueError("entity perspective requires exactly one holder")
        if self.kind == "joint" and len(self.holder_entity_ids) < 2:
            raise ValueError("joint perspective requires at least two holders")
        if self.kind == "system" and self.holder_entity_ids:
            raise ValueError("system perspective cannot have entity holders")


@dataclass(frozen=True, slots=True)
class WorldCognition:
    """A belief/understanding attached to a world object.

    ``target`` answers "what is this about?".
    ``perspective`` answers "from whose point of view?".
    ``sources`` answers "what evidence supports or contradicts it?".

    Keeping those dimensions separate is the central epistemic rule of the Next
    architecture.
    """

    id: str
    world_id: str
    target: MemoryTarget
    content: str
    content_type: ContentType
    formed_by: FormedBy
    confidence: int
    cred_status: CredStatus
    perspective: Perspective
    sources: tuple[EvidenceLink, ...] = ()
    scope: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
