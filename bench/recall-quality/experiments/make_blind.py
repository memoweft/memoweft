"""Independent synthetic author. Run and commit BEFORE implementing either arm.

No retrieval imports, parameters, scores, or per-query output. The author sees
category instructions, never the experiment implementation. Only structural
validation and exact overlap exclusion use the exposed regression collections.
"""
from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
from time import perf_counter
from threading import Lock

import httpx

HERE = Path(__file__).resolve().parent
MODEL = "mimo-v2.6-flash"
CATEGORIES = {
    "synonym": "同义表达，记忆和问句采用不同词；围绕个人兴趣或习惯",
    "hierarchy": "上位概念问句指向一个具体个人偏好；不要让干扰项也属于该类",
    "bilingual": "中文记忆和英文或中英混合问句；含不同主题",
    "colloquial": "口语表达和书面表达之间转换，语义明确",
    "abbreviation": "通用缩写和全称转换，记忆是个人经验",
    "typo": "轻微错别字或拼音替代核心词，仍能唯一理解",
    "indirect": "个人过敏、限制或环境条件对活动/物品选择有直接关系",
    "time": "不同表达询问同一个人的时间习惯；不得推断不确定日期",
    "correction": "原偏好已被纠正，新记忆明确提同一主题，问句应只返回新偏好",
    "nearby": "相近但不同的兴趣/物品，问句有足够细节唯一对应目标",
    "polysemy": "纯负例：个人生活记忆的词在问句中有另一含义，不相关",
    "lifecycle": "被权限/遗忘/过期/账号隔离屏蔽的记忆，问句语义相关但不可返回",
    "technical_polysemy": "纯负例：日常真实风格编程/系统/数据库/网络技术问句夹着多义词，个人生活记忆与它不相关",
}
SYSTEM = """你是独立的合成评测数据作者，只输出 JSON。每批生成15个互不重复的场景。
全部内容虚构，禁止真实个人资料。不要设计检索实现或猜测它的阈值。
每个场景输出 memory, query, distractor, second_distractor 四个字符串。
正例 memory 是直接相关的个人事实，query 是真实用户可能提问的改述；两条干扰记忆完全无关。
负例 query 必须与所有记忆都无关，即使共享一个多义词也不要有合理个性化用途。
correction 类再输出 previous，memory 是当前新事实，previous 是明确被它纠正的旧事实。
lifecycle 类 memory 是唯一语义相关事实，但将被程序标为不可见，其它记忆完全不相关。
技术负例禁用上一轮的 cat 命令、rose 时态、tea tree、8 pm、running process、种花家、
几何交点、美式英语、exercise book、painting software 这10个题型。
不要使用常见的骑行、摄影、猫狗、川粤菜、草莓、拿铁、玫瑰、跑步、登山、园艺种子。
尽量涉及不同的生活、办公、工具、旅行、学习主题。格式 {"samples":[{...}, ...]}。"""


def main():
    target = HERE / "blind.json"
    if target.exists():
        raise SystemExit("Refusing to overwrite frozen blind data")
    key = os.environ.get("MIMO_API_KEY")
    if not key:
        raise SystemExit("MIMO_API_KEY required in process environment")
    exposed = {
        c["query"]
        for split in ("dev", "challenge", "holdout", "review")
        for c in json.loads((HERE.parent / f"{split}.json").read_text(encoding="utf-8"))["cases"]
    }
    cases, usage, requests = [], Counter(), 0
    lock = Lock()
    scratch = HERE.parents[2] / ".local/mem5/blind-author"
    scratch.mkdir(parents=True, exist_ok=True)
    started = perf_counter()
    states = ["forgotten", "archived", "muted", "invalid", "local_denied", "cloud_denied", "expired", "future", "cross_evidence", "other_account"]
    with httpx.Client(headers={"Authorization": "Bearer " + key}, timeout=180) as client:
        def author(entry):
            nonlocal requests
            category, description = entry
            samples = []
            for attempt in range(3):
                response = client.post("https://api.xiaomimimo.com/v1/chat/completions", json={
                "model": MODEL, "temperature": 0.8, "max_tokens": 6500,
                "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": category + ": " + description}],
            })
                if response.status_code != 200:
                    raise RuntimeError("Synthetic author HTTP " + str(response.status_code))
                data = response.json()
                u = data.get("usage", {})
                with lock:
                    requests += 1
                    usage.update({k: u.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")})
                (scratch / f"{category}-{attempt}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                incoming = json.loads(data["choices"][0]["message"]["content"])["samples"]
                for s in incoming:
                    if s.get("query") not in {x.get("query") for x in samples}:
                        samples.append(s)
                if len(samples) >= 15:
                    print("authored category", category, "count", 15, flush=True)
                    return category, samples[:15]
            raise RuntimeError("Author batch short after 3 attempts")
        with ThreadPoolExecutor(max_workers=4) as executor:
            authored = list(executor.map(author, CATEGORIES.items()))
        for category, samples in authored:
            for i, s in enumerate(samples):
                assert all(isinstance(s.get(k), str) and 1 < len(s[k]) < 300 for k in ("memory", "query", "distractor", "second_distractor"))
                assert s["query"] not in exposed and s["query"] not in {c["query"] for c in cases}
                state = states[i % len(states)] if category == "lifecycle" else "active"
                memories = [
                    {"id": "target", "content": s["memory"], "state": state},
                    {"id": "distractor", "content": s["distractor"]},
                    {"id": "second-distractor", "content": s["second_distractor"]},
                ]
                if category == "correction":
                    assert isinstance(s.get("previous"), str) and s["previous"] != s["memory"]
                    memories.insert(0, {"id": "previous", "content": s["previous"], "state": "invalid"})
                    memories[1]["predecessor"] = "previous"
                positive = category not in {"polysemy", "lifecycle", "technical_polysemy"}
                case_id = f"blind-{category}-{i:02}"
                cases.append({"id": case_id, "seed": case_id, "category": category,
                              "query": s["query"], "tier": "cloud" if state == "cloud_denied" else "local",
                              "memories": memories, "expect": ["target"] if positive else [],
                              "forbid": [m["id"] for m in memories if not positive or m["id"] != "target"]})
    raw = json.dumps({"version": 1, "provenance": "Independent MiMo synthetic author; committed before route implementation. Core 180: 150 positive/30 negative, plus 15 new technical negatives. No exposed seed reuse. Model-author labels, no human semantic adjudication.", "cases": cases}, ensure_ascii=False, indent=2) + "\n"
    target.write_text(raw, encoding="utf-8", newline="\n")
    manifest = {"model": MODEL, "requests": requests, "usage": dict(usage), "seconds": perf_counter() - started,
                "aborted_prior_author_run": {"requests": 5, "usage": None, "reason": "Short batch rejected structurally; no fixture was written; responses were not retained. Tokens unavailable. No retrieval implementation or scoring existed."},
                "sha256_lf": sha256(raw.encode()).hexdigest(), "queries": len(cases), "positive": 150, "negative": 45,
                "categories": dict(Counter(c["category"] for c in cases)),
                "protocol": "No per-case reading/output during tuning. Blind runner refuses a second final invocation; freeze parameters before first final run."}
    (HERE / "blind-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
