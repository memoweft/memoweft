from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from test_formation_accuracy import _item, _run
from test_hermes_batch_adapter_v5 import _batch, _model, _run as run_job, _set_evidence, _v8_item
from test_hermes_world_worker import MutableClock, _job


def form(path: Path, raw: str = '乘船时我一直选靠窗的座位。', topic: str = '乘船', aliases: Any = None) -> str:
    item = _item('ignored')
    item.update(entity={'canonical_name': topic, 'kind': 'topic', 'aliases': ['坐船'] if aliases is None else aliases},
                supports=[{'evidence_id': 'evidence-1', 'sentence_id': 't0'}])
    row, _ = _run(path, raw, [_model(_batch(item)), _model(_batch(item))])
    assert row['state'] == 'applied'
    with sqlite3.connect(path) as db:
        return str(db.execute('SELECT id FROM cognition').fetchone()[0])


def test_topic_synonym_uses_existing_aliases_without_rewriting_the_fact(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    cid = form(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition_target').fetchone()[0] == 0
        content = db.execute('SELECT content FROM cognition').fetchone()[0]
        assert '靠窗' in content and '坐船' not in content
        assert json.loads(db.execute("SELECT aliases_json FROM entity WHERE kind='topic'").fetchone()[0]) == ['坐船']
        before = db.total_changes
        first = recall_world_snapshot(db, 'owner', '坐船时，选什么座位？')
        assert first is not None and first.selected_item_ids == (('cognition', cid),)
        assert '靠窗' in first.rendered_recall
        assert '坐船' not in first.rendered_recall
        assert first == recall_world_snapshot(db, 'owner', '坐船时，选什么座位？')
        assert db.total_changes == before
        unrelated = recall_world_snapshot(db, 'owner', '买桌子时该选什么颜色？')
        assert unrelated is not None and unrelated.count == 0


def test_optional_topic_labels_do_not_change_owner_claim_identity(tmp_path: Path) -> None:
    raw = '乘船时我一直选靠窗的座位。'
    topic_path, plain_path = tmp_path / 'topic.sqlite3', tmp_path / 'plain.sqlite3'
    labeled_id = form(topic_path, raw)
    item = _item('ignored')
    item['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
    row, _ = _run(plain_path, raw, [_model(_batch(item))])
    assert row['state'] == 'applied'
    with sqlite3.connect(topic_path) as labeled, sqlite3.connect(plain_path) as plain:
        original = plain.execute('SELECT id, content FROM cognition').fetchone()
        assert original[0] == labeled_id
        assert labeled.execute('SELECT content FROM cognition').fetchone()[0] == original[1]


@pytest.mark.parametrize('mutation,tier', [
    ("UPDATE evidence SET allow_local_read=0", 'local'),
    ("UPDATE evidence SET allow_cloud_read=0", 'cloud'),
    ("UPDATE evidence SET deleted_at='2026-10-01'", 'local'),
    ("UPDATE cognition SET muted_at='2026-10-01'", 'local'),
    ("UPDATE cognition SET archived_at='2026-10-01'", 'local'),
    ("UPDATE entity SET invalid_at='2026-10-01' WHERE kind='topic'", 'local'),
])
def test_synonyms_obey_claim_and_source_lifecycle(tmp_path: Path, mutation: str, tier: Any) -> None:
    path = tmp_path / 'world.sqlite3'
    form(path)
    with sqlite3.connect(path) as db:
        db.execute(mutation)
        snapshot = recall_world_snapshot(db, 'owner', '坐船', model_tier=tier)
        assert snapshot is not None and snapshot.count == 0


@pytest.mark.parametrize('topic,aliases', [
    ('登机', ['坐飞机']), ('乘船', '坐船'), ('乘船', [None]), ('乘船', ['']),
])
def test_unanchored_topic_or_invalid_alias_shape_still_refuses(tmp_path: Path, topic: str, aliases: Any) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item.update(entity={'canonical_name': topic, 'kind': 'topic', 'aliases': aliases},
                supports=[{'evidence_id': 'evidence-1', 'sentence_id': 't0'}])
    row, _ = _run(path, '乘船时我一直选靠窗的座位。', [_model(_batch(item)), _model(_batch(item))])
    assert row['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM entity').fetchone()[0] == 0


def test_topic_alias_lineage_retrieves_only_current_topicless_correction(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    prior = form(path, '我导出图片一直用480像素。', '导出', ['输出'])
    raw = '更正，现在用720像素。'
    item = _v8_item('preference', '用户更正，现在用720像素', (0, len(raw)), action='correct',
                    corrects_cognition_id=prior, evidence_id='evidence-2')
    run_job(path, MutableClock(), [_model(_batch(item))], ('evidence-2',),
            lambda p: _set_evidence(p, 'evidence-2', raw), job_id='job-2')
    assert _job(path, 'job-2')['state'] == 'applied'
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', '输出时该用什么宽度？')
        assert snapshot is not None and snapshot.count == 1
        assert '720' in snapshot.rendered_recall and '480' not in snapshot.rendered_recall
        assert snapshot.selected_item_ids[0][1] != prior
        db.execute("UPDATE evidence SET allow_local_read=0 WHERE id='evidence-1'")
        hidden = recall_world_snapshot(db, 'owner', '输出时该用什么宽度？')
        assert hidden is not None and hidden.count == 0
        direct = recall_world_snapshot(db, 'owner', '720像素')
        assert direct is not None and direct.count == 1
