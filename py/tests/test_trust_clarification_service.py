"""N6 durable clarification request, answer, follow-up, and closure contract."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

import pytest

from memoweft.integrations.hermes.boundary_store import (
    HermesBoundaryEvidenceCandidate,
    HermesBoundaryFormalTarget,
    HermesBoundaryStore,
    ValidatedHermesBoundary,
)
from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.world_worker import (
    WorldJobResult,
    WorldJobStore,
    WorldJobWorker,
)
from memoweft.integrations.trust.clarification_service import (
    ClarificationError,
    ClarificationService,
    derive_clarification_id,
)
from memoweft.store import open_db


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 25, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int = 1) -> None:
        self.value += timedelta(seconds=seconds)


def _canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def _seed_open_clarification(
    path: Path,
    clock: MutableClock,
    *,
    source_text: str = "I met Alex yesterday.",
    question: str = "Which Alex do you mean?",
    result_session_id: str = "result-session",
    source_host_id: str = "hermes:test",
) -> tuple[str, str]:
    db = open_db(str(path))
    try:
        boundary = ValidatedHermesBoundary(
            event_id="boundary-source-" + sha256(source_text.encode()).hexdigest(),
            payload_hash=sha256(("payload:" + source_text).encode()).hexdigest(),
            formal_target=HermesBoundaryFormalTarget(
                boundary_schema_version=1,
                provider_name="memoweft",
                parent_session_id="parent-session",
                result_session_id=result_session_id,
                mode="rotation" if result_session_id != "parent-session" else "in_place",
                subject_id="owner",
                host_id=source_host_id,
            ),
            evidence=(
                HermesBoundaryEvidenceCandidate(
                    origin_id="origin-" + sha256(source_text.encode()).hexdigest(),
                    raw_content=source_text,
                ),
            ),
        )
        receipt = HermesBoundaryStore(db, clock=clock).accept(boundary)
    finally:
        db.close()
    store = WorldJobStore(path, clock=clock)
    claim = store.claim_one("n6-test-worker")
    assert claim is not None and claim.job_id == receipt.job_id
    assert store.settle(
        claim,
        WorldJobResult.clarification_required(
            "model_clarification_required", display=question
        ),
    )
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        outcome = db.execute(
            "SELECT outcome_id FROM terminal_outcome WHERE job_id = ?",
            (receipt.job_id,),
        ).fetchone()
        assert outcome is not None
        clarification_id = derive_clarification_id(receipt.job_id, outcome["outcome_id"])
    finally:
        db.close()
    return clarification_id, receipt.job_id


def _service(path: Path, clock: MutableClock) -> ClarificationService:
    return ClarificationService(
        path, subject_id="owner", host_id="hermes:test", clock=clock
    )


def test_terminal_clarification_opens_exact_durable_record_atomically(
    tmp_path: Path,
) -> None:
    path = tmp_path / "clarification.sqlite3"
    clock = MutableClock()
    clarification_id, source_job_id = _seed_open_clarification(path, clock)

    records = _service(path, clock).list_clarifications(state="open")
    assert records == [
        {
            "schema_version": 1,
            "clarification_id": clarification_id,
            "source_job_id": source_job_id,
            "source_outcome_id": records[0]["source_outcome_id"],
            "subject_id": "owner",
            "result_session_id": "result-session",
            "question": "Which Alex do you mean?",
            "target_hint": None,
            "state": "open",
            "answer_evidence_id": None,
            "follow_up_job_id": None,
            "opened_at": "2026-08-25T00:00:00.000Z",
            "answered_at": None,
            "resolved_at": None,
        }
    ]
    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT COUNT(*) FROM terminal_outcome WHERE job_id = ?", (source_job_id,)
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT COUNT(*) FROM clarification WHERE source_job_id = ?", (source_job_id,)
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_host_bound_inbox_lists_only_clarifications_this_host_can_answer(
    tmp_path: Path,
) -> None:
    path = tmp_path / "host-bound-inbox.sqlite3"
    clock = MutableClock()
    cli_id, _ = _seed_open_clarification(
        path,
        clock,
        source_text="The owner said he is Casey.",
        question="Who does he refer to?",
        result_session_id="cli-session",
        source_host_id="hermes:cli",
    )
    clock.advance()
    weixin_id, _ = _seed_open_clarification(
        path,
        clock,
        source_text="The owner mentioned Alex.",
        question="Which Alex?",
        result_session_id="weixin-session",
        source_host_id="hermes:gateway:weixin",
    )

    cli_service = ClarificationService(
        path, subject_id="owner", host_id="hermes:cli", clock=clock
    )
    listed = cli_service.list_clarifications(state="open")

    assert [record["clarification_id"] for record in listed] == [cli_id]
    assert weixin_id not in {record["clarification_id"] for record in listed}
    receipt = cli_service.answer(
        clarification_id=cli_id,
        result_session_id="cli-session",
        answer="He refers to the owner, Casey.",
    )
    assert receipt["state"] == "answered"


def test_answer_is_exact_evidence_and_one_atomic_follow_up_job(
    tmp_path: Path,
) -> None:
    path = tmp_path / "answer.sqlite3"
    clock = MutableClock()
    clarification_id, _ = _seed_open_clarification(path, clock)
    clock.advance()

    receipt = _service(path, clock).answer(
        clarification_id=clarification_id,
        result_session_id="result-session",
        answer="Alex Chen from the design team.",
    )
    assert receipt["schema_version"] == 1
    assert receipt["clarification_id"] == clarification_id
    assert receipt["state"] == "answered"
    assert receipt["result_session_id"] == "result-session"
    assert receipt["replayed"] is False

    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        evidence = db.execute(
            "SELECT * FROM evidence WHERE id = ?", (receipt["answer_evidence_id"],)
        ).fetchone()
        assert evidence is not None
        assert evidence["subject_id"] == "owner"
        assert evidence["host_id"] == "hermes:test"
        assert evidence["source_kind"] == "spoken"
        assert evidence["raw_content"] == "Alex Chen from the design team."
        assert evidence["summary"] == "Alex Chen from the design team."
        assert evidence["preceding_ai_context"] == "Which Alex do you mean?"
        assert db.execute(
            "SELECT raw_content_hash FROM boundary_evidence_content WHERE evidence_id = ?",
            (receipt["answer_evidence_id"],),
        ).fetchone()[0] == sha256(evidence["raw_content"].encode()).hexdigest()
        job = db.execute(
            "SELECT * FROM memory_world_job WHERE job_id = ?",
            (receipt["follow_up_job_id"],),
        ).fetchone()
        assert job is not None
        assert job["state"] == "pending"
        assert job["parent_session_id"] == "result-session"
        assert job["result_session_id"] == "result-session"
        assert job["subject_id"] == "owner"
        assert job["host_id"] == "hermes:test"
        assert json.loads(job["evidence_ids_json"]) == [receipt["answer_evidence_id"]]
        assert db.execute("SELECT COUNT(*) FROM memory_world_job").fetchone()[0] == 2
    finally:
        db.close()


def test_answer_session_fence_replay_conflict_and_restart_are_zero_duplicate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "replay.sqlite3"
    clock = MutableClock()
    clarification_id, _ = _seed_open_clarification(path, clock)
    service = _service(path, clock)
    with pytest.raises(
        ClarificationError, match="clarification_result_session_mismatch"
    ):
        service.answer(
            clarification_id=clarification_id,
            result_session_id="old-parent-session",
            answer="Wrong lane",
        )
    assert service.answer_latest_for_session(
        result_session_id="old-parent-session", answer="Wrong lane"
    ) is None

    clock.advance()
    first = service.answer(
        clarification_id=clarification_id,
        result_session_id="result-session",
        answer="Alex Chen.",
    )
    restarted = _service(path, clock)
    replay = restarted.answer(
        clarification_id=clarification_id,
        result_session_id="result-session",
        answer="Alex Chen.",
    )
    latest_replay = restarted.answer_latest_for_session(
        result_session_id="result-session", answer="Alex Chen."
    )
    assert latest_replay is not None
    assert replay["replayed"] is True
    assert latest_replay["replayed"] is True
    for field in ("answer_evidence_id", "follow_up_job_id", "clarification_id"):
        assert replay[field] == first[field] == latest_replay[field]
    with pytest.raises(ClarificationError, match="clarification_already_answered"):
        restarted.answer(
            clarification_id=clarification_id,
            result_session_id="result-session",
            answer="A different Alex.",
        )

    db = sqlite3.connect(path)
    try:
        assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM memory_world_job").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM clarification").fetchone()[0] == 1
    finally:
        db.close()


@pytest.mark.parametrize(
    ("result", "expected_terminal"),
    [
        (WorldJobResult.no_change("follow_up_no_change"), "no_change"),
        (
            WorldJobResult.out_of_scope(
                "follow_up_out_of_scope", display="Outside the memory contract."
            ),
            "out_of_scope",
        ),
        (WorldJobResult.dead("follow_up_failed"), "failed"),
    ],
)
def test_follow_up_terminal_resolves_source_clarification(
    tmp_path: Path,
    result: WorldJobResult,
    expected_terminal: str,
) -> None:
    path = tmp_path / f"resolve-{expected_terminal}.sqlite3"
    clock = MutableClock()
    clarification_id, _ = _seed_open_clarification(path, clock)
    clock.advance()
    answer = _service(path, clock).answer(
        clarification_id=clarification_id,
        result_session_id="result-session",
        answer="Alex Chen.",
    )
    clock.advance()
    store = WorldJobStore(path, clock=clock)
    claim = store.claim_one("follow-up-worker")
    assert claim is not None and claim.job_id == answer["follow_up_job_id"]
    assert store.settle(claim, result)

    record = _service(path, clock).get_clarification(clarification_id)
    assert record["state"] == "resolved"
    assert record["resolved_at"] == "2026-08-25T00:00:02.000Z"
    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT terminal_state FROM terminal_outcome WHERE job_id = ?",
            (answer["follow_up_job_id"],),
        ).fetchone()[0] == expected_terminal
    finally:
        db.close()


def test_follow_up_applied_resolves_source_clarification(
    tmp_path: Path,
) -> None:
    path = tmp_path / "resolve-applied.sqlite3"
    clock = MutableClock()
    clarification_id, _ = _seed_open_clarification(path, clock)
    clock.advance()
    answer_text = "Please call me Alex Chen."
    answer = _service(path, clock).answer(
        clarification_id=clarification_id,
        result_session_id="result-session",
        answer=answer_text,
    )
    clock.advance()

    def route(
        _messages: list[dict[str, str]], session_id: str
    ) -> dict[str, object]:
        assert session_id == "result-session"
        return {
            "content": _canonical(
                {
                    "schema_version": 1,
                    "result": "one_cognition",
                    "cognition": {
                        "target": "owner_self",
                        "statement_kind": "preference",
                        "proposition": "The owner prefers to be called Alex Chen.",
                        "supports": [
                            {
                                "evidence_id": answer["answer_evidence_id"],
                                "start": 0,
                                "end": len(answer_text),
                            }
                        ],
                    },
                }
            ),
            "model": "n6-test-model",
        }

    worker = WorldJobWorker(
        path,
        processor=HermesBatchAdapterProcessor(str(path), route, clock=clock),
        clock=clock,
        worker_id="n6-applied-worker",
    )
    assert worker.run_until_quiescent() == 1

    record = _service(path, clock).get_clarification(clarification_id)
    assert record["state"] == "resolved"
    assert record["resolved_at"] == "2026-08-25T00:00:02.000Z"
    db = sqlite3.connect(path)
    try:
        assert db.execute(
            "SELECT terminal_state FROM terminal_outcome WHERE job_id = ?",
            (answer["follow_up_job_id"],),
        ).fetchone()[0] == "applied"
        assert db.execute("SELECT COUNT(*) FROM cognition").fetchone()[0] == 1
        assert db.execute("SELECT revision FROM memory_state").fetchone()[0] == 1
    finally:
        db.close()


def test_follow_up_clarification_closes_old_and_opens_new_chain(
    tmp_path: Path,
) -> None:
    path = tmp_path / "clarification-chain.sqlite3"
    clock = MutableClock()
    first_id, _ = _seed_open_clarification(path, clock)
    clock.advance()
    answer = _service(path, clock).answer(
        clarification_id=first_id,
        result_session_id="result-session",
        answer="The Alex from work.",
    )
    clock.advance()
    store = WorldJobStore(path, clock=clock)
    claim = store.claim_one("follow-up-worker")
    assert claim is not None and claim.job_id == answer["follow_up_job_id"]
    assert store.settle(
        claim,
        WorldJobResult.clarification_required(
            "follow_up_still_ambiguous", display="Which workplace do you mean?"
        ),
    )

    records = _service(path, clock).list_clarifications(
        result_session_id="result-session"
    )
    assert len(records) == 2
    old = next(record for record in records if record["clarification_id"] == first_id)
    new = next(record for record in records if record["clarification_id"] != first_id)
    assert old["state"] == "resolved"
    assert new["state"] == "open"
    assert new["source_job_id"] == answer["follow_up_job_id"]
    assert new["question"] == "Which workplace do you mean?"
    assert derive_clarification_id(
        new["source_job_id"], new["source_outcome_id"]
    ) == new["clarification_id"]
