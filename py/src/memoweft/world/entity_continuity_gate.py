"""Frozen, offline Gate 2 evaluator for trusted entity-continuity mechanics.

The gate deliberately exercises the public review/authority boundary.  In
particular, every resolution under test travels through an authority-issued
``resolution_context`` and ``EntityReferenceResolver.resolve_context``; it
never treats caller-built resolver history as identity authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .entity_resolution import EntityReferenceResolution, EntityReferenceResolver
from .graph import MemoryWorldGraph
from .identity_review import (
    BindingAssignment,
    EntityIdentityDelta,
    EntityReferenceLocator,
    EntityReferenceRewrite,
    IdentityAuthority,
    IdentityEvidence,
    IdentityReviewStateError,
    IdentityReviewValidationError,
    SplitSuccessor,
    VerifiedReferenceMention,
)
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


CORPUS_SCHEMA_VERSION = 1
_HARD_FAILURE = "GATE2_HARD_FAILURE"

# These are deliberately values, not semantic labels.  The evaluator keeps
# role keys privately while every domain identifier supplied to the authority
# is drawn from this frozen per-variant bijection.  Correct target/distractor
# lexical order is intentionally reversed between variants.
_OPAQUE_IDS: Mapping[str, Mapping[str, str]] = {
    "namespace_amber": {
        "world": "W~4e1", "owner": "Q@91", "mother": "Z!08", "friend": "Z#72", "other": "A*63",
        "trip": "T=04", "place": "L:58", "rel-mother": "R/31", "rel-friend": "R/84", "rel-target": "R/16",
        "event": "V^20", "cognition": "C+47", "collapse": "R/92", "fresh-successor-a": "N%51", "fresh-successor-b": "N%09", "reuse-fresh": "N%88",
    },
    "namespace_cobalt": {
        "world": "w|7b2", "owner": "x&14", "mother": "a?95", "friend": "a!06", "other": "z=83",
        "trip": "t.42", "place": "l_71", "rel-mother": "r;65", "rel-friend": "r;02", "rel-target": "r;98",
        "event": "v~33", "cognition": "c$80", "collapse": "r;11", "fresh-successor-a": "n,27", "fresh-successor-b": "n,64", "reuse-fresh": "n,01",
    },
}
_VARIANT_DEFINITIONS = (
    ("namespace_amber", "amber::91?K", False),
    ("namespace_cobalt", "cobalt/%E2%98%83/77", True),
)
_CASE_CATALOG: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("01_owner_alias_pronoun", ("alias.owner_relative_resolved", "alias.review_accepted", "pronoun.cross_session_same_canonical", "alias.no_duplicate_entity"), "hard", "G2-01"),
    ("02_exact_known_alias", ("exact_known_alias.resolves_canonical", "exact_known_alias.no_creation"), "hard", "G2-02"),
    ("03_structured_trip_friend", ("structured_description.trip_friend_resolves", "structured_description.no_creation"), "hard", "G2-03"),
    ("04_same_name_collision", ("same_name_collision.ambiguous", "same_name_collision.no_creation"), "hard", "G2-04"),
    ("05_latest_people_ambiguous", ("latest_people.pronoun_ambiguous", "latest_people.no_creation"), "hard", "G2-05"),
    ("06_role_only_unresolved", ("role_only.unresolved", "role_only.no_creation"), "hard", "G2-06"),
    ("07_unknown_person_unresolved", ("unknown_person.unresolved", "unknown_person.no_creation"), "hard", "G2-07"),
    ("08_no_shared_continuity", ("no_shared_continuity.pronoun_unresolved", "no_shared_continuity.no_creation"), "hard", "G2-08"),
    ("09_evidence_and_span", ("evidence.assistant_rejected", "evidence.exact_codepoint_span_enforced"), "hard", "G2-09"),
    ("10_reject_zero_mutation", ("reject.zero_canonical_mutation", "reject.audit_envelope_retained"), "hard", "G2-10"),
    ("11_stale_tampered_causal", ("review.tamper_fails_closed", "review.time_causal", "review.stale_fails_closed"), "hard", "G2-11"),
    ("12_atom_target_and_alias_collision", ("atom.one_active_target", "alias.collision_rejected"), "hard", "G2-12"),
    ("13_merge_all_surfaces", ("merge.preview_all_seven_surfaces", "merge.explicit_survivor_one_hop_redirect", "merge.complete_tombstone_manifest", "merge.no_free_text_rewrite", "merge.no_auto_alias_union", "merge.source_removed_no_old_refs"), "hard", "G2-13"),
    ("14_merge_cardinality", ("merge.cardinality_collapse_rejected",), "hard", "G2-14"),
    ("15_split_complete", ("split.complete_locator_binding_partitions", "split.fresh_successors", "split.no_one_to_many_redirect", "split.complete_tombstone_no_old_refs"), "hard", "G2-15"),
    ("16_split_incomplete_retired", ("split.incomplete_rejected", "split.retired_id_reuse_rejected"), "hard", "G2-16"),
    ("17_stale_context_revision", ("sealed_context.accepted_revision_stale",), "hard", "G2-17"),
)


@dataclass(frozen=True, slots=True)
class Gate2Observation:
    """One atomic, audit-safe assertion with no volatile implementation IDs."""

    case_id: str
    variant_id: str
    predicate_id: str
    passed: bool
    severity: str
    hard_failure_code: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class Gate2Report:
    corpus_id: str
    observations: tuple[Gate2Observation, ...]
    expected_observation_count: int
    passed: bool
    hard_failure_codes: tuple[str, ...]
    expected_observation_keys: tuple[tuple[str, str, str], ...] = ()
    normalized_semantic_digest: tuple[tuple[str, str, bool, str], ...] = ()
    observation_complete: bool = False
    id_variant_semantically_equivalent: bool = False

    @property
    def case_count(self) -> int:
        return len({item.case_id for item in self.observations if item.case_id != "corpus"})

    @property
    def variant_count(self) -> int:
        return len({item.variant_id for item in self.observations if item.case_id != "corpus"})


class Gate2CorpusError(ValueError):
    """Fail-closed frozen-corpus error with stable codes."""

    def __init__(self, codes: Sequence[str]) -> None:
        self.codes = tuple(sorted(set(codes)))
        super().__init__(", ".join(self.codes))


@dataclass(frozen=True, slots=True)
class _Case:
    id: str
    predicate_ids: tuple[str, ...]
    severity: str
    hard_failure_code: str | None


@dataclass(frozen=True, slots=True)
class _Variant:
    id: str
    namespace: str
    reverse_insertion_order: bool


@dataclass(frozen=True, slots=True)
class Gate2Corpus:
    """Validated frozen corpus; no arbitrary mapping reaches formal evaluation."""

    corpus_id: str
    cases: tuple[_Case, ...]
    variants: tuple[_Variant, ...]


def load_gate2_corpus(path: Path) -> Gate2Corpus:
    """Load JSON without accepting duplicate keys or non-object roots."""

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        output: dict[str, object] = {}
        for key, value in pairs:
            if key in output:
                raise Gate2CorpusError(("corpus.duplicate_json_key",))
            output[key] = value
        return output

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicates)
    except (OSError, json.JSONDecodeError) as error:
        raise Gate2CorpusError(("corpus.json.invalid",)) from error
    if not isinstance(value, dict):
        raise Gate2CorpusError(("corpus.root.invalid",))
    return _parse_corpus(value)


def evaluate_gate2_corpus(corpus: Mapping[str, object]) -> Gate2Report:
    """Evaluate a frozen corpus fully, returning auditable atomic outcomes.

    Invalid corpus material is represented as a deterministic hard-failure
    report so callers always receive a report rather than a partial run.
    """

    try:
        parsed = _parse_corpus(corpus)
    except Gate2CorpusError as error:
        observation = Gate2Observation("corpus", "corpus", "corpus.valid", False, "hard", _HARD_FAILURE, ";".join(error.codes))
        return Gate2Report("invalid-corpus", (observation,), 0, False, (_HARD_FAILURE,))
    return evaluate_entity_continuity_gate(parsed)


def evaluate_entity_continuity_gate(
    corpus: Gate2Corpus,
    *,
    checkpoint: Callable[[str, str, str], None] | None = None,
) -> Gate2Report:
    """Run every frozen case/variant, checkpointing before and after each run.

    ``checkpoint`` receives ``(case_id, variant_id, phase)`` where phase is
    exactly ``before`` or ``after``.  It is intentionally observational: an
    exception propagates, so an external runner cannot silently skip evidence.
    """
    actual_catalog = tuple(
        (item.id, item.predicate_ids, item.severity, item.hard_failure_code or "")
        for item in corpus.cases
    )
    actual_variants = tuple(
        (item.id, item.namespace, item.reverse_insertion_order)
        for item in corpus.variants
    )
    if (
        type(corpus) is not Gate2Corpus
        or corpus.corpus_id != "gate2-entity-continuity-v1"
        or actual_catalog != _CASE_CATALOG
        or actual_variants != _VARIANT_DEFINITIONS
    ):
        raise Gate2CorpusError(("corpus.parsed_catalog.drift",))
    corpus_id, cases, variants = corpus.corpus_id, corpus.cases, corpus.variants
    observations: list[Gate2Observation] = []
    for variant in variants:
        for case in cases:
            if checkpoint is not None:
                checkpoint(case.id, variant.id, "before")
            actual = _run_case(case.id, variant)
            if checkpoint is not None:
                checkpoint(case.id, variant.id, "after")
            for predicate_id in case.predicate_ids:
                passed, detail = actual.get(predicate_id, (False, "predicate.not_produced"))
                observations.append(Gate2Observation(case.id, variant.id, predicate_id, passed, case.severity, case.hard_failure_code if not passed else None, detail))
    ordered = tuple(sorted(observations, key=lambda item: (item.case_id, item.variant_id, item.predicate_id)))
    expected = len(cases) * len(variants)
    expected_observations = sum(len(case.predicate_ids) for case in cases) * len(variants)
    completeness = len(ordered) == expected_observations and expected > 0
    failed_hard = tuple(sorted({item.hard_failure_code for item in ordered if item.hard_failure_code is not None}))
    keys = tuple((item.case_id, item.variant_id, item.predicate_id) for item in ordered)
    normalized = tuple(sorted((item.case_id, item.predicate_id, item.passed, item.detail) for item in ordered))
    by_variant = {variant.id: tuple(sorted((item.case_id, item.predicate_id, item.passed, item.detail) for item in ordered if item.variant_id == variant.id)) for variant in variants}
    equivalent = len(set(by_variant.values())) == 1
    return Gate2Report(corpus_id, ordered, expected_observations, completeness and equivalent and not failed_hard and all(item.passed for item in ordered), failed_hard, keys, normalized, completeness, equivalent)


def _parse_corpus(corpus: Mapping[str, object]) -> Gate2Corpus:
    required = {"schema_version", "corpus_id", "thresholds", "cases", "variants"}
    if (
        set(corpus) != required
        or type(corpus.get("schema_version")) is not int
        or corpus.get("schema_version") != CORPUS_SCHEMA_VERSION
        or corpus.get("corpus_id") != "gate2-entity-continuity-v1"
    ):
        raise Gate2CorpusError(("corpus.schema.invalid",))
    corpus_id = corpus.get("corpus_id")
    thresholds = corpus.get("thresholds")
    raw_cases = corpus.get("cases")
    raw_variants = corpus.get("variants")
    if not isinstance(corpus_id, str) or not corpus_id or not isinstance(thresholds, dict) or not isinstance(raw_cases, list) or not isinstance(raw_variants, list):
        raise Gate2CorpusError(("corpus.shape.invalid",))
    expected_thresholds = {"every_case_passes": True, "every_predicate_passes": True, "zero_hard_failures": True, "exact_observation_completeness": True, "deterministic_repeat_equality": True, "id_variant_semantic_equivalence": True}
    if (
        set(thresholds) != set(expected_thresholds)
        or any(type(thresholds.get(key)) is not bool or thresholds.get(key) is not True for key in expected_thresholds)
    ):
        raise Gate2CorpusError(("corpus.thresholds.invalid",))
    cases: list[_Case] = []
    for raw in raw_cases:
        if not isinstance(raw, dict) or set(raw) != {"id", "predicate_ids", "severity", "hard_failure_code"}:
            raise Gate2CorpusError(("corpus.case.invalid",))
        case_id, predicates, severity, code = raw.get("id"), raw.get("predicate_ids"), raw.get("severity"), raw.get("hard_failure_code")
        if not isinstance(case_id, str) or not case_id or not isinstance(predicates, list) or not predicates or not all(isinstance(item, str) and item for item in predicates) or len(set(predicates)) != len(predicates) or severity != "hard" or not isinstance(code, str) or not code:
            raise Gate2CorpusError(("corpus.case.invalid",))
        cases.append(_Case(case_id, tuple(predicates), severity, code))
    variants: list[_Variant] = []
    for raw in raw_variants:
        if not isinstance(raw, dict) or set(raw) != {"id", "namespace", "reverse_insertion_order"}:
            raise Gate2CorpusError(("corpus.variant.invalid",))
        variant_id, namespace, reversed_order = raw.get("id"), raw.get("namespace"), raw.get("reverse_insertion_order")
        if not isinstance(variant_id, str) or not variant_id or not isinstance(namespace, str) or not namespace or type(reversed_order) is not bool:
            raise Gate2CorpusError(("corpus.variant.invalid",))
        variants.append(_Variant(variant_id, namespace, reversed_order))
    actual_catalog = tuple((item.id, item.predicate_ids, item.severity, item.hard_failure_code or "") for item in cases)
    actual_variants = tuple((item.id, item.namespace, item.reverse_insertion_order) for item in variants)
    if actual_catalog != _CASE_CATALOG:
        raise Gate2CorpusError(("corpus.case_catalog.drift",))
    if actual_variants != _VARIANT_DEFINITIONS:
        raise Gate2CorpusError(("corpus.variant_catalog.drift",))
    return Gate2Corpus(corpus_id, tuple(cases), tuple(variants))


def _run_case(case_id: str, variant: _Variant) -> dict[str, tuple[bool, str]]:
    runners: Mapping[str, Callable[[_Variant], dict[str, tuple[bool, str]]]] = {
        "01_owner_alias_pronoun": _case_alias_pronoun,
        "02_exact_known_alias": _case_exact_alias,
        "03_structured_trip_friend": _case_descriptor,
        "04_same_name_collision": _case_collision,
        "05_latest_people_ambiguous": _case_two_latest,
        "06_role_only_unresolved": _case_role_only,
        "07_unknown_person_unresolved": _case_unknown,
        "08_no_shared_continuity": _case_no_continuity,
        "09_evidence_and_span": _case_evidence_span,
        "10_reject_zero_mutation": _case_reject,
        "11_stale_tampered_causal": _case_stale_tampered_causal,
        "12_atom_target_and_alias_collision": _case_atom_and_alias,
        "13_merge_all_surfaces": _case_merge,
        "14_merge_cardinality": _case_merge_cardinality,
        "15_split_complete": _case_split,
        "16_split_incomplete_retired": _case_split_rejects,
        "17_stale_context_revision": _case_stale_context,
    }
    try:
        return runners[case_id](variant)
    except (IdentityReviewStateError, IdentityReviewValidationError, ValueError, KeyError) as error:
        return {"unexpected": (False, type(error).__name__)}


def _id(variant: _Variant, role: str) -> str:
    return _OPAQUE_IDS[variant.id][role]


def _opaque_token(roles: Mapping[str, str], purpose: str) -> str:
    """Return a deterministic opaque ID for Evidence/conversation/scope input."""

    variant_id = next(item_id for item_id, values in _OPAQUE_IDS.items() if values["owner"] == roles["owner"])
    return sha256((variant_id + "\x00" + purpose).encode("utf-8")).hexdigest()[:24]


def _graph(variant: _Variant, *, all_surfaces: bool = False) -> tuple[MemoryWorldGraph, dict[str, str]]:
    roles = {role: _id(variant, role) for role in ("owner", "mother", "friend", "other", "trip", "place")}
    world_id = _id(variant, "world")
    graph = MemoryWorldGraph(PersonalWorld(world_id, roles["owner"]))
    entities = (
        Entity(roles["owner"], world_id, "person", "Owner"), Entity(roles["mother"], world_id, "person", "Mother", ("妈妈",)),
        Entity(roles["friend"], world_id, "person", "Lin", ("小林",)), Entity(roles["other"], world_id, "person", "Zhou", ("小周",)),
        Entity(roles["trip"], world_id, "activity", "Nanjing trip", ("南京旅游",)), Entity(roles["place"], world_id, "place", "Nanjing", ("南京",)),
    )
    for item in reversed(entities) if variant.reverse_insertion_order else entities:
        graph.add_entity(item)
    relations: list[Relationship] = [Relationship(_id(variant, "rel-mother"), world_id, roles["owner"], roles["mother"], "child_of"), Relationship(_id(variant, "rel-friend"), world_id, roles["friend"] if all_surfaces else roles["owner"], roles["friend"] if not all_surfaces else roles["owner"], "friend", True)]
    if all_surfaces:
        relations.append(Relationship(_id(variant, "rel-target"), world_id, roles["mother"], roles["friend"], "knows"))
    for relationship in reversed(relations) if variant.reverse_insertion_order else relations:
        graph.add_relationship(relationship)
    graph.add_event(WorldEvent(_id(variant, "event"), world_id, "trip", "immutable free text", "2026-01-01T00:00:00+00:00", (EventParticipant(roles["friend"]),), (roles["friend"], roles["trip"], roles["place"]), (_id(variant, "rel-friend"),), (EventFacet("about", "immutable", roles["friend"]),)))
    graph.add_cognition(WorldCognition(_id(variant, "cognition"), world_id, MemoryTarget("entity", roles["friend"]), "immutable free text", "fact", "stated", 90, "stable", Perspective("entity", (roles["friend"],))))
    graph.validate_owner()
    return graph, roles


def _authority(variant: _Variant, *, all_surfaces: bool = False) -> tuple[IdentityAuthority, dict[str, str]]:
    graph, roles = _graph(variant, all_surfaces=all_surfaces)
    return IdentityAuthority(graph), roles


def _mention(authority: IdentityAuthority, roles: Mapping[str, str], evidence_id: str, text: str, when: str, *, continuity: str | None = "scope:shared", conversation: str = "session:a", kind: str | None = None) -> VerifiedReferenceMention:
    opaque_evidence = _opaque_token(roles, "evidence:" + evidence_id)
    opaque_conversation = _opaque_token(roles, "conversation:" + conversation)
    opaque_continuity = None if continuity is None else _opaque_token(roles, "continuity:" + continuity)
    authority.register_evidence(IdentityEvidence(opaque_evidence, authority.view().world_id, opaque_conversation, when, "user", text, opaque_continuity))
    return authority.issue_verified_mention(opaque_evidence, 0, len(text), kind_hint=kind)


def _canonical_lane(authority: IdentityAuthority) -> tuple[object, ...]:
    view = authority.view()
    return (view.revision, view.graph.graph_hash, view.bindings, view.transitions, view.redirects, view.tombstones)


def _accept(authority: IdentityAuthority, delta: EntityIdentityDelta, when: str = "2026-03-01T00:00:00+00:00") -> None:
    review = authority.stage(delta, {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    authority.decide(review.review_id, review.result_hash, "accept", when)


def _accept_staged(authority: IdentityAuthority, review: object, when: str = "2026-03-01T00:00:00+00:00") -> None:
    # Keep the accepted review exactly the one whose preview was inspected.
    result_hash = getattr(review, "result_hash")
    review_id = getattr(review, "review_id")
    authority.decide(review_id, result_hash, "accept", when)


def _resolve(
    authority: IdentityAuthority,
    mention: VerifiedReferenceMention,
) -> EntityReferenceResolution:
    return EntityReferenceResolver().resolve_context(authority.resolution_context(mention))


def _result(**items: bool) -> dict[str, tuple[bool, str]]:
    return {key: (value, "satisfied" if value else "not_satisfied") for key, value in items.items()}


def _case_alias_pronoun(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    alias = _mention(authority, roles, "e:alias", "我妈", "2026-01-01T00:00:00+00:00")
    initial = _resolve(authority, alias)
    review = authority.stage(EntityIdentityDelta.alias(authority.view().world_id, roles["mother"], "我妈", (alias,)), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    authority.decide(review.review_id, review.result_hash, "accept", "2026-01-02T00:00:00+00:00")
    later = _mention(authority, roles, "e:pronoun", "她", "2026-01-03T00:00:00+00:00", conversation="session:b", kind="person")
    resolved = _resolve(authority, later)
    return _result(**{
        "alias.owner_relative_resolved": initial.entity_id == roles["mother"],
        "alias.review_accepted": authority.view().revision == 1,
        "pronoun.cross_session_same_canonical": resolved.entity_id == roles["mother"],
        "alias.no_duplicate_entity": len(authority.view().graph.entities) == 6,
    })


def _case_exact_alias(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    mention = _mention(authority, roles, "e:known", "小林", "2026-01-02T00:00:00+00:00")
    resolution = _resolve(authority, mention)
    return _result(**{"exact_known_alias.resolves_canonical": resolution.entity_id == roles["friend"], "exact_known_alias.no_creation": len(authority.view().graph.entities) == 6})


def _case_descriptor(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    mention = _mention(authority, roles, "e:descriptor", "那个南京旅游的朋友", "2026-01-02T00:00:00+00:00")
    resolution = _resolve(authority, mention)
    return _result(**{"structured_description.trip_friend_resolves": resolution.entity_id == roles["friend"], "structured_description.no_creation": len(authority.view().graph.entities) == 6})


def _case_collision(variant: _Variant) -> dict[str, tuple[bool, str]]:
    graph, roles = _graph(variant)
    graph.entities[roles["other"]] = Entity(roles["other"], graph.world.world_id, "person", "Other Lin", ("小林",))
    authority = IdentityAuthority(graph)
    mention = _mention(authority, roles, "e:collision", "小林", "2026-01-02T00:00:00+00:00")
    resolution = _resolve(authority, mention)
    return _result(**{"same_name_collision.ambiguous": resolution.state == "ambiguous", "same_name_collision.no_creation": len(authority.view().graph.entities) == 6})


def _case_two_latest(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    if variant.reverse_insertion_order:
        second = _mention(authority, roles, "e:second", "小周", "2026-01-01T00:00:00+00:00")
        first = _mention(authority, roles, "e:first", "小林", "2026-01-01T00:00:00+00:00")
        _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["other"], second))
        _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], first))
    else:
        first = _mention(authority, roles, "e:first", "小林", "2026-01-01T00:00:00+00:00")
        second = _mention(authority, roles, "e:second", "小周", "2026-01-01T00:00:00+00:00")
        _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], first))
        _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["other"], second))
    pronoun = _mention(authority, roles, "e:current", "她", "2026-04-01T00:00:00+00:00", conversation="session:b", kind="person")
    resolution = _resolve(authority, pronoun)
    return _result(**{"latest_people.pronoun_ambiguous": resolution.state == "ambiguous", "latest_people.no_creation": len(authority.view().graph.entities) == 6})


def _case_role_only(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    mention = _mention(authority, roles, "e:role", "朋友", "2026-01-02T00:00:00+00:00")
    resolution = _resolve(authority, mention)
    return _result(**{"role_only.unresolved": resolution.state == "unresolved", "role_only.no_creation": len(authority.view().graph.entities) == 6})


def _case_unknown(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    mention = _mention(authority, roles, "e:unknown", "完全陌生的人", "2026-01-02T00:00:00+00:00")
    resolution = _resolve(authority, mention)
    return _result(**{"unknown_person.unresolved": resolution.state == "unresolved", "unknown_person.no_creation": len(authority.view().graph.entities) == 6})


def _case_no_continuity(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    known = _mention(authority, roles, "e:known", "小林", "2026-01-01T00:00:00+00:00", continuity="scope:one")
    _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], known))
    current = _mention(authority, roles, "e:other", "她", "2026-04-01T00:00:00+00:00", continuity="scope:two", conversation="session:b", kind="person")
    resolution = _resolve(authority, current)
    return _result(**{"no_shared_continuity.pronoun_unresolved": resolution.state == "unresolved", "no_shared_continuity.no_creation": len(authority.view().graph.entities) == 6})


def _case_evidence_span(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    assistant_rejected = False
    try:
        authority.register_evidence(IdentityEvidence(_opaque_token(roles, "evidence:assistant"), authority.view().world_id, _opaque_token(roles, "conversation:assistant"), "2026-01-01T00:00:00+00:00", "assistant", "小林"))
    except IdentityReviewValidationError as error:
        assistant_rejected = "evidence.source_role.ineligible" in error.issues
    _mention(authority, roles, "e:span", "前缀😀小林后缀", "2026-01-02T00:00:00+00:00")
    exact = authority.issue_verified_mention(_opaque_token(roles, "evidence:e:span"), 3, 5)
    span_rejected = False
    try:
        authority.issue_verified_mention(_opaque_token(roles, "evidence:e:span"), 3, 8)
    except IdentityReviewValidationError as error:
        span_rejected = "mention.span.invalid" in error.issues
    return _result(**{"evidence.assistant_rejected": assistant_rejected, "evidence.exact_codepoint_span_enforced": exact.text == "小林" and span_rejected})


def _case_reject(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    mention = _mention(authority, roles, "e:reject", "小林", "2026-01-01T00:00:00+00:00")
    before = _canonical_lane(authority)
    review = authority.stage(EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], mention), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    authority.decide(review.review_id, review.result_hash, "reject", "2026-02-01T00:00:00+00:00")
    after = authority.view()
    return _result(**{"reject.zero_canonical_mutation": before == _canonical_lane(authority), "reject.audit_envelope_retained": len(after.review_envelopes) == 1 and len(after.decisions) == 1})


def _case_stale_tampered_causal(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    mention = _mention(authority, roles, "e:stale", "小林", "2026-02-01T00:00:00+00:00")
    review = authority.stage(EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], mention), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    tampered = causal = False
    before_tamper = _canonical_lane(authority)
    try:
        authority.decide(review.review_id, "0" * 64, "accept", "2026-03-01T00:00:00+00:00")
    except IdentityReviewStateError as error:
        tampered = error.code == "RESULT_HASH_MISMATCH" and _canonical_lane(authority) == before_tamper
    before_causal = _canonical_lane(authority)
    try:
        authority.decide(review.review_id, review.result_hash, "accept", "2026-01-01T00:00:00+00:00")
    except IdentityReviewValidationError as error:
        causal = "decided_at.before_evidence" in error.issues and _canonical_lane(authority) == before_causal
    _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], mention))
    stale = authority.stage(EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], mention), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], mention), "2026-05-01T00:00:00+00:00")
    stale_rejected = False
    before_stale = _canonical_lane(authority)
    try:
        authority.decide(stale.review_id, stale.result_hash, "accept", "2026-06-01T00:00:00+00:00")
    except IdentityReviewStateError as error:
        stale_rejected = error.code == "STALE_BASE" and _canonical_lane(authority) == before_stale
    return _result(**{"review.tamper_fails_closed": tampered, "review.time_causal": causal, "review.stale_fails_closed": stale_rejected})


def _case_atom_and_alias(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    mention = _mention(authority, roles, "e:atom", "小林", "2026-01-01T00:00:00+00:00")
    _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], mention))
    target_rejected = alias_rejected = False
    before_target = _canonical_lane(authority)
    try:
        authority.stage(EntityIdentityDelta.bind(authority.view().world_id, roles["other"], mention), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    except IdentityReviewValidationError as error:
        target_rejected = "binding.atom.target_conflict" in error.issues and _canonical_lane(authority) == before_target
    before_alias = _canonical_lane(authority)
    try:
        authority.stage(EntityIdentityDelta.alias(authority.view().world_id, roles["other"], "小林", (mention,)), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    except IdentityReviewValidationError as error:
        alias_rejected = any("alias.conflicts_with" in issue for issue in error.issues) and _canonical_lane(authority) == before_alias
    return _result(**{"atom.one_active_target": target_rejected, "alias.collision_rejected": alias_rejected})


def _case_merge(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant, all_surfaces=True)
    mention = _mention(authority, roles, "e:merge", "小林", "2026-01-01T00:00:00+00:00")
    before = _canonical_lane(authority)
    review = authority.stage(EntityIdentityDelta.merge(authority.view().world_id, roles["other"], (roles["friend"],), (mention,)), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    event = authority.view().graph.events[0].id
    cognition = authority.view().graph.cognitions[0].id
    expected_manifest = tuple(sorted((
        EntityReferenceRewrite(EntityReferenceLocator("relationship.source", _id(variant, "rel-friend")), roles["friend"], roles["other"]),
        EntityReferenceRewrite(EntityReferenceLocator("relationship.target", _id(variant, "rel-target")), roles["friend"], roles["other"]),
        EntityReferenceRewrite(EntityReferenceLocator("event.participant", event, 0), roles["friend"], roles["other"]),
        EntityReferenceRewrite(EntityReferenceLocator("event.related", event, 0), roles["friend"], roles["other"]),
        EntityReferenceRewrite(EntityReferenceLocator("event.facet_about", event, 0), roles["friend"], roles["other"]),
        EntityReferenceRewrite(EntityReferenceLocator("cognition.target", cognition), roles["friend"], roles["other"]),
        EntityReferenceRewrite(EntityReferenceLocator("cognition.perspective", cognition, 0), roles["friend"], roles["other"]),
    ), key=lambda item: (item.locator.surface, item.locator.object_id, item.locator.index)))
    manifest_exact = review.preview_transition.rewrite_manifest == expected_manifest and len(review.preview_transition.rewrite_manifest) == len(set(rewrite.locator for rewrite in expected_manifest))
    preview_unchanged = _canonical_lane(authority) == before
    _accept_staged(authority, review)
    view = authority.view()
    graph = view.graph
    seven = {"relationship.source", "relationship.target", "event.participant", "event.related", "event.facet_about", "cognition.target", "cognition.perspective"}
    source_edge = next(item for item in graph.relationships if item.id == _id(variant, "rel-friend"))
    target_edge = next(item for item in graph.relationships if item.id == _id(variant, "rel-target"))
    event_after = graph.events[0]
    cognition_after = graph.cognitions[0]
    survivor_surfaces = (
        source_edge.source_entity_id,
        target_edge.target_entity_id,
        event_after.participants[0].entity_id,
        event_after.related_entity_ids[0],
        event_after.facets[0].about_entity_id,
        cognition_after.target.id,
        cognition_after.perspective.holder_entity_ids[0],
    )
    no_old_reference = roles["friend"] not in {edge.source_entity_id for edge in graph.relationships} | {edge.target_entity_id for edge in graph.relationships} | {participant.entity_id for event in graph.events for participant in event.participants} | {entity_id for event in graph.events for entity_id in event.related_entity_ids} | {facet.about_entity_id for event in graph.events for facet in event.facets} | {cognition.target.id for cognition in graph.cognitions} | {holder for cognition in graph.cognitions for holder in cognition.perspective.holder_entity_ids}
    redirect = len(view.redirects) == 1 and view.redirects[0].absorbed_entity_id == roles["friend"] and view.redirects[0].survivor_entity_id == roles["other"]
    tombstone = len(view.tombstones) == 1 and view.tombstones[0].entity_id == roles["friend"] and view.tombstones[0].successors == (roles["other"],)
    return _result(**{
        "merge.preview_all_seven_surfaces": manifest_exact and {rewrite.locator.surface for rewrite in expected_manifest} == seven and preview_unchanged,
        "merge.explicit_survivor_one_hop_redirect": redirect,
        "merge.complete_tombstone_manifest": tombstone and manifest_exact,
        "merge.no_free_text_rewrite": graph.events[0].summary == "immutable free text" and graph.cognitions[0].content == "immutable free text",
        "merge.no_auto_alias_union": "小林" not in next(item.aliases for item in graph.entities if item.id == roles["other"]),
        "merge.source_removed_no_old_refs": roles["friend"] not in {item.id for item in graph.entities} and no_old_reference and all(value == roles["other"] for value in survivor_surfaces),
    })


def _case_merge_cardinality(variant: _Variant) -> dict[str, tuple[bool, str]]:
    graph, roles = _graph(variant)
    graph.add_relationship(Relationship(_id(variant, "collapse"), graph.world.world_id, roles["friend"], roles["other"], "knows"))
    authority = IdentityAuthority(graph)
    mention = _mention(authority, roles, "e:collapse", "小林", "2026-01-01T00:00:00+00:00")
    rejected = False
    before = _canonical_lane(authority)
    try:
        authority.stage(EntityIdentityDelta.merge(authority.view().world_id, roles["other"], (roles["friend"],), (mention,)), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    except IdentityReviewValidationError as error:
        rejected = any("cardinality_collapse" in issue or "self_loop" in issue for issue in error.issues) and _canonical_lane(authority) == before
    return _result(**{"merge.cardinality_collapse_rejected": rejected})


def _split_delta(authority: IdentityAuthority, roles: Mapping[str, str], variant: _Variant) -> tuple[EntityIdentityDelta, str]:
    original = _mention(authority, roles, "e:split-original", "小林", "2026-01-01T00:00:00+00:00")
    other = _mention(authority, roles, "e:split-other", "另一个小林", "2026-01-02T00:00:00+00:00")
    _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], original))
    first_id, second_id = _id(variant, "fresh-successor-a"), _id(variant, "fresh-successor-b")
    event = authority.view().graph.events[0].id
    cognition = authority.view().graph.cognitions[0].id
    relation_source = _id(variant, "rel-friend")
    relation_target = _id(variant, "rel-target")
    locators = (
        EntityReferenceRewrite(EntityReferenceLocator("relationship.source", relation_source), roles["friend"], first_id),
        EntityReferenceRewrite(EntityReferenceLocator("relationship.target", relation_target), roles["friend"], first_id),
        EntityReferenceRewrite(EntityReferenceLocator("event.participant", event, 0), roles["friend"], first_id),
        EntityReferenceRewrite(EntityReferenceLocator("event.related", event, 0), roles["friend"], first_id),
        EntityReferenceRewrite(EntityReferenceLocator("event.facet_about", event, 0), roles["friend"], first_id),
        EntityReferenceRewrite(EntityReferenceLocator("cognition.target", cognition), roles["friend"], first_id),
        EntityReferenceRewrite(EntityReferenceLocator("cognition.perspective", cognition, 0), roles["friend"], first_id),
    )
    successors = (SplitSuccessor(Entity(first_id, authority.view().world_id, "person", "小林"), (original,)), SplitSuccessor(Entity(second_id, authority.view().world_id, "person", "另一个小林"), (other,)))
    binding = authority.view().bindings[0]
    return EntityIdentityDelta.split(authority.view().world_id, roles["friend"], successors, locators, (BindingAssignment(binding.binding_id, first_id),)), first_id


def _case_split(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant, all_surfaces=True)
    delta, first_id = _split_delta(authority, roles, variant)
    before = _canonical_lane(authority)
    review = authority.stage(delta, {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    event = authority.view().graph.events[0].id
    cognition = authority.view().graph.cognitions[0].id
    expected_locators = (
        EntityReferenceLocator("relationship.source", _id(variant, "rel-friend")),
        EntityReferenceLocator("relationship.target", _id(variant, "rel-target")),
        EntityReferenceLocator("event.participant", event, 0), EntityReferenceLocator("event.related", event, 0),
        EntityReferenceLocator("event.facet_about", event, 0), EntityReferenceLocator("cognition.target", cognition),
        EntityReferenceLocator("cognition.perspective", cognition, 0),
    )
    exact_partition = tuple(item.locator for item in review.preview_transition.rewrite_manifest) == expected_locators and all(item.expected_entity_id == roles["friend"] and item.replacement_entity_id == first_id for item in review.preview_transition.rewrite_manifest)
    preview_unchanged = _canonical_lane(authority) == before
    _accept_staged(authority, review, "2026-04-01T00:00:00+00:00")
    view = authority.view()
    graph = view.graph
    event_after = graph.events[0]
    cognition_after = graph.cognitions[0]
    source_edge = next(item for item in graph.relationships if item.id == _id(variant, "rel-friend"))
    target_edge = next(item for item in graph.relationships if item.id == _id(variant, "rel-target"))
    surface_values = (source_edge.source_entity_id, target_edge.target_entity_id, event_after.participants[0].entity_id, event_after.related_entity_ids[0], event_after.facets[0].about_entity_id, cognition_after.target.id, cognition_after.perspective.holder_entity_ids[0])
    no_old = roles["friend"] not in {item.id for item in graph.entities} and all(value != roles["friend"] for value in surface_values)
    tombstone = len(view.tombstones) == 1 and view.tombstones[0].entity_id == roles["friend"] and view.tombstones[0].retired_entity.id == roles["friend"] and view.tombstones[0].successors == (first_id, _id(variant, "fresh-successor-b"))
    no_redirect = not view.redirects
    binding_partition = tuple((item.accepted_entity_id, item.current_entity_id) for item in view.bindings) == ((roles["friend"], first_id), (_id(variant, "fresh-successor-b"), _id(variant, "fresh-successor-b")))
    non_identity_text = event_after.summary == "immutable free text" and cognition_after.content == "immutable free text"
    return _result(**{"split.complete_locator_binding_partitions": exact_partition and preview_unchanged and binding_partition, "split.fresh_successors": {first_id, _id(variant, "fresh-successor-b")} <= {item.id for item in graph.entities} and len(graph.entities) == 7, "split.no_one_to_many_redirect": no_redirect, "split.complete_tombstone_no_old_refs": tombstone and no_old and all(value == first_id for value in surface_values) and non_identity_text})


def _case_split_rejects(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant, all_surfaces=True)
    delta, _ = _split_delta(authority, roles, variant)
    missing_locator = missing_binding = False
    before_locator = _canonical_lane(authority)
    try:
        authority.stage(EntityIdentityDelta.split(authority.view().world_id, roles["friend"], delta.successors, (), delta.binding_assignments), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    except IdentityReviewValidationError as error:
        missing_locator = error.issues == ("split.rewrites.partition.incomplete",) and _canonical_lane(authority) == before_locator
    before_binding = _canonical_lane(authority)
    try:
        authority.stage(EntityIdentityDelta.split(authority.view().world_id, roles["friend"], delta.successors, delta.rewrites, ()), {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    except IdentityReviewValidationError as error:
        missing_binding = error.issues == ("split.binding_assignments.partition.incomplete",) and _canonical_lane(authority) == before_binding
    _accept(authority, delta, "2026-04-01T00:00:00+00:00")
    before_reuse = _canonical_lane(authority)
    event = authority.view().graph.events[0].id
    cognition = authority.view().graph.cognitions[0].id
    first_id = _id(variant, "fresh-successor-a")
    replacement = _id(variant, "reuse-fresh")
    original = delta.successors[0].support_mentions[0]
    extra = _mention(authority, roles, "e:retired-reuse", "替身", "2026-04-02T00:00:00+00:00")
    rewrites = (
        EntityReferenceRewrite(EntityReferenceLocator("relationship.source", _id(variant, "rel-friend")), first_id, replacement),
        EntityReferenceRewrite(EntityReferenceLocator("relationship.target", _id(variant, "rel-target")), first_id, replacement),
        EntityReferenceRewrite(EntityReferenceLocator("event.participant", event, 0), first_id, replacement),
        EntityReferenceRewrite(EntityReferenceLocator("event.related", event, 0), first_id, replacement),
        EntityReferenceRewrite(EntityReferenceLocator("event.facet_about", event, 0), first_id, replacement),
        EntityReferenceRewrite(EntityReferenceLocator("cognition.target", cognition), first_id, replacement),
        EntityReferenceRewrite(EntityReferenceLocator("cognition.perspective", cognition, 0), first_id, replacement),
    )
    binding = authority.view().bindings[0]
    reuse = EntityIdentityDelta.split(authority.view().world_id, first_id, (SplitSuccessor(Entity(roles["friend"], authority.view().world_id, "person", "小林"), (original,)), SplitSuccessor(Entity(replacement, authority.view().world_id, "person", "替身"), (extra,))), rewrites, (BindingAssignment(binding.binding_id, replacement),))
    retired = False
    try:
        authority.stage(reuse, {"gate": "entity-continuity", "reason": "exact-user-evidence"})
    except IdentityReviewValidationError as error:
        retired = any(issue.endswith(".id.retired") for issue in error.issues) and _canonical_lane(authority) == before_reuse
    return _result(**{"split.incomplete_rejected": missing_locator and missing_binding, "split.retired_id_reuse_rejected": retired})


def _case_stale_context(variant: _Variant) -> dict[str, tuple[bool, str]]:
    authority, roles = _authority(variant)
    current = _mention(authority, roles, "e:current", "她", "2026-04-01T00:00:00+00:00", kind="person")
    sealed = authority.resolution_context(current)
    previous = _mention(authority, roles, "e:previous", "小林", "2026-01-01T00:00:00+00:00")
    _accept(authority, EntityIdentityDelta.bind(authority.view().world_id, roles["friend"], previous))
    stale = False
    before = _canonical_lane(authority)
    try:
        EntityReferenceResolver().resolve_context(sealed)
    except IdentityReviewStateError as error:
        stale = error.code == "CONTEXT_SEAL_INVALID" and _canonical_lane(authority) == before
    return _result(**{"sealed_context.accepted_revision_stale": stale})
