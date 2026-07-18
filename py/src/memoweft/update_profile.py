"""与 TypeScript updateProfile 管线保持行为一致的写路径更新入口。

把未整理的近期对话沉淀成事件 → 重算画像 → 对新现象归因 → 重建召回索引。
索引是读路径优化:嵌入器/检索器挂了**不该让已落库的画像更新失败**(index_error 记下、不回滚)。
“是否开口问”仍由宿主独立决定；MemoWeft 提供理解，宿主负责表达。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Protocol

from .attribute import AttributeResult, attribute
from .clock import Clock, system_clock, to_iso_z
from .config import CONFIG, Config
from .consolidate import ConsolidateResult, consolidate
from .distill import DistillResult, distill
from .llm.client import LLMClient
from .store.cognition import SqliteCognitionStore
from .store.event import SqliteEventStore
from .store.evidence import SqliteEvidenceStore
from .store.semantic_resolution import SqliteSemanticResolutionStore
from .store.transaction import Transaction
from .types import Lang


class Retriever(Protocol):
    """召回索引写面(updateProfile 只用 index_all;KeywordRetriever 结构性满足)。"""

    def index_all(self, items: list[tuple[str, str]]) -> None: ...


@dataclass(slots=True)
class UpdateProfileTimings:
    """各步耗时（ms），供性能诊断使用。"""

    distill_ms: float
    consolidate_ms: float
    attribute_ms: float
    index_ms: float
    total_ms: float


@dataclass(slots=True)
class UpdateProfileMetrics:
    profile_size: int
    prompt_chars: int


@dataclass(slots=True)
class UpdateProfileResult:
    distilled: DistillResult
    consolidated: ConsolidateResult
    attributed: AttributeResult
    indexed: int
    #: 索引重建失败原因;None = 成功。索引失败不回滚画像。
    index_error: Optional[str]
    metrics: UpdateProfileMetrics
    timings: UpdateProfileTimings


def update_profile(
    subject_id: str,
    *,
    evidence_store: SqliteEvidenceStore,
    event_store: SqliteEventStore,
    cognition_store: SqliteCognitionStore,
    retriever: Retriever,
    llm: LLMClient,
    semantic_resolution_store: Optional[SqliteSemanticResolutionStore] = None,
    transaction: Optional[Transaction] = None,
    cfg: Config = CONFIG,
    clock: Clock = system_clock,
    lang: Optional[Lang] = None,
) -> UpdateProfileResult:
    """依次执行 distill、consolidate、attribute，并重建召回索引。"""
    t0 = time.monotonic()
    distilled = distill(subject_id, evidence_store, event_store, llm, lang=lang, cfg=cfg)
    t1 = time.monotonic()
    consolidated = consolidate(
        subject_id,
        event_store=event_store, evidence_store=evidence_store, cognition_store=cognition_store, llm=llm,
        semantic_resolution_store=semantic_resolution_store, transaction=transaction,
        cfg=cfg, now_iso=to_iso_z(clock()), lang=lang,
    )
    t2 = time.monotonic()
    # 归因自动并入：内部自带节流（无现象或无原因时不调用模型）。
    attributed = attribute(
        subject_id, evidence_store=evidence_store, cognition_store=cognition_store, llm=llm, cfg=cfg, clock=clock, lang=lang
    )
    t3 = time.monotonic()
    # 重建索引：只索引未失效(active)且未静音(muted)的认知——静音仍参与画像演化，但不占检索槽。
    cogs = [c for c in cognition_store.active(subject_id) if c.muted_at is None]
    indexed = 0
    index_error: Optional[str] = None
    try:
        retriever.index_all([(c.id, c.content) for c in cogs])
        indexed = len(cogs)
    except Exception as e:  # 索引是读路径优化:失败不回滚画像
        index_error = str(e)
    t4 = time.monotonic()

    return UpdateProfileResult(
        distilled=distilled,
        consolidated=consolidated,
        attributed=attributed,
        indexed=indexed,
        index_error=index_error,
        metrics=UpdateProfileMetrics(profile_size=consolidated.profile_size, prompt_chars=consolidated.prompt_chars),
        timings=UpdateProfileTimings(
            distill_ms=(t1 - t0) * 1000.0,
            consolidate_ms=(t2 - t1) * 1000.0,
            attribute_ms=(t3 - t2) * 1000.0,
            index_ms=(t4 - t3) * 1000.0,
            total_ms=(t4 - t0) * 1000.0,
        ),
    )
