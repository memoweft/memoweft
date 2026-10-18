"""Issue #128: frozen first interpretation, existing single compiler rewrite."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from test_hermes_batch_adapter_v5 import _model, _set_evidence
from test_hermes_world_worker import MutableClock, _initialize_database, _insert_job, _job, _policy
from test_state_goals import form


FROZEN: dict[str, Any] = {"schema_version": 8, "result": "cognitions", "cognitions": [{
    "action": "form", "statement_kind": "preference", "formed_by": "stated",
    "proposition": "我其实更喜欢绿茶。",
    "supports": [{"evidence_id": "8f4a5730-332b-4c8f-a7bf-d12d4aec20b2", "segment_id": "s0"}],
    "supersedes_cognition_id": "cognition-c025387745f414a78035aab2d453c5546b91a4e6d81e6cae67b2900118201818",
    "entity": {"canonical_name": "绿茶", "kind": "topic", "aliases": []},
}]}


@pytest.mark.parametrize('rewrite', ['subject', 'repeat-value', 'unsupported', 'other-person', 'other-account'])
def test_frozen_value_topic_gets_one_grounded_subject_rewrite(tmp_path: Path, rewrite: str) -> None:
    path = tmp_path / 'world.sqlite'
    _initialize_database(path)
    prior = form(path, '我喜欢喝红茶。', 1)
    clock = MutableClock()
    eid = str(FROZEN['cognitions'][0]['supports'][0]['evidence_id'])
    _insert_job(path, clock, job_id='correction', evidence_ids=(eid,))
    _set_evidence(path, eid, '我其实更喜欢绿茶。')
    first = copy.deepcopy(FROZEN)
    # Only the predecessor ID is rebound to this independently built World.
    first['cognitions'][0]['supersedes_cognition_id'] = prior
    second = copy.deepcopy(first)
    if rewrite != 'repeat-value':
        del second['cognitions'][0]['entity']
    if rewrite == 'unsupported':
        second['cognitions'][0]['supports'] = [{'evidence_id': eid, 'quote': '我喜欢咖啡。'}]
    if rewrite == 'other-person':
        second['cognitions'][0]['entity'] = {'canonical_name': '小王', 'kind': 'person'}
    if rewrite == 'other-account':
        with sqlite3.connect(path) as db:
            db.execute("UPDATE cognition SET subject_id='another-owner' WHERE id=?", (prior,))
    calls: list[Any] = []

    def route(messages: list[dict[str, str]], *args: Any, **kwargs: Any) -> dict[str, object]:
        calls.append(messages)
        assert len(calls) <= 2
        return _model(first if len(calls) == 1 else second)

    worker = WorldJobWorker(path, processor=HermesBatchAdapterProcessor(str(path), route, clock=clock),
                            policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    result = _job(path, 'correction')
    if rewrite == 'subject':
        assert result['state'] == 'applied', result['world_result_json']
        assert len(calls) == 2
        assert 'same preference/state slot' in json.dumps(calls[1])
        with sqlite3.connect(path) as db:
            snapshot = recall_world_snapshot(db, 'owner', '喜欢喝什么茶', lang='zh')
            assert snapshot is not None
            assert '绿茶' in snapshot.rendered_recall and '红茶' not in snapshot.rendered_recall
            assert db.execute('SELECT count(*) FROM cognition_transitions').fetchone() == (1,)
    else:
        assert result['state'] == 'no_change', result['world_result_json']
        with sqlite3.connect(path) as db:
            assert db.execute('SELECT count(*) FROM cognition_transitions').fetchone() == (0,)
            assert db.execute('SELECT count(*) FROM cognition').fetchone() == (1,)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT raw_content FROM evidence WHERE id=?', (eid,)).fetchone() == ('我其实更喜欢绿茶。',)
