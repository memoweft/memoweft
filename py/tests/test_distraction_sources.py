"""Frozen interference audit and real boundary/worker/recall source checks."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any, Literal

import pytest

from memoweft.integrations.dsh_bridge import DshMemoWeftRuntime
from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from test_dsh_interactions import _boundary
from test_hermes_batch_adapter_v5 import _batch, _model


FIXTURE: dict[str, Any] = json.loads(
    (Path(__file__).parent / 'fixtures/distraction-exam.json').read_text(encoding='utf-8')
)


def _exercise(tmp_path: Path, user: str, assistant: str, quote: str, *,
              lang: Literal['en', 'zh'], confirmed: bool = False,
              assistant_support: bool = False) -> tuple[str, list[tuple[str, str]], str]:
    runtime = DshMemoWeftRuntime()
    runtime.initialize('audit', dsh_home=str(tmp_path), subject_id='owner',
                       auto_route=False, model_tier='local', lang=lang)
    try:
        # Both speakers cross the real durable-ingest boundary. Only the user
        # may become Evidence; the assistant is persisted as role-tagged context.
        receipt = runtime.ingest_durable_boundary(_boundary('audit', 'a', [
            {'role': 'assistant', 'content': assistant, 'message_id': 'assistant-a'},
            {'role': 'user', 'content': user, 'message_id': 'user-a'},
        ]))
        assert runtime.db_path is not None
        path = runtime.db_path
        with sqlite3.connect(path) as db:
            evidence = db.execute('SELECT id,raw_content FROM evidence').fetchall()
        assert len(evidence) == 1 and evidence[0][1] == user
        item: dict[str, Any] = dict(action='form', statement_kind='attribute',
            formed_by='confirmed' if confirmed else 'stated', proposition='placeholder',
            supports=[dict(evidence_id=evidence[0][0], quote=assistant if assistant_support else quote)])
        if confirmed:
            item['assistant_claim'] = assistant

        def route(*args: Any, **kwargs: Any) -> dict[str, object]:
            # Keep recommending form on the existing compiler rewrite, too.
            # A scripted no_change must never masquerade as a compiler guard.
            return _model(_batch(item))

        worker = WorldJobWorker(path, processor=HermesBatchAdapterProcessor(
            str(path), route, lang=lang, model_tier='local'))
        assert worker.run_until_quiescent() == 1
        with sqlite3.connect(path) as db:
            row = db.execute('SELECT state,world_result_json FROM memory_world_job WHERE job_id=?', (receipt['job_id'],)).fetchone()
            assert row is not None
            rows = db.execute('SELECT id,content FROM cognition').fetchall()
            assert db.execute('SELECT raw_content FROM evidence').fetchall() == [(user,)]
            assert db.execute('SELECT count(*) FROM world_event').fetchone() == (0,)
            before = list(db.iterdump())
            snapshot = recall_world_snapshot(db, 'owner', assistant if confirmed else quote, lang=lang)
            assert list(db.iterdump()) == before
            if rows:
                assert snapshot is not None
                assert snapshot.selected_item_ids == (('cognition', rows[0][0]),)
                assert rows[0][1] in snapshot.rendered_recall
                assert db.execute('SELECT count(*) FROM cognition_evidence WHERE relation=\'support\'').fetchone() == (1,)
            else:
                assert snapshot is None or not snapshot.selected_item_ids
            return str(row[0]), rows, snapshot.rendered_recall if snapshot else ''
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: str(c['id']))
@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_exam_user_fact_survives_assistant_distraction(
    tmp_path: Path, case: dict[str, Any], lang: Literal['en', 'zh'],
) -> None:
    source = case[lang]
    state, rows, rendered = _exercise(tmp_path, source['user'], source['assistant'],
                                      source['quote'], lang=lang)
    # Four pre-existing source-policy limitations are recorded explicitly;
    # this confirmation fix must neither claim to fix nor silently relabel them.
    assert state == source['baseline_state']
    if state == 'no_change':
        assert rows == []
        return
    assert len(rows) == 1
    assert source['assistant'] not in rendered
    # Exact expected content, including the existing Chinese first-person form.
    expected = source['quote']
    if lang == 'zh':
        expected = '用户' + (expected[1:] if expected.startswith('我') else expected)
    assert rows[0][1] == expected
    assert expected in rendered


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: str(c['id']))
@pytest.mark.parametrize('lang', ['en', 'zh'])
def test_exam_assistant_cannot_be_used_as_user_support(
    tmp_path: Path, case: dict[str, Any], lang: Literal['en', 'zh'],
) -> None:
    source = case[lang]
    state, rows, _ = _exercise(tmp_path, source['user'], source['assistant'],
                              source['quote'], lang=lang, assistant_support=True)
    assert state == 'no_change'
    assert rows == []


# These are NEW adversarial variants, not claims about the frozen exam's
# interpreter output. The old arbitrary two-character overlap accepts them.
@pytest.mark.parametrize(('lang', 'user', 'assistant'), [
    ('en', 'I like tea.', 'You live in Oslo.'),
    ('zh', '我喜欢茶。', '你喜欢咖啡。'),
    ('en', 'No, I live in Lima.', 'You live in Oslo.'),
    ('zh', '我喜欢金毛犬。', '你喜欢尝试非传统宠物。'),
    ('en', 'I enjoy art.', 'You enjoy extreme sports.'),
    ('zh', '我认为电影让我放松。', '你认为电影应该带来脑力挑战。'),
])
def test_unconfirmed_statement_cannot_import_assistant_claim(
    tmp_path: Path, lang: Literal['en', 'zh'], user: str, assistant: str,
) -> None:
    state, rows, _ = _exercise(tmp_path, user, assistant, user, lang=lang, confirmed=True)
    assert state == 'no_change'
    assert rows == []


# Cropping a bare affirmative must not erase the rest of the user's reply.
@pytest.mark.parametrize(('lang', 'user', 'assistant', 'quote'), [
    ('en', 'Yes, not Oslo.', 'You live in Oslo.', 'Yes'),
    ('zh', '是的，不是咖啡。', '你喜欢咖啡。', '是的'),
    ('en', 'Yes, tea.', 'You enjoy team sports.', 'Yes, tea.'),
    ('zh', '对，就是金毛犬。', '你喜欢非传统宠物。', '对，就是金毛犬。'),
])
def test_confirmation_cannot_crop_denial_or_use_partial_value(
    tmp_path: Path, lang: Literal['en', 'zh'], user: str, assistant: str, quote: str,
) -> None:
    state, rows, _ = _exercise(tmp_path, user, assistant, quote, lang=lang, confirmed=True)
    assert state == 'no_change'
    assert rows == []


@pytest.mark.parametrize(('lang', 'user', 'assistant'), [
    ('en', 'Yes.', 'You like jasmine tea.'),
    ('en', 'Correct!', 'You like jasmine tea.'),
    ('en', 'Yes, jasmine tea.', 'You like jasmine tea.'),
    ('en', 'Yes, TEA.', 'You like tea.'),
    ('en', 'Yes, do that.', 'When you want coffee, order decaf.'),
    ('zh', '对。', '你开的是小鹏G6。'),
    ('zh', '没错。', '你开的是小鹏G6。'),
    ('zh', '对，就是G6。', '你开的是小鹏G6。'),
    ('zh', '是的，正是茉莉花茶。', '你喜欢茉莉花茶。'),
])
def test_explicit_confirmations_keep_the_proposal_and_user_evidence(
    tmp_path: Path, lang: Literal['en', 'zh'], user: str, assistant: str,
) -> None:
    state, rows, _ = _exercise(tmp_path, user, assistant, user, lang=lang, confirmed=True)
    assert state == 'applied'
    assert len(rows) == 1
