"""A confirmation in a new durable turn must retain the earlier proposal."""
import json
import sqlite3
from pathlib import Path
from typing import Any, cast

import pytest

from test_hermes_batch_adapter_v2 import _run, _set_evidence, _model, _batch, _form
from test_hermes_world_worker import MutableClock, _insert_job, _job
from memoweft.integrations.trust.query_service import QueryService

CLAIM = '以后用户想组队时，我提醒用户找王小明。'


@pytest.mark.parametrize('confirmation', ['好', '行，就这么办', '好，以后我想组队时，就提醒我找王小明。'])
def test_prior_turn_proposal_forms_with_both_sources(tmp_path: Path, confirmation: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    clock = MutableClock()

    def setup(path: Path) -> None:
        _set_evidence(path, 'evidence-1', confirmation)
        _insert_job(path, clock, job_id='prior', evidence_ids=('proposal-evidence',))
        with sqlite3.connect(path) as db:
            db.execute("UPDATE memory_world_job SET state='no_change', completed_at=created_at WHERE job_id='prior'")
            db.execute("INSERT INTO interaction_context (id, subject_id, conversation_id, episode_id, context_json, context_hash, created_at) VALUES ('proposal-context','owner','session-parent','boundary-prior',?,'hash','2000-01-01T00:00:00.000Z')",
                       (json.dumps([{'role': 'assistant', 'content': CLAIM, 'message_id': 'assistant-proposal'}]),))
    stated = confirmation.startswith('好，以后')
    proposition = '用户' + confirmation if stated else CLAIM
    _run(path, clock, [_model(_batch(_form(proposition, (0, len(confirmation)),
          formed_by='stated' if stated else 'confirmed', assistant_claim=CLAIM)))], ('evidence-1',), setup)
    assert _job(path)['state'] == 'applied', _job(path)['world_result_json']
    with sqlite3.connect(path) as db:
        assert {r[0] for r in db.execute('SELECT evidence_id FROM cognition_evidence')} == {'evidence-1', 'proposal-evidence'}
        assert db.execute("SELECT count(*) FROM evidence WHERE raw_content=?", (CLAIM,)).fetchone()[0] == 0
        cognition_id = db.execute('SELECT id FROM cognition').fetchone()[0]
    query = QueryService(path, subject_id='owner')
    sources = cast(list[dict[str, Any]], query.get_world_item_provenance('cognition', cognition_id)['provenance'])
    proposals = [proposal for source in sources for proposal in source.get('assistant_sources', [])]
    assert proposals == [{'message_id': 'assistant-proposal', 'content': CLAIM,
                          'conversation_id': 'session-parent', 'recorded_at': '2000-01-01T00:00:00.000Z'}]
    recalled = cast(dict[str, Any], query.preview_recall('我们之前说组队可以找谁？')['preview'])
    assert cognition_id in str(recalled['selected_item_ids'])
    assert CLAIM in recalled['rendered_recall']
    with sqlite3.connect(path) as db:
        db.execute("UPDATE evidence SET allow_local_read=0 WHERE id='proposal-evidence'")
    assert cast(dict[str, Any], query.preview_recall('我们之前说组队可以找谁？')['preview'])['count'] == 0
    sources = cast(list[dict[str, Any]], query.get_world_item_provenance('cognition', cognition_id)['provenance'])
    assert not [proposal for source in sources for proposal in source.get('assistant_sources', [])]


@pytest.mark.parametrize('changed', ['other_subject', 'other_conversation', 'future', 'denied', 'deleted', 'refusal', 'invented'])
def test_unavailable_or_unconfirmed_proposal_cannot_form(tmp_path: Path, changed: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    clock = MutableClock()
    confirmation = '不行，就别这么办' if changed == 'refusal' else '行，就这么办'

    def setup(path: Path) -> None:
        _set_evidence(path, 'evidence-1', confirmation)
        _insert_job(path, clock, job_id='prior', evidence_ids=('proposal-evidence',))
        with sqlite3.connect(path) as db:
            db.execute("UPDATE memory_world_job SET state='no_change', completed_at=created_at WHERE job_id='prior'")
            db.execute("INSERT INTO interaction_context (id, subject_id, conversation_id, episode_id, context_json, context_hash, created_at) VALUES ('proposal-context',?,?,'boundary-prior',?,'hash',?)",
                       ('other' if changed == 'other_subject' else 'owner',
                        'other' if changed == 'other_conversation' else 'session-parent',
                        json.dumps([{'role': 'assistant', 'content': '别的提议' if changed == 'invented' else CLAIM, 'message_id': 'assistant-proposal'}]),
                        '2099-01-01T00:00:00.000Z' if changed == 'future' else '2000-01-01T00:00:00.000Z'))
            if changed == 'denied': db.execute("UPDATE evidence SET allow_inference=0 WHERE id='proposal-evidence'")
            if changed == 'deleted': db.execute("UPDATE evidence SET deleted_at=recorded_at WHERE id='proposal-evidence'")
    _run(path, clock, [_model(_batch(_form(CLAIM, (0, len(confirmation)),
          formed_by='confirmed', assistant_claim=CLAIM)))], ('evidence-1',), setup)
    assert _job(path)['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 0
