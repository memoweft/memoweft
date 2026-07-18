"""与 TypeScript KeywordRetriever 共享契约的 FTS5 trigram + bm25 关键词召回。

shared/parity/fts.json 验证 node:sqlite 与 CPython sqlite3 在包含 CJK 的语料上具有相同 DDL、查询和排序语义。
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .driver import FtsUnavailableError

_ALLOWED_TOKENIZERS = frozenset({"trigram", "unicode61"})
# FTS5 语法元字符（双引号、前缀星、括号、列限定冒号、NEAR 脱字号）；规范化时全部移除。
_FTS5_SYNTAX = re.compile(r'["*():^]')
_WS = re.compile(r"\s+")


def to_match_query(query: str) -> str:
    """把用户 query 消毒成安全 FTS5 MATCH 串(去掉元字符→按空白切 term→每 term 双引号包成短语→OR 连接)。

    全空白或全元字符输入返回空串，调用方据此返回 []；语义与 keywordRetriever.ts 一致。
    """
    cleaned = _FTS5_SYNTAX.sub(" ", query)
    terms = [t for t in _WS.split(cleaned) if t]
    phrases = ['"' + t.replace('"', '""') + '"' for t in terms]
    return " OR ".join(phrases)


@dataclass(frozen=True, slots=True)
class Hit:
    id: str
    score: float  # 正向(-bm25),越大越相关(与向量余弦口径一致)


class KeywordRetriever:
    """FTS5 trigram 关键词检索器（与 KeywordRetriever 对齐；支持 create/index/search）。"""

    def __init__(self, db: sqlite3.Connection, tokenizer: str = "trigram") -> None:
        if tokenizer not in _ALLOWED_TOKENIZERS:
            raise ValueError(f"未知的 FTS5 tokenizer:{tokenizer}(仅支持 trigram / unicode61)")
        self._db = db
        try:
            db.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS cognition_fts USING fts5("
                f"cognition_id UNINDEXED, text, tokenize='{tokenizer}')"
            )
        except sqlite3.OperationalError as e:
            raise FtsUnavailableError(
                f"当前 SQLite 未编译 FTS5,关键词召回不可用(tokenize='{tokenizer}')。"
            ) from e

    def index_all(self, items: list[tuple[str, str]]) -> None:
        """替换式建索引(空集 = 清空)。items = [(cognition_id, text)...]。"""
        self._db.execute("DELETE FROM cognition_fts")
        if items:
            self._db.executemany("INSERT INTO cognition_fts (cognition_id, text) VALUES (?, ?)", items)

    def search(self, query: str, top_k: int = 5) -> list[Hit]:
        """召回:query 消毒成 MATCH 串 → bm25 升序(最相关在前)。空 query / 空 MATCH → []。"""
        if not query.strip():
            return []
        match = to_match_query(query)
        if not match:
            return []
        return self.search_match(match, top_k)

    def search_match(self, match: str, top_k: int = 10) -> list[Hit]:
        """用已成形的 MATCH 串直接查(供 parity golden 用预成形 match)。"""
        rows = self._db.execute(
            "SELECT cognition_id, bm25(cognition_fts) AS rank FROM cognition_fts "
            "WHERE cognition_fts MATCH ? ORDER BY rank LIMIT ?",
            (match, top_k),
        ).fetchall()
        return [Hit(id=r[0], score=-r[1]) for r in rows]
