"""Conservative Entity reference-resolution kernel for Stage 2.

This module is deliberately storage- and model-free.  It never mutates the
accepted graph while deciding.  The authoritative Stage 2 path is
``resolve_context``: it consumes the sealed Evidence/review context issued by
``IdentityAuthority`` in :mod:`memoweft.world.identity_review`.  The lower-level
``resolve`` method remains a compatibility kernel for already-trusted inputs;
caller-created history is not identity authority.

The resolver prefers an explicit unresolved/ambiguous result over a weak merge.
The resolver's local alias proposal remains an isolated preview.  Durable alias,
merge, and split transitions are separate, explicitly reviewed identity edits;
they are never guessed by this module.
"""
from __future__ import annotations

from collections.abc import Collection as CollectionABC, Sequence as SequenceABC
from dataclasses import dataclass
from datetime import datetime
import re
import unicodedata
from typing import Collection, Literal, Sequence, TYPE_CHECKING

from .delta import UnresolvedReference
from .graph import MemoryWorldGraph
from ..types import EvidenceLink
from .model import (
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

if TYPE_CHECKING:
    from .identity_review import IdentityResolutionContext

ResolutionState = Literal["resolved", "ambiguous", "unresolved"]
ResolutionBasis = Literal[
    "canonical",
    "alias",
    "owner-relative-alias",
    "recent-user-context",
    "structured-description",
    "none",
]


class EntityIdentityValidationError(ValueError):
    """A caller-owned identity input or alias preview failed closed."""

    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = issues
        super().__init__("Entity identity validation failed: " + ", ".join(issues))


@dataclass(frozen=True, slots=True)
class ReferenceMention:
    """One caller-verified mention carried by eligible user Evidence.

    Mention extraction stays with the caller.  Keeping it separate prevents a
    deterministic identity resolver from becoming a second semantic extractor.
    """

    text: str
    evidence_id: str
    conversation_id: str
    occurred_at: str
    source_role: Literal["user"]
    kind_hint: str | None = None
    continuity_id: str | None = None

    def __post_init__(self) -> None:
        issues = _input_issues(
            (
                ("text", self.text),
                ("evidence_id", self.evidence_id),
                ("conversation_id", self.conversation_id),
                ("occurred_at", self.occurred_at),
            )
        )
        if self.kind_hint is not None and not _non_empty(self.kind_hint):
            issues.append("kind_hint.invalid")
        if self.continuity_id is not None and not _non_empty(self.continuity_id):
            issues.append("continuity_id.invalid")
        if _timestamp(self.occurred_at) is None:
            issues.append("occurred_at.invalid_timestamp")
        if self.source_role != "user":
            issues.append("source_role.ineligible")
        if issues:
            raise EntityIdentityValidationError(tuple(issues))


@dataclass(frozen=True, slots=True)
class AcceptedEntityReference:
    """An authority-bearing prior binding supplied by the trusted review layer.

    The DTO validates shape and temporal consistency; it is not itself proof
    that the cited Evidence or review decision exists.
    """

    entity_id: str
    mention: str
    evidence_id: str
    conversation_id: str
    occurred_at: str
    source_role: Literal["user"]
    continuity_id: str | None = None

    def __post_init__(self) -> None:
        issues = _input_issues(
            (
                ("entity_id", self.entity_id),
                ("mention", self.mention),
                ("evidence_id", self.evidence_id),
                ("conversation_id", self.conversation_id),
                ("occurred_at", self.occurred_at),
            )
        )
        if self.continuity_id is not None and not _non_empty(self.continuity_id):
            issues.append("continuity_id.invalid")
        if _timestamp(self.occurred_at) is None:
            issues.append("occurred_at.invalid_timestamp")
        if self.source_role != "user":
            issues.append("source_role.ineligible")
        if issues:
            raise EntityIdentityValidationError(tuple(issues))


@dataclass(frozen=True, slots=True)
class EntityCandidate:
    """One deterministic candidate and the accepted-graph reasons for it."""

    entity_id: str
    score: int
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        issues: list[str] = []
        if not _non_empty(self.entity_id):
            issues.append("candidate.entity_id.invalid")
        if isinstance(self.score, bool) or not isinstance(self.score, int) or self.score <= 0:
            issues.append("candidate.score.invalid")
        if not isinstance(self.reasons, tuple) or not self.reasons:
            issues.append("candidate.reasons.invalid")
        elif any(not _non_empty(reason) for reason in self.reasons):
            issues.append("candidate.reasons.item.invalid")
        if issues:
            raise EntityIdentityValidationError(tuple(issues))


@dataclass(frozen=True, slots=True)
class EntityAliasProposal:
    """A review-only alias addition backed by eligible user Evidence."""

    entity_id: str
    alias: str
    evidence_ids: tuple[str, ...]
    basis: Literal["owner-relative-alias"]

    def __post_init__(self) -> None:
        issues: list[str] = []
        if not _non_empty(self.entity_id):
            issues.append("entity_id.invalid")
        if not _non_empty(self.alias):
            issues.append("alias.invalid")
        if self.basis != "owner-relative-alias":
            issues.append("basis.invalid")
        if not isinstance(self.evidence_ids, tuple):
            issues.append("evidence_ids.not_tuple")
        else:
            if not self.evidence_ids:
                issues.append("evidence_ids.empty")
            valid_evidence_ids = tuple(
                evidence_id
                for evidence_id in self.evidence_ids
                if _non_empty(evidence_id)
            )
            if len(valid_evidence_ids) != len(self.evidence_ids):
                issues.append("evidence_ids.item.invalid")
            elif len(set(valid_evidence_ids)) != len(valid_evidence_ids):
                issues.append("evidence_ids.duplicate")
        if issues:
            raise EntityIdentityValidationError(tuple(issues))

    def preview(
        self,
        base: MemoryWorldGraph,
        verified_user_mentions: Collection[ReferenceMention],
    ) -> MemoryWorldGraph:
        """Return an isolated alias preview backed by verified user mention spans."""

        if not isinstance(base, MemoryWorldGraph):
            raise EntityIdentityValidationError(("base.type.invalid",))
        graph_issues = _base_identity_shape_issues(base)
        if graph_issues:
            raise EntityIdentityValidationError(graph_issues)
        issues: list[str] = []
        entity_id_valid = _non_empty(self.entity_id)
        alias_valid = _non_empty(self.alias)
        evidence_ids_valid = isinstance(self.evidence_ids, tuple)
        evidence_ids = self.evidence_ids if evidence_ids_valid else ()
        if not entity_id_valid:
            issues.append("entity_id.invalid")
        if not alias_valid:
            issues.append("alias.invalid")
        if self.basis != "owner-relative-alias":
            issues.append("basis.invalid")
        if not evidence_ids_valid:
            issues.append("evidence_ids.not_tuple")
        else:
            if not evidence_ids:
                issues.append("evidence_ids.empty")
            valid_evidence_ids = tuple(
                evidence_id
                for evidence_id in evidence_ids
                if _non_empty(evidence_id)
            )
            if (
                len(valid_evidence_ids) == len(evidence_ids)
                and len(set(valid_evidence_ids)) != len(valid_evidence_ids)
            ):
                issues.append("evidence_ids.duplicate")
            for index, evidence_id in enumerate(evidence_ids):
                if not _non_empty(evidence_id):
                    issues.append(f"evidence_ids[{index}].invalid")

        if isinstance(verified_user_mentions, str) or not isinstance(
            verified_user_mentions, CollectionABC
        ):
            raise TypeError("verified_user_mentions must be a collection of ReferenceMention")
        eligible: dict[str, ReferenceMention] = {}
        for index, mention in enumerate(verified_user_mentions):
            if not isinstance(mention, ReferenceMention):
                issues.append(f"verified_user_mentions[{index}].type.invalid")
                continue
            if mention.evidence_id in eligible:
                issues.append(f"verified_user_mentions[{index}].evidence_id.duplicate")
                continue
            eligible[mention.evidence_id] = mention
        alias_key = _identity_key(self.alias) if alias_valid else ""
        for evidence_id in evidence_ids:
            if not _non_empty(evidence_id):
                continue
            if evidence_id not in eligible:
                issues.append(f"evidence_id.not_eligible:{evidence_id}")
            elif alias_valid and _identity_key(eligible[evidence_id].text) != alias_key:
                issues.append(f"evidence_mention.mismatch:{evidence_id}")
        verified_times = tuple(
            _required_timestamp(eligible[evidence_id].occurred_at)
            for evidence_id in evidence_ids
            if evidence_id in eligible
        )
        preview_time = (
            max(verified_times)
            if evidence_ids and len(verified_times) == len(evidence_ids)
            else None
        )

        entity = base.entities.get(self.entity_id) if entity_id_valid else None
        if entity_id_valid and entity is None:
            issues.append("entity_id.unknown")
        if alias_valid and not alias_key:
            issues.append("alias.identity_key.empty")
        conflicts = tuple(
            candidate.id
            for candidate in sorted(base.entities.values(), key=lambda item: item.id)
            if candidate.id != self.entity_id
            and alias_key
            in {
                _identity_key(candidate.canonical_name),
                *map(_identity_key, candidate.aliases),
            }
        )
        if conflicts:
            issues.extend(f"alias.conflicts_with:{entity_id}" for entity_id in conflicts)
        family = _owner_relative_family(self.alias) if alias_valid else None
        if alias_valid and family is None:
            issues.append("alias.owner_relative_form.invalid")
        elif (
            alias_valid
            and family is not None
            and entity is not None
            and preview_time is not None
        ):
            supported_entity_ids = tuple(
                candidate.entity_id
                for candidate in _owner_relative_candidates(
                    base,
                    self.alias,
                    None,
                    preview_time,
                )
            )
            if entity.id not in supported_entity_ids:
                issues.append("alias.owner_relative_target.unsupported")
            if len(supported_entity_ids) != 1:
                issues.append("alias.owner_relative_target.not_unique")
        if issues:
            raise EntityIdentityValidationError(tuple(issues))

        assert entity is not None
        known_aliases = {
            _identity_key(entity.canonical_name),
            *map(_identity_key, entity.aliases),
        }
        aliases = entity.aliases if alias_key in known_aliases else (*entity.aliases, self.alias.strip())
        replacement = Entity(
            id=entity.id,
            world_id=entity.world_id,
            kind=entity.kind,
            canonical_name=entity.canonical_name,
            aliases=aliases,
        )
        result = MemoryWorldGraph(
            world=base.world,
            entities=base.entities.copy(),
            relationships=base.relationships.copy(),
            events=base.events.copy(),
            cognitions=base.cognitions.copy(),
        )
        result.entities[replacement.id] = replacement
        return result


@dataclass(frozen=True, slots=True)
class EntityReferenceResolution:
    """A conservative resolution decision; non-resolved states stay explicit."""

    mention: ReferenceMention
    state: ResolutionState
    basis: ResolutionBasis
    candidates: tuple[EntityCandidate, ...]
    entity_id: str | None = None
    alias_proposal: EntityAliasProposal | None = None
    uncertainty_code: str | None = None

    def __post_init__(self) -> None:
        issues: list[str] = []
        if not isinstance(self.mention, ReferenceMention):
            issues.append("resolution.mention.invalid")
        if self.state not in ("resolved", "ambiguous", "unresolved"):
            issues.append("resolution.state.invalid")
        if self.basis not in (
            "canonical",
            "alias",
            "owner-relative-alias",
            "recent-user-context",
            "structured-description",
            "none",
        ):
            issues.append("resolution.basis.invalid")
        candidates_valid = isinstance(self.candidates, tuple) and all(
            isinstance(candidate, EntityCandidate) for candidate in self.candidates
        )
        if not candidates_valid:
            issues.append("resolution.candidates.invalid")
        elif len({candidate.entity_id for candidate in self.candidates}) != len(
            self.candidates
        ):
            issues.append("resolution.candidates.duplicate")
        if self.alias_proposal is not None and not isinstance(
            self.alias_proposal, EntityAliasProposal
        ):
            issues.append("resolution.alias_proposal.invalid")

        if self.state == "resolved":
            if self.basis == "none":
                issues.append("resolution.resolved.basis.none")
            if candidates_valid and not self.candidates:
                issues.append("resolution.resolved.candidates.empty")
            if not _non_empty(self.entity_id):
                issues.append("resolution.resolved.entity_id.invalid")
            elif (
                candidates_valid
                and self.candidates
                and self.entity_id != self.candidates[0].entity_id
            ):
                issues.append("resolution.resolved.entity_id.not_primary")
            if self.uncertainty_code is not None:
                issues.append("resolution.resolved.uncertainty.present")
            if (
                isinstance(self.alias_proposal, EntityAliasProposal)
                and self.alias_proposal.entity_id != self.entity_id
            ):
                issues.append("resolution.resolved.alias_target.mismatch")
            if candidates_valid and self.candidates:
                primary_reasons = self.candidates[0].reasons
                expected_reason_prefixes: dict[str, tuple[str, ...]] = {
                    "canonical": ("canonical:",),
                    "alias": ("alias:",),
                    "owner-relative-alias": ("owner-family-relationship:",),
                    "recent-user-context": ("accepted-user-reference:",),
                    "structured-description": (
                        "owner-relationship:",
                        "event-type:",
                        "event-anchor:",
                    ),
                }
                expected = (
                    expected_reason_prefixes.get(self.basis, ())
                    if isinstance(self.basis, str)
                    else ()
                )
                basis_supported = not expected or any(
                    reason.startswith(expected) for reason in primary_reasons
                )
                if not basis_supported:
                    issues.append("resolution.resolved.basis.not_supported")
                elif isinstance(self.mention, ReferenceMention):
                    if self.basis in {"canonical", "alias"}:
                        exact_reason_payloads = tuple(
                            reason.split(":", 1)[1]
                            for reason in primary_reasons
                            if reason.startswith(("canonical:", "alias:"))
                        )
                        mention_key = _identity_key(self.mention.text)
                        if any(
                            _identity_key(payload) != mention_key
                            for payload in exact_reason_payloads
                        ):
                            issues.append(
                                "resolution.resolved.exact_reason.mention_mismatch"
                            )
                    elif self.basis == "structured-description":
                        issues.extend(
                            _structured_resolution_reason_issues(
                                self.mention.text,
                                primary_reasons,
                            )
                        )
            if self.basis == "owner-relative-alias":
                if not isinstance(self.alias_proposal, EntityAliasProposal):
                    issues.append("resolution.resolved.alias_proposal.missing")
                elif isinstance(self.mention, ReferenceMention) and (
                    _identity_key(self.alias_proposal.alias)
                    != _identity_key(self.mention.text)
                    or self.alias_proposal.evidence_ids != (self.mention.evidence_id,)
                ):
                    issues.append("resolution.resolved.alias_proposal.not_bound")
            elif isinstance(self.alias_proposal, EntityAliasProposal):
                issues.append("resolution.resolved.alias_proposal.unexpected")
        else:
            if self.basis != "none":
                issues.append("resolution.non_resolved.basis.present")
            if self.entity_id is not None:
                issues.append("resolution.non_resolved.entity_id.present")
            if self.alias_proposal is not None:
                issues.append("resolution.non_resolved.alias_proposal.present")
            if not _non_empty(self.uncertainty_code):
                issues.append("resolution.non_resolved.uncertainty.missing")
            if (
                self.state == "ambiguous"
                and candidates_valid
                and len(self.candidates) < 2
            ):
                issues.append("resolution.ambiguous.candidates.insufficient")
        if issues:
            raise EntityIdentityValidationError(tuple(issues))

    @property
    def candidate_entity_ids(self) -> tuple[str, ...]:
        return tuple(candidate.entity_id for candidate in self.candidates)

    def as_unresolved_reference(self) -> UnresolvedReference | None:
        """Project ambiguity into the existing review-only WorldDelta sidecar."""

        if self.state == "resolved":
            return None
        return UnresolvedReference(self.mention.text, (self.mention.evidence_id,))


class EntityReferenceResolver:
    """Resolve explicit mentions without fuzzy or model-selected identity merges."""

    def resolve_context(
        self,
        context: IdentityResolutionContext,
    ) -> EntityReferenceResolution:
        """Resolve one authority-sealed mention against its exact current base.

        This is the authoritative Stage 2 entry point.  The context performs a
        single issuer/registry/revision/hash check and returns a detached graph,
        the exact current user mention, and accepted history.  The legacy
        ``resolve`` method remains available as a storage-free compatibility
        kernel, but caller-created history is not identity authority.
        """

        from .identity_review import IdentityResolutionContext

        if not isinstance(context, IdentityResolutionContext):
            raise EntityIdentityValidationError(("context.type.invalid",))
        inputs = context.resolver_inputs()
        return self.resolve(
            inputs.base,
            inputs.current_mention,
            inputs.accepted_history,
        )

    def resolve(
        self,
        base: MemoryWorldGraph,
        mention: ReferenceMention,
        accepted_history: Sequence[AcceptedEntityReference] = (),
    ) -> EntityReferenceResolution:
        if not isinstance(base, MemoryWorldGraph):
            raise EntityIdentityValidationError(("base.type.invalid",))
        if not isinstance(mention, ReferenceMention):
            raise TypeError("mention must be a ReferenceMention")
        graph_issues = _base_identity_shape_issues(base)
        if graph_issues:
            raise EntityIdentityValidationError(graph_issues)
        if isinstance(accepted_history, (str, bytes)) or not isinstance(
            accepted_history, SequenceABC
        ):
            raise EntityIdentityValidationError(("accepted_history.not_sequence",))
        history = tuple(accepted_history)
        mention_time = _required_timestamp(mention.occurred_at)
        kind_hint = _explicit_kind_hint(mention)
        history_issue_list: list[str] = []
        evidence_metadata: dict[str, tuple[str, datetime, str | None]] = {}
        for index, reference in enumerate(history):
            if not isinstance(reference, AcceptedEntityReference):
                history_issue_list.append(f"accepted_history[{index}].type.invalid")
                continue
            if reference.entity_id not in base.entities:
                history_issue_list.append(f"accepted_history[{index}].entity_id.unknown")
            reference_time = _required_timestamp(reference.occurred_at)
            if reference_time >= mention_time:
                history_issue_list.append(f"accepted_history[{index}].occurred_at.not_prior")
            metadata = (
                reference.conversation_id,
                reference_time,
                reference.continuity_id,
            )
            prior_metadata = evidence_metadata.setdefault(reference.evidence_id, metadata)
            if metadata != prior_metadata:
                history_issue_list.append(
                    f"accepted_history[{index}].evidence_metadata.conflict"
                )
        history_issues = tuple(history_issue_list)
        if history_issues:
            raise EntityIdentityValidationError(history_issues)

        # Pronouns are never durable aliases.  Resolve them only through a
        # bounded, accepted user-reference context; an Entity literally named
        # or aliased "她"/"she" cannot hijack this branch.
        if _is_pronoun(mention.text):
            surface_kinds = _pronoun_surface_kinds(mention.text)
            if kind_hint is not None and kind_hint not in surface_kinds:
                return _unresolved(mention, (), "PRONOUN_KIND_CONFLICT")
            context = _recent_context_candidates(base, history, mention, kind_hint)
            if len(context) == 1:
                return _resolved(mention, context, "recent-user-context")
            if context:
                return _ambiguous(mention, context, "RECENT_USER_CONTEXT_AMBIGUOUS")
            return _unresolved(mention, (), "NO_ACCEPTED_USER_REFERENCE_CONTEXT")

        # Owner-relative kinship terms require structural family evidence.  An
        # unrelated Entity cannot win merely because it has an alias such as
        # "我妈"; exact labels and structural candidates are combined and any
        # disagreement remains ambiguous.
        if _owner_relative_family(mention.text) is not None:
            exact_family = _exact_label_candidates(base, mention.text, None)
            owner_relative = _owner_relative_candidates(
                base,
                mention.text,
                kind_hint,
                mention_time,
            )
            combined = _combine_candidates(exact_family, owner_relative)
            owner_ids = {candidate.entity_id for candidate in owner_relative}
            exact_ids = {candidate.entity_id for candidate in exact_family}
            if len(owner_ids) == 1 and exact_ids.issubset(owner_ids):
                entity_id = next(iter(owner_ids))
                known_labels = {
                    _identity_key(base.entities[entity_id].canonical_name),
                    *map(_identity_key, base.entities[entity_id].aliases),
                }
                proposal = (
                    None
                    if _identity_key(mention.text) in known_labels
                    else EntityAliasProposal(
                        entity_id=entity_id,
                        alias=mention.text.strip(),
                        evidence_ids=(mention.evidence_id,),
                        basis="owner-relative-alias",
                    )
                )
                basis: ResolutionBasis = "owner-relative-alias"
                if exact_family:
                    basis = (
                        "canonical"
                        if exact_family[0].reasons[0].startswith("canonical:")
                        else "alias"
                    )
                return _resolved(mention, combined, basis, alias_proposal=proposal)
            if len(combined) > 1:
                return _ambiguous(mention, combined, "OWNER_RELATIVE_ALIAS_COLLISION")
            return _unresolved(mention, combined, "OWNER_RELATIVE_IDENTITY_UNSUPPORTED")

        # Relationship and role labels name a relation, not a durable identity.
        # They must carry an explicit structural anchor before a single entity
        # may be selected, even when a legacy entity label happens to match.
        if _is_role_only_surface(mention.text):
            return _unresolved(mention, (), "ROLE_ONLY_REFERENCE_REQUIRES_BINDING")

        # Exact accepted labels are collision-checked across every Entity kind
        # before a kind hint is considered.  Otherwise a hint could hide a
        # cross-kind duplicate and turn it into a false unique match.
        exact = _exact_label_candidates(base, mention.text, None)
        if exact:
            if len(exact) == 1:
                entity = base.entities[exact[0].entity_id]
                if kind_hint is not None and not _kind_matches(entity, kind_hint):
                    return _unresolved(mention, exact, "EXACT_LABEL_KIND_CONFLICT")
                exact_basis: ResolutionBasis = (
                    "canonical" if exact[0].reasons[0].startswith("canonical:") else "alias"
                )
                return _resolved(mention, exact, exact_basis)
            return _ambiguous(mention, exact, "EXACT_LABEL_COLLISION")

        if _has_possessive_entity_anchor(base, mention.text):
            return _unresolved(mention, (), "POSSESSIVE_REFERENCE_REQUIRES_ROLE_BINDING")

        embedded_entities = _embedded_referable_entity_candidates(base, mention.text)
        if embedded_entities:
            return _unresolved(
                mention,
                embedded_entities,
                "EMBEDDED_ENTITY_ROLE_REQUIRES_BINDING",
            )

        requirements = _descriptor_requirements(base, mention.text)
        described = _structured_description_candidates(
            base,
            mention.text,
            kind_hint,
            requirements,
            mention_time,
        )
        if described:
            eligible = tuple(
                candidate
                for candidate in described
                if _satisfies_descriptor_requirements(
                    base,
                    candidate,
                    requirements,
                    mention_time,
                )
            )
            if len(eligible) == 1:
                ordered = (eligible[0], *(
                    candidate for candidate in described if candidate.entity_id != eligible[0].entity_id
                ))
                return _resolved(mention, ordered, "structured-description")
            code = (
                "STRUCTURED_DESCRIPTION_AMBIGUOUS"
                if len(described) > 1 or len(eligible) > 1
                else "INSUFFICIENT_STRUCTURED_IDENTITY_EVIDENCE"
            )
            return _ambiguous(mention, described, code) if len(described) > 1 else _unresolved(
                mention, described, code
            )
        return _unresolved(mention, (), "NO_SAFE_ENTITY_CANDIDATE")

    def retrieve_candidates(
        self,
        base: MemoryWorldGraph,
        mention: ReferenceMention,
        accepted_history: Sequence[AcceptedEntityReference] = (),
    ) -> tuple[EntityCandidate, ...]:
        """Return the same deterministic candidates exposed by ``resolve``."""

        return self.resolve(base, mention, accepted_history).candidates


def _resolved(
    mention: ReferenceMention,
    candidates: tuple[EntityCandidate, ...],
    basis: ResolutionBasis,
    *,
    alias_proposal: EntityAliasProposal | None = None,
) -> EntityReferenceResolution:
    return EntityReferenceResolution(
        mention=mention,
        state="resolved",
        basis=basis,
        candidates=candidates,
        entity_id=candidates[0].entity_id,
        alias_proposal=alias_proposal,
    )


def _ambiguous(
    mention: ReferenceMention,
    candidates: tuple[EntityCandidate, ...],
    code: str,
) -> EntityReferenceResolution:
    return EntityReferenceResolution(
        mention=mention,
        state="ambiguous",
        basis="none",
        candidates=candidates,
        uncertainty_code=code,
    )


def _unresolved(
    mention: ReferenceMention,
    candidates: tuple[EntityCandidate, ...],
    code: str,
) -> EntityReferenceResolution:
    return EntityReferenceResolution(
        mention=mention,
        state="unresolved",
        basis="none",
        candidates=candidates,
        uncertainty_code=code,
    )


def _exact_label_candidates(
    base: MemoryWorldGraph,
    text: str,
    kind_hint: str | None,
) -> tuple[EntityCandidate, ...]:
    key = _identity_key(text)
    if not key:
        return ()
    candidates: list[EntityCandidate] = []
    for entity in sorted(base.entities.values(), key=lambda item: item.id):
        if not _kind_matches(entity, kind_hint):
            continue
        reasons: list[str] = []
        if key == _identity_key(entity.canonical_name):
            reasons.append(f"canonical:{entity.canonical_name}")
        reasons.extend(
            f"alias:{alias}" for alias in entity.aliases if key == _identity_key(alias)
        )
        if reasons:
            candidates.append(EntityCandidate(entity.id, 100, tuple(reasons)))
    return tuple(candidates)


def _combine_candidates(
    first: tuple[EntityCandidate, ...],
    second: tuple[EntityCandidate, ...],
) -> tuple[EntityCandidate, ...]:
    combined: dict[str, EntityCandidate] = {}
    for candidate in (*first, *second):
        previous = combined.get(candidate.entity_id)
        reasons = set(candidate.reasons)
        score = candidate.score
        if previous is not None:
            reasons.update(previous.reasons)
            score = max(score, previous.score)
        combined[candidate.entity_id] = EntityCandidate(
            candidate.entity_id,
            score,
            tuple(sorted(reasons)),
        )
    return tuple(combined[entity_id] for entity_id in sorted(combined))


_OWNER_RELATIVE_FAMILIES: dict[str, frozenset[str]] = {
    "mother": frozenset({"mother", "mom", "mum", "my mother", "my mom", "my mum", "妈妈", "我妈", "母亲", "我母亲"}),
    "father": frozenset({"father", "dad", "my father", "my dad", "爸爸", "我爸", "父亲", "我父亲"}),
}


def _owner_relative_candidates(
    base: MemoryWorldGraph,
    text: str,
    kind_hint: str | None,
    at_time: datetime,
) -> tuple[EntityCandidate, ...]:
    family = _owner_relative_family(text)
    if family is None:
        return ()
    candidates: list[EntityCandidate] = []
    for entity in sorted(base.entities.values(), key=lambda item: item.id):
        if (
            entity.id == base.world.owner_entity_id
            or entity.kind.casefold() not in {"person", "agent"}
            or not _kind_matches(entity, kind_hint)
        ):
            continue
        labels = {_normalize(entity.canonical_name), *map(_normalize, entity.aliases)}
        if not labels.intersection(_OWNER_RELATIVE_FAMILIES[family]):
            continue
        relationship_ids = _owner_family_relationships(
            base,
            entity.id,
            family,
            at_time,
        )
        if relationship_ids:
            candidates.append(
                EntityCandidate(
                    entity.id,
                    90,
                    tuple(f"owner-family-relationship:{value}" for value in relationship_ids),
                )
            )
    return tuple(candidates)


def _owner_relative_family(text: str) -> str | None:
    key = _normalize(text)
    return next(
        (family for family, values in _OWNER_RELATIVE_FAMILIES.items() if key in values),
        None,
    )


def _owner_family_relationships(
    base: MemoryWorldGraph,
    entity_id: str,
    family: str,
    at_time: datetime,
) -> tuple[str, ...]:
    forward = {"child of", "son of", "daughter of"}
    reverse = {"parent of", f"{family} of"}
    return tuple(
        relationship.id
        for relationship in sorted(base.relationships.values(), key=lambda item: item.id)
        if _relationship_is_active_at(relationship, at_time)
        and (
            (
                relationship.source_entity_id == base.world.owner_entity_id
                and relationship.target_entity_id == entity_id
                and _normalize(relationship.relation_type) in forward
            )
            or (
                relationship.source_entity_id == entity_id
                and relationship.target_entity_id == base.world.owner_entity_id
                and _normalize(relationship.relation_type) in reverse
            )
        )
    )


def _relationship_is_active_at(
    relationship: Relationship,
    at_time: datetime,
) -> bool:
    if relationship.status not in {None, "active"}:
        return False
    valid_from = (
        _timestamp(relationship.valid_from)
        if relationship.valid_from is not None
        else None
    )
    valid_to = (
        _timestamp(relationship.valid_to)
        if relationship.valid_to is not None
        else None
    )
    if relationship.valid_from is not None and valid_from is None:
        return False
    if relationship.valid_to is not None and valid_to is None:
        return False
    if valid_from is not None and at_time < valid_from:
        return False
    if valid_to is not None and at_time >= valid_to:
        return False
    if valid_from is not None and valid_to is not None and valid_to <= valid_from:
        return False
    return True


_PRONOUNS = frozenset({
    "她", "他", "它", "ta", "she", "her", "he", "him", "it", "they", "them", "对方", "那个人",
})
_ANIMAL_PRONOUNS = frozenset({"它", "it"})
_PERSON_OR_AGENT_PRONOUNS = _PRONOUNS - _ANIMAL_PRONOUNS


def _is_pronoun(text: str) -> bool:
    return _normalize(text) in _PRONOUNS


def _pronoun_surface_kinds(text: str) -> frozenset[str]:
    key = _normalize(text)
    if key in _ANIMAL_PRONOUNS:
        return frozenset({"animal"})
    if key in _PERSON_OR_AGENT_PRONOUNS:
        return frozenset({"person", "agent"})
    return frozenset()


def _recent_context_candidates(
    base: MemoryWorldGraph,
    history: tuple[AcceptedEntityReference, ...],
    mention: ReferenceMention,
    kind_hint: str | None,
) -> tuple[EntityCandidate, ...]:
    if not history:
        return ()
    eligible_history = tuple(
        reference
        for reference in history
        if reference.conversation_id == mention.conversation_id
        or (
            mention.continuity_id is not None
            and reference.continuity_id == mention.continuity_id
        )
    )
    if not eligible_history:
        return ()
    latest_time = max(_required_timestamp(reference.occurred_at) for reference in eligible_history)
    # Time, not caller list order, defines the most recent accepted context.
    # Equal-time bindings remain one ambiguity group even across Evidence IDs.
    latest = [
        reference
        for reference in eligible_history
        if _required_timestamp(reference.occurred_at) == latest_time
    ]
    grouped: dict[str, list[AcceptedEntityReference]] = {}
    surface_kinds = _pronoun_surface_kinds(mention.text)
    for reference in latest:
        entity = base.entities[reference.entity_id]
        if (
            _normalize(entity.kind) in surface_kinds
            and _kind_matches(entity, kind_hint)
        ):
            grouped.setdefault(reference.entity_id, []).append(reference)
    return tuple(
        EntityCandidate(
            entity_id,
            80,
            tuple(
                f"accepted-user-reference:{reference.conversation_id}:{reference.evidence_id}"
                for reference in references
            ),
        )
        for entity_id, references in sorted(grouped.items())
        if not _pronoun_family_conflict(
            base,
            mention.text,
            base.entities[entity_id],
            references,
            _required_timestamp(mention.occurred_at),
        )
    )


def _pronoun_family_conflict(
    base: MemoryWorldGraph,
    pronoun: str,
    entity: Entity,
    references: Sequence[AcceptedEntityReference],
    at_time: datetime,
) -> bool:
    key = _normalize(pronoun)
    pronoun_family = (
        "mother"
        if key in {"她", "she", "her"}
        else "father"
        if key in {"他", "he", "him"}
        else None
    )
    if pronoun_family is None:
        return False
    opposite = "father" if pronoun_family == "mother" else "mother"
    reference_families = {
        family
        for reference in references
        if (family := _owner_relative_family(reference.mention)) is not None
    }
    if opposite in reference_families:
        return True
    opposite_form = "爸爸" if opposite == "father" else "妈妈"
    opposite_entity_ids = {
        candidate.entity_id
        for candidate in _owner_relative_candidates(
            base,
            opposite_form,
            "person",
            at_time,
        )
    }
    return entity.id in opposite_entity_ids


_RELATION_TERMS: dict[str, frozenset[str]] = {
    "friend": frozenset({"friend", "朋友", "好友"}),
    "colleague": frozenset({"colleague", "coworker", "同事"}),
    "classmate": frozenset({"classmate", "同学"}),
    "romantic interest": frozenset({"romantic interest", "喜欢的人", "心仪的人"}),
    "owns": frozenset({"pet", "宠物", "猫", "狗"}),
}

_STRICTLY_OWNER_TO_TARGET_RELATIONS = frozenset({"owns", "romantic interest"})

_ROLE_ONLY_SURFACES = frozenset(
    {
        "朋友",
        "friend",
        "同事",
        "colleague",
        "同学",
        "classmate",
        "宠物",
        "pet",
        "喜欢的人",
        "romantic interest",
    }
)

_EVENT_TERMS: dict[str, frozenset[str]] = {
    "trip": frozenset({"trip", "travel", "旅游", "旅行"}),
    "interpersonal conflict": frozenset({"conflict", "argument", "争执", "吵架"}),
}


def _structured_resolution_reason_issues(
    text: str,
    reasons: tuple[str, ...],
) -> tuple[str, ...]:
    """Validate the graph-independent minimum encoded by a descriptor mention."""

    mention_key = _normalize(text)
    derived_relationship_types = frozenset(
        relation_type
        for relation_type, terms in _RELATION_TERMS.items()
        if any(_safe_term_in_mention(term, mention_key) for term in terms)
    )
    derived_event_types = frozenset(
        event_type
        for event_type, terms in _EVENT_TERMS.items()
        if any(_safe_term_in_mention(term, mention_key) for term in terms)
    )
    relationship_reason_types = frozenset(
        _normalize(reason.split(":", 1)[1])
        for reason in reasons
        if reason.startswith("owner-relationship:")
    )
    event_reason_types = frozenset(
        _normalize(reason.split(":", 1)[1])
        for reason in reasons
        if reason.startswith("event-type:")
    )
    known_relationship_reasons = relationship_reason_types.intersection(
        _RELATION_TERMS
    )
    known_event_reasons = event_reason_types.intersection(_EVENT_TERMS)
    anchor_payloads = tuple(
        reason.split(":", 1)[1]
        for reason in reasons
        if reason.startswith("event-anchor:")
    )

    issues: list[str] = []
    if (
        known_relationship_reasons != derived_relationship_types
        or known_event_reasons != derived_event_types
        or any(not _non_empty(payload) for payload in anchor_payloads)
    ):
        issues.append("resolution.resolved.structured.reason.not_derived")
    if len(derived_event_types) > 1:
        issues.append("resolution.resolved.structured.event_types.multiple")
    family_count = sum(
        (
            bool(derived_relationship_types),
            bool(derived_event_types),
            bool(anchor_payloads),
        )
    )
    if family_count < 2:
        issues.append("resolution.resolved.structured.requirements.insufficient")
    return tuple(issues)


@dataclass(frozen=True, slots=True)
class _DescriptorRequirements:
    relationship_types: frozenset[str]
    event_types: frozenset[str]
    anchor_entity_ids: frozenset[str]

    @property
    def family_count(self) -> int:
        return sum(
            (
                bool(self.relationship_types),
                bool(self.event_types),
                bool(self.anchor_entity_ids),
            )
        )


def _descriptor_requirements(
    base: MemoryWorldGraph,
    text: str,
) -> _DescriptorRequirements:
    mention_key = _normalize(text)
    relationship_types = frozenset(
        relation_type
        for relation_type, terms in _RELATION_TERMS.items()
        if any(_safe_term_in_mention(term, mention_key) for term in terms)
    )
    event_types = frozenset(
        event_type
        for event_type, terms in _EVENT_TERMS.items()
        if any(_safe_term_in_mention(term, mention_key) for term in terms)
    )
    anchor_entity_ids = frozenset(
        entity.id
        for entity in base.entities.values()
        if entity.kind.casefold() not in {"person", "animal", "agent"}
        and any(
            _safe_term_in_mention(label, mention_key)
            for label in (entity.canonical_name, *entity.aliases)
        )
    )
    return _DescriptorRequirements(relationship_types, event_types, anchor_entity_ids)


def _is_role_only_surface(text: str) -> bool:
    """Return whether ``text`` names only a relationship role, not an entity."""

    return _identity_key(text) in _ROLE_ONLY_SURFACES


def _satisfies_descriptor_requirements(
    base: MemoryWorldGraph,
    candidate: EntityCandidate,
    requirements: _DescriptorRequirements,
    at_time: datetime,
) -> bool:
    if requirements.family_count < 2:
        return False
    entity = base.entities[candidate.entity_id]
    human_relations = {"friend", "colleague", "classmate", "romantic interest"}
    if requirements.relationship_types.intersection(human_relations) and (
        entity.kind.casefold() not in {"person", "agent"}
    ):
        return False
    if "owns" in requirements.relationship_types and entity.kind.casefold() != "animal":
        return False
    # A WorldEvent has one canonical event_type.  Without an explicit compound-
    # event/link contract, multiple event-type terms cannot be safely joined
    # across otherwise unrelated historical events.
    if len(requirements.event_types) > 1:
        return False
    reasons = set(candidate.reasons)
    if any(
        f"owner-relationship:{relation_type}" not in reasons
        for relation_type in requirements.relationship_types
    ):
        return False
    connected_relationships = _connected_owner_relationships(
        base,
        candidate.entity_id,
        at_time,
    )
    connected_relationship_ids = {
        relationship.id for relationship in connected_relationships
    }
    connected_events = _connected_events(
        base,
        candidate.entity_id,
        connected_relationship_ids,
    )
    anchors = requirements.anchor_entity_ids
    if requirements.event_types:
        for event_type in requirements.event_types:
            if not any(
                _normalize(event.event_type) == event_type
                and anchors.issubset(
                    _event_anchor_ids(base, event, candidate.entity_id)
                )
                for event in connected_events
            ):
                return False
    elif anchors and not any(
        anchors.issubset(_event_anchor_ids(base, event, candidate.entity_id))
        for event in connected_events
    ):
        return False
    return True


def _structured_description_candidates(
    base: MemoryWorldGraph,
    text: str,
    kind_hint: str | None,
    requirements: _DescriptorRequirements,
    at_time: datetime,
) -> tuple[EntityCandidate, ...]:
    mention_key = _normalize(text)
    candidates: list[EntityCandidate] = []
    for entity in sorted(base.entities.values(), key=lambda item: item.id):
        if (
            entity.id == base.world.owner_entity_id
            or entity.id in requirements.anchor_entity_ids
            or not _kind_matches(entity, kind_hint)
        ):
            continue
        reasons = _structured_reasons(base, entity, mention_key, at_time)
        if reasons:
            candidates.append(EntityCandidate(entity.id, len(reasons), reasons))
    return tuple(sorted(candidates, key=lambda item: (-item.score, item.entity_id)))


def _structured_reasons(
    base: MemoryWorldGraph,
    entity: Entity,
    mention_key: str,
    at_time: datetime,
) -> tuple[str, ...]:
    reasons: set[str] = set()
    labels = (entity.canonical_name, *entity.aliases)
    if any(_safe_term_in_mention(label, mention_key) for label in labels):
        reasons.add(f"entity-label:{entity.id}")

    connected_relationships = _connected_owner_relationships(
        base,
        entity.id,
        at_time,
    )
    for relationship in connected_relationships:
        relation_key = _normalize(relationship.relation_type)
        terms = _RELATION_TERMS.get(relation_key, frozenset({relation_key}))
        if any(_safe_term_in_mention(term, mention_key) for term in terms):
            reasons.add(f"owner-relationship:{relation_key}")

    connected_relationship_ids = {relationship.id for relationship in connected_relationships}
    for event in _connected_events(base, entity.id, connected_relationship_ids):
        event_key = _normalize(event.event_type)
        event_terms = _EVENT_TERMS.get(event_key, frozenset({event_key}))
        if any(_safe_term_in_mention(term, mention_key) for term in event_terms):
            reasons.add(f"event-type:{event_key}")
        related_ids = {
            *event.related_entity_ids,
            *(participant.entity_id for participant in event.participants),
        }
        for related_id in sorted(related_ids - {entity.id, base.world.owner_entity_id}):
            related = base.entities.get(related_id)
            if related is None:
                continue
            if any(
                _safe_term_in_mention(label, mention_key)
                for label in (related.canonical_name, *related.aliases)
            ):
                reasons.add(f"event-anchor:{related.id}")
    return tuple(sorted(reasons))


def _connected_owner_relationships(
    base: MemoryWorldGraph,
    entity_id: str,
    at_time: datetime,
) -> tuple[Relationship, ...]:
    return tuple(
        relationship
        for relationship in base.relationships.values()
        if _relationship_is_active_at(relationship, at_time)
        and _relationship_supports_owner_target(base, relationship, entity_id)
    )


def _relationship_supports_owner_target(
    base: MemoryWorldGraph,
    relationship: Relationship,
    entity_id: str,
) -> bool:
    """Return whether an owner-relative descriptor may traverse this edge."""

    owner_id = base.world.owner_entity_id
    if (
        relationship.source_entity_id == owner_id
        and relationship.target_entity_id == entity_id
    ):
        return True
    if not (
        relationship.source_entity_id == entity_id
        and relationship.target_entity_id == owner_id
    ):
        return False
    relation_key = _normalize(relationship.relation_type)
    if relation_key in _STRICTLY_OWNER_TO_TARGET_RELATIONS:
        return False
    return relationship.bidirectional


def _connected_events(
    base: MemoryWorldGraph,
    entity_id: str,
    connected_relationship_ids: set[str],
) -> tuple[WorldEvent, ...]:
    return tuple(
        event
        for event in base.events.values()
        if entity_id in {participant.entity_id for participant in event.participants}
        or entity_id in event.related_entity_ids
        or bool(connected_relationship_ids.intersection(event.relationship_ids))
    )


def _event_anchor_ids(
    base: MemoryWorldGraph,
    event: WorldEvent,
    entity_id: str,
) -> frozenset[str]:
    related_ids = {
        *event.related_entity_ids,
        *(participant.entity_id for participant in event.participants),
    }
    return frozenset(related_ids - {entity_id, base.world.owner_entity_id})


def _embedded_referable_entity_candidates(
    base: MemoryWorldGraph,
    text: str,
) -> tuple[EntityCandidate, ...]:
    candidates: list[EntityCandidate] = []
    for entity in sorted(base.entities.values(), key=lambda item: item.id):
        if entity.kind.casefold() not in {"person", "animal", "agent"}:
            continue
        matched_labels = tuple(
            label
            for label in (entity.canonical_name, *entity.aliases)
            if _identity_label_in_text(label, text)
        )
        if matched_labels:
            candidates.append(
                EntityCandidate(
                    entity.id,
                    100,
                    tuple(f"embedded-entity-label:{label}" for label in matched_labels),
                )
            )
    return tuple(candidates)


def _identity_label_in_text(label: str, text: str) -> bool:
    key = _identity_key(label)
    content = _identity_key(text)
    if not key:
        return False
    if any("\u3400" <= character <= "\u9fff" for character in key):
        compact = key.replace(" ", "")
        return len(compact) >= 2 and compact in content.replace(" ", "")
    return re.search(rf"(?<!\w){re.escape(key)}(?!\w)", content) is not None


def _has_possessive_entity_anchor(base: MemoryWorldGraph, text: str) -> bool:
    normalized = _normalize(text)
    compact = normalized.replace(" ", "")
    normalized_relation_terms = {
        _normalize(term)
        for terms in _RELATION_TERMS.values()
        for term in terms
    }
    compact_relation_terms = {
        _normalize(term).replace(" ", "")
        for terms in _RELATION_TERMS.values()
        for term in terms
    }
    for entity in base.entities.values():
        if entity.kind.casefold() not in {"person", "animal", "agent"}:
            continue
        for label in (entity.canonical_name, *entity.aliases):
            anchor = _normalize(label).replace(" ", "")
            marker = f"{anchor}的"
            if len(anchor) >= 2 and marker in compact:
                suffix = compact.split(marker, 1)[1]
                if any(term and term in suffix for term in compact_relation_terms):
                    return True
            normalized_anchor = _normalize(label)
            if len(normalized_anchor) < 2:
                continue
            english_markers = (
                f"{normalized_anchor} s ",
                *(
                    f"{term} of {normalized_anchor}"
                    for term in normalized_relation_terms
                    if term
                ),
            )
            if any(marker in f"{normalized} " for marker in english_markers):
                return True
    return False


def _explicit_kind_hint(mention: ReferenceMention) -> str | None:
    """Return only a caller-supplied hint; surface heuristics never hide collisions."""

    return _normalize(mention.kind_hint) if mention.kind_hint is not None else None


def _kind_matches(entity: Entity, kind_hint: str | None) -> bool:
    return kind_hint is None or _normalize(entity.kind) == kind_hint


def _base_identity_shape_issues(base: MemoryWorldGraph) -> tuple[str, ...]:
    """Fail closed on every identity-bearing edge the resolver can traverse."""

    if not isinstance(base, MemoryWorldGraph):
        return ("base.type.invalid",)

    issues: list[str] = []
    world = base.world
    if not isinstance(world, PersonalWorld):
        issues.append("base.world.type.invalid")
        world_id: str | None = None
        owner_entity_id: str | None = None
    else:
        world_id = world.world_id if _non_empty(world.world_id) else None
        owner_entity_id = (
            world.owner_entity_id if _non_empty(world.owner_entity_id) else None
        )
        if world_id is None:
            issues.append("base.world.world_id.invalid")
        if owner_entity_id is None:
            issues.append("base.world.owner_entity_id.invalid")

    entities = base.entities if isinstance(base.entities, dict) else {}
    relationships = base.relationships if isinstance(base.relationships, dict) else {}
    events = base.events if isinstance(base.events, dict) else {}
    cognitions = base.cognitions if isinstance(base.cognitions, dict) else {}
    if not isinstance(base.entities, dict):
        issues.append("base.entities.not_dict")
    if not isinstance(base.relationships, dict):
        issues.append("base.relationships.not_dict")
    if not isinstance(base.events, dict):
        issues.append("base.events.not_dict")
    if not isinstance(base.cognitions, dict):
        issues.append("base.cognitions.not_dict")

    object_kinds: dict[str, str] = {}

    def record_object_id(prefix: str, kind: str, object_id: object) -> None:
        if not _non_empty(object_id):
            return
        assert isinstance(object_id, str)
        previous_kind = object_kinds.setdefault(object_id, kind)
        if previous_kind != kind:
            issues.append(f"{prefix}.id.cross_kind_collision")

    entity_ids: set[str] = set()
    for index, (entity_key, entity) in enumerate(entities.items()):
        prefix = f"base.entities[{index}]"
        if not isinstance(entity, Entity):
            issues.append(f"{prefix}.type.invalid")
            continue
        if not _non_empty(entity_key) or entity_key != entity.id:
            issues.append(f"{prefix}.key.invalid")
        if not _non_empty(entity.id):
            issues.append(f"{prefix}.id.invalid")
        else:
            entity_ids.add(entity.id)
            record_object_id(prefix, "entity", entity.id)
        if world_id is None or entity.world_id != world_id:
            issues.append(f"{prefix}.world_id.invalid")
        if not _non_empty(entity.kind):
            issues.append(f"{prefix}.kind.invalid")
        if not _non_empty(entity.canonical_name):
            issues.append(f"{prefix}.canonical_name.invalid")
        if not isinstance(entity.aliases, tuple):
            issues.append(f"{prefix}.aliases.not_tuple")
        else:
            issues.extend(
                f"{prefix}.aliases[{alias_index}].invalid"
                for alias_index, alias in enumerate(entity.aliases)
                if not _non_empty(alias)
            )

    if owner_entity_id is not None and owner_entity_id not in entity_ids:
        issues.append("base.world.owner_entity_id.unknown")

    relationship_ids: set[str] = set()
    for index, (relationship_key, relationship) in enumerate(relationships.items()):
        prefix = f"base.relationships[{index}]"
        if not isinstance(relationship, Relationship):
            issues.append(f"{prefix}.type.invalid")
            continue
        if not _non_empty(relationship_key) or relationship_key != relationship.id:
            issues.append(f"{prefix}.key.invalid")
        if not _non_empty(relationship.id):
            issues.append(f"{prefix}.id.invalid")
        else:
            relationship_ids.add(relationship.id)
            record_object_id(prefix, "relationship", relationship.id)
        if world_id is None or relationship.world_id != world_id:
            issues.append(f"{prefix}.world_id.invalid")
        for endpoint_name, endpoint_id in (
            ("source_entity_id", relationship.source_entity_id),
            ("target_entity_id", relationship.target_entity_id),
        ):
            if not _non_empty(endpoint_id):
                issues.append(f"{prefix}.{endpoint_name}.invalid")
            elif endpoint_id not in entity_ids:
                issues.append(f"{prefix}.{endpoint_name}.unknown")
        if not _non_empty(relationship.relation_type):
            issues.append(f"{prefix}.relation_type.invalid")
        if not isinstance(relationship.bidirectional, bool):
            issues.append(f"{prefix}.bidirectional.invalid")
        if relationship.status is not None and not _non_empty(relationship.status):
            issues.append(f"{prefix}.status.invalid")
        for boundary_name, boundary in (
            ("valid_from", relationship.valid_from),
            ("valid_to", relationship.valid_to),
        ):
            if boundary is not None and _timestamp(boundary) is None:
                issues.append(f"{prefix}.{boundary_name}.invalid")

    event_ids: set[str] = set()
    for index, (event_key, event) in enumerate(events.items()):
        prefix = f"base.events[{index}]"
        if not isinstance(event, WorldEvent):
            issues.append(f"{prefix}.type.invalid")
            continue
        if not _non_empty(event_key) or event_key != event.id:
            issues.append(f"{prefix}.key.invalid")
        if not _non_empty(event.id):
            issues.append(f"{prefix}.id.invalid")
        else:
            event_ids.add(event.id)
            record_object_id(prefix, "event", event.id)
        if world_id is None or event.world_id != world_id:
            issues.append(f"{prefix}.world_id.invalid")
        if not _non_empty(event.event_type):
            issues.append(f"{prefix}.event_type.invalid")
        if not _non_empty(event.summary):
            issues.append(f"{prefix}.summary.invalid")
        if _timestamp(event.occurred_at) is None:
            issues.append(f"{prefix}.occurred_at.invalid")
        _event_participant_issues(issues, prefix, event.participants, entity_ids)
        _event_reference_issues(
            issues,
            prefix,
            "related_entity_ids",
            event.related_entity_ids,
            entity_ids,
        )
        _event_reference_issues(
            issues,
            prefix,
            "relationship_ids",
            event.relationship_ids,
            relationship_ids,
        )
        _event_facet_issues(issues, prefix, event.facets, entity_ids)
        _tuple_string_issues(issues, prefix, "evidence_ids", event.evidence_ids)

    for index, (cognition_key, cognition) in enumerate(cognitions.items()):
        prefix = f"base.cognitions[{index}]"
        if not isinstance(cognition, WorldCognition):
            issues.append(f"{prefix}.type.invalid")
            continue
        if not _non_empty(cognition_key) or cognition_key != cognition.id:
            issues.append(f"{prefix}.key.invalid")
        if not _non_empty(cognition.id):
            issues.append(f"{prefix}.id.invalid")
        else:
            record_object_id(prefix, "cognition", cognition.id)
        if world_id is None or cognition.world_id != world_id:
            issues.append(f"{prefix}.world_id.invalid")
        _cognition_target_issues(
            issues,
            prefix,
            cognition.target,
            world_id,
            entity_ids,
            relationship_ids,
            event_ids,
        )
        if not _non_empty(cognition.content):
            issues.append(f"{prefix}.content.invalid")
        if not isinstance(cognition.content_type, str) or cognition.content_type not in {
            "fact",
            "preference",
            "goal",
            "project",
            "state",
            "trait",
            "hypothesis",
            "trend",
        }:
            issues.append(f"{prefix}.content_type.invalid")
        if not isinstance(cognition.formed_by, str) or cognition.formed_by not in {
            "stated",
            "observed",
            "ruled",
            "confirmed",
            "inferred",
        }:
            issues.append(f"{prefix}.formed_by.invalid")
        if (
            isinstance(cognition.confidence, bool)
            or not isinstance(cognition.confidence, int)
        ):
            issues.append(f"{prefix}.confidence.invalid")
        if not isinstance(cognition.cred_status, str) or cognition.cred_status not in {
            "candidate",
            "low",
            "limited",
            "stable",
            "conflicted",
            "contested",
        }:
            issues.append(f"{prefix}.cred_status.invalid")
        _cognition_perspective_issues(issues, prefix, cognition.perspective, entity_ids)
        _cognition_source_issues(issues, prefix, cognition.sources)
        if cognition.scope is not None and not _non_empty(cognition.scope):
            issues.append(f"{prefix}.scope.invalid")
        for boundary_name, boundary in (
            ("valid_at", cognition.valid_at),
            ("invalid_at", cognition.invalid_at),
        ):
            if boundary is not None and _timestamp(boundary) is None:
                issues.append(f"{prefix}.{boundary_name}.invalid")
    return tuple(issues)


def _tuple_string_issues(
    issues: list[str],
    prefix: str,
    field_name: str,
    values: object,
) -> None:
    if not isinstance(values, tuple):
        issues.append(f"{prefix}.{field_name}.not_tuple")
        return
    issues.extend(
        f"{prefix}.{field_name}[{index}].invalid"
        for index, value in enumerate(values)
        if not _non_empty(value)
    )


def _event_reference_issues(
    issues: list[str],
    prefix: str,
    field_name: str,
    values: object,
    known_ids: set[str],
) -> None:
    if not isinstance(values, tuple):
        issues.append(f"{prefix}.{field_name}.not_tuple")
        return
    for item_index, object_id in enumerate(values):
        item_prefix = f"{prefix}.{field_name}[{item_index}]"
        if not _non_empty(object_id):
            issues.append(f"{item_prefix}.invalid")
        elif object_id not in known_ids:
            issues.append(f"{item_prefix}.unknown")


def _event_participant_issues(
    issues: list[str],
    prefix: str,
    participants: object,
    entity_ids: set[str],
) -> None:
    if not isinstance(participants, tuple):
        issues.append(f"{prefix}.participants.not_tuple")
        return
    for participant_index, participant in enumerate(participants):
        participant_prefix = f"{prefix}.participants[{participant_index}]"
        if not isinstance(participant, EventParticipant):
            issues.append(f"{participant_prefix}.type.invalid")
            continue
        if not _non_empty(participant.entity_id):
            issues.append(f"{participant_prefix}.entity_id.invalid")
        elif participant.entity_id not in entity_ids:
            issues.append(f"{participant_prefix}.entity_id.unknown")
        if participant.role is not None and not _non_empty(participant.role):
            issues.append(f"{participant_prefix}.role.invalid")


def _event_facet_issues(
    issues: list[str],
    prefix: str,
    facets: object,
    entity_ids: set[str],
) -> None:
    if not isinstance(facets, tuple):
        issues.append(f"{prefix}.facets.not_tuple")
        return
    for facet_index, facet in enumerate(facets):
        facet_prefix = f"{prefix}.facets[{facet_index}]"
        if not isinstance(facet, EventFacet):
            issues.append(f"{facet_prefix}.type.invalid")
            continue
        if not _non_empty(facet.key):
            issues.append(f"{facet_prefix}.key.invalid")
        if not _non_empty(facet.value):
            issues.append(f"{facet_prefix}.value.invalid")
        if facet.about_entity_id is not None:
            if not _non_empty(facet.about_entity_id):
                issues.append(f"{facet_prefix}.about_entity_id.invalid")
            elif facet.about_entity_id not in entity_ids:
                issues.append(f"{facet_prefix}.about_entity_id.unknown")


def _cognition_target_issues(
    issues: list[str],
    prefix: str,
    target: object,
    world_id: str | None,
    entity_ids: set[str],
    relationship_ids: set[str],
    event_ids: set[str],
) -> None:
    if not isinstance(target, MemoryTarget):
        issues.append(f"{prefix}.target.type.invalid")
        return
    if not isinstance(target.kind, str) or target.kind not in {
        "world",
        "entity",
        "relationship",
        "event",
    }:
        issues.append(f"{prefix}.target.kind.invalid")
        return
    if not _non_empty(target.id):
        issues.append(f"{prefix}.target.id.invalid")
        return
    known_ids = {
        "world": {world_id} if world_id is not None else set(),
        "entity": entity_ids,
        "relationship": relationship_ids,
        "event": event_ids,
    }
    if target.id not in known_ids[target.kind]:
        issues.append(f"{prefix}.target.id.unknown")


def _cognition_perspective_issues(
    issues: list[str],
    prefix: str,
    perspective: object,
    entity_ids: set[str],
) -> None:
    if not isinstance(perspective, Perspective):
        issues.append(f"{prefix}.perspective.type.invalid")
        return
    if not isinstance(perspective.kind, str) or perspective.kind not in {
        "entity",
        "joint",
        "system",
    }:
        issues.append(f"{prefix}.perspective.kind.invalid")
        return
    holder_ids = perspective.holder_entity_ids
    if not isinstance(holder_ids, tuple):
        issues.append(f"{prefix}.perspective.holder_entity_ids.not_tuple")
        return
    for holder_index, holder_id in enumerate(holder_ids):
        holder_prefix = f"{prefix}.perspective.holder_entity_ids[{holder_index}]"
        if not _non_empty(holder_id):
            issues.append(f"{holder_prefix}.invalid")
        elif holder_id not in entity_ids:
            issues.append(f"{holder_prefix}.unknown")
    holder_count = len(holder_ids)
    if (
        (perspective.kind == "entity" and holder_count != 1)
        or (perspective.kind == "joint" and holder_count < 2)
        or (perspective.kind == "system" and holder_count != 0)
    ):
        issues.append(f"{prefix}.perspective.holder_entity_ids.arity.invalid")


def _cognition_source_issues(issues: list[str], prefix: str, sources: object) -> None:
    if not isinstance(sources, tuple):
        issues.append(f"{prefix}.sources.not_tuple")
        return
    for source_index, source in enumerate(sources):
        source_prefix = f"{prefix}.sources[{source_index}]"
        if not isinstance(source, EvidenceLink):
            issues.append(f"{source_prefix}.type.invalid")
            continue
        if not _non_empty(source.evidence_id):
            issues.append(f"{source_prefix}.evidence_id.invalid")
        if not isinstance(source.relation, str) or source.relation not in {
            "support",
            "contradict",
        }:
            issues.append(f"{source_prefix}.relation.invalid")


def _safe_term_in_mention(value: str, mention_key: str) -> bool:
    key = _normalize(value)
    if not key:
        return False
    if any("\u3400" <= character <= "\u9fff" for character in key):
        compact = key.replace(" ", "")
        mention_compact = mention_key.replace(" ", "")
        return len(compact) >= 2 and compact in mention_compact
    # Latin-script identity terms require token boundaries.  This prevents a
    # short name such as "Li" from matching inside "Alice" and keeps phrase
    # boundaries instead of deleting all whitespace.
    return len(key) >= 2 and f" {key} " in f" {mention_key} "


def _identity_key(value: str) -> str:
    """Normalize exact identity labels without erasing meaningful punctuation."""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(normalized.split())


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(
        character if (character.isalnum() or character.isspace()) else " "
        for character in normalized.replace("_", " ").replace("-", " ")
    )
    return " ".join(normalized.split())


def _non_empty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _input_issues(values: tuple[tuple[str, object], ...]) -> list[str]:
    return [f"{name}.invalid" for name, value in values if not _non_empty(value)]


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _required_timestamp(value: str) -> datetime:
    parsed = _timestamp(value)
    assert parsed is not None
    return parsed


__all__ = [
    "AcceptedEntityReference",
    "EntityAliasProposal",
    "EntityCandidate",
    "EntityIdentityValidationError",
    "EntityReferenceResolution",
    "EntityReferenceResolver",
    "ReferenceMention",
    "ResolutionBasis",
    "ResolutionState",
]
