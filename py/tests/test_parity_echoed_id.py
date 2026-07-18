"""逐位对拍 resolveEchoedId(shared/parity/echoed-id.json):三级解析 + 护栏。"""
from __future__ import annotations

from conftest import parity

from memoweft import resolve_echoed_id


def test_echoed_id_bit_exact() -> None:
    data = parity("echoed-id.json")
    for case in data["cases"]:
        i = case["input"]
        raw = i["raw"]  # 生成器把 undefined 存成了 None
        tag_map = {k: v for k, v in i["tagMap"]}
        got = resolve_echoed_id(raw, set(i["whitelist"]), tag_map if tag_map else None)
        assert got == case["expected"], f"resolveEchoedId 分叉 @ {i}: got {got!r}, want {case['expected']!r}"
