from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from test_formation_accuracy import _item, _run
from test_hermes_batch_adapter_v5 import _batch, _model


@pytest.mark.parametrize('raw', [
    '这次先列一个提纲。',
    '只回复已完成，不调用工具。',
    '替我写一句产品说明。',
    'This time reply with one sentence.',
    'Please summarize this file.',
    'Only reply OK.',
])
def test_task_directives_do_not_become_preferences(tmp_path: Path, raw: str) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
    row, calls = _run(path, raw, [_model(_batch(item))])
    assert row['state'] == 'no_change' and len(calls) == 1
    outcome = json.loads(row['world_result_json'])
    assert outcome['normalizations'] == [{'item_index': 0, 'rule': 'excluded_task_scoped_instruction'}]
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 0
        assert db.execute('SELECT raw_content FROM evidence').fetchone()[0] == raw


@pytest.mark.parametrize('raw', [
    '以后先给简短定义。',
    '我最近固定把打印质量设为高。',
    '我通常使用竖版页面。',
    '以后处理这个文件都保留页码。',
    'From now on, reply in short paragraphs.',
    'Always summarize before giving detail.',
    'I prefer serif fonts.',
])
def test_ongoing_preferences_and_arrangements_still_form(tmp_path: Path, raw: str) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _item('ignored')
    item['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
    row, _ = _run(path, raw, [_model(_batch(item))])
    assert row['state'] == 'applied'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 1


def test_independent_ongoing_claim_survives_excluded_task_item(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    raw = '我以后都用竖版页面。只回复完成，不调用工具。'
    stable, transient = _item('ignored'), _item('ignored')
    stable['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
    transient['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't1'}]
    row, calls = _run(path, raw, [_model(_batch(stable, transient))])
    assert row['state'] == 'applied' and len(calls) == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 1
        content = db.execute('SELECT content FROM cognition').fetchone()[0]
        assert '竖版' in content and '回复' not in content and '工具' not in content
        assert db.execute('SELECT raw_content FROM evidence').fetchone()[0] == raw


def test_current_file_operation_is_already_excluded_before_model_dispatch(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    raw = '帮我把这个文件改成两列。'
    row, calls = _run(path, raw, [_model(_batch(_item('ignored')))])
    assert row['state'] == 'no_change' and calls == []
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 0
        assert db.execute('SELECT raw_content FROM evidence').fetchone()[0] == raw


def test_mixed_selected_sentences_are_rewritten_without_silent_source_trimming(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    raw = '以后都用竖版页面。只回复完成。'
    mixed, stable = _item('ignored'), _item('ignored')
    mixed['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'},
                         {'evidence_id': 'evidence-1', 'sentence_id': 't1'}]
    stable['supports'] = [{'evidence_id': 'evidence-1', 'sentence_id': 't0'}]
    row, calls = _run(path, raw, [_model(_batch(mixed)), _model(_batch(stable))])
    assert row['state'] == 'applied' and len(calls) == 2
    assert json.loads(row['model_result_json'])['formation_rewrite']['error']['code'] == 'mixed_task_and_ongoing_instruction'
    with sqlite3.connect(path) as db:
        assert '回复' not in db.execute('SELECT content FROM cognition').fetchone()[0]
