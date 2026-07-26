"""reconcile 同题聚簇(clusterByCosine)确定性 parity：Python `_cluster_by_cosine` 与 TS
(shared/parity/reconcile.json)逐位一致。

极性判(judge_contradiction)是 LLM、不进夹具(同护栏)；这里只钉确定性部分：连通分量聚簇(并查集)的
簇划分与下标序跨语言逐位一致——含传递闭包(A-C断经B连)、多簇、孤立点排除(size<2)、阈值门。
"""
from __future__ import annotations

from typing import Any

from conftest import parity

from memoweft.reconcile import _cluster_by_cosine


def test_cluster_by_cosine_bit_exact() -> None:
    data: Any = parity("reconcile.json")
    assert len(data["cases"]) >= 1
    for case in data["cases"]:
        i = case["input"]
        got = _cluster_by_cosine(i["vecs"], i["threshold"])
        assert got == case["expected"], f"clusterByCosine 分叉 @ {i}: got {got}, want {case['expected']}"
