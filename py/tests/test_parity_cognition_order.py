"""cognition all/active 排序 parity:Python insert 同一认知集 → id 序与 TS golden 一致。

验证 ORDER BY confidence DESC, created_at ASC，并确保 active 排除 invalid/archived。
"""
from __future__ import annotations

from typing import Any, Optional

from conftest import parity

from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.types import Cognition


def _cog(
    id: str,
    confidence: int,
    created_at: str,
    *,
    invalid_at: Optional[str] = None,
    archived_at: Optional[str] = None,
) -> Cognition:
    return Cognition(
        id=id,
        subject_id="owner",
        content=f"内容 {id}",
        content_type="preference",
        formed_by="stated",
        confidence=confidence,
        cred_status="limited",
        scope=None,
        valid_at=None,
        invalid_at=invalid_at,
        asked_at=None,
        archived_at=archived_at,
        muted_at=None,
        created_at=created_at,
        updated_at=created_at,
    )


def test_cognition_order_matches_ts() -> None:
    want: Any = parity("cognition-order.json")
    db = open_db(":memory:")
    try:
        store = SqliteCognitionStore(db)
        cogs = [
            _cog("c1", 600, "2026-01-01T00:00:01.000Z"),
            _cog("c2", 600, "2026-01-01T00:00:00.000Z"),  # 同分、created_at 更早 → 排 c1 之前
            _cog("c3", 800, "2026-01-01T00:00:05.000Z"),  # 最高分 → 最前
            _cog("c4", 300, "2026-01-01T00:00:00.000Z", invalid_at="2026-01-02T00:00:00.000Z"),
            _cog("c5", 500, "2026-01-01T00:00:00.000Z", archived_at="2026-01-02T00:00:00.000Z"),
        ]
        for c in cogs:
            store.insert(c, [])
        assert [c.id for c in store.all("owner")] == want["all"]
        assert [c.id for c in store.active("owner")] == want["active"]
    finally:
        db.close()
