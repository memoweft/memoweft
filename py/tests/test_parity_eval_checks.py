""" eval 判定纯函数 parity:check_structural / parse_yes_no 与 TS(shared/parity/eval-checks.json)一致。

外加 harness 冒烟：run_scenario 用 stub llm 实际执行写路径 + score_gists 的 conflict 确定性硬判（不调 judge）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from conftest import parity

from memoweft.evaluation import Check, check_structural, parse_yes_no, run_scenario, score_gists
from memoweft.llm.client import ChatMessage, UsageStats
from memoweft.types import ModelTier


class _SeqLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._n = 0

    def chat(self, messages: list[ChatMessage]) -> str:
        r = self._replies[self._n] if self._n < len(self._replies) else "{}"
        self._n += 1
        return r

    @property
    def call_count(self) -> int:
        return self._n

    @property
    def tier(self) -> Optional[ModelTier]:
        return "cloud"

    @property
    def usage(self) -> Optional[UsageStats]:
        return None


class _NeverLLM(_SeqLLM):
    """judge 不该被调用时用它:一调就炸。"""

    def __init__(self) -> None:
        super().__init__([])

    def chat(self, messages: list[ChatMessage]) -> str:
        raise AssertionError("conflict 场景的 shouldForm 应走确定性硬判,不该调 judge")


def _dump(checks: list[Check]) -> list[dict[str, Any]]:
    return [{"name": c.name, "pass": c.passed, "detail": c.detail} for c in checks]


def test_check_structural_matches_ts() -> None:
    data: Any = parity("eval-checks.json")
    for case in data["checkStructural"]["cases"]:
        got = _dump(check_structural(case["input"]["scenario"], case["input"]["run"]))
        assert got == case["expected"], f"[{case['label']}] checkStructural 分叉"


def test_parse_yes_no_matches_ts() -> None:
    data: Any = parity("eval-checks.json")
    for case in data["parseYesNo"]["cases"]:
        assert parse_yes_no(case["input"]) == case["expected"], f"parseYesNo {case['input']!r}"


def test_run_scenario_smoke_and_structural_green() -> None:
    """harness 能实际执行写路径：stub llm（① distill 摘要 ② consolidate JSON）→ 结构判定全绿。"""
    scenario: dict[str, Any] = {
        "id": "SMOKE-01", "lang": "zh", "discipline": "fact-vs-belief",
        "messages": [{"sourceKind": "spoken", "rawContent": "我每天喝咖啡"}],
        "expect": {"newCognitions": {"min": 1, "max": 1, "types": ["preference"], "formedBy": ["stated"]}},
    }
    llm = _SeqLLM([
        "用户聊到每天喝咖啡",
        '{"new":[{"content":"喜欢喝咖啡","content_type":"preference","formed_by":"stated","support_evidence_ids":["e1"]}]}',
    ])
    run = run_scenario(scenario, llm, now=datetime(2026, 1, 2, tzinfo=timezone.utc))
    assert run["error"] is None
    assert run["consolidated"]["createdCount"] == 1
    assert run["consolidated"]["created"][0]["formedBy"] == "stated"
    checks = check_structural(scenario, run)
    assert all(c.passed for c in checks), [c for c in checks if not c.passed]


def test_score_gists_conflict_is_deterministic() -> None:
    """conflict 的 shouldForm 走确定性硬判(GIST_SCORING_VERSION v2):看在册 conflicted 认知,不调 judge。"""
    scenario: dict[str, Any] = {"discipline": "conflict", "lang": "zh", "expect": {"shouldFormGists": ["冲突已暴露"]}}
    run: dict[str, Any] = {"active": [{"content": "喜欢早睡", "credStatus": "conflicted"}]}
    scores = score_gists(scenario, run, _NeverLLM())
    assert scores.form_results[0]["hit"] is True
    assert scores.form_results[0]["deterministic"] is True
    assert scores.gist_recall == 1.0
    assert scores.over_infer_rate is None  # 无 shouldNot 要点


def test_score_gists_conflict_miss_when_not_surfaced() -> None:
    """没有在册 conflicted 认知 → 判 miss(模型误删/失效旧认知时正确判红)。"""
    scenario: dict[str, Any] = {"discipline": "conflict", "lang": "zh", "expect": {"shouldFormGists": ["冲突已暴露"]}}
    run: dict[str, Any] = {"active": [{"content": "喜欢早睡", "credStatus": "limited"}]}
    scores = score_gists(scenario, run, _NeverLLM())
    assert scores.form_results[0]["hit"] is False
    assert scores.gist_recall == 0.0
