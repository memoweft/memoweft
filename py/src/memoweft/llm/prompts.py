"""受治理提示词载入 —— 从 shared/prompts.json（TS 生成的单一源）载入 8 条。

TS shared:check 验证 prompts.json 与 registry 一致，shared-assets.test 验证其 sha256 快照。
Python 直接加载同一 JSON，以共享资产契约保证跨语言文本一致性。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from .._shared import load_shared
from ..types import Lang


@dataclass(frozen=True, slots=True)
class VersionedPrompt:
    """受治理提示词的 id、version 与双语 text 结构。"""

    id: str
    version: str
    text: Mapping[Lang, str]


@lru_cache(maxsize=1)
def _registry() -> dict[str, VersionedPrompt]:
    data: Any = load_shared("prompts.json")
    out: dict[str, VersionedPrompt] = {}
    for p in data["prompts"]:
        out[p["id"]] = VersionedPrompt(
            id=p["id"], version=p["version"], text={"zh": p["text"]["zh"], "en": p["text"]["en"]}
        )
    return out


def get_prompt(prompt_id: str) -> VersionedPrompt:
    return _registry()[prompt_id]


def prompt_text(prompt_id: str, lang: Lang) -> str:
    return _registry()[prompt_id].text[lang]


def prompt_versions() -> dict[str, str]:
    """返回 id → version 映射。"""
    return {pid: p.version for pid, p in _registry().items()}


def json_repair_nudge_text(lang: Lang) -> str:
    """JSON 重试纠偏提示(jsonRepairNudge,以 role:'user' 追加)。"""
    return prompt_text("jsonRepairNudge", lang)
