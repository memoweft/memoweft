"""Windows #522: a new preference quote must reach the first new conversation."""
from pathlib import Path
import json
import sqlite3
from typing import Any
import pytest

from test_recent_evidence import _runtime, _ingest, _recent, _db_path, _subject
from memoweft.integrations.hermes.batch_adapter import HermesBatchAdapterProcessor
from memoweft.integrations.hermes.world_worker import WorldJobWorker
from memoweft.integrations.trust.query_service import QueryService
from test_hermes_batch_adapter_v5 import _batch, _model


def test_new_session_preference_question_sees_pending_changed_value(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, '记住我喜欢喝红茶', 'a', 'first')
        def route(messages: Any, *, session_id: str) -> dict[str, object]:
            payload = json.loads(messages[-1]['content'])
            e = payload['evidence'][0]
            item = {'action': 'form', 'target': 'owner_self', 'statement_kind': 'preference',
                    'formed_by': 'stated', 'proposition': e['text'],
                    'supports': [{'evidence_id': e['id'], 'sentence_id': 't0'}]}
            if '绿茶' in e['text']:
                item['supersedes_cognition_id'] = payload['current_cognitions'][0]['id']
            return _model(_batch(item))
        worker = WorldJobWorker(_db_path(runtime), processor=HermesBatchAdapterProcessor(
            str(_db_path(runtime)), route, model_tier='local'))
        assert worker.run_until_quiescent() == 1
        query = QueryService(_db_path(runtime), subject_id=_subject(runtime))
        assert '红茶' in str(query.preview_recall('我喜欢喝什么？')['preview'])
        _ingest(runtime, '我其实更喜欢绿茶', 'b', 'correction')
        rows = _recent(runtime, '我喜欢喝什么？')
        assert any(row['text'] == '我其实更喜欢绿茶' for row in rows)
        # Nomination changes neither the formal old fact nor its revision. Once
        # the real compiler commits the successor, only the formal green remains.
        assert query.preview_recall('我喜欢喝什么？')['world_revision'] == 1
        assert worker.run_until_quiescent() == 1
        assert _recent(runtime, '我喜欢喝什么？') == []
        result = query.preview_recall('我喜欢喝什么？')
        assert result['world_revision'] == 2
        assert '绿茶' in str(result['preview']) and '红茶' not in str(result['preview'])
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('quote,query', [
    ('我其实更喜欢绿茶', '我喜欢喝什么？'),
    ('我现在偏好步行，坐车容易晕。', '我偏好什么出行方式？'),
    ('我不喜欢嘈杂的环境。', '我喜欢什么样的环境？'),
    ('I actually prefer cycling.', 'What do I prefer for commuting?'),
    ("I do not like noisy rooms.", 'What are my preferences?'),
])
def test_pending_self_preference_is_verbatim_context_not_a_formed_fact(tmp_path: Path, quote: str, query: str) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, quote)
        rows = _recent(runtime, query)
        assert [row['text'] for row in rows] == [quote]
        assert all('preceding_text' not in row and 'correction_status' not in row for row in rows)
        assert _recent(runtime, query, subject='other-account') == []
        with sqlite3.connect(_db_path(runtime)) as db:
            assert db.execute('SELECT count(*) FROM cognition').fetchone()[0] == 0
            db.execute('UPDATE evidence SET allow_cloud_read=0')
        assert _recent(runtime, query, tier='cloud') == []
        with sqlite3.connect(_db_path(runtime)) as db:
            db.execute("UPDATE evidence SET deleted_at='2026-10-19T00:00:00Z'")
        assert _recent(runtime, query) == []
    finally:
        runtime.shutdown()


@pytest.mark.parametrize('quote,query', [
    ('我姐姐其实更喜欢绿茶', '我喜欢喝什么？'),
    ('我喜欢喝什么？', 'What are my preferences?'),
    ('请翻译：我其实更喜欢绿茶', '我的偏好是什么？'),
    ('我以前喜欢绿茶', '我喜欢喝什么？'),
    ('我其实更喜欢绿茶', '你好'),
    ('我其实更喜欢绿茶', '妈妈喜欢喝什么？'),
    ('My sister prefers green tea.', 'What are my preferences?'),
    ('What do I prefer?', 'What are my preferences?'),
])
def test_preference_fallback_does_not_nominate_other_people_questions_or_tasks(
    tmp_path: Path, quote: str, query: str,
) -> None:
    runtime = _runtime(tmp_path)
    try:
        _ingest(runtime, quote)
        assert _recent(runtime, query) == []
    finally:
        runtime.shutdown()
