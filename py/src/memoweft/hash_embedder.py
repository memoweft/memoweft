r"""确定性词袋哈希嵌入器：不访问网络，相同输入始终产生相同输出。

跨语言数值契约：
  - fnv1a32 用 Math.imul(32位)+ charCodeAt(UTF-16 **码元**)—— 见 _math.imul / utf16_code_units。
  - tokenize 的汉字切分用 Array.from(**码点**);故 fnv1a32 与 tokenize 的迭代单位【不同】(BMP 下一致)。
  - \p{L}/\p{N}/\p{Han} 用第三方 `regex` 模块(stdlib re 无 \p{Script=...})。
"""
from __future__ import annotations

import math

import regex

from ._math import imul, utf16_code_units

DEFAULT_DIM = 256

_FNV_OFFSET = 0x811C9DC5
_FNV_PRIME = 0x01000193

_RUN_RE = regex.compile(r"\p{L}+|\p{N}+")
_SEG_RE = regex.compile(r"\p{Han}+|[^\p{Han}]+")
_HAN_RE = regex.compile(r"\p{Han}")


def fnv1a32(s: str) -> int:
    """按 UTF-16 码元计算 32 位 FNV-1a，并返回 uint32。"""
    h = _FNV_OFFSET
    for code in utf16_code_units(s):
        h = (h ^ code) & 0xFFFFFFFF
        h = imul(h, _FNV_PRIME)
    return h & 0xFFFFFFFF


def _tokens_from_run(run: str) -> list[str]:
    """将字母或数字连续段切分为汉字单字与相邻 bigram，非汉字子段保持为单个 token。"""
    out: list[str] = []
    for seg in _SEG_RE.findall(run):
        if _HAN_RE.search(seg):
            chars = list(seg)  # 码点切(= JS Array.from),兼顾星区汉字
            out.extend(chars)  # 单字
            for i in range(len(chars) - 1):
                out.append(chars[i] + chars[i + 1])  # char-bigram
        else:
            out.append(seg)
    return out


def tokenize(text: str) -> list[str]:
    """转为小写后按 \\p{L}+|\\p{N}+ 提取连续段，再逐段细分。"""
    out: list[str] = []
    for run in _RUN_RE.findall(text.lower()):
        out.extend(_tokens_from_run(run))
    return out


class HashEmbedder:
    """确定性词袋哈希嵌入器。"""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        if not isinstance(dim, int) or dim <= 0:
            raise ValueError(f"HashEmbedder dim 必须是正整数,收到 {dim}")
        self.dim = dim
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for token in tokenize(text):
            idx = fnv1a32(token) % self.dim
            vec[idx] += 1.0  # TF 累加
        sum_sq = sum(v * v for v in vec)
        if sum_sq == 0:
            return vec  # 空文本 / 无 token:全零
        norm = math.sqrt(sum_sq)
        return [v / norm for v in vec]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """一组文本 → 向量(TS 是 async,此处同步;纯计算无 I/O)。"""
        if not texts:
            return []
        self._call_count += 1
        return [self._embed_one(t) for t in texts]
