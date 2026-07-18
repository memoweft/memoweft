"""跨语言数值一致性所需的 JavaScript 数值语义兼容函数。"""
from __future__ import annotations

import math

_U32 = 0xFFFFFFFF


def round_half_up(x: float) -> int:
    """实现 JS `Math.round` 的半值向 +∞ 语义，而非 Python 内置 round 的银行家舍入。

    computeConfidence 与 effectiveConfidence 都依赖 Math.round；
    本项目的分数恒 ≥0,故 floor(x+0.5) 与 Math.round 对所有输入一致(负数半值行为此处用不到)。
    """
    return math.floor(x + 0.5)


def imul(a: int, b: int) -> int:
    """实现 JS `Math.imul` 的 32 位整数乘法，并以无符号位模式返回低 32 位。"""
    return (a * b) & _U32


def utf16_code_units(s: str) -> list[int]:
    """将字符串展开为 UTF-16 码元，与 JS `str.charCodeAt(i)` 的迭代语义一致。

    关键 parity 点:JS fnv1a32 用 charCodeAt(码元),而 tokenize 的汉字切分用 Array.from(码点)。
    BMP 字符两者一致;星区字符(代理对)会分叉 —— 故 fnv1a32 必须按码元、不能按 Python 的码点迭代。
    """
    b = s.encode("utf-16-le")
    return [b[i] | (b[i + 1] << 8) for i in range(0, len(b), 2)]
