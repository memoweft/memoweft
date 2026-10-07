"""In-memory graph assembly for MemoWeft Next.

This is deliberately storage-agnostic.  Its first job is to make the new domain
rules executable and testable before we commit to a SQLite schema or retrieval
backend.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Entity, MemoryTarget, PersonalWorld, Relationship, WorldCognition, WorldEvent


@dataclass(frozen=True, slots=True)
class WorldSlice:
    """A local subgraph suitable for recall/context assembly."""

    entity_ids: frozenset[str]
    relationship_ids: frozenset[str]
    event_ids: frozenset[str]
    cognition_ids: frozenset[str]


@dataclass(slots=True)
class MemoryWorldGraph:
    """Reference graph for validating and locally expanding a personal world."""

    world: PersonalWorld
    entities: dict[str, Entity] = field(default_factory=dict)
    relationships: dict[str, Relationship] = field(default_factory=dict)
    events: dict[str, WorldEvent] = field(default_factory=dict)
    cognitions: dict[str, WorldCognition] = field(default_factory=dict)

    def _require_world(self, world_id: str) -> None:
        if world_id != self.world.world_id:
            raise ValueError(f"object belongs to world {world_id!r}, expected {self.world.world_id!r}")

    def add_entity(self, entity: Entity) -> None:
        self._require_world(entity.world_id)
        if entity.id in self.entities:
            raise ValueError(f"duplicate entity id: {entity.id}")
        self.entities[entity.id] = entity

    def add_relationship(self, relationship: Relationship) -> None:
        self._require_world(relationship.world_id)
        self._require_entity(relationship.source_entity_id)
        self._require_entity(relationship.target_entity_id)
        if relationship.id in self.relationships:
            raise ValueError(f"duplicate relationship id: {relationship.id}")
        self.relationships[relationship.id] = relationship

    def add_event(self, event: WorldEvent) -> None:
        self._require_world(event.world_id)
        for participant in event.participants:
            self._require_entity(participant.entity_id)
        for entity_id in event.related_entity_ids:
            self._require_entity(entity_id)
        for relationship_id in event.relationship_ids:
            self._require_relationship(relationship_id)
        for facet in event.facets:
            if facet.about_entity_id is not None:
                self._require_entity(facet.about_entity_id)
        if event.id in self.events:
            raise ValueError(f"duplicate event id: {event.id}")
        self.events[event.id] = event

    def add_cognition(self, cognition: WorldCognition) -> None:
        self._require_world(cognition.world_id)
        self._require_target(cognition.target)
        for holder in cognition.perspective.holder_entity_ids:
            self._require_entity(holder)
        if cognition.id in self.cognitions:
            raise ValueError(f"duplicate cognition id: {cognition.id}")
        self.cognitions[cognition.id] = cognition

    def validate_owner(self) -> None:
        self._require_entity(self.world.owner_entity_id)

    def expand(self, anchor: MemoryTarget, *, depth: int = 1) -> WorldSlice:
        """Expand outward from one world object.

        This is not semantic retrieval yet.  It is the graph primitive semantic
        retrieval will call *after* it resolves an anchor such as the Nanjing
        trip or a past conflict event.
        """

        if depth < 0:
            raise ValueError("depth must be >= 0")
        self._require_target(anchor)

        entity_ids: set[str] = set()
        relationship_ids: set[str] = set()
        event_ids: set[str] = set()
        cognition_ids: set[str] = set()

        frontier: set[tuple[str, str]] = {(anchor.kind, anchor.id)}
        seen: set[tuple[str, str]] = set()

        for _ in range(depth + 1):
            next_frontier: set[tuple[str, str]] = set()
            for kind, object_id in frontier:
                key = (kind, object_id)
                if key in seen:
                    continue
                seen.add(key)

                if kind == "world":
                    next_frontier.add(("entity", self.world.owner_entity_id))
                elif kind == "entity":
                    entity_ids.add(object_id)
                    for rel in self.relationships.values():
                        if object_id in (rel.source_entity_id, rel.target_entity_id):
                            relationship_ids.add(rel.id)
                            next_frontier.add(("relationship", rel.id))
                    for event in self.events.values():
                        participant_ids = {p.entity_id for p in event.participants}
                        if object_id in participant_ids or object_id in event.related_entity_ids:
                            event_ids.add(event.id)
                            next_frontier.add(("event", event.id))
                elif kind == "relationship":
                    relationship_ids.add(object_id)
                    rel = self.relationships[object_id]
                    next_frontier.add(("entity", rel.source_entity_id))
                    next_frontier.add(("entity", rel.target_entity_id))
                    for event in self.events.values():
                        if object_id in event.relationship_ids:
                            event_ids.add(event.id)
                            next_frontier.add(("event", event.id))
                elif kind == "event":
                    event_ids.add(object_id)
                    event = self.events[object_id]
                    for participant in event.participants:
                        entity_ids.add(participant.entity_id)
                        next_frontier.add(("entity", participant.entity_id))
                    for entity_id in event.related_entity_ids:
                        entity_ids.add(entity_id)
                        next_frontier.add(("entity", entity_id))
                    for relationship_id in event.relationship_ids:
                        relationship_ids.add(relationship_id)
                        next_frontier.add(("relationship", relationship_id))

                for cognition in self.cognitions.values():
                    if cognition.target.kind == kind and cognition.target.id == object_id:
                        cognition_ids.add(cognition.id)
            frontier = next_frontier

        return WorldSlice(
            entity_ids=frozenset(entity_ids),
            relationship_ids=frozenset(relationship_ids),
            event_ids=frozenset(event_ids),
            cognition_ids=frozenset(cognition_ids),
        )

    def _require_target(self, target: MemoryTarget) -> None:
        if target.kind == "world":
            if target.id != self.world.world_id:
                raise ValueError(f"unknown world target: {target.id}")
            return
        if target.kind == "entity":
            self._require_entity(target.id)
            return
        if target.kind == "relationship":
            self._require_relationship(target.id)
            return
        if target.kind == "event":
            if target.id not in self.events:
                raise ValueError(f"unknown event id: {target.id}")
            return
        raise ValueError(f"unsupported target kind: {target.kind}")

    def _require_entity(self, entity_id: str) -> None:
        if entity_id not in self.entities:
            raise ValueError(f"unknown entity id: {entity_id}")

    def _require_relationship(self, relationship_id: str) -> None:
        if relationship_id not in self.relationships:
            raise ValueError(f"unknown relationship id: {relationship_id}")
