"""与 TypeScript portable model 契约一致的便携包常量与形状。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

#: 便携包格式标记。
BUNDLE_FORMAT = "memoweft-bundle"
#: 便携包结构版本；v2 新增 interactionContexts 与 semanticResolutions，
#: v3 新增 2.0 World 对象（entities / relationships / worldEvents 及其
#: provenance 链接、cognitionTargets），v2 bundle 仍按旧结构原样可导。
BUNDLE_SCHEMA_VERSION = 3

#: 导入模式:dryRun 只算不写 / merge 实际写入。
ImportMode = Literal["dryRun", "merge"]


@dataclass(slots=True)
class ImportCounts:
    """将写入(dryRun)/ 已写入(merge)的条数。"""

    evidence: int = 0
    events: int = 0
    cognitions: int = 0
    event_evidence: int = 0
    cognition_evidence: int = 0
    interaction_contexts: int = 0
    semantic_resolutions: int = 0
    entities: int = 0
    relationships: int = 0
    world_events: int = 0
    relationship_evidence: int = 0
    world_event_evidence: int = 0
    cognition_targets: int = 0


@dataclass(slots=True)
class ImportDuplicates:
    """按 id(或 originId)判重跳过的条数。"""

    evidence: int = 0
    events: int = 0
    cognitions: int = 0
    entities: int = 0
    relationships: int = 0
    world_events: int = 0


@dataclass(slots=True)
class ImportPlan:
    """导入计划/结果(对齐 model.ts 的 ImportPlan)。"""

    mode: ImportMode
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    counts: ImportCounts = field(default_factory=ImportCounts)
    duplicates: ImportDuplicates = field(default_factory=ImportDuplicates)
