"""FTS5 trigram parity:同数据同 MATCH 查询,CPython 的 bm25 排序与 TS(shared/parity/fts.json)一致。

只校验 id 排序；bm25 分数可能随 SQLite 小版本变化，但两种实现的排序应保持一致。
"""
from __future__ import annotations

from typing import Any

from conftest import parity

from memoweft.store import KeywordRetriever, fts5_available, open_db


def test_fts5_available() -> None:
    db = open_db(":memory:")
    try:
        assert fts5_available(db), "CPython stdlib sqlite3 应带 FTS5(见 FTS5 parity spike)"
    finally:
        db.close()


def test_fts_trigram_ordering_matches_ts() -> None:
    golden: dict[str, Any] = parity("fts.json")
    db = open_db(":memory:")
    try:
        kr = KeywordRetriever(db, tokenizer="trigram")
        kr.index_all([(row[0], row[1]) for row in golden["data"]])
        for case in golden["cases"]:
            got = [h.id for h in kr.search_match(case["match"], top_k=10)]
            assert got == case["ids"], f"FTS 排序分叉 @ MATCH {case['match']!r}: got {got}, want {case['ids']}"
    finally:
        db.close()
