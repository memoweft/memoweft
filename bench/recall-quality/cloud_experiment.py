"""Optional, explicitly requested MiMo experiment; never imported by Core/CI.

Synthetic development cases only. Queries alone are sent for rewriting; the
answer smoke sends synthetic recalled claims. Keys are process environment
only. Responses/errors never log headers, machine paths, or identity.
"""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import json
import os
import sqlite3

import httpx
from evaluate import HERE, aggregate, historical_module, percentile, recall, seed

MODEL = "mimo-v2.6-flash"
BASE = "https://api.xiaomimimo.com/v1"
stats = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cache_tokens": 0}


def chat(client, messages, json_mode=False):
    stats["requests"] += 1
    started = perf_counter()
    payload = {"model": MODEL, "messages": messages, "max_tokens": 300, "thinking": {"type": "disabled"}}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    response = client.post(BASE + "/chat/completions", json=payload)
    if response.status_code != 200:
        raise RuntimeError("MiMo HTTP " + str(response.status_code))
    data = response.json()
    usage = data.get("usage", {})
    stats["prompt_tokens"] += usage.get("prompt_tokens", 0)
    stats["completion_tokens"] += usage.get("completion_tokens", 0)
    stats["cache_tokens"] += usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    return data["choices"][0]["message"]["content"], (perf_counter() - started) * 1000


def main():
    key = os.environ.get("MIMO_API_KEY")
    if not key:
        raise SystemExit("MIMO_API_KEY is required in the process environment")
    dev = json.loads((HERE / "dev.json").read_text(encoding="utf-8"))["cases"]
    # First and second seed in each category, fixed before calls; 24 requests.
    cases = [case for case in dev if case["id"].endswith(("-00-0", "-01-0"))]
    report = {"model": MODEL, "fixture_split": "dev", "scope": "query rewrite over exact 233cc2f recall; comparison uses final guarded recall", "results": [], "answer_smoke": [], "embedding": {"status": "not_run", "reason": "No authorized MEMOWEFT_EMBED_* endpoint/model/key configured. MiMo text-generation credentials do not establish an embedding model. No local model or new service was started.", "requests": 0, "cost": 0}}
    with httpx.Client(headers={"Authorization": "Bearer " + key}, timeout=45) as client, TemporaryDirectory(prefix="memoweft-cloud-quality-") as folder, historical_module("233cc2f") as baseline:
        for case in cases:
            path = Path(folder) / (case["id"] + ".sqlite3")
            seed(path, case)
            raw, latency = chat(client, [{"role": "system", "content": 'Rewrite the query as at most 3 short equivalent Chinese search phrases for personal memory. Preserve its domain and negation. Include synonyms or relevant personal constraints for recommendations, but never assert that the user has a fact. Return only JSON {"queries":["..."]}.'}, {"role": "user", "content": case["query"]}], json_mode=True)
            start, end = raw.find("{"), raw.rfind("}")
            try:
                variants = json.loads(raw[start:end + 1])["queries"]
                rewrite_error = None
            except (ValueError, KeyError):
                variants, rewrite_error = [], "invalid_json"
            if not isinstance(variants, list) or any(not isinstance(q, str) or len(q) > 256 for q in variants):
                raise ValueError("invalid rewrite shape")
            variants = variants[:3]
            with closing(sqlite3.connect(path)) as db:
                original = baseline.recall_world_snapshot(db, "fictional-owner", case["query"], model_tier=case["tier"])
                guarded = recall.recall_world_snapshot(db, "fictional-owner", case["query"], model_tier=case["tier"])
                selected = [pair[1] for pair in original.selected_item_ids]
                for variant in variants:
                    snapshot = baseline.recall_world_snapshot(db, "fictional-owner", variant, model_tier=case["tier"])
                    selected += [pair[1] for pair in snapshot.selected_item_ids]
                selected = list(dict.fromkeys(selected))[:5]
            rank = next((i + 1 for i, mid in enumerate(selected) if mid in case["expect"]), 0)
            report["results"].append({"id": case["id"], "category": case["category"], "query": case["query"], "rewrites": variants, "rewrite_error": rewrite_error, "expect": case["expect"], "selected": selected, "rank": rank, "wrong": sorted(set(selected) & set(case["forbid"])), "latency_ms": latency, "miss_cause": "candidate" if case["expect"] and not rank else None, "baseline_selected": [pair[1] for pair in original.selected_item_ids], "guarded_selected": [pair[1] for pair in guarded.selected_item_ids]})
            report["usage"] = dict(stats)
            (HERE / "cloud.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print("rewrite completed", case["id"], flush=True)
        # A small answer smoke with intervening turns. Formation is fixture-
        # seeded; this is NOT a full model-backed ingestion/formation E2E claim.
        for case in [cases[0], next(c for c in cases if c["category"] == "indirect"), next(c for c in cases if c["category"] == "correction")]:
            path = Path(folder) / (case["id"] + ".sqlite3")
            with closing(sqlite3.connect(path)) as db:
                snapshot = recall.recall_world_snapshot(db, "fictional-owner", case["query"])
            answer, latency = chat(client, [{"role": "system", "content": "回答最后的问题。依据正式记忆给建议，别编造事实。正式记忆：\n" + snapshot.rendered_recall}, {"role": "user", "content": "我们先聊一下虚构的星形邮票。"}, {"role": "assistant", "content": "可以，稍后再安排活动。"}, {"role": "user", "content": "现在换个话题，先解释一下三角形。"}, {"role": "assistant", "content": "三角形有三条边。"}, {"role": "user", "content": case["query"]}])
            report["answer_smoke"].append({"id": case["id"], "memory": snapshot.rendered_recall, "query": case["query"], "answer": answer, "latency_ms": latency})
    report["overall"] = aggregate(report["results"])
    report["usage"] = stats
    # Published CNY pay-as-you-go rates, checked 2026-10-11; a cost estimate,
    # not a billing receipt. Treat all input as uncached for an upper bound.
    report["estimated_cny_uncached_upper_bound"] = (stats["prompt_tokens"] + 2 * stats["completion_tokens"]) / 1_000_000
    report["pricing_source"] = "https://mimo.mi.com/docs/pricing"
    (HERE / "cloud.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"overall": report["overall"], "usage": stats, "estimated_cny": report["estimated_cny_uncached_upper_bound"]}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, RuntimeError, ValueError) as error:
        # Avoid exception strings that may expose credentials or local paths.
        raise SystemExit("Cloud experiment failed: " + type(error).__name__) from None
