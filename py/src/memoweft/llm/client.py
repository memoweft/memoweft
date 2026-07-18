"""使用同步 httpx.Client 调用 OpenAI 兼容 /chat/completions 的轻量封装。

Python 实现使用同步 httpx.Client，使写路径与 SQLite 调用模型一致；测试无需 asyncio，
  且同步方式不影响逻辑、字节或分布层面的 parity。
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Optional, Protocol

import httpx

from .._jsstr import js_trim
from ..config import resolve_lang
from ..types import ModelTier


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class UsageStats:
    """累计可用的 LLM token 用量；缺失 usage 的调用不计入 calls_with_usage。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls_with_usage: int = 0


@dataclass(frozen=True, slots=True)
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    temperature: Optional[float] = None
    tier: Optional[ModelTier] = None


class LLMClient(Protocol):
    """调用方依赖的最小大模型客户端接口。"""

    def chat(self, messages: list[ChatMessage]) -> str: ...
    @property
    def call_count(self) -> int: ...
    @property
    def tier(self) -> Optional[ModelTier]: ...
    @property
    def usage(self) -> Optional[UsageStats]: ...


# ── 纯文本处理（供 chat 与共享 parity 夹具使用）──


_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def strip_reasoning(s: str) -> str:
    """移除成对闭合的 <think>…</think>（忽略大小写并支持跨行），再执行 js_trim。"""
    return js_trim(_THINK_RE.sub("", s))


def read_reply_text(message: Optional[Mapping[str, Any]]) -> Optional[str]:
    """优先返回非空 content，其次返回 reasoning_content；均不可用时返回空 content 或 None。"""
    content = message.get("content") if message is not None else None
    if isinstance(content, str) and js_trim(content):
        return content
    reasoning = message.get("reasoning_content") if message is not None else None
    if isinstance(reasoning, str) and js_trim(reasoning):
        return reasoning
    return content if isinstance(content, str) else None


# ── env 解析（保持 JS Number 语义）──


_JS_DEC_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_JS_RADIX_RE = re.compile(r"^[+-]?0([xX][0-9a-fA-F]+|[oO][0-7]+|[bB][01]+)$")


def _js_number(s: str) -> float:
    """实现 JS Number(s) 语义，包括空串、进制前缀、Infinity 与无效值。"""
    t = js_trim(s)
    if t == "":
        return 0.0
    low = t.lower()
    if low in ("infinity", "+infinity"):
        return math.inf
    if low == "-infinity":
        return -math.inf
    if _JS_RADIX_RE.match(t):
        return float(int(t, 0))
    if _JS_DEC_RE.match(t):
        return float(t)
    return math.nan


def _js_truthy_num(n: float) -> bool:
    """JS 数值真值:0 与 NaN 为 falsy,其余(含 Infinity)truthy。"""
    return n != 0 and not math.isnan(n)


def _int_or_zero(x: object) -> int:
    """实现整数版 typeof x === 'number' ? x : 0，并显式排除 bool。"""
    return x if isinstance(x, int) and not isinstance(x, bool) else 0


def _read_env_with_fallback(name: str) -> str:
    """优先读取 MEMOWEFT_<name>，其次读取 DLA_<name>；空字符串仍视为显式配置。"""
    v = os.environ.get(f"MEMOWEFT_{name}")
    if v is not None:
        return v
    v = os.environ.get(f"DLA_{name}")
    if v is not None:
        return v
    return ""


def load_llm_config(prefix: str = "LLM") -> LLMConfig:
    """从 MEMOWEFT_* 或兼容的 DLA_* 环境变量组装配置；缺少关键项时抛错。

    注:不做 TS 的 process.loadEnvFile()——Python 侧由宿主/shell 或 python-dotenv 填充 os.environ。
    """
    base = prefix[4:] if prefix.startswith("DLA_") else prefix
    base_url = _read_env_with_fallback(f"{base}_BASE_URL")
    api_key = _read_env_with_fallback(f"{base}_API_KEY")
    model = _read_env_with_fallback(f"{base}_MODEL")
    if not base_url or not api_key or not model:
        zh = f"LLM 配置缺失:请在 .env 设置 MEMOWEFT_{base}_BASE_URL / _API_KEY / _MODEL(或兼容旧名 DLA_{base}_*)"
        en = f"Missing LLM config: set MEMOWEFT_{base}_BASE_URL / _API_KEY / _MODEL in .env (legacy DLA_{base}_* still supported)"
        raise ValueError(zh if resolve_lang() == "zh" else en)
    temp_raw = _read_env_with_fallback(f"{base}_TEMPERATURE")
    temp_num = _js_number(temp_raw)
    temperature = temp_num if temp_raw != "" and math.isfinite(temp_num) else None
    tier_raw = js_trim(_read_env_with_fallback(f"{base}_TIER")).lower()
    tier: Optional[ModelTier] = "local" if tier_raw == "local" else "cloud" if tier_raw == "cloud" else None
    return LLMConfig(base_url=base_url, api_key=api_key, model=model, temperature=temperature, tier=tier)


class OpenAICompatClient:
    """通过同步 httpx.Client 调用 OpenAI 兼容的 /chat/completions 接口。"""

    def __init__(self, cfg: Optional[LLMConfig] = None, *, transport: Optional[httpx.BaseTransport] = None) -> None:
        self._config = cfg if cfg is not None else load_llm_config()
        # transport 是测试接缝(注入 MockTransport);生产不传 = 真实网络。
        self._client = httpx.Client(transport=transport) if transport is not None else httpx.Client()
        self._call_count = 0
        self._usage = UsageStats()

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def tier(self) -> Optional[ModelTier]:
        return self._config.tier

    @property
    def usage(self) -> UsageStats:
        return self._usage

    def chat(self, messages: list[ChatMessage]) -> str:
        self._call_count += 1
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        # 超时遵循 Number(env) || 120000 语义；0/NaN 使用默认值，httpx 接收秒。
        n = _js_number(_read_env_with_fallback("LLM_TIMEOUT_MS"))
        timeout_ms = n if _js_truthy_num(n) else 120000.0
        body = {
            "model": self._config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self._config.temperature if self._config.temperature is not None else 0.3,
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._config.api_key}"}
        try:
            res = self._client.post(url, headers=headers, json=body, timeout=timeout_ms / 1000.0)
        except httpx.TimeoutException as err:
            zh = f"LLM 请求超时(超过 {int(timeout_ms)}ms)"
            en = f"LLM request timed out (exceeded {int(timeout_ms)}ms)"
            raise TimeoutError(zh if resolve_lang() == "zh" else en) from err
        if res.status_code < 200 or res.status_code >= 300:
            text = res.text[:500]
            zh = f"LLM 请求失败 {res.status_code}: {text}"
            en = f"LLM request failed {res.status_code}: {text}"
            raise RuntimeError(zh if resolve_lang() == "zh" else en)
        data: Any = res.json()
        # token 用量:读到才加、读不到静默跳过(累加在 content 校验之前:token 已消耗)。
        raw_usage = data.get("usage")
        if raw_usage:
            p = _int_or_zero(raw_usage.get("prompt_tokens"))
            c = _int_or_zero(raw_usage.get("completion_tokens"))
            tt = raw_usage.get("total_tokens")
            t = tt if isinstance(tt, int) and not isinstance(tt, bool) else p + c
            self._usage = UsageStats(
                prompt_tokens=self._usage.prompt_tokens + p,
                completion_tokens=self._usage.completion_tokens + c,
                total_tokens=self._usage.total_tokens + t,
                calls_with_usage=self._usage.calls_with_usage + 1,
            )
        choices = data.get("choices")
        message = choices[0].get("message") if isinstance(choices, list) and len(choices) > 0 else None
        content = read_reply_text(message)
        if not isinstance(content, str):
            snippet = _json_snippet(data)
            zh = f"LLM 返回格式异常:{snippet}"
            en = f"Unexpected LLM response format: {snippet}"
            raise RuntimeError(zh if resolve_lang() == "zh" else en)
        return strip_reasoning(content)


def _json_snippet(data: object) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))[:500]


__all__ = [
    "ChatMessage",
    "UsageStats",
    "LLMConfig",
    "LLMClient",
    "OpenAICompatClient",
    "strip_reasoning",
    "read_reply_text",
    "load_llm_config",
]
