"""验证 hashEmbedder 与 shared/parity/hash-embedder.json 的跨语言一致性。

fnv1a32 与 tokenize 必须逐值一致；embed 向量仅为 L2 归一化的浮点差异保留严格容差。
"""
from __future__ import annotations

import math
from typing import Any

from conftest import parity

from memoweft import HashEmbedder, fnv1a32, tokenize


def _data() -> Any:
    return parity("hash-embedder.json")


def test_fnv1a32_bit_exact() -> None:
    for case in _data()["fnv1a32"]["cases"]:
        got = fnv1a32(case["input"])
        assert got == case["expected"], f"fnv1a32 分叉 @ {case['input']!r}: got {got}, want {case['expected']}"
        assert 0 <= got <= 0xFFFFFFFF


def test_tokenize_bit_exact() -> None:
    for case in _data()["tokenize"]["cases"]:
        got = tokenize(case["input"])
        assert got == case["expected"], f"tokenize 分叉 @ {case['input']!r}: got {got}, want {case['expected']}"


def test_embed_bit_exact() -> None:
    section = _data()["embed"]
    dim = section["dim"]
    emb = HashEmbedder(dim)
    for case in section["cases"]:
        (got,) = emb.embed([case["input"]["text"]])
        want = case["expected"]
        assert len(got) == len(want) == dim
        for g, w in zip(got, want):
            assert g == w or math.isclose(g, w, rel_tol=1e-12, abs_tol=1e-15), f"embed 分叉 @ {case['input']}: {g!r} vs {w!r}"
