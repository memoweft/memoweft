"""Local model envelopes keep exact source grounding and correction topics."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from memoweft.integrations.hermes.batch_adapter import (
    HermesBatchAdapterProcessor, _SYSTEM_PROMPT, _SYSTEM_PROMPT_EN,
)
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from test_hermes_batch_adapter_v2 import _run, _set_evidence, _batch, _form, _model
from test_hermes_world_worker import MutableClock, _job, _insert_job, _policy


def test_explicit_ongoing_preferences_are_distinct_from_wishes_and_moods() -> None:
    assert '两者都应形成，不应 no_change' in _SYSTEM_PROMPT
    assert '希望你以后用生活例子' in _SYSTEM_PROMPT
    assert '我最近只能周三晚上锻炼' in _SYSTEM_PROMPT
    assert 'Form these, rather than no_change' in _SYSTEM_PROMPT_EN
    assert '带明确短期范围的临时状态' in _SYSTEM_PROMPT
    assert '只有无可形成内容才用 no_change' in _SYSTEM_PROMPT


@pytest.mark.parametrize('raw', [
    '我希望你以后讲技术问题时多用生活例子，少用术语。',
    '我最近只能周三晚上锻炼，安排运动时帮我记着。',
])
def test_fenced_json_forms_only_verbatim_supported_preference(tmp_path: Path, raw: str) -> None:
    path = tmp_path / 'world.sqlite3'
    item = _form('invented unsupported facts', (0, len(raw)))
    item['supports'] = [{'evidence_id': 'evidence-1', 'quote': raw}]
    output = _model(_batch(item))
    output['content'] = '\n```json\n' + str(output['content']) + '\n```\n'
    _run(path, MutableClock(), [output], ('evidence-1',),
         lambda db_path: _set_evidence(db_path, 'evidence-1', raw))
    assert _job(path)['state'] == 'applied'
    with sqlite3.connect(path) as db:
        content = db.execute('SELECT content FROM cognition').fetchone()[0]
        assert content == '用户' + raw[1:]
        assert 'invented' not in content


@pytest.mark.parametrize('content', [
    '```json\n{"schema_version":8,"result":"no_change"}\n```',
    '{"schema_version":8,"result":"no_change"}',
    'Some explanation\n{"schema_version":8,"result":"no_change"}',
    '```json\n{"schema_version":8,"result":"no_change"}\n```\n{}',
])
def test_no_change_or_ambiguous_json_never_invents_world_facts(tmp_path: Path, content: str) -> None:
    path = tmp_path / 'world.sqlite3'
    _run(path, MutableClock(), [{'content': content}, {'content': content}], ('evidence-1',),
         lambda db_path: _set_evidence(db_path, 'evidence-1', '谢谢，今天心情不错。'))
    assert _job(path)['state'] == 'no_change'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM cognition').fetchone()[0] == 0


@pytest.mark.parametrize('valid_direction', [True, False])
def test_user_friend_relationship_preserves_project_context_without_guessing_fields(
    tmp_path: Path, valid_direction: bool,
) -> None:
    path = tmp_path / 'world.sqlite3'
    raw = '小林是跟我一起做项目的朋友，她负责设计。'
    item = _form('ignored', (0, len(raw)), kind='relationship')
    item['supports'] = [{'evidence_id': 'evidence-1', 'quote': raw}]
    item['relation_type'] = 'friend'
    item['target_entity' if valid_direction else 'source_entity'] = {
        'canonical_name': '小林', 'kind': 'person',
    }
    envelope = _batch(item)
    envelope['schema_version'] = 8
    _run(path, MutableClock(), [_model(envelope), _model(envelope)], ('evidence-1',),
         lambda db_path: _set_evidence(db_path, 'evidence-1', raw))
    assert _job(path)['state'] == ('applied' if valid_direction else 'no_change')
    with sqlite3.connect(path) as db:
        relationships = db.execute('SELECT content FROM relationship').fetchall()
        assert len(relationships) == (1 if valid_direction else 0)
        if valid_direction:
            assert '做项目' in relationships[0][0]
            assert '她负责设计' in relationships[0][0]


def test_formation_resolves_short_correction_topic_from_existing_transition_sources(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite3'
    clock = MutableClock()
    raw = '我最近只能周三晚上锻炼。'
    first = _form(raw, (0, len(raw)))
    first['supports'] = [{'evidence_id': 'evidence-1', 'quote': raw}]
    _run(path, clock, [_model(_batch(first))], ('evidence-1',),
         lambda db_path: _set_evidence(db_path, 'evidence-1', raw))
    with sqlite3.connect(path) as db:
        prior_id = db.execute('SELECT id FROM cognition').fetchone()[0]
    _insert_job(path, clock, job_id='job-2', evidence_ids=('evidence-2',))
    correction = '不对，是周五晚上。'
    _set_evidence(path, 'evidence-2', correction)
    item = _form(correction, (0, len(correction)), evidence_id='evidence-2')
    item['supports'] = [{'evidence_id': 'evidence-2', 'quote': correction}]
    item.update(action='correct', corrects_cognition_id=prior_id)
    processor = HermesBatchAdapterProcessor(str(path), lambda *a, **kw: _model(_batch(item)), clock=clock)
    worker = WorldJobWorker(path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    _insert_job(path, clock, job_id='job-3', evidence_ids=('evidence-3',))
    _set_evidence(path, 'evidence-3', '再改成周六晚上。')
    captured: list[dict[str, object]] = []

    def route(messages: list[dict[str, str]], **kwargs: object) -> dict[str, object]:
        captured.append(json.loads(messages[-1]['content']))
        return _model({'schema_version': 8, 'result': 'no_change'})

    processor = HermesBatchAdapterProcessor(str(path), route, clock=clock)
    worker = WorldJobWorker(path, processor=processor, policy=_policy(), clock=clock)
    assert worker.run_until_quiescent() == 1
    current = captured[-1]['current_cognitions']
    assert isinstance(current, list)
    assert len(current) == 1
    assert '周五' in current[0]['content']
    assert '锻炼' in current[0]['predecessor_context'][0]
    evidence = captured[-1]['evidence']
    assert isinstance(evidence, list)
    assert '锻炼' not in evidence[0]['text']  # context is not new Evidence

    # Muting the original removes its topic from formation as well as recall.
    with sqlite3.connect(path) as db:
        db.execute('UPDATE cognition SET muted_at = ? WHERE id = ?', ('2026-08-20T00:00:00Z', prior_id))
    _insert_job(path, clock, job_id='job-4', evidence_ids=('evidence-4',))
    _set_evidence(path, 'evidence-4', '改成周日晚上。')
    assert worker.run_until_quiescent() == 1
    current = captured[-1]['current_cognitions']
    assert isinstance(current, list)
    assert 'predecessor_context' not in current[0]
