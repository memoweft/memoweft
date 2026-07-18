"""JS 字符串语义复刻 —— trim 白名单 / UTF-16 length·slice(与 Python 码点不同)。跨语言字节 parity 依赖它。

- js_trim:复刻 JS String.prototype.trim() 去除集(ES WhiteSpace ∪ LineTerminator),含 U+FEFF(BOM);
    Python str.strip() 的 Unicode 空白集不同(不去 BOM、却去 U+001C-1F/U+0085)。
- utf16_length / utf16_slice_head:JS String.length / slice 按 UTF-16 code unit(astral 字符计 2),
    Python len/切片按码点 → 含 emoji 时分叉。
"""
from __future__ import annotations

# ES String.prototype.trim() 去除集,用【码点数字】构造(勿写字面空白,否则源码不可靠):
#   U+0009..000D(TAB/LF/VT/FF/CR)+ U+0020(SP)+ U+00A0(NBSP)+ U+1680 + U+2000..200A(Zs)
#   + U+2028/2029(行/段分隔)+ U+202F/205F/3000 + U+FEFF(BOM)。
_JS_WS_CODEPOINTS = (
    0x09, 0x0A, 0x0B, 0x0C, 0x0D,
    0x20, 0xA0, 0x1680,
    0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200A,
    0x2028, 0x2029, 0x202F, 0x205F, 0x3000,
    0xFEFF,
)
_JS_WS = frozenset(chr(cp) for cp in _JS_WS_CODEPOINTS)


def js_trim(s: str) -> str:
    """复刻 JS String.prototype.trim()。"""
    i, j = 0, len(s)
    while i < j and s[i] in _JS_WS:
        i += 1
    while j > i and s[j - 1] in _JS_WS:
        j -= 1
    return s[i:j]


def utf16_length(s: str) -> int:
    """JS String.length = UTF-16 code unit 数(astral 字符计 2)。"""
    return len(s.encode("utf-16-le")) // 2


def utf16_slice_head(s: str, n: int) -> str:
    """JS s.slice(0, n):前 n 个 UTF-16 code unit(切断代理对 → 孤立 surrogate,与 JS 一致)。"""
    if utf16_length(s) <= n:
        return s
    head = s.encode("utf-16-le")[: n * 2]
    return head.decode("utf-16-le", errors="surrogatepass")
