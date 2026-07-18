"""隐私读取门 —— 移植自 src/evidence/privacy.ts。

filter_readable_by_tier:把「当前模型 tier 不许读」的证据挡在喂给该模型的材料之外。
  cloud → 留 allow_cloud_read;local → 留 allow_local_read。缺省 cloud(最保守)。保序过滤。
⚠️ 只管读取权;是否可推画像是另一维 allow_inference,由 distill/consolidate/attribute 在本关之外另筛。
"""
from __future__ import annotations

from typing import Protocol, TypeVar

from .types import ModelTier


class _Readable(Protocol):
    # 用 @property(只读)—— frozen dataclass(Evidence)的只读字段才满足 Protocol 约束。
    @property
    def allow_cloud_read(self) -> bool: ...
    @property
    def allow_local_read(self) -> bool: ...


_T = TypeVar("_T", bound=_Readable)


def filter_readable_by_tier(items: list[_T], tier: ModelTier = "cloud") -> list[_T]:
    """按 tier 保留「允许读」的项;其余原样顺序保留。缺省 cloud(最保守)。对齐 privacy.ts:16-21。"""
    if tier == "local":
        return [e for e in items if e.allow_local_read]
    return [e for e in items if e.allow_cloud_read]


class _CloudReadable(Protocol):
    @property
    def allow_cloud_read(self) -> bool: ...


_C = TypeVar("_C", bound=_CloudReadable)


def filter_cloud_readable(items: list[_C]) -> list[_C]:
    """已弃用别名(= tier='cloud');保兼容。对齐 privacy.ts:27。"""
    return [e for e in items if e.allow_cloud_read]
