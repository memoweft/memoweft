"""与 TypeScript jsonRepair 实现保持行为一致的 JSON 解析加固。

extract_json_object 使用与 TypeScript 相同的确定性扫描规则，而不依赖更宽松的修复库；
  这保证首次解析失败时仍执行一次修复重试，并保持 llmCalls 计数一致。初次解析严格拒绝 NaN/Infinity。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from .._jsstr import js_trim, utf16_length
from ..config import resolve_lang
from ..types import Lang
from .client import ChatMessage, LLMClient
from .prompts import json_repair_nudge_text

_logger = logging.getLogger("memoweft.jsonRepair")

def _strip_code_fences(s: str) -> str:
    """移除 ```json … ``` 或 ``` … ``` 围栏；没有围栏时返回原文本。

    与 TS stripCodeFences 同口径的线性扫描，不用回溯正则：模型原文不可信，缺闭围栏的
    大段输入会让回溯型正则做大量重复尝试（ReDoS）。按「首个开围栏 → 其后首个闭围栏」取
    内容；不识别的语言标签作为围栏内容保留，而不是跳到后续围栏改变解析对象。
    """
    opening = s.find("```")
    if opening == -1:
        return s
    content_start = opening + 3
    closing = s.find("```", content_start)
    if closing == -1:
        return s
    # 模型常把 JSON 紧贴标签后输出（```json{...}）。保持旧正则的贪婪可选标签语义：
    # 开头四字符是 json（含 ```jsonish）就消费该前缀，再跳空白（js_trim 对齐 JS .trim 口径）。
    after_label = content_start + 4
    if s[content_start:after_label].lower() == "json":
        content_start = after_label
        while content_start < closing and js_trim(s[content_start]) == "":
            content_start += 1
    return s[content_start:closing]


def extract_json_object(raw: str) -> Optional[str]:
    """去围栏 → js_trim → 从首个 { 起括号配平取【第一个平衡闭合】对象(跳字符串内花括号/转义);抠不到→None。

    该扫描规则与 TypeScript 实现共享契约，并能忽略 reasoning 残留与尾随文本。
    """
    s = js_trim(_strip_code_fences(raw))
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    i = start
    while i < len(s):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
        i += 1
    return None


def _reject_constant(_x: str) -> Any:
    # JS JSON.parse 拒 NaN/Infinity/-Infinity;Python json.loads 默认接受 → 用 parse_constant 抛错对齐。
    raise ValueError("non-standard JSON constant (NaN/Infinity)")


def parse_json_object(raw: str) -> Optional[dict[str, Any]]:
    """提取并解析 JSON 对象；数组、标量、null 或解析失败均返回 None。"""
    text = extract_json_object(raw)
    if text is None:
        return None
    try:
        v = json.loads(text, parse_constant=_reject_constant)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def _default_log(msg: str) -> None:
    _logger.warning("[memoweft/jsonRepair] %s", msg)


def _js_bool(b: bool) -> str:
    return "true" if b else "false"


def _fail_msg(s: str, lang: Lang, *, first: bool) -> str:
    # 只记结构特征(长度用 UTF-16 code unit 对齐 JS .length、是否含围栏/花括号),【不记模型原文】(隐私优先)。
    length = utf16_length(s)
    has_fence = "```" in s
    has_brace = "{" in s
    if lang == "zh":
        head = "首次输出非合法 JSON,重试一次。" if first else "重试后仍非合法 JSON,放弃本轮。"
        return f"{head}解析失败:长度={length}、含代码围栏={_js_bool(has_fence)}、含花括号={_js_bool(has_brace)}"
    head = (
        "First output was not valid JSON, retrying once. "
        if first
        else "Still not valid JSON after retry, giving up this round. "
    )
    return f"{head}Parse failed: length={length}, hasCodeFence={_js_bool(has_fence)}, hasBrace={_js_bool(has_brace)}"


def parse_json_object_with_repair(
    llm: LLMClient,
    messages: list[ChatMessage],
    *,
    log: Optional[Callable[[str], None]] = None,
    lang: Optional[Lang] = None,
) -> Optional[dict[str, Any]]:
    """调用模型并解析对象；失败时记录日志并追加 jsonRepairNudge 重试一次，仍失败则返回 None。

    重试会再调一次模型(call_count +1);调用方统计调用数须在本函数前后取 call_count 差。
    """
    sink = log if log is not None else _default_log
    lg = lang if lang is not None else resolve_lang()

    first = llm.chat(messages)
    parsed = parse_json_object(first)
    if parsed is not None:
        return parsed

    sink(_fail_msg(first, lg, first=True))
    retry_messages = [*messages, ChatMessage(role="user", content=json_repair_nudge_text(lg))]
    second = llm.chat(retry_messages)
    reparsed = parse_json_object(second)
    if reparsed is not None:
        return reparsed

    sink(_fail_msg(second, lg, first=False))
    return None
