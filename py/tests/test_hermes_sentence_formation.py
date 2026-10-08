from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from memoweft.integrations.hermes.batch_adapter import _evidence_sentences
from memoweft.integrations.hermes.recall import recall_world_snapshot
from test_formation_accuracy import _item, _run
from test_hermes_batch_adapter_v5 import _batch, _model


def test_selected_sentence_keeps_topic_and_constraint_for_recommendation(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    raw = '买外套的时候，别挑羊毛的，我一直只穿棉布的。我的密码另有保存。'
    item = _item('ignored')
    item['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
    row, calls = _run(path, raw, [_model(_batch(item))])
    assert row['state'] == 'applied'
    assert len(calls) == 1
    with sqlite3.connect(path) as db:
        content = db.execute('SELECT content FROM cognition').fetchone()[0]
        snapshot = recall_world_snapshot(db, 'owner', '帮我推荐一件适合秋天的外套。')
    assert '买外套的时候' in content and '只穿棉布' in content
    assert '密码' not in content
    assert snapshot is not None and snapshot.count == 1
    assert '棉布' in snapshot.rendered_recall


def test_clause_selector_still_excludes_independent_unselected_information(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item['supports'] = [{'evidence_id': 'evidence-1', 'segment_id': 's0'}]
    row, _ = _run(path, '我喜欢棉布，我的密码另有保存。', [_model(_batch(item))])
    assert row['state'] == 'applied'
    with sqlite3.connect(path) as db:
        assert '密码' not in db.execute('SELECT content FROM cognition').fetchone()[0]


@pytest.mark.parametrize('supports', [
    [{'evidence_id': 'evidence-1', 'sentence_id': 't99'}],
    [{'evidence_id': 'evidence-1', 'sentence_id': 't0', 'segment_id': 's0'}],
    [{'evidence_id': 'evidence-1', 'sentence_id': 't0'},
     {'evidence_id': 'evidence-1', 'segment_id': 's1'}],
    [{'evidence_id': 'unknown', 'sentence_id': 't0'}],
])
def test_invalid_or_overlapping_sentence_sources_cannot_write(tmp_path: Path, supports: list[dict[str, str]]) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item['supports'] = supports
    row, _ = _run(path, '挑衣服的时候，我只穿棉布。', [_model(_batch(item)), _model(_batch(item))])
    assert row['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 0


def test_sentence_source_handles_decimal_and_bilingual_punctuation() -> None:
    raw = 'I use 1.5 units, every time. 下一句，保留逗号。'
    sentences = _evidence_sentences(raw)
    assert [part['text'] for part in sentences] == ['I use 1.5 units, every time.', ' 下一句，保留逗号。']
    assert all(raw[part['start']:part['end']] == part['text'] for part in sentences)


def test_selected_complete_relationship_remains_a_formal_relationship(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item.update(statement_kind='relationship', relation_type='colleague',
                target_entity={'canonical_name': '向遥', 'kind': 'person'},
                supports=[{'evidence_id': 'evidence-1', 'sentence_id': 't0'}])
    row, _ = _run(path, '向遥是我的同事，他负责排班。', [_model(_batch(item))])
    assert row['state'] == 'applied'
    with sqlite3.connect(path) as db:
        content = db.execute('SELECT content FROM relationship').fetchone()[0]
        snapshot = recall_world_snapshot(db, 'owner', '帮我给向遥写一句问候。')
    assert '负责排班' in content
    assert snapshot is not None and any(kind == 'relationship' for kind, _ in snapshot.selected_item_ids)


def test_recommendation_fallback_does_not_invent_unmentioned_topic(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
    _run(path, '挑外套的时候，我只穿棉布。', [_model(_batch(item))])
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', '帮我推荐一个适合旅行的箱子。')
    assert snapshot is not None and snapshot.count == 0


def test_topicless_correction_can_recall_explicit_rejected_value(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
    _run(path, '我保存扫描稿固定用450像素，这是我一直使用的输出宽度。', [_model(_batch(item))])
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', '前面说错了，应当是900像素，450像素作废，后续照新的来。')
    assert snapshot is not None and snapshot.count == 1
    assert '450像素' in snapshot.rendered_recall
