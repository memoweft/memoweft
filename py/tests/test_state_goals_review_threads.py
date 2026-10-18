"""PR #125 review findings: real worker, SQLite mutation and recall evidence.

Only interpretation responses are scripted. Legacy topic-target sidecars are
explicitly seeded after real formation; current topic cues use the normal path.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Literal

import pytest

from memoweft.integrations.hermes.batch_adapter import entity_id_for
from memoweft.integrations.hermes.recall import recall_world_snapshot
from test_hermes_world_worker import _initialize_database
from test_state_goals import form
from test_state_goals_revision import attempt


def _state(db: sqlite3.Connection) -> tuple[Any, ...]:
    """Exclude the new input/job, which exists even when World Apply rejects."""
    return (
        db.execute('SELECT id,content,content_type,confidence,invalid_at FROM cognition ORDER BY id').fetchall(),
        db.execute('SELECT * FROM cognition_transitions ORDER BY id').fetchall(),
        db.execute('SELECT * FROM cognition_target ORDER BY cognition_id').fetchall(),
        db.execute('SELECT * FROM cognition_evidence ORDER BY cognition_id,evidence_id,relation').fetchall(),
        db.execute('SELECT id,kind,canonical_name,aliases_json,invalid_at FROM entity ORDER BY id').fetchall(),
        db.execute('SELECT * FROM evidence_ledger ORDER BY id').fetchall(),
        db.execute('SELECT revision FROM memory_state').fetchone(),
    )


@pytest.mark.parametrize('lang', ['en', 'zh'])
@pytest.mark.parametrize('storage', ['current-cue', 'legacy-target'])
@pytest.mark.parametrize('successor_topic', ['different', 'same', 'omitted'])
def test_review_topic_supersession_checks_parsed_topic_and_recall(
    tmp_path: Path, lang: Literal['en', 'zh'], storage: str, successor_topic: str,
) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    old_raw = 'I prefer paper notebooks.' if lang == 'en' else '我偏好纸质笔记本。'
    topic = 'notebooks' if lang == 'en' else '笔记本'
    new_raw = {
        'en': {'different': 'I now prefer a light breakfast.', 'same': 'I now prefer digital notebooks.',
               'omitted': 'I now prefer digital ones.'},
        'zh': {'different': '我现在偏好清淡早餐。', 'same': '我现在偏好电子笔记本。',
               'omitted': '我现在偏好电子版。'},
    }[lang][successor_topic]
    old = form(path, old_raw, 1, fields={'entity': {'canonical_name': topic, 'kind': 'topic'}})
    with sqlite3.connect(path) as db:
        if storage == 'legacy-target':
            # Older persisted Worlds can have a topic cognition_target. Keep
            # original Evidence and the worker-created topic provenance intact.
            db.execute('INSERT INTO cognition_target(cognition_id,target_entity_id) VALUES(?,?)',
                       (old, entity_id_for('owner', topic)))
        db.commit()
        before = _state(db)
        recalled_before = recall_world_snapshot(db, 'owner', topic, lang=lang)
        assert recalled_before is not None and recalled_before.selected_item_ids == (('cognition', old),)

    fields: dict[str, Any] = {'supersedes_cognition_id': old}
    if successor_topic != 'omitted':
        fields['entity'] = {'canonical_name': ('breakfast' if lang == 'en' else '早餐')
                            if successor_topic == 'different' else topic, 'kind': 'topic'}
    result = attempt(path, new_raw, n=2, fields=fields)
    with sqlite3.connect(path) as db:
        current = recall_world_snapshot(db, 'owner', topic, lang=lang)
        assert current is not None
        if successor_topic == 'different':
            assert result['state'] == 'no_change', result['world_result_json']
            assert json.loads(str(result['world_result_json']))['reason'] == 'supersession_target_entity_mismatch'
            assert _state(db) == before
            assert current.selected_item_ids == (('cognition', old),)
            assert current.rendered_recall == recalled_before.rendered_recall
            assert db.execute('SELECT count(*) FROM cognition WHERE content=?', (new_raw.replace('我', '用户', 1),)).fetchone() == (0,)
        else:
            assert result['state'] == 'applied', result['world_result_json']
            outcome = json.loads(str(result['world_result_json']))['cognitions'][0]
            new = str(outcome['cognition_id'])
            assert current.selected_item_ids == (('cognition', new),)
            assert new_raw.replace('我', '用户', 1) in current.rendered_recall
            assert db.execute('SELECT prior_cognition_id,replacement_cognition_id,reason FROM cognition_transitions').fetchall() == [(old, new, 'supersedes')]
        assert db.execute('SELECT raw_content FROM evidence ORDER BY id').fetchall() == [(old_raw,), (new_raw,)]


@pytest.mark.parametrize('lang', ['en', 'zh'])
@pytest.mark.parametrize('old_kind', ['attribute', 'preference'])
@pytest.mark.parametrize('same_kind', [False, True])
def test_review_predecessor_kind_is_checked_in_both_directions(
    tmp_path: Path, lang: Literal['en', 'zh'], old_kind: str, same_kind: bool,
) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    texts = {
        'en': {'attribute': ('I live in Northport.', 'I now live in Southport.', 'live'),
               'preference': ('I prefer paper notebooks.', 'I now prefer digital notebooks.', 'notebooks')},
        'zh': {'attribute': ('我住在北港。', '我现在住在南港。', '住在'),
               'preference': ('我偏好纸质笔记本。', '我现在偏好电子笔记本。', '笔记本')},
    }[lang]
    new_kind = old_kind if same_kind else 'attribute' if old_kind == 'preference' else 'preference'
    old_raw, _, query = texts[old_kind]
    _, new_raw, _ = texts[new_kind]
    old = form(path, old_raw, 1, kind=old_kind)
    with sqlite3.connect(path) as db:
        before = _state(db)
        recalled_before = recall_world_snapshot(db, 'owner', query, lang=lang)
        assert recalled_before is not None and recalled_before.selected_item_ids == (('cognition', old),)
    result = attempt(path, new_raw, n=2, fields={'statement_kind': new_kind, 'supersedes_cognition_id': old})
    with sqlite3.connect(path) as db:
        current = recall_world_snapshot(db, 'owner', query, lang=lang)
        assert current is not None
        if not same_kind:
            assert result['state'] == 'no_change', result['world_result_json']
            assert json.loads(str(result['world_result_json']))['reason'] == 'supersession_target_kind_mismatch'
            assert _state(db) == before
            assert current.selected_item_ids == (('cognition', old),)
            assert current.rendered_recall == recalled_before.rendered_recall
        else:
            assert result['state'] == 'applied', result['world_result_json']
            new = str(json.loads(str(result['world_result_json']))['cognitions'][0]['cognition_id'])
            assert current.selected_item_ids == (('cognition', new),)
            assert new_raw.replace('我', '用户', 1) in current.rendered_recall
            assert db.execute('SELECT prior_cognition_id,replacement_cognition_id FROM cognition_transitions').fetchall() == [(old, new)]
        assert db.execute('SELECT raw_content FROM evidence ORDER BY id').fetchall() == [(old_raw,), (new_raw,)]
