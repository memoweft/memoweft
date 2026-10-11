"""One formation-time sidecar request per distinct synthetic memory, no queries."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

MODEL = "mimo-v2.6-flash"
BASE = "https://api.xiaomimimo.com/v1/chat/completions"
PROMPT = """为一条虚构个人记忆生成检索旁注。只看记忆，不能猜未来问句。
输出 JSON {"theme":"简短主题", "aliases":["..."]}，最多8个别名。
含常见同义说法、合适的上位词、中文及英文对应，可含通用缩写。
别名只描述原记忆已有主题，不添加个人事实、地点、数字、限制或人物，不混入同形异义词。
保留限制主题（例如过敏对象）和具体对象。没有可靠别名时返回空列表。"""


def key(text):
    return sha256(text.encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def generate(memories, path):
    path = Path(path)
    digest = key(PROMPT)
    existing = read(path) if path.exists() else {"model": MODEL, "prompt_sha256": digest, "entries": {}}
    assert existing["prompt_sha256"] == digest, "Never reuse sidecars from a different prompt"
    api_key = os.environ.get("MIMO_API_KEY")
    if not api_key:
        raise RuntimeError("MIMO_API_KEY required in process environment")
    texts = sorted({m["content"] for m in memories})
    pending = [text for text in texts if key(text) not in existing["entries"]]
    journal = Path(__file__).resolve().parents[3] / ".local/mem5/mimo-ledger"
    journal.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"Authorization": "Bearer " + api_key}, timeout=120) as client:
        def one(text):
            started = perf_counter()
            response = client.post(BASE, json={"model": MODEL, "temperature": 0,
                "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}, "max_tokens": 400,
                "messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}]})
            if response.status_code != 200:
                raise RuntimeError("MiMo HTTP " + str(response.status_code))
            data = response.json()
            # Journal usage before decoding; request accounting survives errors.
            write(journal / (str(uuid4()) + ".json"), {"memory_sha256": key(text), "scope": str(path.name), "usage": data.get("usage", {}), "status": response.status_code})
            status = "valid"
            try:
                decoded = json.loads(data["choices"][0]["message"]["content"])
                assert isinstance(decoded["theme"], str) and len(decoded["theme"]) <= 100
                assert isinstance(decoded["aliases"], list)
                assert all(isinstance(a, str) and 0 < len(a) <= 100 for a in decoded["aliases"])
                if len(decoded["aliases"]) > 8:
                    status = "bounded_to_first_8"
                decoded = {"theme": decoded["theme"], "aliases": decoded["aliases"][:8]}
            except (ValueError, KeyError, AssertionError, TypeError):
                decoded, status = {"theme": "", "aliases": []}, "invalid_empty_sidecar"
            return key(text), {"content": text, "sidecar": decoded, "decode_status": status, "requests": 1, "usage": data["usage"], "latency_ms": (perf_counter() - started) * 1000}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(one, text) for text in pending]
            for count, future in enumerate(as_completed(futures), 1):
                digest, entry = future.result()
                existing["entries"][digest] = entry
                write(path, existing)
                if count % 25 == 0:
                    print("synthetic sidecars", count, "/", len(pending), flush=True)
    return existing


def usage_report(cache):
    entries = list(cache["entries"].values())
    usage = {name: sum(e["usage"].get(name, 0) for e in entries) for name in ("prompt_tokens", "completion_tokens", "total_tokens")}
    cached = sum(e["usage"].get("prompt_tokens_details", {}).get("cached_tokens", 0) for e in entries)
    total = (usage["prompt_tokens"] - cached + .02 * cached + 2 * usage["completion_tokens"]) / 1_000_000
    upper = (usage["prompt_tokens"] + 2 * usage["completion_tokens"]) / 1_000_000
    return {"memories": len(entries), "requests": sum(e["requests"] for e in entries), "usage": usage, "cached_input_tokens": cached,
            "estimated_cny": total, "estimated_cny_uncached": upper, "cny_per_1000_memories": total * 1000 / len(entries),
            "cny_per_1000_uncached": upper * 1000 / len(entries), "sidecar_utf8_bytes": sum(len(json.dumps(e["sidecar"], ensure_ascii=False, separators=(',', ':')).encode()) for e in entries),
            "invalid_sidecars": sum(e.get("decode_status") == "invalid_empty_sidecar" for e in entries),
            "pricing_source": "https://mimo.mi.com/static/docs/price/pay-as-you-go.md", "pricing_checked": "2026-10-11",
            "cny_per_million_tokens": {"input": 1, "cached_input": .02, "output": 2}, "cost_scope": "Standalone sidecar generation, one extra request per memory; estimated, not billing receipt"}
