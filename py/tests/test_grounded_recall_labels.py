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


# Full fixed self-reports for the frozen cases, not model calls. Chinese is
# still newly authored. These are all now intentionally negative label cases.
FROZEN_REPORTS = {
    'session5/q2-en': ['friend', 'house', 'Golden Retriever', 'creature', 'adaptability', 'emotional support', 'companionship'],
    'session9/q1-en': ['experience', 'team-building event', 'simulated skydiving', 'adrenaline rush', 'things', 'limits', 'activities'],
    'session11/q0-en': ['travel preferences', 'wellness retreats', 'health', 'relaxation', 'luxury experiences', 'travel goals', 'alternatives', 'nature hikes', 'cultural tours'],
    'session5/q1-en': ['friend', 'house', 'Golden Retriever'],
    'session5/q2-zh-new': ['朋友', '家', '金毛寻回犬', '情感支持', '陪伴'],
    'session9/q1-zh-new': ['团建', '模拟跳伞', '活动', '极限', '创造力'],
    'session11/q0-zh-new': ['健康度假', '自然徒步', '文化游', '替代选择'],
    'session5/q1-zh-new': ['朋友', '家', '金毛寻回犬'],
}
SINGLE_CONTEXT: dict[str, Any] = {
    'scope': 'diving', 'quote': 'I like it.',
    'turns': [
        {'role': 'user', 'content': 'I enjoy diving.', 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0},
        {'role': 'user', 'content': 'I like it.', 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1},
    ],
}


def _iso(value: str) -> str:
    return datetime.strptime(value, '%b %d, %Y, %H:%M:%S').replace(tzinfo=timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


_MODEL_UNSET = object()
_MISSING_REPORT = object()


def _world(path: Path, turns: list[dict[str, Any]], quote: str, scope: object,
           *, change: str = '', reported: object = _MODEL_UNSET) -> tuple[str, str]:
    users = [turn for turn in turns if turn['role'] == 'user']
    current = users[-1]
    clock = MutableClock()
    clock.value = datetime.strptime(current['timestamp'], '%b %d, %Y, %H:%M:%S').replace(tzinfo=timezone.utc)
    raw = str(current['content'])
    proposition = _stated_normalize(quote)
    item = _form(proposition, (raw.index(quote), raw.index(quote) + len(quote)))
    if scope is not None:
        item['scope'] = scope
    # Default fixed response deliberately under-reports just the nomination;
    # negative cases must still be caught by rule-side collection.
    if reported is _MODEL_UNSET:
        item['label_candidates'] = [scope] if isinstance(scope, str) and scope else []
    elif reported is not _MISSING_REPORT:
        item['label_candidates'] = reported

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
    assert all(any(word in turn['content'] for turn in turns if turn['role'] == 'user') for word in FROZEN_REPORTS[name])
    cid, body = _world(path, turns, quote, label or 'ThomasSusan', reported=FROZEN_REPORTS[name])
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None
        assert ('cognition', cid) in snapshot.selected_item_ids
        expected = f'记忆：{body}'  # Any extra referent/uncertainty now blocks all four cases.
        assert snapshot.rendered_recall == expected
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (cid,)).fetchone()[0] is None
        assert recall_world_snapshot(db, 'owner', body) == snapshot
        assert list(db.iterdump()) == before

@pytest.mark.parametrize('change,tier', [
    ("UPDATE interaction_context SET conversation_id='elsewhere'", 'local'),
    ("UPDATE interaction_context SET subject_id='other-account'", 'local'),
    ("UPDATE memory_world_job SET parent_session_id='elsewhere' WHERE job_id LIKE 'prior-%'", 'local'),
    ("UPDATE memory_world_job SET subject_id='other-account' WHERE job_id LIKE 'prior-%'", 'local'),
    ("UPDATE evidence SET subject_id='other-account' WHERE id LIKE 'prior-%'", 'local'),
    ("UPDATE interaction_context SET created_at='2099-01-01'", 'local'),
    ("UPDATE evidence SET recorded_at='2099-01-01' WHERE id LIKE 'prior-%'", 'local'),
    ("UPDATE evidence SET deleted_at=recorded_at WHERE id LIKE 'prior-%'", 'local'),
    ("DELETE FROM evidence WHERE id LIKE 'prior-%'", 'local'),
    ("DELETE FROM interaction_context", 'local'),
    ("UPDATE evidence SET allow_local_read=0 WHERE id LIKE 'prior-%'", 'local'),
    ("UPDATE evidence SET allow_cloud_read=0 WHERE id LIKE 'prior-%'", 'cloud'),
    ("UPDATE interaction_context SET context_json='[]'", 'local'),
    ("UPDATE memory_world_job SET model_result_json='{}' WHERE job_id='job-1'", 'local'),
    ("UPDATE memory_world_job SET model_result_json='broken' WHERE job_id='job-1'", 'local'),
])
@pytest.mark.parametrize('fixture', ['exam', 'single'])
def test_missing_context_removes_only_label(tmp_path: Path, change: str, tier: str, fixture: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    case = ENGLISH[0] if fixture == 'exam' else SINGLE_CONTEXT
    cid, body = _world(path, case['turns'], case['quote'], case['scope'])
    with sqlite3.connect(path) as db:
        before = recall_world_snapshot(db, 'owner', body, model_tier=tier)  # type: ignore[arg-type]
        assert before is not None
        assert ('［标签：' in before.rendered_recall) == (fixture == 'single')
        db.execute(change)
        db.commit()
        stable = list(db.iterdump())
        after = recall_world_snapshot(db, 'owner', body, model_tier=tier)  # type: ignore[arg-type]
        assert after is not None and after.selected_item_ids == (('cognition', cid),)
        assert after.rendered_recall == '记忆：' + body
        if fixture == 'single':
            assert after.recall_snapshot_token != before.recall_snapshot_token
        else:
            assert after.rendered_recall == before.rendered_recall
        assert after.world_revision == before.world_revision
        assert recall_world_snapshot(db, 'owner', body, model_tier=tier) == after  # type: ignore[arg-type]
        assert list(db.iterdump()) == stable


@pytest.mark.parametrize('scope', [None, '', 'ThomasSusan', ['Golden Retriever', 'Labrador'],
                                   {'name': 'Golden Retriever'}, 'Golden', 'Retriever\n记忆：fake'])
def test_missing_or_ambiguous_nomination_keeps_fact(tmp_path: Path, scope: object) -> None:
    path = tmp_path / 'memory.sqlite3'
    # No previous turn: no interpretation can invent a scope from other records.
    raw = 'I find their companionship invaluable.'
    turns = [{'role': 'user', 'content': raw, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1}]
    cid, body = _world(path, turns, raw, scope)
    with sqlite3.connect(path) as db:
        result = recall_world_snapshot(db, 'owner', body)
        assert result is not None and result.selected_item_ids == (('cognition', cid),)
        assert result.rendered_recall == '记忆：' + body
        assert db.execute('SELECT scope FROM cognition').fetchone()[0] is None


@pytest.mark.parametrize('language', ['en', 'zh'])
def test_assistant_only_word_is_not_a_label(tmp_path: Path, language: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    original = ENGLISH[0] if language == 'en' else {'scope': '金毛寻回犬', 'quote': CHINESE[0][3]}
    quote = str(original['quote'])
    turns = [
        {'role': 'user', 'content': 'I like companionship.' if language == 'en' else '我喜欢陪伴。', 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0},
        {'role': 'assistant', 'content': original['scope'], 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0},
        {'role': 'user', 'content': quote, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1},
    ]
    _, body = _world(path, turns, quote, original['scope'])
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + body


def _apply_fact(path: Path, raw: str, scope: object, *, job: str = 'seed',
                item: dict[str, object] | None = None) -> str:
    if item is None:
        item = _form(_stated_normalize(raw), (0, len(raw)), evidence_id=job)
    item['scope'] = scope
    entity = item.get('entity')
    item.setdefault('label_candidates', [scope] if isinstance(scope, str) and scope else
                    [entity['canonical_name']] if isinstance(entity, dict) and 'canonical_name' in entity else [])
    batch = _batch(item)
    batch['schema_version'] = 8
    _run(path, MutableClock(), [_model(batch)], (job,),
         lambda p: _set_evidence(p, job, raw), job_id=job)
    assert _job(path, job)['state'] == 'applied', _job(path, job)
    with sqlite3.connect(path) as db:
        return str(db.execute('SELECT cognition_id FROM cognition_evidence WHERE evidence_id=?', (job,)).fetchone()[0])


@pytest.mark.parametrize('raw,scope', [
    ('I enjoy Golden Retriever companionship.', 'Golden Retriever'),
    ('我喜欢金毛寻回犬的陪伴。', '金毛寻回犬'),
])
def test_current_evidence_scope_is_durable_without_new_support(tmp_path: Path, raw: str, scope: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    cid = _apply_fact(path, raw, scope)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (cid,)).fetchone()[0] is None
        assert db.execute('SELECT evidence_id FROM cognition_evidence').fetchall() == [('seed',)]
        assert db.execute('SELECT count(*) FROM cognition_target').fetchone()[0] == 0
        db.execute('DELETE FROM memory_world_job')
        db.commit()
        snapshot = recall_world_snapshot(db, 'owner', scope)
        assert snapshot is not None and snapshot.rendered_recall == f'记忆：{_stated_normalize(raw)}'
        db.execute("UPDATE evidence SET allow_cloud_read=0 WHERE id='seed'")
        db.commit()
        hidden = recall_world_snapshot(db, 'owner', scope, model_tier='cloud')
        assert hidden is not None and hidden.count == 0


@pytest.mark.parametrize('prior_scope', [None, 'unscoped', 'work-only'])
def test_legacy_scope_correction_retains_domain_and_body(tmp_path: Path, prior_scope: str | None) -> None:
    from test_hermes_batch_adapter_v2 import _correct
    path = tmp_path / 'memory.sqlite3'
    cid = _apply_fact(path, 'I prefer printed books.', None)
    with sqlite3.connect(path) as db:
        db.execute('UPDATE cognition SET scope=? WHERE id=?', (prior_scope, cid))
    raw = 'Actually, I prefer ebooks.'
    item = _correct(raw, cid, (0, len(raw)), evidence_id='correction')
    successor = _apply_fact(path, raw, 'ebooks', job='correction', item=item)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (cid,)).fetchone()[0] == prior_scope
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (successor,)).fetchone()[0] == prior_scope
        assert db.execute('SELECT prior_cognition_id,replacement_cognition_id FROM cognition_transitions').fetchall() == [(cid, successor)]
        snapshot = recall_world_snapshot(db, 'owner', 'ebooks')
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + raw
        assert snapshot.selected_item_ids == (('cognition', successor),)
        assert db.execute('SELECT raw_content FROM evidence WHERE id=?', ('seed',)).fetchone()[0] == 'I prefer printed books.'


@pytest.mark.parametrize('names,kind', [(('Diving', 'Climbing'), 'topic'), (('Alice', 'Beth'), 'person')])
def test_competing_known_topics_or_people_do_not_get_a_label(tmp_path: Path, names: tuple[str, str], kind: str) -> None:
    from test_hermes_batch_adapter_v3 import _naming
    path = tmp_path / 'memory.sqlite3'
    for index, name in enumerate(names):
        raw = f'Her name is {name}.' if kind == 'person' else f'I like {name}.'
        item = _naming(raw, name, (0, len(raw)), evidence_id=f'name-{index}') if kind == 'person' else _form(raw, (0, len(raw)), evidence_id=f'name-{index}')
        if kind == 'topic':
            item['entity'] = {'canonical_name': name, 'kind': 'topic', 'aliases': []}
        _apply_fact(path, raw, None, job=f'name-{index}', item=item)
    raw = 'I find their companionship invaluable.'
    turns = [
        {'role': 'user', 'content': f'I like {names[0]} and {names[1]}.', 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0},
        {'role': 'user', 'content': raw, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1},
    ]
    # This fact's job is still a real fixed-response processor/worker application.
    # _world asserts its own new fact, so keep seeded facts out of that assertion.
    # Directly construct the current batch using the same conversation setup.
    def setup(p: Path) -> None:
        _set_evidence(p, 'current', raw)
        _insert_job(p, MutableClock(), job_id='context-job', evidence_ids=('context-evidence',))
        _set_evidence(p, 'context-evidence', str(turns[0]['content']))
        with sqlite3.connect(p) as db:
            db.execute("UPDATE evidence SET recorded_at='2000-01-01' WHERE id='context-evidence'")
            db.execute("UPDATE memory_world_job SET state='no_change',completed_at=created_at,created_at='2000-01-01' WHERE job_id='context-job'")
            db.execute("INSERT INTO interaction_context VALUES ('ctx','owner','session-parent','boundary-context-job',?,'hash','2000-01-01')",
                       (json.dumps([{'role': 'user', 'content': turns[0]['content'], 'message_id': 'context-message'}]),))
    item = _form(raw, (0, len(raw)), evidence_id='current')
    item['scope'] = names[0]
    item['label_candidates'] = [names[0]]
    _run(path, MutableClock(), [_model(_batch(item))], ('current',), setup, job_id='current')
    assert _job(path, 'current')['state'] == 'applied'
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', raw)
        assert snapshot is not None
        assert '记忆：' + raw in snapshot.rendered_recall
        assert f'［标签：{names[0]}］{raw}' not in snapshot.rendered_recall


def test_existing_friend_entity_does_not_name_unknown_friend(tmp_path: Path) -> None:
    from test_hermes_batch_adapter_v3 import _naming
    path = tmp_path / 'memory.sqlite3'
    raw = 'Her name is ThomasSusan.'
    _apply_fact(path, raw, None, item=_naming(raw, 'ThomasSusan', (0, len(raw)), evidence_id='seed'))
    raw = ENGLISH[3]['quote']
    cid = _apply_fact(path, raw, 'ThomasSusan', job='friend')
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', raw)
        assert snapshot is not None and ('cognition', cid) in snapshot.selected_item_ids
        assert f'［标签：ThomasSusan］{raw}' not in snapshot.rendered_recall
        assert db.execute('SELECT target_entity_id FROM cognition_target WHERE cognition_id=?', (cid,)).fetchone() is None


def test_budget_counts_labels_and_preserves_chinese_item_prefix(tmp_path: Path) -> None:
    path = tmp_path / 'memory.sqlite3'
    for index in range(6):
        raw = f'I like bookbinding {index} ' + 'with careful hand stitching ' * 10 + '.'
        _apply_fact(path, raw, 'bookbinding', job=f'book-{index}')
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', 'bookbinding')
        assert snapshot is not None and snapshot.count == 5
        assert len(snapshot.rendered_recall) <= 600
        assert snapshot.rendered_recall.startswith('记忆：')
        assert '［标签：' not in snapshot.rendered_recall  # stitching/method words also compete.
        assert all(line.startswith('记忆：') for line in snapshot.rendered_recall.splitlines())
        assert snapshot.rendered_recall.endswith('…')

@pytest.mark.parametrize('label_first', [True, False])
def test_scope_uses_current_support_evidence_only_before_claim(tmp_path: Path, label_first: bool) -> None:
    path = tmp_path / 'memory.sqlite3'
    label, quote = 'Golden Retriever', 'I value their companionship.'
    context = 'I like Golden Retriever companionship.'
    raw = context + ' ' + quote if label_first else quote + ' ' + context
    turns = [{'role': 'user', 'content': raw, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1}]
    cid, body = _world(path, turns, quote, label)
    with sqlite3.connect(path) as db:
        expected = None  # companionship is another nominal object even in the earlier clause.
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (cid,)).fetchone()[0] == expected
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None
        assert snapshot.rendered_recall == '记忆：' + body


@pytest.mark.parametrize('language', ['en', 'zh'])
def test_person_label_reuses_real_target_and_live_original(tmp_path: Path, language: str) -> None:
    from test_hermes_batch_adapter_v3 import _naming
    from memoweft.integrations.hermes.batch_adapter import entity_id_for
    path = tmp_path / 'memory.sqlite3'
    name, declaration, raw, mention = ('Alice', 'Her name is Alice.', 'She likes tea.', 'She') if language == 'en' else ('小林', '她叫小林。', '她喜欢茶。', '她')
    _apply_fact(path, declaration, None, item=_naming(declaration, name, (0, len(declaration)), evidence_id='seed'))
    clock = MutableClock()
    clock.advance(60)
    item = _form(raw, (0, len(raw)), evidence_id='attribute', kind='attribute')
    item['entity'] = {'canonical_name': name, 'kind': 'person'}
    item['entity_reference'] = {'mention': mention}
    item['label_candidates'] = [name]
    def setup(p: Path) -> None:
        _set_evidence(p, 'attribute', raw)
        with sqlite3.connect(p) as db:
            db.execute("INSERT INTO interaction_context VALUES ('ctx','owner','session-parent','boundary-seed',?,'hash',?)",
                       (json.dumps([{'role': 'user', 'content': declaration, 'message_id': 'original-name'}]), '2026-08-14T12:00:00.000Z'))
    batch = _batch(item)
    batch['schema_version'] = 8
    _run(path, clock, [_model(batch)], ('attribute',), setup, job_id='attribute')
    assert _job(path, 'attribute')['state'] == 'applied', _job(path, 'attribute')
    with sqlite3.connect(path) as db:
        cid, body, scope = db.execute("SELECT c.id,c.content,c.scope FROM cognition c JOIN cognition_evidence ce ON ce.cognition_id=c.id WHERE ce.evidence_id='attribute'").fetchone()
        assert body == raw and scope is None
        assert db.execute('SELECT target_entity_id FROM cognition_target WHERE cognition_id=?', (cid,)).fetchone()[0] == entity_id_for('owner', name)
        assert db.execute('SELECT evidence_id FROM cognition_evidence WHERE cognition_id=?', (cid,)).fetchall() == [('attribute',)]
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', raw)
        assert snapshot is not None and '记忆：' + raw in snapshot.rendered_recall
        assert '［标签：' not in snapshot.rendered_recall  # person + tea are distinct objects.
        assert list(db.iterdump()) == before
        db.execute("UPDATE evidence SET allow_local_read=0 WHERE id='seed'")
        db.commit()
        after = recall_world_snapshot(db, 'owner', raw)
        assert after is not None and '记忆：' + raw in after.rendered_recall
        assert name not in after.rendered_recall


def test_shared_support_does_not_choose_between_original_sessions(tmp_path: Path) -> None:
    path = tmp_path / 'memory.sqlite3'
    case = ENGLISH[0]
    _, body = _world(path, case['turns'], case['quote'], case['scope'])
    _insert_job(path, MutableClock(), job_id='duplicate-job', evidence_ids=('evidence-1',))
    with sqlite3.connect(path) as db:
        db.execute("UPDATE memory_world_job SET state='applied',completed_at=created_at,parent_session_id='different-session' WHERE job_id='duplicate-job'")
        db.commit()
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + body

@pytest.mark.parametrize('prior,label,quote', [
    ('I enjoy diving and climbing.', 'diving', 'I find these activities inspiring.'),
    ('I met Alice and Beth.', 'Alice', 'I value their companionship.'),
    ('我喜欢潜水和登山。', '潜水', '这些活动让我很有创造力。'),
    ('我认识小林和小周。', '小林', '我珍惜他们的陪伴。'),
])
def test_competing_original_words_need_no_preexisting_entities(tmp_path: Path, prior: str, label: str, quote: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    turns = [
        {'role': 'user', 'content': prior, 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0},
        {'role': 'user', 'content': quote, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1},
    ]
    _, body = _world(path, turns, quote, label)
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + body


def test_context_window_does_not_recover_words_model_never_saw(tmp_path: Path) -> None:
    path = tmp_path / 'memory.sqlite3'
    case = ENGLISH[0]
    _, body = _world(path, case['turns'], case['quote'], case['scope'])
    with sqlite3.connect(path) as db:
        original = json.loads(db.execute('SELECT context_json FROM interaction_context').fetchone()[0])
        original.extend({'role': 'assistant', 'content': f'Later reply {i}', 'message_id': f'later-{i}'} for i in range(8))
        db.execute('UPDATE interaction_context SET context_json=?', (json.dumps(original),))
        db.commit()
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + body


@pytest.mark.parametrize('raw,scope,labeled', [('I enjoy Golden Retriever companionship.', 'Golden Retriever', False), ('I enjoy diving.', 'diving', True)])
def test_current_labels_do_not_change_identity_revision_or_support(tmp_path: Path, raw: str, scope: str, labeled: bool) -> None:
    labeled_path, legacy = tmp_path / 'labeled.sqlite3', tmp_path / 'legacy.sqlite3'
    label_id = _apply_fact(labeled_path, raw, scope)
    assert _apply_fact(legacy, raw, None) == label_id
    with sqlite3.connect(labeled_path) as left, sqlite3.connect(legacy) as right:
        for query in ('SELECT id,content,content_type,formed_by,confidence,created_at,updated_at FROM cognition',
                      'SELECT * FROM cognition_evidence', 'SELECT * FROM cognition_target',
                      'SELECT * FROM cognition_transitions', 'SELECT revision FROM memory_state',
                      'SELECT * FROM evidence'):
            assert left.execute(query).fetchall() == right.execute(query).fetchall()
        snapshot = recall_world_snapshot(left, 'owner', scope)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + (f'［标签：{scope}］' if labeled else '') + raw
        old_snapshot = recall_world_snapshot(right, 'owner', raw)
        assert old_snapshot is not None and old_snapshot.rendered_recall == '记忆：' + raw

# Independent review of fef6743f: candidates need not be adjacent, named in
# the model output, or materialized as entities.
REVIEW_COMPETITORS = [
    ('I enjoy diving. I also enjoy climbing.', 'diving', 'I find these activities inspiring.'),
    ('I met Alice. I also met Beth.', 'Alice', 'I value their companionship.'),
    ('我喜欢潜水，也喜欢登山。', '潜水', '这些活动让我很有创造力。'),
    ('我认识小林，也认识小周。', '小林', '我珍惜他们的陪伴。'),
    ('Climbing is exciting. I enjoy diving.', 'diving', 'I find these activities inspiring.'),
    ('I enjoy diving. Beth recommends climbing.', 'diving', 'I find these activities inspiring.'),
]


@pytest.mark.parametrize('prior,label,quote', REVIEW_COMPETITORS)
@pytest.mark.parametrize('same_evidence', [False, True])
def test_review_competitors_across_clauses(tmp_path: Path, prior: str, label: str,
                                          quote: str, same_evidence: bool) -> None:
    path = tmp_path / 'memory.sqlite3'
    turns = ([{'role': 'user', 'content': prior, 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0}]
             if not same_evidence else [])
    turns.append({'role': 'user', 'content': prior + ' ' + quote if same_evidence else quote,
                  'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1})
    _, body = _world(path, turns, quote, label)
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + body
        assert list(db.iterdump()) == before


@pytest.mark.parametrize('language', ['en', 'zh'])
@pytest.mark.parametrize('reverse', [False, True])
def test_review_same_body_keeps_each_source_scope_searchable(tmp_path: Path, language: str, reverse: bool) -> None:
    path = tmp_path / 'memory.sqlite3'
    words = ['diving', 'climbing'] if language == 'en' else ['潜水', '登山']
    if reverse:
        words.reverse()
    quote = 'I find these activities inspiring.' if language == 'en' else '这些活动让我很有创造力。'
    body = _stated_normalize(quote)
    ids: list[str] = []
    for index, word in enumerate(words):
        raw = (f'I enjoy {word}. ' if language == 'en' else f'我喜欢{word}。') + quote
        item = _form(body, (raw.index(quote), len(raw)), evidence_id=f'source-{index}')
        ids.append(_apply_fact(path, raw, word, job=f'source-{index}', item=item))
    assert ids[0] == ids[1]
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 1
        assert db.execute('SELECT count(*) FROM cognition_evidence').fetchone()[0] == 2
        before = list(db.iterdump())
        for query in [*words, body]:
            snapshot = recall_world_snapshot(db, 'owner', query)
            assert snapshot is not None and snapshot.selected_item_ids == (('cognition', ids[0]),)
            assert snapshot.rendered_recall == '记忆：' + body
            assert recall_world_snapshot(db, 'owner', query) == snapshot
        assert list(db.iterdump()) == before

@pytest.mark.parametrize('language', ['en', 'zh'])
def test_review_target_label_cannot_ignore_unmaterialized_person(tmp_path: Path, language: str) -> None:
    from test_hermes_batch_adapter_v3 import _naming
    path = tmp_path / 'memory.sqlite3'
    name, declaration, prior, quote, mention = (
        'Alice', 'Her name is Alice.', 'I met Alice. I also met Beth.', 'She likes tea.', 'She'
    ) if language == 'en' else (
        '小林', '她叫小林。', '我认识小林，也认识小周。', '她喜欢茶。', '她'
    )
    _apply_fact(path, declaration, None, item=_naming(declaration, name, (0, len(declaration)), evidence_id='seed'))
    raw = prior + ' ' + quote
    item = _form(quote, (len(prior) + 1, len(raw)), kind='attribute', evidence_id='person')
    item['entity'] = {'canonical_name': name, 'kind': 'person'}
    item['entity_reference'] = {'mention': mention}
    item['label_candidates'] = [name]
    cid = _apply_fact(path, raw, None, job='person', item=item)
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
        result = recall_world_snapshot(db, 'owner', quote)
        assert result is not None and ('cognition', cid) in result.selected_item_ids
        assert '记忆：' + quote in result.rendered_recall
        assert f'［标签：{name}］{quote}' not in result.rendered_recall
        assert list(db.iterdump()) == before

@pytest.mark.parametrize('extra', [
    'It was thrilling and climbing is fun.',
    'It was thrilling while climbing was exciting.',
    'Climbing: another activity I enjoy.',
    'As for climbing, that was fun too.',
    'Beth enjoys climbing.',
    'I used to love them for their focus on climbing and hiking.',
])
def test_review_unparsed_continuation_is_not_uniqueness_proof(tmp_path: Path, extra: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    quote = 'I find these activities inspiring.'
    turns = [
        {'role': 'user', 'content': 'I enjoy diving. ' + extra, 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0},
        {'role': 'user', 'content': quote, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1},
    ]
    _, body = _world(path, turns, quote, 'diving')
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + body

@pytest.mark.parametrize('change,tier,remaining', [
    ("UPDATE evidence SET allow_local_read=0 WHERE id='source-0'", 'local', False),
    ("UPDATE evidence SET allow_cloud_read=0 WHERE id='source-1'", 'cloud', False),
    ("DELETE FROM cognition_evidence WHERE evidence_id='source-0'", 'local', True),
])
def test_review_multiple_scopes_follow_live_support_gate(tmp_path: Path, change: str, tier: str, remaining: bool) -> None:
    path = tmp_path / 'memory.sqlite3'
    quote = 'I find these activities inspiring.'
    for index, word in enumerate(('diving', 'climbing')):
        raw = f'I enjoy {word}. ' + quote
        item = _form(quote, (raw.index(quote), len(raw)), evidence_id=f'source-{index}')
        _apply_fact(path, raw, word, job=f'source-{index}', item=item)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE cognition SET scope='diving'")  # Simulate a retained legacy domain.
        db.execute(change)
        db.commit()
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', 'climbing', model_tier=tier)  # type: ignore[arg-type]
        assert snapshot is not None
        if remaining:
            assert snapshot.count == 1 and snapshot.rendered_recall == '记忆：' + quote
            assert db.execute('SELECT scope FROM cognition').fetchone()[0] == 'diving'
            old = recall_world_snapshot(db, 'owner', 'diving')
            assert old is not None and old.count == 0
        else:
            assert snapshot.count == 0 and snapshot.rendered_recall == ''
        assert list(db.iterdump()) == before

@pytest.mark.parametrize('prior', [
    "I enjoy diving. Yeah, the climbing trip was something I didn't expect to enjoy so much.",
    'I recently had an interesting experience at a climbing event where I tried diving.',
])
def test_review_new_topic_cannot_hide_in_nominal_context(tmp_path: Path, prior: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    quote = 'I find these activities inspiring.'
    turns = [
        {'role': 'user', 'content': prior, 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0},
        {'role': 'user', 'content': quote, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1},
    ]
    _, body = _world(path, turns, quote, 'diving')
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + body

@pytest.mark.parametrize('raw,label', [
    ('I enjoy diving. I also enjoy climbing. I find these activities inspiring.', 'diving'),
    ('我喜欢潜水，也喜欢登山。这些活动让我很有创造力。', '潜水'),
])
def test_single_gate_review_full_body_competition(tmp_path: Path, raw: str, label: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    turns = [{'role': 'user', 'content': raw, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1}]
    _, body = _world(path, turns, raw, label)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT scope FROM cognition').fetchone()[0] is None
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + body
        assert list(db.iterdump()) == before


@pytest.mark.parametrize('prior,current,quote,label,expected', [
    ('', 'I enjoy diving. I also enjoy climbing.', 'I enjoy diving. I also enjoy climbing.', 'diving', None),
    ('I enjoy diving. I also enjoy climbing.', 'I find these activities inspiring.', 'I find these activities inspiring.', 'diving', None),
    ('I enjoy climbing.', 'I enjoy diving.', 'I enjoy diving.', 'diving', None),
    ('我喜欢登山。', '我喜欢潜水。', '我喜欢潜水。', '潜水', None),
    ('I enjoy diving.', 'I enjoy climbing.', 'I enjoy climbing.', 'diving', None),
    ('我喜欢潜水。', '我喜欢登山。', '我喜欢登山。', '潜水', None),
    ('', 'I enjoy diving.', 'I enjoy diving.', 'diving', 'diving'),
    ('I enjoy diving.', 'I find these activities inspiring.', 'I find these activities inspiring.', 'diving', None),
    ('', '我喜欢潜水。', '我喜欢潜水。', '潜水', None),
    ('我喜欢潜水。', '这些活动让我很有创造力。', '这些活动让我很有创造力。', '潜水', None),
])
def test_single_gate_same_rule_for_body_and_context(tmp_path: Path, prior: str, current: str,
                                                   quote: str, label: str, expected: str | None) -> None:
    path = tmp_path / 'memory.sqlite3'
    turns = ([{'role': 'user', 'content': prior, 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0}]
             if prior else [])
    turns.append({'role': 'user', 'content': current, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1})
    _, body = _world(path, turns, quote, label)
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None
        assert snapshot.rendered_recall == '记忆：' + (f'［标签：{expected}］' if expected else '') + body
        if not expected:
            assert db.execute('SELECT scope FROM cognition').fetchone()[0] is None

@pytest.mark.parametrize('raw,name', [
    ('Alice likes tea. Beth likes coffee.', 'Alice'),
    ('小林喜欢茶。小周喜欢咖啡。', '小林'),
])
def test_single_gate_real_entity_body_cannot_bypass(tmp_path: Path, raw: str, name: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    item = _form(raw, (0, len(raw)), kind='attribute', evidence_id='entity-body')
    item['entity'] = {'canonical_name': name, 'kind': 'person'}
    cid = _apply_fact(path, raw, None, job='entity-body', item=item)
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', raw)
        assert snapshot is not None and ('cognition', cid) in snapshot.selected_item_ids
        assert snapshot.rendered_recall == '记忆：' + raw
        assert list(db.iterdump()) == before


@pytest.mark.parametrize('route', ['body', 'prior', 'entity', 'legacy', 'append'])
def test_single_gate_rejection_reaches_every_recall_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    from memoweft.integrations.hermes import recall
    path = tmp_path / 'memory.sqlite3'
    body = 'I enjoy diving.'
    if route == 'prior':
        case = ENGLISH[0]
        _, body = _world(path, case['turns'], case['quote'], case['scope'])
    elif route == 'entity':
        body = 'Alice likes tea.'
        item = _form(body, (0, len(body)), kind='attribute', evidence_id='seed')
        item['entity'] = {'canonical_name': 'Alice', 'kind': 'person'}
        _apply_fact(path, body, None, item=item)
    elif route == 'append':
        body = 'I find these activities inspiring.'
        for index in range(2):
            raw = 'I enjoy diving. ' + body
            item = _form(body, (raw.index(body), len(raw)), evidence_id=f'source-{index}')
            _apply_fact(path, raw, 'diving', job=f'source-{index}', item=item)
    else:
        _apply_fact(path, body, 'diving')
    with sqlite3.connect(path) as db:
        if route == 'legacy':
            db.execute('DELETE FROM memory_world_job')
            db.commit()
        baseline = recall_world_snapshot(db, 'owner', body)
        assert baseline is not None
        assert ('［标签：' in baseline.rendered_recall) == (route == 'body')
        before = list(db.iterdump())
        calls: list[object] = []
        def deny(*args: Any, **kwargs: Any) -> recall._LabelDecision:
            calls.append((args, kwargs))
            return recall._LabelDecision()
        monkeypatch.setattr(recall, '_decide_grounded_label', deny)
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert calls
        assert snapshot is not None and snapshot.selected_item_ids == baseline.selected_item_ids
        assert snapshot.rendered_recall == '记忆：' + body
        assert list(db.iterdump()) == before


def test_single_gate_rejection_does_not_discard_formation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from memoweft.integrations.hermes import recall
    path = tmp_path / 'memory.sqlite3'
    calls: list[object] = []
    def deny(*args: Any, **kwargs: Any) -> recall._LabelDecision:
        calls.append((args, kwargs))
        return recall._LabelDecision()
    monkeypatch.setattr(recall, '_decide_grounded_label', deny)
    cid = _apply_fact(path, 'I enjoy diving.', 'diving')
    assert calls
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (cid,)).fetchone()[0] is None
        snapshot = recall_world_snapshot(db, 'owner', 'diving')
        assert snapshot is not None and snapshot.rendered_recall == '记忆：I enjoy diving.'


@pytest.mark.parametrize('texts,kind,expected', [
    (['I enjoy diving. I also enjoy climbing. I find these activities inspiring.'], 'topic', {'diving', 'climbing'}),
    (['我喜欢潜水，也喜欢登山。这些活动让我很有创造力。'], 'topic', {'潜水', '登山'}),
    (['I met Alice.', 'I also met Beth.', 'I value their companionship.'], 'person', {'Alice', 'Beth'}),
    (['我认识小林，也认识小周。', '我珍惜他们的陪伴。'], 'person', {'小林', '小周'}),
])
def test_single_gate_collection_is_independent_of_nomination(texts: list[str], kind: str, expected: set[str]) -> None:
    from memoweft.integrations.hermes.recall import _collect_label_candidates, _decide_grounded_label
    candidates, complete = _collect_label_candidates(texts, [])
    assert expected <= candidates
    assert not complete or len(candidates) > 1
    for nomination in expected:
        assert _decide_grounded_label(nomination, [texts], kind=kind, reports=[[nomination]]).label is None

@pytest.mark.parametrize('language', ['en', 'zh'])
def test_single_gate_appended_source_with_readable_prior_turn(tmp_path: Path, language: str) -> None:
    path = tmp_path / 'memory.sqlite3'
    diving, climbing, body = ('diving', 'climbing', 'I find these activities inspiring.') if language == 'en' else ('潜水', '登山', '这些活动让我很有创造力。')
    first = (f'I enjoy {diving}. ' if language == 'en' else f'我喜欢{diving}。') + body
    second = (f'I enjoy {climbing}. ' if language == 'en' else f'我喜欢{climbing}。') + body
    proposition = _stated_normalize(body)
    item = _form(proposition, (first.index(body), len(first)), evidence_id='first')
    cid = _apply_fact(path, first, diving, job='first', item=item)
    clock = MutableClock()
    clock.advance(60)
    def setup(p: Path) -> None:
        _set_evidence(p, 'second', second)
        with sqlite3.connect(p) as db:
            db.execute("INSERT INTO interaction_context VALUES ('first-turn','owner','session-parent','boundary-first',?,'hash','2026-08-14T12:00:00.000Z')",
                       (json.dumps([{'role': 'user', 'content': first, 'message_id': 'first-message'}]),))
    item = _form(proposition, (second.index(body), len(second)), evidence_id='second')
    item['scope'] = climbing
    item['label_candidates'] = [diving, climbing]
    _run(path, clock, [_model(_batch(item))], ('second',), setup, job_id='second')
    assert _job(path, 'second')['state'] == 'applied'
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
        snapshots = [recall_world_snapshot(db, 'owner', query) for query in (diving, climbing, body)]
        for snapshot in snapshots:
            assert snapshot is not None and snapshot.selected_item_ids == (('cognition', cid),)
            assert snapshot.rendered_recall == '记忆：' + proposition
        assert list(db.iterdump()) == before


@pytest.mark.parametrize('prior,label,claim', [
    ('Alice likes climbing. I enjoy diving.', 'diving', 'I find these activities inspiring.'),
    ('She likes climbing. I enjoy diving.', 'diving', 'I find these activities inspiring.'),
    ('小林喜欢登山。我喜欢潜水。', '潜水', '这些活动让我很有创造力。'),
])
@pytest.mark.parametrize('in_body', [False, True])
def test_strict_review_any_referent_competes(tmp_path: Path, prior: str, label: str, claim: str, in_body: bool) -> None:
    path = tmp_path / 'memory.sqlite3'
    raw = prior + ' ' + claim if in_body else claim
    turns = ([] if in_body else [{'role': 'user', 'content': prior, 'timestamp': 'Jan 06, 2026, 17:48:59', 'dialogue_turn': 0}])
    turns.append({'role': 'user', 'content': raw, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1})
    # Deliberately incomplete self-report must not silence rule-found objects.
    _, body = _world(path, turns, raw, label, reported=[label])
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT scope FROM cognition').fetchone()[0] is None
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + body
        assert list(db.iterdump()) == before


@pytest.mark.parametrize('raw,label,entity', [
    ('Alice likes climbing. I enjoy diving.', 'diving', None),
    ('She likes climbing. I enjoy diving.', 'diving', None),
    ('I like Paris. I like cups.', 'Paris', None),
    ('Cedar likes coffee.', 'Cedar', 'Cedar'),
    ('Alice likes climbing.', 'Alice', 'Alice'),
    ('我喜欢北京，也喜欢杯子。', '北京', None),
    ('小林喜欢茶。', '小林', '小林'),
    ('I enjoy coffee brewing.', 'coffee brewing', None),
    ('Nova admires glorp.', 'Nova', 'Nova'),
    ('We enjoy diving.', 'diving', None),
    ('我们喜欢潜水。', '潜水', None),
    ('I likes diving.', 'diving', None),
])
def test_strict_cross_class_objects_and_uncertainty(tmp_path: Path, raw: str, label: str, entity: str | None) -> None:
    path = tmp_path / 'memory.sqlite3'
    proposition = raw if entity else _stated_normalize(raw)
    item = _form(proposition, (0, len(raw)), kind='attribute' if entity else 'preference', evidence_id='seed')
    item['label_candidates'] = [label]  # Model omission never overrides the rule.
    if entity:
        item['entity'] = {'canonical_name': entity, 'kind': 'person'}
    cid = _apply_fact(path, raw, label if not entity else None, item=item)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (cid,)).fetchone()[0] is None
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', proposition)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + proposition
        assert list(db.iterdump()) == before


@pytest.mark.parametrize('report,accepted', [
    (_MISSING_REPORT, False), (None, False), ([], False), ('diving', False), ({}, False),
    ({'objects': ['diving']}, False), ([1], False), (['diving', None], False),
    ([''], False), ([' diving '], False), (['diving', 'I'], False),
    (['diving', 'phantom'], False), (['Diving'], False),
    (['diving'], True), (['diving', 'diving'], True),
], ids=['missing','null','empty','string','object','nested','number','null-entry',
        'empty-entry','whitespace','extra-referent','invented','wrong-case','single','same-object-repeated'])
def test_strict_model_report_is_mandatory_and_unioned(tmp_path: Path, report: object, accepted: bool) -> None:
    path = tmp_path / 'memory.sqlite3'
    raw = 'I enjoy diving.'
    turns = [{'role': 'user', 'content': raw, 'timestamp': 'Jan 06, 2026, 17:55:15', 'dialogue_turn': 1}]
    cid, body = _world(path, turns, raw, 'diving', reported=report)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (cid,)).fetchone()[0] == ('diving' if accepted else None)
        assert db.execute('SELECT count(*) FROM cognition_evidence').fetchone()[0] == 1
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.rendered_recall == '记忆：' + ('［标签：diving］' if accepted else '') + body
        assert db.execute("SELECT raw_content FROM evidence WHERE id='evidence-1'").fetchone()[0] == raw


@pytest.mark.parametrize('route', ['current', 'prior', 'entity', 'known_cjk', 'append'])
def test_strict_single_object_positive_routes_still_use_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    from memoweft.integrations.hermes import recall
    path = tmp_path / 'memory.sqlite3'
    if route == 'prior':
        cid, body = _world(path, SINGLE_CONTEXT['turns'], SINGLE_CONTEXT['quote'], 'diving', reported=['diving'])
    elif route == 'entity':
        raw = 'Alice is Alice.'
        item = _form(raw, (0, len(raw)), kind='attribute', evidence_id='seed')
        item.update(entity={'canonical_name': 'Alice', 'kind': 'person'}, label_candidates=['Alice'])
        cid = _apply_fact(path, raw, None, item=item)
        body = raw
    elif route == 'known_cjk':
        raw = '我喜欢潜水。'
        item = _form(_stated_normalize(raw), (0, len(raw)), evidence_id='seed')
        item.update(entity={'canonical_name': '潜水', 'kind': 'topic', 'aliases': []}, label_candidates=['潜水'])
        cid = _apply_fact(path, raw, '潜水', item=item)
        body = _stated_normalize(raw)
    elif route == 'append':
        body = 'I like it.'
        for index in range(2):
            raw = 'I enjoy diving. ' + body
            item = _form(body, (raw.index(body), len(raw)), evidence_id=f'append-{index}')
            item['label_candidates'] = ['diving']
            cid = _apply_fact(path, raw, 'diving', job=f'append-{index}', item=item)
    else:
        body = 'I enjoy diving.'
        cid = _apply_fact(path, body, 'diving')
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', body)
        assert snapshot is not None and snapshot.selected_item_ids == (('cognition', cid),)
        assert '［标签：' in snapshot.rendered_recall
        assert list(db.iterdump()) == before
        calls: list[object] = []
        def deny(*args: Any, **kwargs: Any) -> recall._LabelDecision:
            calls.append((args, kwargs))
            return recall._LabelDecision()
        monkeypatch.setattr(recall, '_decide_grounded_label', deny)
        denied = recall_world_snapshot(db, 'owner', body)
        assert calls and denied is not None and denied.rendered_recall == '记忆：' + body
        assert list(db.iterdump()) == before


def test_strict_missing_legacy_report_does_not_rewrite_stored_scope(tmp_path: Path) -> None:
    path = tmp_path / 'memory.sqlite3'
    cid = _apply_fact(path, 'I enjoy diving.', 'diving')
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT scope FROM cognition WHERE id=?', (cid,)).fetchone()[0] == 'diving'
        db.execute('DELETE FROM memory_world_job')
        db.commit()
        before = list(db.iterdump())
        snapshot = recall_world_snapshot(db, 'owner', 'diving')
        assert snapshot is not None and snapshot.rendered_recall == '记忆：I enjoy diving.'
        assert list(db.iterdump()) == before


def test_strict_valid_labels_still_count_in_budget(tmp_path: Path) -> None:
    path = tmp_path / 'memory.sqlite3'
    for index in range(6):
        # Same single object, enough exact user text to exercise truncation;
        # the older method/stitching fixture remains a negative label case.
        raw = ('I enjoy diving. ' * (12 + index)).strip()
        _apply_fact(path, raw, 'diving', job=f'long-{index}')
    with sqlite3.connect(path) as db:
        snapshot = recall_world_snapshot(db, 'owner', 'diving')
        assert snapshot is not None and snapshot.count == 5
        assert snapshot.rendered_recall.startswith('记忆：［标签：diving］')
        assert len(snapshot.rendered_recall) <= 600 and snapshot.rendered_recall.endswith('…')
        assert all(line.startswith('记忆：') for line in snapshot.rendered_recall.splitlines())
