"""expire 纯规则 parity:Python 建同一认知集 → 过期结果与 TS(shared/parity/expire.json)一致。

钉 ageDays 严格 > 阈值 + 名单外永不过期 + 归档 active 排除不碰(P2-2,写路径第一个逐位对拍绿灯)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from conftest import parity

from memoweft.clock import parse_iso_ms, to_iso_z
from memoweft.expire import expire
from memoweft.store import open_db
from memoweft.store.cognition import SqliteCognitionStore
from memoweft.types import Cognition, ContentType

NOW = "2026-02-01T00:00:00.000Z"


def _days_ago(d: int) -> str:
    # 复刻 TS daysAgo:new Date(now - d*DAY).toISOString();整天差 → 整秒,与 TS 逐位一致。
    now_ms = parse_iso_ms(NOW)
    dt = datetime.fromtimestamp((now_ms - d * 86_400_000) / 1000.0, tz=timezone.utc)
    return to_iso_z(dt)


def _now_dt() -> datetime:
    return datetime.fromtimestamp(parse_iso_ms(NOW) / 1000.0, tz=timezone.utc)


def _cog(id: str, content_type: ContentType, updated_at: str, *, archived_at: Optional[str] = None) -> Cognition:
    return Cognition(
        id=id, subject_id="owner", content=f"内容 {id}", content_type=content_type, formed_by="stated",
        confidence=300, cred_status="low", scope=None, valid_at=None, invalid_at=None,
        asked_at=None, archived_at=archived_at, muted_at=None, created_at=updated_at, updated_at=updated_at,
    )


def _seed(store: SqliteCognitionStore) -> None:
    cogs = [
        _cog("s-fresh", "state", _days_ago(3)),
        _cog("s-boundary", "state", _days_ago(7)),
        _cog("s-old", "state", _days_ago(8)),
        _cog("h-boundary", "hypothesis", _days_ago(14)),
        _cog("h-old", "hypothesis", _days_ago(15)),
        _cog("t-old", "trend", _days_ago(31)),
        _cog("f-old", "fact", _days_ago(100)),
        _cog("p-old", "preference", _days_ago(100)),
        _cog("s-arch", "state", _days_ago(30), archived_at=_days_ago(1)),
    ]
    for c in cogs:
        store.insert(c, [])


def test_expire_matches_ts() -> None:
    want: Any = parity("expire.json")
    db = open_db(":memory:")
    try:
        store = SqliteCognitionStore(db)
        _seed(store)
        expired = expire("owner", store, _now_dt())
        assert expired == want["expired"]
        invalid_ids = sorted(c.id for c in store.all("owner") if c.invalid_at is not None)
        assert invalid_ids == want["invalidIds"]
    finally:
        db.close()


def test_expire_sets_now_iso_and_idempotent() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteCognitionStore(db)
        store.insert(_cog("s", "state", _days_ago(10)), [])
        assert expire("owner", store, _now_dt()) == 1
        c = store.get("s")
        assert c is not None and c.invalid_at == NOW  # 标成 now 的 ISO
        # 幂等:已 invalid → active 排除 → 第二次 0
        assert expire("owner", store, _now_dt()) == 0
    finally:
        db.close()
