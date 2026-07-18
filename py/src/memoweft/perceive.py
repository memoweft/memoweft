"""感知 —— 移植自 src/pipeline/perceive.ts。把原始输入包装成 EvidenceInput(默认 spoken,不落库)。

授权默认全下沉到 evidence.put(按 source_kind 分流),perceive 不判授权。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import CONFIG, Config
from .types import EvidenceInput, SourceKind


@dataclass(slots=True)
class PerceiveOptions:
    subject_id: Optional[str] = None
    host_id: Optional[str] = None
    source_kind: Optional[SourceKind] = None
    origin_id: Optional[str] = None
    occurred_at: Optional[str] = None


def perceive(raw_content: str, opts: Optional[PerceiveOptions] = None, cfg: Config = CONFIG) -> EvidenceInput:
    """对齐 perceive.ts:16-26。subject_id/host_id 缺省取 cfg.identity;source_kind 缺省 spoken。"""
    o = opts if opts is not None else PerceiveOptions()
    return EvidenceInput(
        subject_id=o.subject_id if o.subject_id is not None else cfg.identity.subject_id,
        host_id=o.host_id if o.host_id is not None else cfg.identity.host_id,
        source_kind=o.source_kind if o.source_kind is not None else "spoken",
        origin_id=o.origin_id,
        occurred_at=o.occurred_at,
        raw_content=raw_content,
        # summary 留空:存储层补成 raw_content(v1)
    )
