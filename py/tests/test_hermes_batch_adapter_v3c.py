"""V5 formal batch adapter: alias merge + relationship correction (V3-C window).

Owner decisions (2026-08-16): explicit-equivalence alias merges only, the
earlier-formed name is canonical; relationship 改口替换 corrects are included;
Recall category expansion is a small deterministic fallback-only table.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable, cast

from support.json_assertions import as_object, as_objects

from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor,
    entity_id_for,
    relationship_id_for,
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker

from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)

_RAW_ALIAS = "杨杨就是小杨"
_RAW_REL_CORRECT = "小王是小李的女朋友"

_T0 = "2026-08-14T10:00:00.000Z"
_T1 = "2026-08-14T11:00:00.000Z"


def _route(script: list[Any]) -> Callable[..., dict[str, object]]:
    def route(messages: list[dict[str, str]], session_id: str) -> dict[str, object]:
        del messages, session_id
        return cast(dict[str, object], script.pop(0))

    return route


def _set_evidence(db_path: Path, evidence_id: str, raw: str) -> None:
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute(
            "UPDATE evidence SET raw_content = ? WHERE id = ?", (raw, evidence_id)
        )
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? "
            "WHERE evidence_id = ?",
            (hashlib.sha256(raw.encode("utf-8")).hexdigest(), evidence_id),
        )
    finally:
        db.close()


def _run(
    db_path: Path,
    clock: MutableClock,
    script: list[Any],
    evidence_ids: tuple[str, ...],
    setup: Callable[[Path], None] | None = None,
    job_id: str = "job-1",
) -> None:
    _initialize_database(db_path)
    _insert_job(db_path, clock, job_id=job_id, evidence_ids=evidence_ids)
    if setup is not None:
        setup(db_path)
    processor = HermesBatchAdapterProcessor(str(db_path), _route(script), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1


def _v5_item(
    kind: str,
    proposition: str,
    *spans: tuple[int, int],
    action: str = "form",
    entity: dict[str, object] | None = None,
    alias_of: dict[str, object] | None = None,
    source_entity: dict[str, object] | None = None,
    target_entity: dict[str, object] | None = None,
    relation_type: str | None = None,
    corrects_relationship_id: str | None = None,
    formed_by: str = "stated",
    evidence_id: str = "evidence-1",
) -> dict[str, object]:
    item: dict[str, object] = {
        "action": action,
        "target": "owner_self",
        "statement_kind": kind,
        "formed_by": formed_by,
        "proposition": proposition,
        "supports": [
            {"evidence_id": evidence_id, "start": s, "end": e}
            for s, e in spans
        ],
    }
    if entity is not None:
        item["entity"] = entity
    if alias_of is not None:
        item["alias_of"] = alias_of
    if source_entity is not None:
        item["source_entity"] = source_entity
    if target_entity is not None:
        item["target_entity"] = target_entity
    if relation_type is not None:
        item["relation_type"] = relation_type
    if corrects_relationship_id is not None:
        item["corrects_relationship_id"] = corrects_relationship_id
    return item


def _batch(*items: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 5, "result": "cognitions", "cognitions": list(items)}


def _model(content: dict[str, object]) -> dict[str, object]:
    return {"content": json.dumps(content), "model": "deepseek-v4-flash"}


def _seed_entity(db_path: Path, name: str, created_at: str) -> str:
    entity_id = entity_id_for("owner", name)
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO entity (id, world_id, kind, canonical_name, "
        "invalid_at, created_at, updated_at) VALUES (?, 'owner', 'person', ?, "
        "NULL, ?, ?)",
        (entity_id, name, created_at, created_at),
    )
    db.close()
    return entity_id


def _seed_relationship(
    db_path: Path,
    source: str,
    relation_type: str,
    target: str,
    content: str,
    created_at: str,
    evidence_id: str = "seed-evidence",
) -> str:
    rid = relationship_id_for(
        "owner",
        entity_id_for("owner", source),
        relation_type,
        entity_id_for("owner", target),
    )
    db = sqlite3.connect(db_path, isolation_level=None)
    db.execute(
        "INSERT OR IGNORE INTO evidence (id, subject_id, source_kind, host_id, "
        "origin_id, occurred_at, recorded_at, raw_content, summary, "
        "allow_local_read, allow_cloud_read, allow_inference, deleted_at) "
        "VALUES (?, 'owner', 'spoken', 'hermes:test', ?, ?, ?, ?, ?, 1, 1, 1, NULL)",
        (
            evidence_id,
            f"seed:{evidence_id}",
            created_at,
            created_at,
            f"seed {evidence_id}",
            f"seed {evidence_id}",
        ),
    )
    db.execute(
        "INSERT OR IGNORE INTO relationship (id, world_id, source_entity_id, "
        "target_entity_id, relation_type, content, formed_by, confidence, "
        "cred_status, invalid_at, created_at, updated_at) VALUES (?, 'owner', "
        "?, ?, ?, ?, 'stated', 600, 'stable', NULL, ?, ?)",
        (
            rid,
            entity_id_for("owner", source),
            entity_id_for("owner", target),
            relation_type,
            content,
            created_at,
            created_at,
        ),
    )
    db.execute(
        "INSERT OR IGNORE INTO relationship_evidence (relationship_id, "
        "evidence_id, relation) VALUES (?, ?, 'support')",
        (rid, evidence_id),
    )
    db.close()
    return rid


# ── alias merge ─────────────────────────────────────────────────────────────

def test_alias_merge_earlier_formed_name_is_canonical(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "杨杨", _T1)

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    assert row["terminal_state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 1
    item = as_objects(outcome["cognitions"])[0]
    assert item["statement_kind"] == "alias"
    assert item["canonical_entity_id"] == entity_id_for("owner", "小杨")
    assert item["canonical_name"] == "小杨"
    assert item["merged_entity_id"] == entity_id_for("owner", "杨杨")
    assert item["merged_name"] == "杨杨"
    assert item["reanchored_relationships"] == 0
    assert item["repointed_cognitions"] == 0
    db = sqlite3.connect(db_path)
    try:
        aliases = db.execute(
            "SELECT aliases_json FROM entity WHERE id = ?",
            (entity_id_for("owner", "小杨"),),
        ).fetchone()
        assert json.loads(str(aliases[0])) == ["杨杨"]
        merged = db.execute(
            "SELECT invalid_at FROM entity WHERE id = ?",
            (entity_id_for("owner", "杨杨"),),
        ).fetchone()
        assert merged[0] is not None
        ledger = db.execute(
            "SELECT content FROM evidence_ledger WHERE id LIKE ?",
            ("evidence-ledger-%",),
        ).fetchall()
        assert any(json.loads(str(r[0]))["relation"] == "alias" for r in ledger)
    finally:
        db.close()


def test_alias_merge_field_order_does_not_matter(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "杨杨", _T1)

    """entity/alias_of order is meaningless: canonical always resolves by
    earlier formation."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "小杨", "kind": "person"},
                    alias_of={"canonical_name": "杨杨", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    item = as_objects(json.loads(str(_job(db_path)["world_result_json"]))["cognitions"])[0]
    assert item["canonical_entity_id"] == entity_id_for("owner", "小杨")
    assert item["merged_entity_id"] == entity_id_for("owner", "杨杨")


def test_alias_merge_reanchors_relationship_to_canonical(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小王", _T0)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "杨杨", _T1)
        _seed_relationship(
                path, "小王", "girlfriend", "杨杨", "小王是杨杨的女朋友", _T1
            )

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]
    old_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "杨杨"),
    )
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    item = as_objects(json.loads(str(row["world_result_json"]))["cognitions"])[0]
    assert item["reanchored_relationships"] == 1
    new_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小杨"),
    )
    db = sqlite3.connect(db_path)
    try:
        old = db.execute(
            "SELECT invalid_at FROM relationship WHERE id = ?", (old_rid,)
        ).fetchone()
        assert old[0] is not None
        new = db.execute(
            "SELECT source_entity_id, target_entity_id, content, invalid_at "
            "FROM relationship WHERE id = ?",
            (new_rid,),
        ).fetchone()
        assert new is not None
        assert new[3] is None
        assert new[0] == entity_id_for("owner", "小王")
        assert new[1] == entity_id_for("owner", "小杨")
        assert new[2] == "小王是杨杨的女朋友"  # content preserved verbatim
        # Support chain forwarded to the re-anchored row.
        assert db.execute(
            "SELECT COUNT(*) FROM relationship_evidence WHERE relationship_id = ?",
            (new_rid,),
        ).fetchone()[0] == 1
        ledgers = [
            json.loads(str(r[0]))
            for r in db.execute("SELECT content FROM evidence_ledger").fetchall()
        ]
        assert any(l["relation"] == "alias_repoint" for l in ledgers)
    finally:
        db.close()


def test_alias_merge_repoints_cognition_targets(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]

    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "杨杨", _T1)
        db = sqlite3.connect(path, isolation_level=None)
        db.execute(
            "INSERT OR IGNORE INTO cognition (id, subject_id, content, "
            "content_type, formed_by, confidence, cred_status, valid_at, "
            "created_at, updated_at) VALUES ('cognition-seed-1', 'owner', "
            "'杨杨是女生', 'attribute', 'stated', 600, 'stable', ?, ?, ?)",
            (_T1, _T1, _T1),
        )
        db.execute(
            "INSERT OR IGNORE INTO cognition_target (cognition_id, "
            "target_entity_id) VALUES ('cognition-seed-1', ?)",
            (entity_id_for("owner", "杨杨"),),
        )
        db.close()

    _run(db_path, clock, script, ("evidence-1",), setup)
    item = as_objects(json.loads(str(_job(db_path)["world_result_json"]))["cognitions"])[0]
    assert item["repointed_cognitions"] == 1
    db = sqlite3.connect(db_path)
    try:
        target = db.execute(
            "SELECT target_entity_id FROM cognition_target "
            "WHERE cognition_id = 'cognition-seed-1'"
        ).fetchone()
        assert target[0] == entity_id_for("owner", "小杨")
    finally:
        db.close()


def test_alias_merge_same_timestamp_tie_breaks_on_formation_order(
    tmp_path: Path,
) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "杨杨", _T0)

    """Both entities share one created_at (one batch): the earlier-inserted
    row wins as canonical (Owner decision: earlier formation)."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    item = as_objects(json.loads(str(row["world_result_json"]))["cognitions"])[0]
    assert item["canonical_entity_id"] == entity_id_for("owner", "小杨")
    assert item["merged_entity_id"] == entity_id_for("owner", "杨杨")


def test_alias_merge_replay_is_zero_write(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "杨杨", _T1)

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
        job_id="job-1",
    )
    first = json.loads(str(_job(db_path, job_id="job-1")["world_result_json"]))
    assert first["world_revision"] == 1
    # Exact replay through a fresh job (the merged entity is already invalid).
    script2 = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]
    _run(db_path, clock, script2, ("evidence-1",), job_id="job-2")
    second = json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))
    assert second["world_revision"] == 1  # no bump
    assert as_objects(second["cognitions"]) == as_objects(first["cognitions"])  # identical outcome


def test_alias_item_missing_target_defaults_to_owner_self(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "杨杨", _T1)

    """Observed live: the real model omits ``target`` on alias items.  The
    perspective is 一律 owner_self, so absent target reads as owner_self."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v5_item(
        "alias", "杨杨就是小杨", (0, 6),
        entity={"canonical_name": "杨杨", "kind": "person"},
        alias_of={"canonical_name": "小杨", "kind": "person"},
    )
    del item["target"]
    script = [_model(_batch(item))]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    item = as_objects(json.loads(str(row["world_result_json"]))["cognitions"])[0]
    assert item["canonical_entity_id"] == entity_id_for("owner", "小杨")


def test_alias_unknown_entity_is_zero_write(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小杨", _T0)

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]
    # Only one of the two entities exists.
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "alias_entity_unknown"
    )
    db = sqlite3.connect(db_path)
    try:
        # Zero writes: the surviving entity is untouched and no ledger exists.
        assert db.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM evidence_ledger").fetchone()[0] == 0
    finally:
        db.close()


def test_alias_identical_names_rejected_at_parse(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是杨杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "杨杨", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "杨杨就是杨杨"),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "alias_target_is_itself"
    )


def test_alias_names_must_appear_in_support_slices(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(
        db_path, clock, evidence_ids=("evidence-1", "evidence-2", "evidence-3")
    )
    # The proposition occurs twice in the batch, so the span-repair fallback
    # fails closed and the model's own slice (which lacks 小杨) is judged.
    _set_evidence(db_path, "evidence-1", "杨杨就是小杨")
    _set_evidence(db_path, "evidence-2", "杨杨就是小杨")
    _set_evidence(db_path, "evidence-3", "杨杨来了")
    _seed_entity(db_path, "小杨", _T0)
    _seed_entity(db_path, "杨杨", _T1)
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 4),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                    evidence_id="evidence-3",
                )
            )
        )
    ]
    processor = HermesBatchAdapterProcessor(str(db_path), _route(script), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "entity_name_not_in_span"
    )


def test_alias_rejected_in_v4_envelope(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v5_item(
        "alias", "杨杨就是小杨", (0, 6),
        entity={"canonical_name": "杨杨", "kind": "person"},
        alias_of={"canonical_name": "小杨", "kind": "person"},
    )
    script = [
        {
            "content": json.dumps(
                {"schema_version": 4, "result": "cognitions", "cognitions": [item]}
            ),
            "model": "m",
        }
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_ALIAS),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unsupported_statement_kind"
    )


# ── relationship 改口替换 ───────────────────────────────────────────────────

def test_relationship_correct_invalidates_prior_and_forms_new(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_REL_CORRECT)
        _seed_entity(path, "小王", _T0)
        _seed_entity(path, "小杨", _T0)
        _seed_relationship(
                path, "小王", "girlfriend", "小杨", "小王是小杨的女朋友", _T0
            )

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    prior_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小杨"),
    )
    script = [
        _model(
            _batch(
                _v5_item(
                    "relationship", "小王是小李的女朋友", (0, 9),
                    action="correct",
                    source_entity={"canonical_name": "小王", "kind": "person"},
                    target_entity={"canonical_name": "小李", "kind": "person"},
                    relation_type="girlfriend",
                    corrects_relationship_id=prior_rid,
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 1
    item = as_objects(outcome["cognitions"])[0]
    assert item["action"] == "correct"
    assert item["statement_kind"] == "relationship"
    assert item["prior_relationship_id"] == prior_rid
    new_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小李"),
    )
    assert item["replacement_relationship_id"] == new_rid
    assert item["confidence"] == 600
    db = sqlite3.connect(db_path)
    try:
        prior = db.execute(
            "SELECT invalid_at FROM relationship WHERE id = ?", (prior_rid,)
        ).fetchone()
        assert prior[0] is not None
        new = db.execute(
            "SELECT target_entity_id, invalid_at FROM relationship WHERE id = ?",
            (new_rid,),
        ).fetchone()
        assert new is not None
        assert new[0] == entity_id_for("owner", "小李")
        assert new[1] is None
        assert db.execute(
            "SELECT COUNT(*) FROM relationship_evidence WHERE relationship_id = ?",
            (new_rid,),
        ).fetchone()[0] == 1
        ledgers = [
            json.loads(str(r[0]))
            for r in db.execute("SELECT content FROM evidence_ledger").fetchall()
        ]
        assert any(
            l["relation"] == "corrects"
            and l.get("prior_relationship_id") == prior_rid
            for l in ledgers
        )
    finally:
        db.close()


def test_relationship_correct_replay_is_zero_write(tmp_path: Path) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_REL_CORRECT)
        _seed_entity(path, "小王", _T0)
        _seed_entity(path, "小杨", _T0)
        _seed_relationship(
            path, "小王", "girlfriend", "小杨", "小王是小杨的女朋友", _T0
        )

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    prior_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小杨"),
    )
    script = [
        _model(
            _batch(
                _v5_item(
                    "relationship", "小王是小李的女朋友", (0, 9),
                    action="correct",
                    source_entity={"canonical_name": "小王", "kind": "person"},
                    target_entity={"canonical_name": "小李", "kind": "person"},
                    relation_type="girlfriend",
                    corrects_relationship_id=prior_rid,
                )
            )
        )
    ]
    _run(db_path, clock, script, ("evidence-1",), setup, job_id="job-1")
    first = json.loads(str(_job(db_path, job_id="job-1")["world_result_json"]))
    assert first["world_revision"] == 1
    script2 = [
        _model(
            _batch(
                _v5_item(
                    "relationship", "小王是小李的女朋友", (0, 9),
                    action="correct",
                    source_entity={"canonical_name": "小王", "kind": "person"},
                    target_entity={"canonical_name": "小李", "kind": "person"},
                    relation_type="girlfriend",
                    corrects_relationship_id=prior_rid,
                )
            )
        )
    ]
    _run(db_path, clock, script2, ("evidence-1",), job_id="job-2")
    second = json.loads(str(_job(db_path, job_id="job-2")["world_result_json"]))
    assert second["world_revision"] == 1  # replay: no bump
    assert as_objects(second["cognitions"]) == as_objects(first["cognitions"])


def test_alias_reanchor_merges_with_existing_current_relationship(
    tmp_path: Path,
) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小王", _T0)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "杨杨", _T1)
        _seed_relationship(
                path, "小王", "girlfriend", "小杨", "小王的女朋友叫杨杨", _T1
            )
        _seed_relationship(
                path, "小王", "girlfriend", "杨杨", "小王的女朋友叫杨杨", _T1,
                evidence_id="seed-evidence-b",
            )

    """The re-pointed triple already exists as a current relationship: the
    alias re-anchor merges support chains instead of duplicating the row."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    item = as_objects(json.loads(str(row["world_result_json"]))["cognitions"])[0]
    assert item["reanchored_relationships"] == 1
    merged_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小杨"),
    )
    db = sqlite3.connect(db_path)
    try:
        merged = db.execute(
            "SELECT invalid_at, confidence FROM relationship WHERE id = ?",
            (merged_rid,),
        ).fetchone()
        assert merged[0] is None
        assert merged[1] == 640  # both seed links merged under one row
        assert db.execute(
            "SELECT COUNT(*) FROM relationship_evidence WHERE relationship_id = ?",
            (merged_rid,),
        ).fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM relationship").fetchone()[0] == 2
    finally:
        db.close()


def test_alias_reanchor_revives_historical_relationship(tmp_path: Path) -> None:
    """The re-pointed triple matches a superseded (invalid) row: it is revived
    under its deterministic id instead of creating a duplicate."""
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "alias", "杨杨就是小杨", (0, 6),
                    entity={"canonical_name": "杨杨", "kind": "person"},
                    alias_of={"canonical_name": "小杨", "kind": "person"},
                )
            )
        )
    ]
    revived_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "boyfriend",
        entity_id_for("owner", "小杨"),
    )

    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_ALIAS)
        _seed_entity(path, "小王", _T0)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "杨杨", _T1)
        db = sqlite3.connect(path, isolation_level=None)
        # Historical (superseded) row under the re-pointed triple.
        db.execute(
            "INSERT OR IGNORE INTO relationship (id, world_id, source_entity_id, "
            "target_entity_id, relation_type, content, formed_by, confidence, "
            "cred_status, invalid_at, created_at, updated_at) VALUES (?, 'owner', "
            "?, ?, 'boyfriend', '小王是杨杨的男朋友', 'stated', 600, 'stable', "
            "?, ?, ?)",
            (
                revived_rid,
                entity_id_for("owner", "小王"),
                entity_id_for("owner", "小杨"),
                _T0,
                _T0,
                _T0,
            ),
        )
        db.execute(
            "INSERT OR IGNORE INTO relationship_evidence (relationship_id, "
            "evidence_id, relation) VALUES (?, 'seed-evidence-2', 'support')",
            (revived_rid,),
        )
        db.close()
        _seed_relationship(
            path, "小王", "boyfriend", "杨杨", "小王是杨杨的男朋友", _T1
        )

    _run(db_path, clock, script, ("evidence-1",), setup)
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        revived = db.execute(
            "SELECT invalid_at, confidence, content FROM relationship WHERE id = ?",
            (revived_rid,),
        ).fetchone()
        assert revived[0] is None  # revived to current
        assert revived[1] == 640
        assert revived[2] == "小王是杨杨的男朋友"
        assert db.execute("SELECT COUNT(*) FROM relationship").fetchone()[0] == 2
    finally:
        db.close()


def test_relationship_correct_merges_into_existing_current_relationship(
    tmp_path: Path,
) -> None:
    def setup(path: Path) -> None:
        _set_evidence(path, "evidence-1", _RAW_REL_CORRECT)
        _seed_entity(path, "小王", _T0)
        _seed_entity(path, "小杨", _T0)
        _seed_entity(path, "小李", _T0)
        _seed_relationship(
                path, "小王", "girlfriend", "小杨", "小王是小杨的女朋友", _T0
            )
        _seed_relationship(
                path, "小王", "girlfriend", "小李", "小王是小李的女朋友", _T0
            )

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    prior_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小杨"),
    )
    new_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小李"),
    )
    script = [
        _model(
            _batch(
                _v5_item(
                    "relationship", "小王是小李的女朋友", (0, 9),
                    action="correct",
                    source_entity={"canonical_name": "小王", "kind": "person"},
                    target_entity={"canonical_name": "小李", "kind": "person"},
                    relation_type="girlfriend",
                    corrects_relationship_id=prior_rid,
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        setup,
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    outcome = json.loads(str(row["world_result_json"]))
    assert outcome["world_revision"] == 1
    item = as_objects(outcome["cognitions"])[0]
    assert item["prior_relationship_id"] == prior_rid
    assert item["replacement_relationship_id"] == new_rid
    db = sqlite3.connect(db_path)
    try:
        prior = db.execute(
            "SELECT invalid_at FROM relationship WHERE id = ?", (prior_rid,)
        ).fetchone()
        assert prior[0] is not None
        merged = db.execute(
            "SELECT invalid_at, confidence FROM relationship WHERE id = ?",
            (new_rid,),
        ).fetchone()
        assert merged[0] is None
        assert merged[1] == 640
        assert db.execute("SELECT COUNT(*) FROM relationship").fetchone()[0] == 2
        assert db.execute(
            "SELECT COUNT(*) FROM relationship_evidence WHERE relationship_id = ?",
            (new_rid,),
        ).fetchone()[0] == 2
    finally:
        db.close()


def test_relationship_correct_unknown_target_is_zero_write(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    script = [
        _model(
            _batch(
                _v5_item(
                    "relationship", "小王是小李的女朋友", (0, 9),
                    action="correct",
                    source_entity={"canonical_name": "小王", "kind": "person"},
                    target_entity={"canonical_name": "小李", "kind": "person"},
                    relation_type="girlfriend",
                    corrects_relationship_id="relationship-missing",
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_REL_CORRECT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "relationship_correction_target_unknown"
    )


def test_relationship_correct_rejected_in_v4_envelope(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v5_item(
        "relationship", "小王是小李的女朋友", (0, 9),
        action="correct",
        source_entity={"canonical_name": "小王", "kind": "person"},
        target_entity={"canonical_name": "小李", "kind": "person"},
        relation_type="girlfriend",
        corrects_relationship_id="relationship-x",
    )
    script = [
        {
            "content": json.dumps(
                {"schema_version": 4, "result": "cognitions", "cognitions": [item]}
            ),
            "model": "m",
        }
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_REL_CORRECT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "invalid_cognition_action"
    )


def test_relationship_correct_target_is_itself_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    new_rid = relationship_id_for(
        "owner", entity_id_for("owner", "小王"), "girlfriend",
        entity_id_for("owner", "小李"),
    )
    script = [
        _model(
            _batch(
                _v5_item(
                    "relationship", "小王是小李的女朋友", (0, 9),
                    action="correct",
                    source_entity={"canonical_name": "小王", "kind": "person"},
                    target_entity={"canonical_name": "小李", "kind": "person"},
                    relation_type="girlfriend",
                    corrects_relationship_id=new_rid,
                )
            )
        )
    ]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", _RAW_REL_CORRECT),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "correction_target_is_itself"
    )


def test_corrects_relationship_id_rejected_on_other_kinds(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v5_item("attribute", "用户喜欢咖啡", (0, 5))
    item["corrects_relationship_id"] = "relationship-x"
    script = [_model(_batch(item))]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "我喜欢咖啡"),
    )
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert (
        json.loads(str(row["world_result_json"]))["reason"]
        == "unexpected_correction_target"
    )


def test_sync_owner_alias_creates_ledger_and_query_service_visible(tmp_path: Path) -> None:
    from memoweft.integrations.trust.currentness import current_entity_aliases
    from memoweft.integrations.trust import QueryService
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    item = _v5_item("preference", "用户以后叫我云", (7, 12))
    script = [_model(_batch(item))]
    _run(
        db_path, clock, script, ("evidence-1",),
        lambda path: _set_evidence(path, "evidence-1", "我喜欢吃玉米，以后叫我云"),
    )
    row = _job(db_path)
    assert row["state"] == "applied"
    db = sqlite3.connect(db_path)
    try:
        owner_row = db.execute("SELECT id, canonical_name, aliases_json FROM entity WHERE canonical_name = '用户'").fetchone()
        assert owner_row is not None
        owner_id = owner_row[0]
        aliases = json.loads(owner_row[2])
        assert aliases == ["云"]
        current = current_entity_aliases(db, "owner", owner_id, surface="trust_local")
        assert current == ("云",)
    finally:
        db.close()
    qs = QueryService(db_path, subject_id="owner")
    world = qs.list_world_items("entity")
    owner_item = next(it for it in as_objects(world["items"]) if it["item_id"] == owner_id)
    assert as_object(owner_item["value"])["aliases"] == ["云"]
    assert as_object(owner_item["value"])["current_aliases"] == ["云"]
