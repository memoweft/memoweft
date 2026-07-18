"""自然过期 —— 移植自 src/background/expire.ts。

临时类(state/hypothesis/trend)久没被印证 → 标 invalid_at(保留可溯源、不再召回);
稳定类(fact/preference 等,不在 expire_after_days 名单)永不自动失效。纯规则、无 LLM。
归档由 active() 排除(归档要保住可恢复,不给标失效)。
"""
from __future__ import annotations

from datetime import datetime

from .clock import epoch_ms, parse_iso_ms, to_iso_z
from .config import CONFIG, Config
from .store.cognition import SqliteCognitionStore
from .types import CognitionPatch

_DAY_MS = 86_400_000


def expire(subject_id: str, store: SqliteCognitionStore, now: datetime, cfg: Config = CONFIG) -> int:
    """把临时类里超过期阈值(严格 >)的认知标 invalid_at。稳定类不动。返回过期条数。对齐 expire.ts:28-43。"""
    thresholds = cfg.expire_after_days
    now_ms = epoch_ms(now)
    now_iso = to_iso_z(now)
    expired = 0
    for c in store.active(subject_id):
        days = thresholds.get(c.content_type)
        if days is None:
            continue  # 不在过期名单 → 永不自动失效(明确偏好/事实)
        age_days = (now_ms - parse_iso_ms(c.updated_at)) / _DAY_MS
        if age_days > days:
            store.update(c.id, CognitionPatch(invalid_at=now_iso))
            expired += 1
    return expired
