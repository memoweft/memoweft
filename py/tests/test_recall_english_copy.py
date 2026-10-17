"""Kernel-generated recall copy is localized; source strings are never translated."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any, cast

import pytest
import regex

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.trust import QueryService
from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.dsh_bridge.interactions import _render
from memoweft.store import open_db
from test_hermes_batch_adapter_v2 import _batch, _correct, _form, _model, _run, _set_evidence
from test_hermes_world_worker import MutableClock, _insert_job, _job
from test_recall_dates_language import _OWNER, _seed
from test_dsh_interactions import _boundary


CLAIM = 'When the user wants coffee, remind the user to order decaf.'
_CJK = regex.compile(r'[\p{Han}\p{Hiragana}\p{Katakana}\p{Hangul}\p{Bopomofo}\u3000-\u303f\uff00-\uffef]')
_MAIN: dict[str, Any] = json.loads((Path(__file__).parent / 'fixtures/recall-copy-chinese-main.json').read_text(encoding='utf-8'))


def _confirmed_world(path: Path, claim: str = CLAIM) -> str:
    clock = MutableClock()
    confirmation = 'Yes, do that.'
    def setup(path: Path) -> None:
        _set_evidence(path, 'evidence-1', confirmation)
        _insert_job(path, clock, job_id='prior', evidence_ids=('proposal-evidence',))
        _set_evidence(path, 'proposal-evidence', 'I would like a coffee reminder.')
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("UPDATE memory_world_job SET state='no_change',completed_at=created_at WHERE job_id='prior'")
            db.execute("INSERT INTO interaction_context(id,subject_id,conversation_id,episode_id,context_json,context_hash,created_at) "
                       "VALUES('proposal-context','owner','session-parent','boundary-prior',?,'hash','2000-01-01T00:00:00.000Z')",
                       (json.dumps([{'role': 'assistant', 'content': claim, 'message_id': 'assistant-proposal'}]),))
    _run(path, clock, [_model(_batch(_form(claim, (0, len(confirmation)), formed_by='confirmed', assistant_claim=claim)))],
         ('evidence-1',), setup)
    assert _job(path)['state'] == 'applied', _job(path)['world_result_json']
    with closing(sqlite3.connect(path)) as db, db:
        return str(db.execute('SELECT id FROM cognition').fetchone()[0])


@pytest.mark.parametrize('fixture', _MAIN['snapshots'])
@pytest.mark.parametrize('explicit', [False, True])
def test_chinese_confirmation_preview_is_exactly_original_main(tmp_path: Path, fixture: dict[str, Any], explicit: bool) -> None:
    path = tmp_path / 'world.sqlite'
    _confirmed_world(path, fixture['claim'])
    service = QueryService(path, subject_id='owner', lang='zh') if explicit else QueryService(path, subject_id='owner')
    assert service.preview_recall('coffee') == fixture['expected'], _MAIN['baseline']


@pytest.mark.parametrize('fixture', _MAIN['histories'])
def test_chinese_history_copy_is_exactly_original_main(fixture: dict[str, Any]) -> None:
    commitments: list[object] = [SimpleNamespace(**fixture['commitment'])]
    assert _render(fixture['items'], commitments) == fixture['expected']
    assert _render(fixture['items'], commitments, lang='zh') == fixture['expected']


def test_real_english_confirmation_localizes_only_generated_explanation(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    cognition_id = _confirmed_world(path)
    before = path.read_bytes()
    english = cast(dict[str, Any], QueryService(path, subject_id='owner', lang='en').preview_recall('coffee')['preview'])
    chinese = cast(dict[str, Any], QueryService(path, subject_id='owner', lang='zh').preview_recall('coffee')['preview'])
    assert english['selected_item_ids'] == chinese['selected_item_ids'] == [['cognition', cognition_id]]
    assert english['currentness_digest'] == chinese['currentness_digest']
    assert english['rendered_recall'].endswith("\nAssistant's words the user confirmed: " + CLAIM)
    assert not _CJK.search(english['rendered_recall'])
    assert chinese['rendered_recall'].endswith('\n确认依据中的助手原话：' + CLAIM)
    assert path.read_bytes() == before
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute('SELECT content FROM cognition').fetchone()[0] == CLAIM
        assert {row[0] for row in db.execute('SELECT raw_content FROM evidence')} == {'Yes, do that.', 'I would like a coffee reminder.'}


@pytest.mark.parametrize('kind', ['cognition', 'relationship', 'event', 'past', 'targeted'])
def test_english_entry_kinds_have_no_kernel_cjk_copy(tmp_path: Path, kind: str) -> None:
    path = tmp_path / 'world.sqlite'
    db = open_db(str(path))
    try:
        _seed(db, 'synthetic topic.', kind='event' if kind == 'event' else 'cognition',
              event_at='2026-07-14', expression='July 14')
        if kind == 'past':
            db.execute('UPDATE cognition SET confidence=200')
        elif kind in {'targeted', 'relationship'}:
            db.execute("INSERT INTO entity(id,world_id,kind,canonical_name,created_at,updated_at,aliases_json) "
                       "VALUES('named',?,'topic','Atlas','2026-01-01','2026-01-01','[]')", (_OWNER,))
            db.execute('INSERT INTO evidence_ledger(id,content,payload_json) VALUES(?,?,?)', ('name-source',
                       json.dumps({'relation': 'support', 'entity_id': 'named', 'evidence_id': 'source-fact'}), '{"schema_version":1}'))
            if kind == 'targeted':
                db.execute("INSERT INTO cognition_target(cognition_id,target_entity_id,perspective_entity_id) VALUES('fact','named',NULL)")
            else:
                db.execute("INSERT INTO relationship(id,world_id,source_entity_id,target_entity_id,relation_type,content,formed_by,confidence,cred_status,created_at,updated_at) "
                           "VALUES('relation',?,'named','named','likes','synthetic topic.','stated',600,'limited','2026-01-01','2026-01-01')", (_OWNER,))
                db.execute("INSERT INTO relationship_evidence(relationship_id,evidence_id,relation) VALUES('relation','source-fact','support')")
                db.execute("DELETE FROM cognition_evidence WHERE cognition_id='fact'")
                db.execute("DELETE FROM cognition WHERE id='fact'")
        db.commit()
        before = db.total_changes
        result = recall_world_snapshot(db, _OWNER, 'synthetic topic', lang='en')
        legacy = recall_world_snapshot(db, _OWNER, 'synthetic topic')
        assert result is not None and legacy is not None and result.count == 1
        assert result.selected_item_ids == legacy.selected_item_ids
        assert result.currentness_digest == legacy.currentness_digest
        assert not _CJK.search(result.rendered_recall), result.rendered_recall
        if kind == 'targeted':
            assert result.rendered_recall.endswith('Atlas: synthetic topic.')
            assert legacy.rendered_recall == '记忆：Atlas：synthetic topic.'
        assert db.total_changes == before
    finally:
        db.close()


def test_source_cjk_that_looks_like_the_generated_prefix_stays_verbatim(tmp_path: Path) -> None:
    path = tmp_path / 'world.sqlite'
    claim = CLAIM + ' The quoted note says 确认依据中的助手原话：原样。'
    _confirmed_world(path, claim)
    preview = cast(dict[str, Any], QueryService(path, subject_id='owner', lang='en').preview_recall('coffee')['preview'])
    assert preview['rendered_recall'].endswith("\nAssistant's words the user confirmed: " + claim)
    assert preview['rendered_recall'].count('确认依据中的助手原话：') == 2, 'only the two source copies keep the literal Chinese wording'
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute('SELECT content FROM cognition').fetchone()[0] == claim


def test_configured_english_history_context_has_no_generated_chinese(tmp_path: Path) -> None:
    runtime = DshMemoWeftRuntime()
    runtime.initialize('copy', dsh_home=str(tmp_path), subject_id=_OWNER, model_tier='local', auto_route=False, lang='en')
    try:
        runtime.ingest_durable_boundary(_boundary('coffee-session', 'c', [
            {'role': 'user', 'content': 'Please suggest a coffee reminder.', 'message_id': 'request', 'timestamp': 1788890000.0},
            {'role': 'assistant', 'content': CLAIM, 'message_id': 'suggestion', 'timestamp': 1788890001.0},
        ]))
        result = runtime.query_interactions(projection='history', conversation_id='coffee-session', user_message_id='request')
        text = str(result['rendered_context'])
        assert CLAIM in text and 'Please suggest a coffee reminder.' in text
        assert not _CJK.search(text), text
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('kind', ['recommendation', 'commitment', 'agreement', 'unknown'])
def test_english_history_commitment_and_role_copy_is_ascii(kind: str) -> None:
    commitment = SimpleNamespace(kind=kind, content='A verbatim commitment.', conversation_id='synthetic')
    items: list[dict[str, object]] = [{'conversation_id': 'synthetic', 'created_at': '2026-01-01',
        'turns': [{'role': 'user', 'content': 'A source sentence.'}, {'role': 'tool', 'content': 'A tool source.'},
                  {'role': 'assistant', 'content': 'A' * 170}]}]
    text = _render(items, [commitment], lang='en')
    assert 'A verbatim commitment.' in text and 'A source sentence.' in text and 'A tool source.' in text
    assert '...' in text
    assert not _CJK.search(text), text


@pytest.mark.parametrize('rejected', [False, True])
def test_english_correction_history_and_pending_notices_have_no_generated_cjk(tmp_path: Path, rejected: bool) -> None:
    path = tmp_path / 'world.sqlite'
    clock = MutableClock()
    old = 'synthetic coffee topic: caffeinated coffee.'
    _run(path, clock, [_model(_batch(_form(old, (0, len(old)))))], ('evidence-1',),
         lambda p: _set_evidence(p, 'evidence-1', old))
    assert _job(path)['state'] == 'applied'
    with closing(sqlite3.connect(path)) as db, db:
        prior = str(db.execute('SELECT id FROM cognition').fetchone()[0])
    correction = 'Correction: synthetic coffee topic should use decaf coffee.'
    item = _correct(correction, prior, (0, len(correction)))
    if rejected:
        item['statement_kind'] = 'invalid-synthetic-kind'
    _run(path, clock, [_model(_batch(item)), _model(_batch(item))], ('evidence-2',),
         lambda p: _set_evidence(p, 'evidence-2', correction), job_id='job-2')
    assert _job(path, job_id='job-2')['state'] == ('no_change' if rejected else 'applied')
    preview = cast(dict[str, Any], QueryService(path, subject_id='owner', lang='en').preview_recall('why synthetic coffee topic?')['preview'])
    assert preview['count'] >= 1
    assert not _CJK.search(preview['rendered_recall']), preview['rendered_recall']
    assert not _CJK.search(json.dumps(preview['pending_corrections'], ensure_ascii=False))
    if rejected:
        assert preview['pending_corrections'] and preview['pending_corrections'][0]['text'] == correction
    else:
        assert 'Replaced understanding (no longer current):' in preview['rendered_recall']
