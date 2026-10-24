"""Sparse synthetic memory must not turn weak similarity into context."""
from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Sequence

import pytest

from memoweft.integrations.hermes.recall import recall_world_snapshot
from memoweft.semantic.config import SemanticConfig
from memoweft.semantic.runtime import SemanticCandidate
from memoweft.semantic.store import content_hash
from test_semantic_recall import fixture


class SparseProvider:
    """Controlled encoder scores; exercise the production recall and quote gates."""
    def __init__(self) -> None:
        self.config = SemanticConfig.from_env()

    def candidates(self, db: sqlite3.Connection, subject_id: str, query: str) -> Sequence[SemanticCandidate]:
        return tuple(SemanticCandidate('cognition', str(i), self.score(query, str(text)), content_hash(str(text)))
                     for i, text in db.execute('SELECT id,content FROM cognition'))

    @staticmethod
    def score(query: str, text: str) -> float:
        return 0.94 if '季节' in query and '秋天' in text else 0.80

    def score_texts(self, query: str, texts: Sequence[str]) -> Sequence[float]:
        return [self.score(query, text) for text in texts]


@pytest.mark.parametrize('query', ['你好', '您好', 'Hello', '早上好', '今天怎样'])
def test_sparse_unrelated_memories_do_not_fill_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, query: str) -> None:
    monkeypatch.delenv('MEMOWEFT_SEMANTIC_THRESHOLD', raising=False)
    monkeypatch.setenv('MEMOWEFT_SEMANTIC_ENABLED', '1')
    path = tmp_path / 'sparse.sqlite3'
    fixture.seed(path, {'memories': [
        {'id': 'autumn', 'content': '我喜欢秋天。'},
        {'id': 'tea', 'content': '我喜欢茉莉花茶。'},
        {'id': 'hobby', 'content': '我周末常画水彩。'},
    ]})
    with closing(sqlite3.connect(path)) as db:
        before = db.total_changes
        unrelated = recall_world_snapshot(db, 'fictional-owner', query, candidate_provider=SparseProvider())
        assert unrelated is not None
        assert unrelated.selected_item_ids == ()
        assert unrelated.rendered_recall == ''
        relevant = recall_world_snapshot(db, 'fictional-owner', '我喜欢什么季节', candidate_provider=SparseProvider())
        assert relevant is not None
        assert ('cognition', 'autumn') in relevant.selected_item_ids
        assert '秋天' in relevant.rendered_recall
        assert '用户原话' in relevant.rendered_recall
        assert db.total_changes == before
