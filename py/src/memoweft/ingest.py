"""通用观察摄入，将 Observation 转为 origin_id 幂等的 observed 证据。

授权(隐私默认本地):observed 证据默认 { local:True, cloud:False, inference:True };显式 > 默认。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import CONFIG, Config
from .store.evidence import SqliteEvidenceStore
from .types import Evidence, EvidenceInput


@dataclass(slots=True)
class Observation:
    kind: str
    occurred_at: str
    content: str
    origin_id: Optional[str] = None
    allow_local_read: Optional[bool] = None
    allow_cloud_read: Optional[bool] = None
    allow_inference: Optional[bool] = None


@dataclass(slots=True)
class IngestResult:
    stored: list[Evidence]
    skipped: int


def ingest_observations(
    subject_id: str,
    observations: list[Observation],
    evidence_store: SqliteEvidenceStore,
    *,
    host_id: Optional[str] = None,
    cfg: Config = CONFIG,
) -> IngestResult:
    """批量摄入 observed 证据；origin_id 提供幂等性，已存在项计入 skipped。"""
    hid = host_id if host_id is not None else cfg.identity.host_id
    d = cfg.observed_defaults
    stored: list[Evidence] = []
    skipped = 0
    for obs in observations:
        # 幂等:带 origin_id 且已存在 → 跳过、计 skipped。
        if obs.origin_id and evidence_store.find_by_origin(obs.origin_id) is not None:
            skipped += 1
            continue
        ev = evidence_store.put(
            EvidenceInput(
                subject_id=subject_id,
                source_kind="observed",
                host_id=hid,
                origin_id=obs.origin_id,
                occurred_at=obs.occurred_at,
                raw_content=obs.content,
                # 显式授权优先；此处传入 observed 的保守默认值，与 put 的默认策略保持一致。
                allow_local_read=obs.allow_local_read if obs.allow_local_read is not None else d.allow_local_read,
                allow_cloud_read=obs.allow_cloud_read if obs.allow_cloud_read is not None else d.allow_cloud_read,
                allow_inference=obs.allow_inference if obs.allow_inference is not None else d.allow_inference,
            )
        )
        stored.append(ev)
    return IngestResult(stored=stored, skipped=skipped)
