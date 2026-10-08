from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from test_formation_accuracy import _item, _run
from test_hermes_batch_adapter_v5 import _batch, _model, _run as run_job, _set_evidence, _v8_item
from test_hermes_world_worker import MutableClock, _job


def seed(path: Path) -> str:
    raw = '我一直把导出宽度设为480像素。'
    _run(path, raw, [_model(_batch(_item(raw)))])
    with sqlite3.connect(path) as db:
        return str(db.execute('SELECT id FROM cognition').fetchone()[0])


def correction(path: Path, prior: str, extra: dict[str, Any] | None = None) -> dict[str, object]:
    raw = '更正，导出宽度以后用960像素。'
    item = _v8_item('preference', '用户更正，导出宽度以后用960像素', (0, len(raw)),
                    corrects_cognition_id=prior, evidence_id='evidence-2', extra=extra)
    run_job(path, MutableClock(), [_model(_batch(item)), _model(_batch(item))], ('evidence-2',),
            lambda p: _set_evidence(p, 'evidence-2', raw), job_id='job-2')
    return _job(path, 'job-2')


def test_form_with_current_target_becomes_correction_without_model_rewrite(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    prior = seed(path)
    row = correction(path, prior)
    assert row['state'] == 'applied'
    model = json.loads(str(row['model_result_json']))
    assert 'formation_rewrite' not in model
    assert json.loads(model['content'])['cognitions'][0]['action'] == 'form'
    outcome = json.loads(str(row['world_result_json']))
    assert outcome['normalizations'] == [{'item_index': 0, 'rule': 'form_with_current_correction_target'}]
    assert outcome['cognitions'][0]['action'] == 'correct'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT invalid_at FROM cognition WHERE id=?', (prior,)).fetchone()[0]
        assert db.execute('SELECT content FROM cognition WHERE invalid_at IS NULL').fetchone()[0].endswith('960像素')
        assert db.execute("SELECT raw_content FROM evidence WHERE id='evidence-2'").fetchone()[0] == '更正，导出宽度以后用960像素。'


@pytest.mark.parametrize('mutation', [
    "UPDATE cognition SET invalid_at='2026-10-01'",
    "UPDATE cognition SET archived_at='2026-10-01'",
    "UPDATE cognition SET muted_at='2026-10-01'",
    "UPDATE evidence SET allow_inference=0 WHERE id='evidence-1'",
    "UPDATE evidence SET allow_cloud_read=0 WHERE id='evidence-1'",
])
def test_ineligible_current_target_still_cannot_write(tmp_path: Path, mutation: str) -> None:
    path = tmp_path / 'world.sqlite3'
    prior = seed(path)
    with sqlite3.connect(path) as db:
        db.execute(mutation)
    assert correction(path, prior)['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 1


@pytest.mark.parametrize('extra', [
    {'statement_kind': 'attribute'},
    {'corrects_relationship_id': 'relationship-unknown'},
    {'supersedes_cognition_id': 'cognition-unknown'},
    {'contradicts_cognition_id': 'cognition-unknown'},
    {'retract': True},
    {'corrects_cognition_id': 'cognition-unknown'},
])
def test_conflicting_correction_fields_still_cannot_write(tmp_path: Path, extra: dict[str, Any]) -> None:
    path = tmp_path / 'world.sqlite3'
    prior = seed(path)
    assert correction(path, prior, extra)['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 1
        assert db.execute('SELECT invalid_at FROM cognition WHERE id=?', (prior,)).fetchone()[0] is None


@pytest.mark.parametrize('redundant', [
    {'canonical_name': '向遥', 'kind': 'person'},
    {'canonical_name': '向遥'},
])
def test_redundant_relationship_entity_is_removed_and_logged(tmp_path: Path, redundant: dict[str, str]) -> None:
    path = tmp_path / 'world.sqlite3'
    raw = '向遥是我的同事，他负责排班。'
    item = _item('ignored')
    item.update(statement_kind='relationship', relation_type='colleague',
                target_entity={'canonical_name': '向遥', 'kind': 'person'}, entity=redundant,
                supports=[{'evidence_id': 'evidence-1', 'sentence_id': 't0'}])
    row, calls = _run(path, raw, [_model(_batch(item))])
    assert row['state'] == 'applied' and len(calls) == 1
    outcome = json.loads(row['world_result_json'])
    assert outcome['normalizations'] == [{'item_index': 0, 'rule': 'redundant_relationship_entity'}]
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT content FROM relationship').fetchone()[0].rstrip('。').endswith('负责排班')
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 0


@pytest.mark.parametrize('redundant', [
    {'canonical_name': '另一人', 'kind': 'person'},
    {'canonical_name': '向遥', 'kind': 'organization'},
    {'canonical_name': '向遥', 'kind': 'person', 'other': 'value'},
    '向遥',
])
def test_inconsistent_relationship_entity_still_refuses_whole_batch(tmp_path: Path, redundant: object) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item.update(statement_kind='relationship', relation_type='colleague',
                target_entity={'canonical_name': '向遥', 'kind': 'person'}, entity=redundant)
    row, _ = _run(path, '向遥是我的同事。', [_model(_batch(item)), _model(_batch(item))])
    assert row['state'] == 'no_change'
    assert json.loads(row['model_result_json'])['formation_rewrite']['final_error'] == 'unexpected_entity'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM relationship').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM entity').fetchone()[0] == 0
