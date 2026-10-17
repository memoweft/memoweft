"""G9 narrow labels: frozen English failures, newly written Chinese equivalents.

English dialogue and timestamps are copied verbatim from the stopped HaluMem
artifacts 004/008/010 (session numbers in the audit are one-based). Chinese
fixtures are NEW regression fixtures, not original Chinese exam failures.
No answer model or evaluator is run; all formation responses are fixed JSON.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from memoweft.integrations.hermes.batch_adapter import _stated_normalize
from memoweft.integrations.hermes.recall import recall_world_snapshot
from test_hermes_batch_adapter_v2 import _batch, _form, _model, _run, _set_evidence
from test_hermes_world_worker import MutableClock, _insert_job, _job

ENGLISH: list[dict[str, Any]] = json.loads(
    Path(__file__).with_name('grounded_label_fixtures.json').read_text(encoding='utf-8')
)
# Newly authored equivalents. They are not translations supplied by the dataset.
CHINESE = [
    ('session5/q2', '金毛寻回犬', '我最近在一个朋友家接触了金毛寻回犬。', '它们提供的情感支持和陪伴很宝贵。'),
    ('session9/q1', '模拟跳伞', '我最近参加团建时体验了模拟跳伞。', '在这些活动中挑战极限让我更有创造力。'),
    ('session11/q0', '健康度假', '我最近一直在重新考虑健康度假。', '我正在考虑自然徒步和文化游这样的替代选择。'),
    ('session5/q1', '', '', '我最近在一个朋友家接触了金毛寻回犬。'),
]
CASES = [(case['case'] + '-en', case['scope'], case['turns'], case['quote']) for case in ENGLISH]
CASES += [(name + '-zh-new', label,
           ([{'role': 'user', 'content': prior, 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0}]
            if prior else []) +
           [{'role': 'user', 'content': quote, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1}], quote)
          for name, label, prior, quote in CHINESE]


def _iso(value: str) -> str:
    return datetime.strptime(value, '%b %d, %Y, %H:%M:%S').replace(tzinfo=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _world(path: Path, turns: list[dict[str, Any]], quote: str, scope: object,
           *, change: str = '') -> tuple[str, str]:
    users = [turn for turn in turns if turn['role'] == 'user']
    current = users[-1]
    clock = MutableClock()
    clock.value = datetime.strptime(current['timestamp'], '%b %d, %Y, %H:%M:%S').replace(tzinfo=timezone.utc)
    raw = str(current['content'])
    proposition = _stated_normalize(quote)
    item = _form(proposition, (raw.index(quote), raw.index(quote) + len(quote)))
    if scope is not None:
        item['scope'] = scope

    def setup(path: Path) -> None:
        _set_evidence(path, 'evidence-1', raw)
        for index, user in enumerate(users[:-1]):
            prior_clock = MutableClock()
            prior_clock.value = datetime.strptime(user['timestamp'], '%b %d, %Y, %H:%M:%S').replace(tzinfo=timezone.utc)
            eid, jid = f'prior-evidence-{index}', f'prior-{index}'
            _insert_job(path, prior_clock, job_id=jid, evidence_ids=(eid,))
            _set_evidence(path, eid, str(user['content']))
            context = [{**turn, 'message_id': f'message-{index}-{turn["role"]}'} for turn in turns
                       if turn['dialogue_turn'] == user['dialogue_turn']]
            with sqlite3.connect(path) as db:
                db.execute("UPDATE memory_world_job SET state='no_change', completed_at=created_at WHERE job_id=?", (jid,))
                db.execute("INSERT INTO interaction_context (id,subject_id,conversation_id,episode_id,context_json,context_hash,created_at) VALUES (?,'owner','session-parent',?,?,'hash',?)",
                           (f'context-{index}', f'boundary-{jid}', json.dumps(context), _iso(user['timestamp'])))
        with sqlite3.connect(path) as db:
            if change:
                db.execute(change)
    _run(path, clock, [_model(_batch(item))], ('evidence-1',), setup)
    assert _job(path)['state'] == 'applied', _job(path)
    with sqlite3.connect(path) as db:
        cid, actual = db.execute('SELECT id,content FROM cognition').fetchone()
        assert actual == proposition
        assert db.execute("SELECT raw_content FROM evidence WHERE id='evidence-1'").fetchone()[0] == raw
        assert db.execute('SELECT evidence_id FROM cognition_evidence WHERE cognition_id=?', (cid,)).fetchall() == [('evidence-1',)]
    return str(cid), proposition


@pytest.mark.parametrize('name,label,turns,quote', CASES, ids=[case[0] for case in CASES])
def test_frozen_failures_keep_body_and_ground_labels(tmp_path: Path, name: str, label: str,
                                                    turns: list[dict[str, Any]], quote: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    cid, body = _world(path, turns, quote, label or 'ThomasSusan')
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', label or body)
        assert snapshot is not None
        assert ('cognition', cid) in snapshot.selected_item_ids
        expected = f'记忆：［标签：{label}］{body}' if label else f'记忆：{body}'
        assert snapshot.rendered_recall == expected
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (cid,)).fetchone()[0] is None
        assert recall_world_snapshot(db, 'owner', label or body) == snapshot
        assert list(db.iterdump()) == before
