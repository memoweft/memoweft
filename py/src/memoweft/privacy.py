"""与 TypeScript evidence privacy 契约保持一致的隐私读取边界。

filter_readable_by_tier:从提供给当前模型的材料中排除该 tier 无权读取的证据。
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
    """按 tier 保留允许读取的项并维持顺序；未指定 tier 时使用保守的 cloud 默认值。"""
    if tier == "local":
        return [e for e in items if e.allow_local_read]
    return [e for e in items if e.allow_cloud_read]


class _CloudReadable(Protocol):
    @property
    def allow_cloud_read(self) -> bool: ...


_C = TypeVar("_C", bound=_CloudReadable)


def filter_cloud_readable(items: list[_C]) -> list[_C]:
    """保留用于兼容的已弃用别名，等价于 tier='cloud'。"""
    return [e for e in items if e.allow_cloud_read]
