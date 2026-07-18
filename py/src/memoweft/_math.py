"""跨语言数值 parity 的两个硬点(D-0042「三个 parity 杀手」之①②)。"""
from __future__ import annotations

import math

_U32 = 0xFFFFFFFF


def round_half_up(x: float) -> int:
    """复刻 JS `Math.round`:半值向 +∞(**不是** Python 内置 round 的银行家舍入)。

    computeConfidence(confidence.ts:30)与 effectiveConfidence(decay.ts:37)都用 Math.round;
    本项目的分数恒 ≥0,故 floor(x+0.5) 与 Math.round 对所有输入一致(负数半值行为此处用不到)。
    """
    return math.floor(x + 0.5)


def imul(a: int, b: int) -> int:
    """复刻 JS `Math.imul`:32 位整数乘、取低 32 位(位模式,按无符号保存)。"""
    return (a * b) & _U32


def utf16_code_units(s: str) -> list[int]:
    """把字符串按 **UTF-16 码元** 展开(复刻 JS `str.charCodeAt(i)` 逐位迭代)。

    关键 parity 点:JS fnv1a32 用 charCodeAt(码元),而 tokenize 的汉字切分用 Array.from(码点)。
    BMP 字符两者一致;星区字符(代理对)会分叉 —— 故 fnv1a32 必须按码元、不能按 Python 的码点迭代。
    """
    b = s.encode("utf-16-le")
    return [b[i] | (b[i + 1] << 8) for i in range(0, len(b), 2)]
