"""parse_json_object_with_repair:重试逻辑 + 首过严格(库不抢救 NaN)+ 日志隐私(不泄原文)。"""
from __future__ import annotations

import time
from typing import Optional

from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.llm.json_repair import extract_json_object, parse_json_object_with_repair
from memoweft.types import ModelTier


class _StubLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._call_count = 0

    def chat(self, messages: list[ChatMessage]) -> str:
        r = self._replies[self._call_count]
        self._call_count += 1
        return r

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def tier(self) -> Optional[ModelTier]:
        return None

    @property
    def usage(self) -> Optional[UsageStats]:
        return None


def _msgs() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="x")]


def test_first_ok_no_retry() -> None:
    llm = _StubLLM(['{"a":1}'])
    logs: list[str] = []
    r = parse_json_object_with_repair(llm, _msgs(), log=logs.append)
    assert r == {"a": 1}
    assert llm.call_count == 1  # 首过成功不重试
    assert logs == []


def test_retry_then_ok() -> None:
    llm = _StubLLM(["不是 JSON", '{"ok":1}'])
    logs: list[str] = []
    r = parse_json_object_with_repair(llm, _msgs(), log=logs.append, lang="en")
    assert r == {"ok": 1}
    assert llm.call_count == 2  # 首坏必重试
    assert len(logs) == 1


def test_two_fails_returns_none() -> None:
    llm = _StubLLM(["坏", "还是坏"])
    logs: list[str] = []
    r = parse_json_object_with_repair(llm, _msgs(), log=logs.append, lang="zh")
    assert r is None
    assert llm.call_count == 2
    assert len(logs) == 2


def test_nan_forces_retry_library_does_not_rescue() -> None:
    # '{"a":NaN}' 首过 parse 失败(parse_constant 拒 NaN)→ 触发重试。
    # 保持严格 JSON 语义：若第三方修复库把 NaN 转成 null，重试次数会从 2 变成 1。
    llm = _StubLLM(['{"a":NaN}', "still bad"])
    logs: list[str] = []
    r = parse_json_object_with_repair(llm, _msgs(), log=logs.append, lang="en")
    assert r is None
    assert llm.call_count == 2  # 首坏必重试(库未抢救)
    # 日志只记结构特征,不泄模型原文(隐私)。
    assert all("NaN" not in m and "still bad" not in m for m in logs)


def test_extract_json_object_no_redos_on_unterminated_fence() -> None:
    # 病理输入:超长「开围栏 + 无闭围栏」。回溯型正则会 O(n^2) 退化(ReDoS);线性扫描毫秒级返回。
    # 断言正确性(无对象 → None)+ 宽松耗时上界,防未来有人把线性扫描改回正则时无声回归。与 TS 同型。
    raw = "解释\n```json\n" + "x" * 200_000
    start = time.perf_counter()
    result = extract_json_object(raw)
    elapsed = time.perf_counter() - start
    assert result is None
    assert elapsed < 1.0, f"解析耗时 {elapsed:.3f}s,疑似回退到回溯型正则"
