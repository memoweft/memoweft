"""Natural corrections retain their topic through existing Core transitions."""
from pathlib import Path
import sqlite3

import pytest

from memoweft.integrations.hermes.recall import _match_world_rows, recall_world_snapshot
from test_hermes_batch_adapter_v2 import _batch, _correct, _form, _model, _run, _set_evidence
from test_hermes_world_worker import MutableClock


def _corrected_world(path: Path) -> str:
    clock = MutableClock()
    old = "我最近只能周三晚上锻炼，安排运动时帮我记着。"
    _run(path, clock, [_model(_batch(_form("用户" + old[1:-1], (0, len(old)))))],
         ("evidence-1",), lambda p: _set_evidence(p, "evidence-1", old))
    with sqlite3.connect(path) as db:
        prior = str(db.execute("SELECT id FROM cognition").fetchone()[0])
    correction = "不对，是周五晚上。"
    _run(path, clock, [_model(_batch(_correct("用户不对，是周五晚上", prior,
         (0, len(correction)))))], ("evidence-2",),
         lambda p: _set_evidence(p, "evidence-2", correction), job_id="job-2")
    return prior


def test_short_correction_recalls_successor_by_old_topic_without_old_injection(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    prior = _corrected_world(path)
    with sqlite3.connect(path) as db:
        before = db.total_changes
        snapshot = recall_world_snapshot(db, "owner", "下周给我安排一次锻炼，放在哪天比较合适？")
        assert snapshot is not None
        assert "周五" in snapshot.rendered_recall
        assert "周三" not in snapshot.rendered_recall
        assert snapshot.count == 1
        assert snapshot.selected_item_ids[0][1] != prior
        assert db.total_changes == before
        assert db.execute("SELECT invalid_at FROM cognition WHERE id=?", (prior,)).fetchone()[0]
        assert db.execute("SELECT raw_content FROM evidence WHERE id='evidence-1'").fetchone()[0].startswith("我最近只能周三")
        assert recall_world_snapshot(db, "owner", "下周给我安排一次锻炼，放在哪天比较合适？") == snapshot


@pytest.mark.parametrize("change,tier", [
    ("UPDATE evidence SET allow_cloud_read=0 WHERE id='evidence-1'", "cloud"),
    ("UPDATE evidence SET allow_local_read=0 WHERE id='evidence-1'", "local"),
    ("UPDATE evidence SET deleted_at='2026-10-08' WHERE id='evidence-1'", "local"),
    ("UPDATE cognition SET muted_at='2026-10-08' WHERE invalid_at IS NOT NULL", "local"),
    ("UPDATE cognition SET archived_at='2026-10-08' WHERE invalid_at IS NOT NULL", "local"),
])
def test_predecessor_cues_obey_source_permissions_and_lifecycle(tmp_path: Path, change: str, tier: str) -> None:
    path = tmp_path / "world.sqlite3"
    _corrected_world(path)
    with sqlite3.connect(path) as db:
        db.execute(change)
        db.commit()
        snapshot = recall_world_snapshot(db, "owner", "锻炼", model_tier=tier)  # type: ignore[arg-type]
        assert snapshot is not None
        assert snapshot.count == 0
        direct = recall_world_snapshot(db, "owner", "周五", model_tier=tier)  # type: ignore[arg-type]
        assert direct is not None
        assert "周五" in direct.rendered_recall


def test_correction_chain_keeps_original_topic_and_only_final_value(tmp_path: Path) -> None:
    path = tmp_path / "world.sqlite3"
    _corrected_world(path)
    with sqlite3.connect(path) as db:
        prior = str(db.execute("SELECT id FROM cognition WHERE invalid_at IS NULL").fetchone()[0])
    raw = "再改一下，是周六晚上。"
    _run(path, MutableClock(), [_model(_batch(_correct("用户再改一下，是周六晚上", prior,
         (0, len(raw)), evidence_id="evidence-3")))], ("evidence-3",),
         lambda p: _set_evidence(p, "evidence-3", raw), job_id="job-3")
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, "owner", "锻炼")
        assert snapshot is not None and snapshot.count == 1
        assert "周六" in snapshot.rendered_recall
        assert "周三" not in snapshot.rendered_recall and "周五" not in snapshot.rendered_recall


def test_named_current_query_excludes_superseded_preference_but_history_remains() -> None:
    rows = [
        {"kind": "cognition", "id": "old", "content": "小王每天喝咖啡", "confidence": 600,
         "anchors": ("小王",), "is_superseded": True},
        {"kind": "cognition", "id": "new", "content": "小王现在只喝茶", "confidence": 600,
         "anchors": ("小王",), "is_superseded": False},
    ]
    assert [hit["id"] for hit in _match_world_rows("小王现在喝什么？", rows)] == ["new"]
    assert "old" in [hit["id"] for hit in _match_world_rows("小王以前喝咖啡吗？", rows)]
