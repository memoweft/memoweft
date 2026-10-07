import sqlite3
from pathlib import Path

import pytest

from support.json_assertions import as_string

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.dsh_bridge.reprocess import reprocess_job
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from test_dsh_interactions import _boundary


def test_reprocess_reuses_evidence_and_preserves_old_terminal_and_history(tmp_path: Path) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize("s", dsh_home=str(tmp_path), auto_route=False, model_tier="local")
    first = runtime.ingest_durable_boundary(_boundary("s", "a", [
        {"role": "user", "content": "我朋友叫彦", "message_id": "person-intro"},
        {"role": "assistant", "content": "你好。", "message_id": "reply"},
    ]))
    path = runtime.db_path
    assert path is not None
    WorldJobWorker(path).run_until_quiescent()
    with sqlite3.connect(path) as db:
        old = db.execute("SELECT * FROM memory_world_job WHERE job_id=?", (first["job_id"],)).fetchone()
        evidence = db.execute("SELECT * FROM evidence").fetchall()
        interactions = db.execute("SELECT * FROM interaction_context").fetchall()
    args = dict(subject_id=as_string(runtime.subject_id), job_id=as_string(first["job_id"]), request_id="repair-quote-support")
    result = reprocess_job(path, **args)
    assert result["stored"] == 0 and result["skipped"] == 1
    assert result["job_id"] != first["job_id"]
    assert reprocess_job(path, **args) == result
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM memory_world_job WHERE job_id=?", (first["job_id"],)).fetchone() == old
        assert db.execute("SELECT * FROM evidence").fetchall() == evidence
        assert db.execute("SELECT * FROM interaction_context").fetchall() == interactions
        assert db.execute("SELECT COUNT(*) FROM memory_world_job").fetchone()[0] == 2
        db.execute("UPDATE evidence SET allow_inference=0")
    with pytest.raises(ValueError, match="source_evidence_not_eligible"):
        reprocess_job(path, **{**args, "request_id": "denied"})
    with pytest.raises(ValueError, match="source_job_not_reprocessable"):
        reprocess_job(path, **{**args, "subject_id": "other"})
    runtime.shutdown()
