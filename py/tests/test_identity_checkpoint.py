"""Focused tests for the storage-neutral identity authority checkpoint."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import json
from typing import Any, cast

import pytest

from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.identity_review import (
    EntityIdentityDelta,
    IdentityAuthority,
    IdentityAuthorityState,
    IdentityEvidence,
    IdentityReviewStateError,
    IdentityReviewValidationError,
    VerifiedReferenceMention,
)
from memoweft.world.model import Entity, PersonalWorld


def _graph() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld("w", "owner"))
    graph.add_entity(Entity("owner", "w", "person", "Owner"))
    graph.add_entity(Entity("a", "w", "person", "Ana"))
    graph.add_entity(Entity("b", "w", "person", "Annie"))
    return graph


def _pending_authority() -> tuple[
    IdentityAuthority,
    VerifiedReferenceMention,
    VerifiedReferenceMention,
]:
    authority = IdentityAuthority(_graph())
    authority.register_evidence(
        IdentityEvidence(
            "e:a",
            "w",
            "c",
            "2026-01-01T00:00:00+00:00",
            "user",
            "Ana",
        )
    )
    authority.register_evidence(
        IdentityEvidence(
            "e:unused",
            "w",
            "c",
            "2026-01-02T00:00:00+00:00",
            "user",
            "Annie",
        )
    )
    mention = authority.issue_verified_mention("e:a", 0, 3)
    unused = authority.issue_verified_mention("e:unused", 0, 5)
    authority.stage(EntityIdentityDelta.bind("w", "a", mention), {"owner": True})
    return authority, mention, unused


def _complete_authority() -> IdentityAuthority:
    authority = IdentityAuthority(_graph())
    for evidence in (
        IdentityEvidence(
            "e:a", "w", "c", "2026-01-01T00:00:00+00:00", "user", "Ana"
        ),
        IdentityEvidence(
            "e:b", "w", "c", "2026-01-04T00:00:00+00:00", "user", "Annie"
        ),
    ):
        authority.register_evidence(evidence)
    mention_a = authority.issue_verified_mention("e:a", 0, 3)
    mention_b = authority.issue_verified_mention("e:b", 0, 5)

    binding = authority.stage(
        EntityIdentityDelta.bind("w", "a", mention_a),
        {"kind": "bind"},
    )
    authority.decide(
        binding.review_id,
        binding.result_hash,
        "accept",
        "2026-01-02T00:00:00+00:00",
    )
    merge = authority.stage(
        EntityIdentityDelta.merge("w", "b", ("a",), (mention_a,)),
        {"kind": "merge"},
    )
    authority.decide(
        merge.review_id,
        merge.result_hash,
        "accept",
        "2026-01-03T00:00:00+00:00",
    )
    rejected = authority.stage(
        EntityIdentityDelta.bind("w", "b", mention_b),
        {"kind": "rejected"},
    )
    authority.decide(
        rejected.review_id,
        rejected.result_hash,
        "reject",
        "2026-01-05T00:00:00+00:00",
    )
    return authority


def test_checkpoint_round_trip_keeps_authority_seals_unused_mentions_and_pending_hash() -> None:
    authority, _, unused = _pending_authority()
    state = authority.checkpoint()

    assert isinstance(state, IdentityAuthorityState)
    assert len(state.mentions) == 2
    assert json.loads(json.dumps(asdict(state), ensure_ascii=False))["authority_id"] == state.authority_id

    restored = IdentityAuthority.restore(state)
    assert restored.checkpoint() == state
    assert restored.issue_verified_mention("e:unused", 0, 5) == unused
    pending = state.pending[0]
    restored.decide(
        pending.review_id,
        pending.result_hash,
        "accept",
        "2026-02-01T00:00:00+00:00",
    )
    assert restored.view().revision == 1
    assert restored.view().bindings[0].mention.atom_hash == state.mentions[0].atom_hash


def test_checkpoint_and_restore_are_deeply_detached() -> None:
    authority, _, _ = _pending_authority()
    state = authority.checkpoint()
    restored = IdentityAuthority.restore(state)

    object.__setattr__(state.graph.entities[0], "canonical_name", "tampered")
    object.__setattr__(state.mentions[0], "text", "tampered")

    assert authority.view().graph.entities[0].canonical_name != "tampered"
    assert restored.view().graph.entities[0].canonical_name != "tampered"
    assert restored.checkpoint().mentions[0].text != "tampered"


def test_checkpoint_with_graph_preserves_ledger_and_makes_old_pending_stale() -> None:
    authority, _, _ = _pending_authority()
    old_state = authority.checkpoint()
    replacement_graph = old_state.graph.to_graph()
    replacement_graph.entities["b"] = replace(
        replacement_graph.entities["b"],
        aliases=("Ann",),
    )

    state = authority.checkpoint_with_graph(replacement_graph)
    assert state.authority_id == old_state.authority_id
    assert state.revision == old_state.revision
    assert state.evidence == old_state.evidence
    assert state.mentions == old_state.mentions
    assert state.pending == old_state.pending
    assert state.graph.graph_hash != old_state.graph.graph_hash

    restored = IdentityAuthority.restore(state)
    pending = state.pending[0]
    with pytest.raises(IdentityReviewStateError, match="STALE_BASE"):
        restored.decide(
            pending.review_id,
            pending.result_hash,
            "accept",
            "2026-02-01T00:00:00+00:00",
        )
    restored.decide(
        pending.review_id,
        pending.result_hash,
        "reject",
        "2026-02-01T00:00:00+00:00",
    )


def test_complete_accepted_and_rejected_ledgers_round_trip() -> None:
    authority = _complete_authority()
    state = authority.checkpoint()
    restored = IdentityAuthority.restore(state)

    assert restored.checkpoint() == state
    assert state.revision == 2
    assert len(state.transitions) == 2
    assert {item.status for item in state.decisions} == {"accepted", "rejected"}
    assert state.bindings[0].current_entity_id == "b"
    assert state.redirects[0].survivor_entity_id == "b"
    assert state.tombstones[0].entity_id == "a"


def test_restore_rejects_graph_evidence_mention_and_pending_hash_tampering() -> None:
    authority, _, _ = _pending_authority()
    state = authority.checkpoint()
    tampered_states: list[IdentityAuthorityState] = [
        replace(state, authority_id="0" * 32),
        replace(state, graph=replace(state.graph, graph_hash="0" * 64)),
        replace(
            state,
            evidence=(replace(state.evidence[0], content="forged"), *state.evidence[1:]),
        ),
        replace(
            state,
            pending=(replace(state.pending[0], preview_hash="0" * 64),),
        ),
        replace(
            state,
            pending=(replace(state.pending[0], result_hash="0" * 64),),
        ),
    ]
    forged_mention_state = deepcopy(state)
    object.__setattr__(forged_mention_state.mentions[0], "_issuer_seal", "0" * 64)
    tampered_states.append(forged_mention_state)

    for tampered in tampered_states:
        with pytest.raises(IdentityReviewValidationError, match=r"state\."):
            IdentityAuthority.restore(tampered)


def test_restore_rejects_decision_binding_transition_redirect_and_tombstone_tampering() -> None:
    state = _complete_authority().checkpoint()
    binding = state.bindings[0]
    transition = state.transitions[0]
    redirect = state.redirects[0]
    tombstone = state.tombstones[0]
    tampered_states = (
        replace(
            state,
            decisions=(
                replace(state.decisions[0], result_hash="0" * 64),
                *state.decisions[1:],
            ),
        ),
        replace(
            state,
            bindings=(replace(binding, current_entity_id="missing"),),
        ),
        replace(
            state,
            transitions=(replace(transition, revision=7), *state.transitions[1:]),
        ),
        replace(
            state,
            redirects=(replace(redirect, survivor_entity_id="missing"),),
        ),
        replace(
            state,
            tombstones=(replace(tombstone, successors=(tombstone.entity_id,)),),
        ),
    )

    for tampered in tampered_states:
        with pytest.raises(IdentityReviewValidationError, match=r"state\."):
            IdentityAuthority.restore(tampered)


def test_restore_rejects_non_state_input_with_domain_error() -> None:
    with pytest.raises(IdentityReviewValidationError, match=r"state\.type\.invalid"):
        IdentityAuthority.restore(cast(Any, object()))
