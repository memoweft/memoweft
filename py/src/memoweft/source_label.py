"""来源标注 —— 移植自 src/evidence/sourceLabel.ts(D-0018)。

source_label:evidence.source_kind → 给固化 LLM 的来源前缀(observed/tool 不是用户原话)。
ai_context_suffix:上一轮 AI 那句 → 只读上下文后缀(D-0033);js_trim + UTF-16 slice(240)复刻 JS 字节。
"""
from __future__ import annotations

from typing import Optional

from ._jsstr import js_trim, utf16_length, utf16_slice_head
from .types import Lang, SourceKind

_LABELS: dict[SourceKind, dict[Lang, str]] = {
    "spoken": {"zh": "用户说", "en": "user said"},
    "observed": {"zh": "行为观察", "en": "observed behavior"},
    "tool": {"zh": "工具返回", "en": "tool result"},
    "inferred": {"zh": "AI 推测", "en": "AI inference"},
}

#: AI 上文注入上限(UTF-16 code unit)。对齐 sourceLabel.ts:27。
AI_CONTEXT_MAX = 240


def source_label(source_kind: SourceKind, lang: Lang) -> str:
    """来源前缀(含尾随空格);未知退回 spoken。对齐 sourceLabel.ts:21-24。"""
    label = _LABELS.get(source_kind, _LABELS["spoken"])
    return f"[{label[lang]}] "


def ai_context_suffix(text: Optional[str], lang: Lang) -> str:
    """上一轮 AI 那句 → 只读上下文后缀;空/纯空白 → ''。对齐 sourceLabel.ts:35-42。"""
    t = js_trim(text if text is not None else "")
    if not t:
        return ""
    clipped = utf16_slice_head(t, AI_CONTEXT_MAX) + "…" if utf16_length(t) > AI_CONTEXT_MAX else t
    if lang == "zh":
        return f'  ⟨AI 前一句(仅上下文,非用户原话、不可作证据):"{clipped}"⟩'
    return f'  ⟨preceding AI turn (context only, NOT the user\'s words, not usable as evidence): "{clipped}"⟩'
