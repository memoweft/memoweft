"""Focused contracts for the SQLite-backed identity authority."""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import sqlite3

import pytest

from memoweft.world.graph import MemoryWorldGraph
from memoweft.world.delta import WorldDelta
from memoweft.world.identity_review import (
    EntityIdentityDelta,
    IdentityEvidence,
    IdentityReviewStateError,
)
from memoweft.world.identity_store import (
    IdentityPersistenceConflictError,
    IdentityPersistenceIntegrityError,
    IdentityPersistenceVersionError,
    PersistentIdentityAuthority,
    SqliteIdentityStore,
    sync_identity_graph,
)
from memoweft.world.loop import EvidenceRecord, MemoryLoop, _graph_json
from memoweft.world.model import Entity, PersonalWorld


def _graph() -> MemoryWorldGraph:
    graph = MemoryWorldGraph(PersonalWorld("w", "owner"))
    graph.add_entity(Entity("owner", "w", "person", "Owner"))
    graph.add_entity(Entity("a", "w", "person", "Ana"))
    graph.add_entity(Entity("b", "w", "person", "Annie"))
    return graph


def _hash_text(value: str) -> str:
    return "sha256:" + sha256(value.encode("utf-8")).hexdigest()


def _evidence(
    evidence_id: str,
    content: str,
    occurred_at: str = "2026-01-01T00:00:00+00:00",
) -> IdentityEvidence:
    return IdentityEvidence(
        evidence_id,
        "w",
        "conversation",
        occurred_at,
        "user",
        content,
    )


def test_empty_database_bootstraps_one_hash_bound_graph_reference(
    tmp_path: Path,
) -> None:
    loop = MemoryLoop(tmp_path / "memory.db", _graph())
    loop.connection.execute("PRAGMA user_version = 37")
    authority = PersistentIdentityAuthority(loop.connection)

    row = loop.connection.execute(
        """SELECT identity_schema_version, memory_revision, state_json,
                  state_hash, storage_generation
           FROM identity_state WHERE singleton = 1"""
    ).fetchone()
    assert row is not None
    assert (row[0], row[1], row[4]) == (1, 0, 1)
    assert row[3] == _hash_text(row[2])
    assert '"$type":"graph-ref"' in row[2]
    assert '"canonical_name"' not in row[2]
    assert loop.connection.execute("PRAGMA user_version").fetchone()[0] == 37
    assert authority.view().revision == 0
    loop.close()


def test_pending_roundtrips_and_accepts_original_result_hash_after_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.db"
    loop = MemoryLoop(database, _graph())
    authority = PersistentIdentityAuthority(loop.connection)
    authority.register_evidence(_evidence("e:ana", "Ana"))
    mention = authority.issue_verified_mention("e:ana", 0, 3)
    pending = authority.stage(
        EntityIdentityDelta.bind("w", "a", mention),
        {"reviewer": "owner", "reason": "same person"},
    )
    original_hash = pending.result_hash
    loop.close()

    reopened_loop = MemoryLoop(database, _graph())
    reopened = PersistentIdentityAuthority(reopened_loop.connection)
    assert reopened.view().pending[0].result_hash == original_hash
    decision = reopened.decide(
        pending.review_id,
        original_hash,
        "accept",
        "2026-02-01T00:00:00+00:00",
    )
    assert decision.status == "accepted"
    assert reopened.view().bindings[0].current_entity_id == "a"
    reopened_loop.close()


def test_reject_and_accept_ledgers_survive_reopen(tmp_path: Path) -> None:
    database = tmp_path / "memory.db"
    loop = MemoryLoop(database, _graph())
    authority = PersistentIdentityAuthority(loop.connection)
    authority.register_evidence(_evidence("e:ana", "Ana"))
    mention = authority.issue_verified_mention("e:ana", 0, 3)

    rejected = authority.stage(
        EntityIdentityDelta.bind("w", "a", mention),
        {"ordinal": 1},
    )
    authority.decide(
        rejected.review_id,
        rejected.result_hash,
        "reject",
        "2026-02-01T00:00:00+00:00",
    )
    accepted = authority.stage(
        EntityIdentityDelta.bind("w", "a", mention),
        {"ordinal": 2},
    )
    authority.decide(
        accepted.review_id,
        accepted.result_hash,
        "accept",
        "2026-02-02T00:00:00+00:00",
    )
    loop.close()

    reopened_loop = MemoryLoop(database, _graph())
    reopened = PersistentIdentityAuthority(reopened_loop.connection).view()
    assert {item.status for item in reopened.decisions} == {"accepted", "rejected"}
    assert {item.review_id for item in reopened.review_envelopes} == {
        rejected.review_id,
        accepted.review_id,
    }
    assert reopened.revision == 1
    assert len(reopened.bindings) == 1
    reopened_loop.close()


def test_alias_accept_updates_world_and_identity_in_one_commit(tmp_path: Path) -> None:
    database = tmp_path / "memory.db"
    loop = MemoryLoop(database, _graph())
    authority = PersistentIdentityAuthority(loop.connection)
    authority.register_evidence(_evidence("e:alias", "小安"))
    mention = authority.issue_verified_mention("e:alias", 0, 2)
    pending = authority.stage(
        EntityIdentityDelta.alias("w", "a", "小安", (mention,)),
        {"reviewer": "owner"},
    )
    authority.decide(
        pending.review_id,
        pending.result_hash,
        "accept",
        "2026-02-01T00:00:00+00:00",
    )

    world = loop.view()
    identity = authority.view()
    row = loop.connection.execute(
        """SELECT memory_revision, memory_snapshot_hash, identity_graph_hash
           FROM identity_state WHERE singleton = 1"""
    ).fetchone()
    assert world.revision == 1
    assert world.graph.entities["a"].aliases == ("小安",)
    identity_entities = {item.id: item for item in identity.graph.entities}
    assert identity_entities["a"].aliases == ("小安",)
    assert row is not None
    assert row[0] == world.revision
    assert row[1] == world.snapshot_hash
    assert row[2] == identity.graph.graph_hash
    recalled = loop.recall("小安是谁？")
    assert recalled.status == "resolved"
    assert recalled.primary_anchor is not None
    assert recalled.primary_anchor.target.id == "a"
    loop.close()

    with MemoryLoop(database, _graph()) as reopened:
        assert reopened.recall("小安是谁？") == recalled


def test_external_world_sync_refreshes_existing_facade_and_stales_pending(
    tmp_path: Path,
) -> None:
    loop = MemoryLoop(tmp_path / "memory.db", _graph())
    authority = PersistentIdentityAuthority(loop.connection)
    authority.register_evidence(_evidence("e:ana", "Ana"))
    mention = authority.issue_verified_mention("e:ana", 0, 3)
    pending = authority.stage(
        EntityIdentityDelta.bind("w", "a", mention),
        {"reviewer": "owner"},
    )
    old_graph = loop.view().graph
    new_graph = MemoryWorldGraph(
        old_graph.world,
        old_graph.entities.copy(),
        old_graph.relationships.copy(),
        old_graph.events.copy(),
        old_graph.cognitions.copy(),
    )
    new_graph.entities["a"] = replace(new_graph.entities["a"], canonical_name="Anna")
    snapshot_json = _graph_json(new_graph)
    snapshot_hash = _hash_text(snapshot_json)

    loop.connection.execute("BEGIN IMMEDIATE")
    loop.connection.execute(
        """UPDATE memory_state SET revision = 1, snapshot_json = ?, snapshot_hash = ?
           WHERE singleton = 1 AND revision = 0""",
        (snapshot_json, snapshot_hash),
    )
    sync_identity_graph(
        loop.connection,
        old_graph,
        new_graph,
        expected_memory_revision=0,
        new_memory_revision=1,
        new_snapshot_hash=snapshot_hash,
    )
    loop.connection.execute("COMMIT")

    # The already-created façade notices the storage generation change.
    refreshed_entities = {item.id: item for item in authority.view().graph.entities}
    assert refreshed_entities["a"].canonical_name == "Anna"
    with pytest.raises(IdentityReviewStateError, match="STALE_BASE"):
        authority.decide(
            pending.review_id,
            pending.result_hash,
            "accept",
            "2026-02-01T00:00:00+00:00",
        )
    loop.close()


def test_memory_loop_accept_uses_sync_hook_on_the_shared_connection(
    tmp_path: Path,
) -> None:
    loop = MemoryLoop(tmp_path / "memory.db", _graph())
    authority = PersistentIdentityAuthority(loop.connection)
    authority.register_evidence(_evidence("e:ana", "Ana"))
    mention = authority.issue_verified_mention("e:ana", 0, 3)
    identity_pending = authority.stage(
        EntityIdentityDelta.bind("w", "a", mention),
        {"reviewer": "owner"},
    )
    world_pending = loop.stage_addition(
        WorldDelta(
            "w",
            ("e:cara",),
            new_entities=(Entity("c", "w", "person", "Cara"),),
        ),
        (EvidenceRecord("e:cara", "Cara joined the world"),),
    )
    loop.decide(world_pending.id, world_pending.result_hash, "accept")

    refreshed = authority.view()
    assert {item.id for item in refreshed.graph.entities} == {"owner", "a", "b", "c"}
    with pytest.raises(IdentityReviewStateError, match="STALE_BASE"):
        authority.decide(
            identity_pending.review_id,
            identity_pending.result_hash,
            "accept",
            "2026-02-01T00:00:00+00:00",
        )
    loop.close()


def test_memory_loop_accepts_world_and_initial_identity_binding_in_one_sqlite_commit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.db"
    loop = MemoryLoop(database, _graph())
    evidence = EvidenceRecord(
        "e:project",
        "上次那个项目",
        metadata={
            "conversation_id": "conversation:one",
            "occurred_at": "2026-08-11T12:00:00+00:00",
            "continuity_scope": "session:one",
        },
    )
    from memoweft.world.identity_store import ReviewedIdentityBinding

    pending = loop.stage_addition(
        WorldDelta(
            "w",
            (evidence.id,),
            new_entities=(Entity("project:alpha", "w", "project", "那个项目"),),
        ),
        (evidence,),
        identity_bindings=(
            ReviewedIdentityBinding(
                "project:alpha",
                evidence.id,
                "conversation:one",
                "2026-08-11T12:00:00+00:00",
                2,
                6,
                "project",
                "session:one",
            ),
        ),
    )

    accepted = loop.decide(pending.id, pending.result_hash, "accept")
    assert accepted.revision == 1
    assert "project:alpha" in accepted.graph.entities
    row = loop.connection.execute(
        "SELECT memory_revision, memory_snapshot_hash FROM identity_state WHERE singleton = 1"
    ).fetchone()
    assert row is not None
    assert tuple(row) == (accepted.revision, accepted.snapshot_hash)
    authority = PersistentIdentityAuthority(loop.connection).view()
    assert len(authority.bindings) == 1
    binding = authority.bindings[0]
    assert binding.current_entity_id == "project:alpha"
    assert binding.mention.text == "那个项目"
    assert binding.mention.continuity_scope == "session:one"
    loop.close()

    with MemoryLoop(database, _graph()) as reopened_loop:
        reopened = PersistentIdentityAuthority(reopened_loop.connection).view()
        assert reopened.graph.to_graph().entities["project:alpha"].canonical_name == "那个项目"
        assert reopened.bindings[0].current_entity_id == "project:alpha"


def test_memory_loop_identity_binding_failure_rolls_back_world_and_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = MemoryLoop(tmp_path / "memory.db", _graph())
    PersistentIdentityAuthority(loop.connection)
    evidence = EvidenceRecord(
        "e:project",
        "那个项目",
        metadata={
            "conversation_id": "conversation:one",
            "occurred_at": "2026-08-11T12:00:00+00:00",
            "continuity_scope": "session:one",
        },
    )
    from memoweft.world import identity_store
    from memoweft.world.identity_store import ReviewedIdentityBinding

    pending = loop.stage_addition(
        WorldDelta(
            "w",
            (evidence.id,),
            new_entities=(Entity("project:alpha", "w", "project", "项目"),),
        ),
        (evidence,),
        identity_bindings=(
            ReviewedIdentityBinding(
                "project:alpha",
                evidence.id,
                "conversation:one",
                "2026-08-11T12:00:00+00:00",
                0,
                4,
                "project",
                "session:one",
            ),
        ),
    )

    def fail_identity_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected identity failure")

    monkeypatch.setattr(identity_store, "accept_reviewed_identity_bindings", fail_identity_write)
    with pytest.raises(RuntimeError, match="injected identity failure"):
        loop.decide(pending.id, pending.result_hash, "accept")

    assert loop.view().revision == 0
    assert "project:alpha" not in loop.view().graph.entities
    assert loop.connection.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 0
    assert loop.connection.execute(
        "SELECT status FROM proposals WHERE id = ?", (pending.id,)
    ).fetchone()[0] == "pending"
    assert PersistentIdentityAuthority(loop.connection).view().bindings == ()
    loop.close()


def test_product_bundle_identity_failure_rolls_back_its_single_sqlite_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Product bundles share the same accept transaction as identity bindings."""
    loop = MemoryLoop(tmp_path / "product-bundle.db", _graph())
    PersistentIdentityAuthority(loop.connection)
    evidence = EvidenceRecord(
        "e:product-bundle",
        "晨星项目",
        metadata={
            "conversation_id": "conversation:product",
            "occurred_at": "2026-08-12T08:00:00+00:00",
            "continuity_scope": "session:product",
        },
    )
    from memoweft.world import identity_store
    from memoweft.world.identity_store import ReviewedIdentityBinding

    pending = loop.stage_product_bundle(
        WorldDelta(
            "w",
            (evidence.id,),
            new_entities=(Entity("project:morningstar", "w", "project", "晨星项目"),),
        ),
        (evidence,),
        {"display": {"title": "晨星项目"}},
        identity_bindings=(
            ReviewedIdentityBinding(
                "project:morningstar",
                evidence.id,
                "conversation:product",
                "2026-08-12T08:00:00+00:00",
                0,
                4,
                "project",
                "session:product",
            ),
        ),
    )

    def fail_identity_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected product bundle identity failure")

    monkeypatch.setattr(identity_store, "accept_reviewed_identity_bindings", fail_identity_write)
    with pytest.raises(RuntimeError, match="injected product bundle identity failure"):
        loop.decide(pending.id, pending.result_hash, "accept")

    assert loop.view().revision == 0
    assert "project:morningstar" not in loop.view().graph.entities
    assert loop.connection.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 0
    assert loop.connection.execute(
        "SELECT status FROM proposals WHERE id = ?", (pending.id,)
    ).fetchone()[0] == "pending"
    assert PersistentIdentityAuthority(loop.connection).view().bindings == ()
    loop.close()


def test_product_bundle_accepts_multiple_identity_bindings_under_one_review(
    tmp_path: Path,
) -> None:
    loop = MemoryLoop(tmp_path / "product-bundle-bindings.db", _graph())
    evidence = EvidenceRecord(
        "e:two-entities",
        "晨星和蓝图",
        metadata={
            "conversation_id": "conversation:two",
            "occurred_at": "2026-08-12T08:10:00+00:00",
            "continuity_scope": "session:two",
        },
    )
    from memoweft.world.identity_store import ReviewedIdentityBinding

    pending = loop.stage_product_bundle(
        WorldDelta(
            "w",
            (evidence.id,),
            new_entities=(
                Entity("project:morningstar", "w", "project", "晨星"),
                Entity("document:blueprint", "w", "document", "蓝图"),
            ),
        ),
        (evidence,),
        identity_bindings=(
            ReviewedIdentityBinding(
                "project:morningstar", evidence.id, "conversation:two",
                "2026-08-12T08:10:00+00:00", 0, 2, "project", "session:two",
            ),
            ReviewedIdentityBinding(
                "document:blueprint", evidence.id, "conversation:two",
                "2026-08-12T08:10:00+00:00", 3, 5, "document", "session:two",
            ),
        ),
    )
    accepted = loop.decide(pending.id, pending.result_hash, "accept")
    assert accepted.revision == 1
    identity = PersistentIdentityAuthority(loop.connection).view()
    assert {item.current_entity_id for item in identity.bindings} == {
        "project:morningstar", "document:blueprint"
    }
    loop.close()


def test_rejected_identity_binding_proposal_never_bootstraps_or_mutates_identity(
    tmp_path: Path,
) -> None:
    loop = MemoryLoop(tmp_path / "memory.db", _graph())
    evidence = EvidenceRecord(
        "e:project",
        "那个项目",
        metadata={
            "conversation_id": "conversation:one",
            "occurred_at": "2026-08-11T12:00:00+00:00",
            "continuity_scope": "session:one",
        },
    )
    from memoweft.world.identity_store import ReviewedIdentityBinding

    pending = loop.stage_addition(
        WorldDelta(
            "w",
            (evidence.id,),
            new_entities=(Entity("project:alpha", "w", "project", "项目"),),
        ),
        (evidence,),
        identity_bindings=(
            ReviewedIdentityBinding(
                "project:alpha",
                evidence.id,
                "conversation:one",
                "2026-08-11T12:00:00+00:00",
                0,
                4,
                "project",
                "session:one",
            ),
        ),
    )

    rejected = loop.decide(pending.id, pending.result_hash, "reject")
    assert rejected.revision == 0
    assert "project:alpha" not in rejected.graph.entities
    assert loop.connection.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 0
    assert loop.connection.execute(
        "SELECT COUNT(*) FROM identity_state WHERE singleton = 1"
    ).fetchone()[0] == 0
    loop.close()


def test_accepted_revision_retires_context_from_precommit_authority(
    tmp_path: Path,
) -> None:
    loop = MemoryLoop(tmp_path / "memory.db", _graph())
    authority = PersistentIdentityAuthority(loop.connection)
    authority.register_evidence(_evidence("e:ana", "Ana"))
    authority.register_evidence(
        _evidence("e:current", "她", "2026-03-01T00:00:00+00:00")
    )
    ana = authority.issue_verified_mention("e:ana", 0, 3)
    current = authority.issue_verified_mention("e:current", 0, 1)
    old_context = authority.resolution_context(current)
    pending = authority.stage(
        EntityIdentityDelta.bind("w", "a", ana),
        {"reviewer": "owner"},
    )
    authority.decide(
        pending.review_id,
        pending.result_hash,
        "accept",
        "2026-02-01T00:00:00+00:00",
    )

    with pytest.raises(IdentityReviewStateError, match="CONTEXT_SEAL_INVALID"):
        old_context.to_accepted_entity_references()
    assert authority.resolution_context(current).bindings[0].current_entity_id == "a"
    loop.close()


def test_transaction_failure_rolls_back_world_and_keeps_live_authority(
    tmp_path: Path,
) -> None:
    loop = MemoryLoop(tmp_path / "memory.db", _graph())
    authority = PersistentIdentityAuthority(loop.connection)
    authority.register_evidence(_evidence("e:alias", "小安"))
    mention = authority.issue_verified_mention("e:alias", 0, 2)
    pending = authority.stage(
        EntityIdentityDelta.alias("w", "a", "小安", (mention,)),
        {"reviewer": "owner"},
    )
    before_world = loop.view()
    before_generation = authority.storage_generation
    loop.connection.execute(
        """CREATE TRIGGER fail_identity_write BEFORE UPDATE ON identity_state
           BEGIN SELECT RAISE(ABORT, 'forced identity write failure'); END"""
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced identity write failure"):
        authority.decide(
            pending.review_id,
            pending.result_hash,
            "accept",
            "2026-02-01T00:00:00+00:00",
        )

    after_world = loop.view()
    after_identity = authority.view()
    assert (after_world.revision, after_world.snapshot_hash) == (
        before_world.revision,
        before_world.snapshot_hash,
    )
    assert after_world.graph.entities["a"].aliases == ()
    assert [item.review_id for item in after_identity.pending] == [pending.review_id]
    assert authority.storage_generation == before_generation
    loop.connection.execute("DROP TRIGGER fail_identity_write")
    loop.close()


def test_hash_corruption_and_schema_version_have_stable_domain_errors(
    tmp_path: Path,
) -> None:
    loop = MemoryLoop(tmp_path / "memory.db", _graph())
    PersistentIdentityAuthority(loop.connection)
    loop.connection.execute(
        "UPDATE identity_state SET state_json = state_json || ' ' WHERE singleton = 1"
    )
    with pytest.raises(IdentityPersistenceIntegrityError) as hash_error:
        PersistentIdentityAuthority(loop.connection)
    assert hash_error.value.code == "IDENTITY_STATE_HASH_MISMATCH"

    loop.connection.execute(
        """UPDATE identity_state
           SET state_json = substr(state_json, 1, length(state_json) - 1),
               identity_schema_version = 99
           WHERE singleton = 1"""
    )
    with pytest.raises(IdentityPersistenceVersionError) as version_error:
        PersistentIdentityAuthority(loop.connection)
    assert version_error.value.code == "IDENTITY_SCHEMA_VERSION_UNSUPPORTED"
    loop.close()


def test_compare_and_swap_conflict_does_not_replace_stale_live_state(
    tmp_path: Path,
) -> None:
    loop = MemoryLoop(tmp_path / "memory.db", _graph())
    first = PersistentIdentityAuthority(loop.connection)
    second = PersistentIdentityAuthority(loop.connection)
    first.register_evidence(_evidence("e:first", "Ana"))

    # Force a stale expected generation after first has committed.  Public
    # calls normally auto-refresh; this isolates the store-level CAS contract.
    stale = second._stored  # noqa: SLF001
    current = first._stored  # noqa: SLF001
    with pytest.raises(IdentityPersistenceConflictError) as conflict:
        second._store._compare_and_swap(  # noqa: SLF001
            stale,
            stale.state,
            memory_revision=current.memory_revision,
            memory_snapshot_hash=current.memory_snapshot_hash,
        )
    assert conflict.value.code == "IDENTITY_STORAGE_CONFLICT"
    assert second.view().evidence[0].id == "e:first"
    loop.close()


def test_legacy_memory_loop_database_gets_identity_area_without_user_version_change(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.db"
    loop = MemoryLoop(database, _graph())
    loop.connection.execute("DROP TABLE identity_state")
    loop.connection.execute("PRAGMA user_version = 2")
    loop.close()

    connection = sqlite3.connect(database, isolation_level=None)
    store = SqliteIdentityStore(connection)
    snapshot = store.bootstrap()
    assert snapshot.storage_generation == 1
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(identity_state)")
    }
    assert {
        "identity_schema_version",
        "identity_graph_hash",
        "state_json",
        "state_hash",
        "storage_generation",
    } <= columns
    connection.close()
