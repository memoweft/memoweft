from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import memoweft.integrations.hermes as hermes_integration
from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor,
    _SYSTEM_PROMPT,
    _SYSTEM_PROMPT_EN,
    entity_id_for,
)
from memoweft.integrations.hermes import HermesMemoWeftRuntime, _build_provider_class
from memoweft.integrations.hermes.recall import RecallSnapshotV1
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from memoweft.integrations.trust.currentness import (
    current_entity_aliases,
    subject_currentness_facts,
)

from test_hermes_batch_adapter import _one_cognition
from test_hermes_world_worker import (
    MutableClock,
    _initialize_database,
    _insert_job,
    _job,
    _policy,
)


def _route(calls: list[object]) -> object:
    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        calls.append((messages, session_id))
        return {
            "content": json.dumps({"schema_version": 1, "result": "no_change"}),
            "model": "scripted",
        }

    return route


@pytest.mark.parametrize(
    ("model_tier", "permission_column"),
    [
        ("cloud", "allow_cloud_read"),
        ("cloud", "allow_inference"),
        ("local", "allow_local_read"),
        ("local", "allow_inference"),
    ],
)
def test_forbidden_evidence_never_dispatches_formation_route(
    tmp_path: Path, model_tier: str, permission_column: str
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute(f"UPDATE evidence SET {permission_column} = 0 WHERE id = 'evidence-1'")
    finally:
        db.close()
    calls: list[object] = []
    processor = HermesBatchAdapterProcessor(
        str(db_path), _route(calls), clock=clock, model_tier=model_tier
    )
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)

    assert worker.run_until_quiescent() == 1
    assert calls == []
    row = _job(db_path)
    assert row["model_dispatch_started_at"] is None
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    finally:
        db.close()


def test_chinese_and_english_event_contracts_allow_only_happened_confirmed_events() -> None:
    assert "除已发生/已确定事件外的一次性事实" in _SYSTEM_PROMPT
    assert "未发生/不确定是否发生/含糊内容不产出" in _SYSTEM_PROMPT
    assert "one-off facts other than happened/confirmed events" in _SYSTEM_PROMPT_EN
    assert "Not-yet-happened/uncertain/vague content is not produced" in _SYSTEM_PROMPT_EN


@pytest.mark.parametrize(
    ("lang", "raw", "prompt_phrase"),
    [
        ("zh", "昨天我去了南京", "除已发生/已确定事件外的一次性事实"),
        ("en", "Yesterday I went to Nanjing", "one-off facts other than happened/confirmed events"),
    ],
)
def test_event_contract_reaches_captured_route_and_applies(
    tmp_path: Path, lang: str, raw: str, prompt_phrase: str
) -> None:
    """The localized Event allowance is a live route contract, not a substring-only check."""

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (raw,))
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-1'",
            (__import__("hashlib").sha256(raw.encode("utf-8")).hexdigest(),),
        )
    finally:
        db.close()
    prompts: list[str] = []

    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        del session_id
        prompts.append(messages[0]["content"])
        return {
            "content": json.dumps(
                {
                    "schema_version": 7,
                    "result": "cognitions",
                    "cognitions": [
                        {
                            "action": "form",
                            "target": "owner_self",
                            "statement_kind": "event",
                            "formed_by": "stated",
                            "proposition": raw,
                            "supports": [{"evidence_id": "evidence-1", "start": 0, "end": len(raw)}],
                        }
                    ],
                }
            ),
            "model": "scripted",
        }

    processor = HermesBatchAdapterProcessor(str(db_path), route, clock=clock, lang=lang)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    assert prompts and prompt_phrase in prompts[0]
    row = _job(db_path)
    assert row["state"] == "applied"
    assert row["terminal_state"] == "applied"


def test_runtime_prefetch_snapshot_is_structured_optional_and_keeps_legacy_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The provider forwards the Core DTO without importing a Hermes host type."""

    empty = RecallSnapshotV1(
        subject_id="owner",
        world_revision=0,
        selected_item_ids=(),
        currentness_digest="a" * 64,
        rendered_recall="",
        recall_snapshot_token="b" * 64,
        count=0,
    )
    calls: list[tuple[str, str]] = []

    def snapshot(db: sqlite3.Connection, subject_id: str, query: str) -> RecallSnapshotV1:
        del db
        calls.append((subject_id, query))
        return empty

    monkeypatch.setattr(hermes_integration, "recall_world_snapshot", snapshot)
    runtime = HermesMemoWeftRuntime()
    runtime.initialize(
        "session", hermes_home=str(tmp_path), agent_context="primary", user_id="owner"
    )
    try:
        runtime._last_recall_count = 4
        assert runtime.prefetch_snapshot("nothing", session_id="session") is empty
        assert runtime.last_recall_count == 0
        assert calls and calls[0][1] == "nothing"
        # The old string injection route remains unchanged and has no hit.
        assert runtime.prefetch("nothing", session_id="session") == ""
    finally:
        runtime.shutdown()

    class _Base:
        def __init__(self) -> None:
            pass

    provider = _build_provider_class(_Base)()
    provider.initialize(
        "session", hermes_home=str(tmp_path / "provider"), agent_context="primary", user_id="owner"
    )
    try:
        assert provider.prefetch_snapshot("empty", session_id="session") is empty
    finally:
        provider.shutdown()

    unavailable = HermesMemoWeftRuntime()
    unavailable.initialize(
        "session", hermes_home=str(tmp_path / "unavailable"), agent_context="primary", user_id="owner"
    )
    monkeypatch.setattr(hermes_integration, "recall_world_snapshot", lambda *_args: None)
    try:
        assert unavailable.prefetch_snapshot("unavailable", session_id="session") is None
        assert unavailable.last_recall_count == 0
    finally:
        unavailable.shutdown()

    failing = HermesMemoWeftRuntime()
    failing.initialize(
        "session", hermes_home=str(tmp_path / "failure"), agent_context="primary", user_id="owner"
    )
    monkeypatch.setattr(
        hermes_integration,
        "recall_world_snapshot",
        lambda *_args: (_ for _ in ()).throw(sqlite3.OperationalError("read failed")),
    )
    try:
        assert failing.prefetch_snapshot("broken", session_id="session") is None
    finally:
        failing.shutdown()


def test_revoked_after_dispatch_never_applies_world_mutation(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "user Evidence evidence-1"
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    calls: list[object] = []

    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        calls.append((messages, session_id))
        db = sqlite3.connect(db_path, isolation_level=None)
        try:
            db.execute("UPDATE evidence SET allow_inference = 0 WHERE id = 'evidence-1'")
        finally:
            db.close()
        return {
            "content": json.dumps(_one_cognition(raw, (0, len(raw)))),
            "model": "scripted",
        }

    processor = HermesBatchAdapterProcessor(str(db_path), route, clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)

    assert worker.run_until_quiescent() == 1
    assert len(calls) == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == (
        "evidence_inference_denied_before_apply"
    )
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    finally:
        db.close()


def test_revoked_after_heartbeat_before_marker_never_calls_route(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    calls: list[object] = []

    processor = HermesBatchAdapterProcessor(str(db_path), _route(calls), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    original_heartbeat = worker.store.heartbeat

    def heartbeat_after_which_permission_is_revoked(claim: object) -> bool:
        alive = original_heartbeat(claim)  # type: ignore[arg-type]
        if alive:
            db = sqlite3.connect(db_path, isolation_level=None)
            try:
                db.execute("UPDATE evidence SET allow_cloud_read = 0 WHERE id = 'evidence-1'")
            finally:
                db.close()
        return alive

    worker.store.heartbeat = heartbeat_after_which_permission_is_revoked  # type: ignore[method-assign]
    assert worker.run_until_quiescent() == 1
    assert calls == []
    row = _job(db_path)
    assert row["model_dispatch_started_at"] is None
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    finally:
        db.close()


def test_revoked_at_marker_transaction_entry_never_calls_route(tmp_path: Path) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    calls: list[object] = []
    processor = HermesBatchAdapterProcessor(str(db_path), _route(calls), clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    original_mark = worker.store.mark_dispatch_started

    def mark_after_revocation(claim: object, *, model_tier: str = "cloud") -> bool:
        db = sqlite3.connect(db_path, isolation_level=None)
        try:
            db.execute("UPDATE evidence SET allow_cloud_read = 0 WHERE id = 'evidence-1'")
        finally:
            db.close()
        return original_mark(claim, model_tier=model_tier)  # type: ignore[arg-type]

    worker.store.mark_dispatch_started = mark_after_revocation  # type: ignore[method-assign]
    assert worker.run_until_quiescent() == 1
    assert calls == []
    row = _job(db_path)
    assert row["model_dispatch_started_at"] is None
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    finally:
        db.close()


def test_unreferenced_bound_evidence_revoked_before_apply_blocks_whole_batch(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw_one = "user Evidence evidence-1"
    raw_two = "user Evidence evidence-2"
    _initialize_database(db_path)
    _insert_job(db_path, clock, evidence_ids=("evidence-1", "evidence-2"))
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-2'", (raw_two,))
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-2'",
            (__import__("hashlib").sha256(raw_two.encode("utf-8")).hexdigest(),),
        )
    finally:
        db.close()
    calls: list[object] = []

    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        calls.append((messages, session_id))
        db = sqlite3.connect(db_path, isolation_level=None)
        try:
            db.execute("UPDATE evidence SET allow_inference = 0 WHERE id = 'evidence-2'")
        finally:
            db.close()
        return {
            "content": json.dumps(_one_cognition(raw_one, (0, len(raw_one)))),
            "model": "scripted",
        }

    processor = HermesBatchAdapterProcessor(str(db_path), route, clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    assert len(calls) == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == (
        "evidence_inference_denied_before_apply"
    )
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 0
    finally:
        db.close()


@pytest.mark.parametrize("revoked_evidence_id", ["history-left", "history-right"])
def test_alias_merge_rechecks_each_historical_entity_provenance_before_apply(
    tmp_path: Path, revoked_evidence_id: str
) -> None:
    """Either alias endpoint can lose authority after the route returns."""

    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    raw = "杨杨就是小杨"
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    now = "2026-08-14T12:00:00.000Z"
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute("UPDATE evidence SET raw_content = ? WHERE id = 'evidence-1'", (raw,))
        db.execute(
            "UPDATE boundary_evidence_content SET raw_content_hash = ? WHERE evidence_id = 'evidence-1'",
            (__import__("hashlib").sha256(raw.encode("utf-8")).hexdigest(),),
        )
        for evidence_id in ("history-left", "history-right"):
            db.execute(
                "INSERT INTO evidence (id, subject_id, source_kind, host_id, origin_id, "
                "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
                "allow_cloud_read, allow_inference, deleted_at) "
                "VALUES (?, 'owner', 'spoken', 'hermes:test', ?, ?, ?, ?, ?, 1, 1, 1, NULL)",
                (evidence_id, evidence_id, now, now, evidence_id, evidence_id),
            )
        for entity_id, name, evidence_id in (
            (entity_id_for("owner", "小杨"), "小杨", "history-left"),
            (entity_id_for("owner", "杨杨"), "杨杨", "history-right"),
        ):
            db.execute(
                "INSERT INTO entity (id, world_id, kind, canonical_name, invalid_at, created_at, updated_at, aliases_json) "
                "VALUES (?, 'owner', 'person', ?, NULL, ?, ?, '[]')",
                (entity_id, name, now, now),
            )
            db.execute(
                "INSERT INTO evidence_ledger (id, content, payload_json) VALUES (?, ?, ?)",
                (
                    f"ledger-{entity_id}",
                    json.dumps({"entity_id": entity_id, "evidence_id": evidence_id, "relation": "support"}),
                    json.dumps({"schema_version": 1}),
                ),
            )
    finally:
        db.close()

    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        del messages, session_id
        db = sqlite3.connect(db_path, isolation_level=None)
        try:
            db.execute(
                "UPDATE evidence SET allow_inference = 0 WHERE id = ?", (revoked_evidence_id,)
            )
        finally:
            db.close()
        return {
            "content": json.dumps(
                {
                    "schema_version": 5,
                    "result": "cognitions",
                    "cognitions": [
                        {
                            "action": "form",
                            "target": "owner_self",
                            "statement_kind": "alias",
                            "formed_by": "stated",
                            "proposition": raw,
                            "entity": {"canonical_name": "小杨", "kind": "person"},
                            "alias_of": {"canonical_name": "杨杨", "kind": "person"},
                            "supports": [{"evidence_id": "evidence-1", "start": 0, "end": len(raw)}],
                        }
                    ],
                }
            ),
            "model": "scripted",
        }

    processor = HermesBatchAdapterProcessor(str(db_path), route, clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    row = _job(db_path)
    assert row["state"] == "no_change"
    assert json.loads(str(row["world_result_json"]))["reason"] == (
        "historical_entity_provenance_not_current_before_apply"
    )
    db = sqlite3.connect(db_path)
    try:
        assert db.execute("SELECT COUNT(*) FROM memory_state").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM entity WHERE invalid_at IS NOT NULL").fetchone()[0] == 0
    finally:
        db.close()


def test_alias_currentness_fails_closed_when_matching_ledger_payload_is_invalid_json(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    _initialize_database(db_path)
    now = "2026-08-14T12:00:00.000Z"
    entity_id = entity_id_for("owner", "小杨")
    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        db.execute(
            "INSERT INTO evidence (id, subject_id, source_kind, host_id, origin_id, "
            "occurred_at, recorded_at, raw_content, summary, allow_local_read, "
            "allow_cloud_read, allow_inference, deleted_at) "
            "VALUES ('history-good', 'owner', 'spoken', 'hermes:test', 'history-good', "
            "?, ?, 'good', 'good', 1, 1, 1, NULL)",
            (now, now),
        )
        db.execute(
            "INSERT INTO entity (id, world_id, kind, canonical_name, invalid_at, created_at, updated_at, aliases_json) "
            "VALUES (?, 'owner', 'person', '小杨', NULL, ?, ?, ?)",
            (entity_id, now, now, json.dumps(["杨杨"])),
        )
        alias_content = json.dumps(
            {
                "relation": "alias",
                "canonical_entity_id": entity_id,
                "merged_entity_id": "merged-id",
                "alias_name": "杨杨",
            }
        )
        db.execute(
            "INSERT INTO evidence_ledger (id, content, payload_json) VALUES "
            "('alias-good', ?, ?), ('alias-invalid-json', ?, '{not-json')",
            (alias_content, json.dumps({"schema_version": 1, "evidence_ids": ["history-good"]}), alias_content),
        )
        assert current_entity_aliases(
            db, "owner", entity_id, surface="formation", model_tier="cloud"
        ) == ()
        facts = subject_currentness_facts(db, "owner")
        assert any(
            fact["evidence_id"] is None
            and fact["ledger_relation"] == "alias:malformed:alias-invalid-json"
            for fact in facts
        )
    finally:
        db.close()


@pytest.mark.parametrize("foreign_history_subject", [False, True])
def test_formation_payload_excludes_restricted_history_and_closes_entities(
    tmp_path: Path, foreign_history_subject: bool
) -> None:
    db_path = tmp_path / "memoweft.sqlite3"
    clock = MutableClock()
    _initialize_database(db_path)
    _insert_job(db_path, clock)
    now = "2026-08-14T12:00:00.000Z"

    def add_evidence(
        db: sqlite3.Connection, evidence_id: str, cloud: int, *, subject: str = "owner"
    ) -> None:
        db.execute(
            """INSERT INTO evidence (
                   id, subject_id, source_kind, host_id, origin_id, occurred_at,
                   recorded_at, raw_content, summary, allow_local_read,
                   allow_cloud_read, allow_inference, corrects_evidence_id,
                   deleted_at, preceding_ai_context
                 ) VALUES (?, ?, 'spoken', 'hermes:test', ?, ?, ?, ?, ?, 1, ?, 1, NULL, NULL, NULL)""",
            (evidence_id, subject, f"origin-{evidence_id}", now, now, evidence_id, evidence_id, cloud),
        )

    db = sqlite3.connect(db_path, isolation_level=None)
    try:
        add_evidence(
            db,
            "history-good",
            1,
            subject="foreign" if foreign_history_subject else "owner",
        )
        add_evidence(db, "history-restricted", 0)
        entities = (
            "entity-ledger",
            "entity-cognition-target",
            "entity-relationship-source",
            "entity-relationship-target",
            "entity-event-participant",
            "entity-event-object",
            "entity-restricted",
        )
        for entity_id in entities:
            db.execute(
                """INSERT INTO entity (
                       id, world_id, kind, canonical_name, invalid_at, created_at,
                       updated_at, aliases_json
                     ) VALUES (?, 'owner', 'person', ?, NULL, ?, ?, ?)""",
                (
                    entity_id,
                    entity_id,
                    now,
                    now,
                    json.dumps(["restricted-alias"]) if entity_id == "entity-ledger" else "[]",
                ),
            )
        for entity_id, evidence_id in (
            ("entity-ledger", "history-good"),
            ("entity-restricted", "history-restricted"),
        ):
            db.execute(
                "INSERT INTO evidence_ledger (id, content, payload_json) VALUES (?, ?, ?)",
                (
                    f"ledger-{entity_id}",
                    json.dumps(
                        {"entity_id": entity_id, "evidence_id": evidence_id, "relation": "support"},
                        sort_keys=True,
                    ),
                    json.dumps({"schema_version": 1}, sort_keys=True),
                ),
            )
        db.execute(
            "INSERT INTO evidence_ledger (id, content, payload_json) VALUES (?, ?, ?)",
            (
                "ledger-restricted-alias",
                json.dumps(
                    {
                        "relation": "alias",
                        "canonical_entity_id": "entity-ledger",
                        "merged_entity_id": "entity-restricted",
                        "alias_name": "restricted-alias",
                    },
                    sort_keys=True,
                ),
                json.dumps(
                    {"schema_version": 1, "evidence_ids": ["history-restricted"]},
                    sort_keys=True,
                ),
            ),
        )
        db.execute(
            """INSERT INTO cognition (
                   id, subject_id, content, content_type, formed_by, confidence,
                   cred_status, scope, invalid_at, asked_at, archived_at, muted_at,
                   created_at, updated_at
                 ) VALUES ('cognition-good', 'owner', 'known', 'attribute', 'stated',
                           600, 'limited', NULL, NULL, NULL, NULL, NULL, ?, ?)""",
            (now, now),
        )
        db.execute(
            "INSERT INTO cognition_evidence (cognition_id, evidence_id, relation) VALUES ('cognition-good', 'history-good', 'support')"
        )
        db.execute(
            "INSERT INTO cognition_target (cognition_id, target_entity_id, perspective_entity_id) VALUES ('cognition-good', 'entity-cognition-target', NULL)"
        )
        db.execute(
            """INSERT INTO relationship (
                   id, world_id, source_entity_id, target_entity_id, relation_type,
                   content, formed_by, confidence, cred_status, invalid_at,
                   created_at, updated_at
                 ) VALUES ('relationship-good', 'owner', 'entity-relationship-source',
                           'entity-relationship-target', 'friend', 'friends', 'stated',
                           600, 'limited', NULL, ?, ?)""",
            (now, now),
        )
        db.execute(
            "INSERT INTO relationship_evidence (relationship_id, evidence_id, relation) VALUES ('relationship-good', 'history-good', 'support')"
        )
        db.execute(
            """INSERT INTO world_event (
                   id, world_id, content, occurred_at, time_expression,
                   participants_json, objects_json, formed_by, confidence,
                   cred_status, invalid_at, created_at, updated_at
                 ) VALUES ('event-good', 'owner', 'went', NULL, NULL, ?, ?, 'stated',
                           600, 'limited', NULL, ?, ?)""",
            (
                json.dumps(["entity-event-participant"]),
                json.dumps(["entity-event-object"]),
                now,
                now,
            ),
        )
        db.execute(
            "INSERT INTO world_event_evidence (world_event_id, evidence_id, relation) VALUES ('event-good', 'history-good', 'support')"
        )
    finally:
        db.close()

    payloads: list[dict[str, object]] = []

    def route(messages: list[dict[str, str]], *, session_id: str) -> dict[str, object]:
        del session_id
        payloads.append(json.loads(messages[1]["content"]))
        return {
            "content": json.dumps({"schema_version": 1, "result": "no_change"}),
            "model": "scripted",
        }

    processor = HermesBatchAdapterProcessor(str(db_path), route, clock=clock)
    worker = WorldJobWorker(db_path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    assert len(payloads) == 1
    payload = payloads[0]
    if foreign_history_subject:
        assert payload["current_cognitions"] == []
        assert payload["current_relationships"] == []
        assert payload["current_events"] == []
        assert payload["current_entities"] == []
    else:
        assert [item["id"] for item in payload["current_cognitions"]] == ["cognition-good"]
        assert [item["id"] for item in payload["current_relationships"]] == ["relationship-good"]
        assert [item["id"] for item in payload["current_events"]] == ["event-good"]
        assert payload["current_events"][0]["participants"] == ["entity-event-participant"]
        assert payload["current_events"][0]["objects"] == ["entity-event-object"]
        assert {item["id"] for item in payload["current_entities"]} == {
            "entity-ledger",
            "entity-cognition-target",
            "entity-relationship-source",
            "entity-relationship-target",
            "entity-event-participant",
            "entity-event-object",
        }
        entity_ledger = next(
            item for item in payload["current_entities"] if item["id"] == "entity-ledger"
        )
        assert entity_ledger["aliases"] == []

    db = sqlite3.connect(db_path)
    try:
        facts = subject_currentness_facts(db, "owner")
    finally:
        db.close()
    historical_facts = [fact for fact in facts if fact["evidence_id"] == "history-good"]
    assert historical_facts
    assert all(fact["evidence_subject_id"] == ("foreign" if foreign_history_subject else "owner") for fact in historical_facts)
    assert all("raw_content" not in fact and "occurred_at" not in fact for fact in facts)
    assert facts == tuple(
        sorted(
            facts,
            key=lambda fact: (
                str(fact["kind"]),
                str(fact["item_id"]),
                str(fact["ledger_relation"]),
                str(fact["evidence_id"]),
            ),
        )
    )
