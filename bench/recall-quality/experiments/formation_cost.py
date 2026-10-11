"""Paired MiMo calls: current real formation prompt vs added retrieval sidecars.

This measures incremental usage of a proposed co-generation envelope. It does
not apply the extended envelope to World or claim compiler acceptance.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import sys
from time import perf_counter

from aliases import BASE, MODEL, PROMPT, read, write
from runner import HERE, OUT, load
from memoweft.integrations.hermes.batch_adapter import _SYSTEM_PROMPT, _user_payload, _evidence_segments, _evidence_sentences
import httpx

EXTRA = """\n实验追加：正常输出既有完整 schema_version=8 解释，不改变原有认知。
另在最外层附 retrieval_sidecars 数组，每个元素含 cognition_index、theme、aliases。
每个认知写一个检索旁注。该附加字段不是真实记忆事实，也不替代原有 entity 字段。
""" + PROMPT


def main():
    path = OUT / "formation-cost.json"
    if path.exists():
        raise SystemExit("Paired cost measurement already recorded")
    categories = {"synonym", "hierarchy", "bilingual", "colloquial", "abbreviation", "indirect"}
    cases = [c for c in load("dev") if c["category"] in categories and c["id"].endswith(("-00-0", "-01-0"))]
    api_key = os.environ.get("MIMO_API_KEY")
    assert api_key, "MIMO_API_KEY required"
    report = {"model": MODEL, "sample_rule": "First two development seeds in six fixed positive categories; 12 pairs; no blind/review input",
              "scope": "Current _SYSTEM_PROMPT and _user_payload, synthetic Evidence, empty World; extended top-level sidecars are measured but not applied/compiled", "calls": []}
    with httpx.Client(headers={"Authorization": "Bearer " + api_key}, timeout=120) as client:
        def one(case, extended):
            text = next(m["content"] for m in case["memories"] if m["id"] in case["expect"])
            evidence = [{"id": "synthetic-evidence", "text": text, "segments": _evidence_segments(text), "sentences": _evidence_sentences(text)}]
            started = perf_counter()
            response = client.post(BASE, json={"model": MODEL, "temperature": 0, "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"}, "max_tokens": 1500,
                "messages": [{"role": "system", "content": _SYSTEM_PROMPT + (EXTRA if extended else "")}, {"role": "user", "content": _user_payload([], [], [], [], evidence)}]})
            if response.status_code != 200:
                raise RuntimeError("MiMo HTTP " + str(response.status_code))
            data = response.json()
            content = json.loads(data["choices"][0]["message"]["content"])
            return {"case_id": case["id"], "arm": "extended" if extended else "current", "requests": 1,
                    "usage": data["usage"], "latency_ms": (perf_counter() - started) * 1000, "response": content}
        with ThreadPoolExecutor(max_workers=4) as executor:
            tasks = [executor.submit(one, c, e) for i, c in enumerate(cases) for e in ([False, True] if i % 2 == 0 else [True, False])]
            for future in as_completed(tasks):
                report["calls"].append(future.result())
                write(path, report)
    totals = {}
    for arm in ("current", "extended"):
        calls = [c for c in report["calls"] if c["arm"] == arm]
        totals[arm] = {k: sum(c["usage"].get(k, 0) for c in calls) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        totals[arm]["cached_input_tokens"] = sum(c["usage"].get("prompt_tokens_details", {}).get("cached_tokens", 0) for c in calls)
        totals[arm]["formed_cognitions"] = sum(len(c["response"].get("cognitions", [])) for c in calls)
        totals[arm]["requests"] = len(calls)
    delta = {k: totals["extended"][k] - totals["current"][k] for k in ("prompt_tokens", "completion_tokens", "total_tokens", "requests")}
    report.update(totals=totals, incremental=delta,
        incremental_cny_per_1000_uncached=(delta["prompt_tokens"] + 2 * delta["completion_tokens"]) / 1_000_000 * 1000 / len(cases),
        incremental_tokens_per_input={k: v / len(cases) for k, v in delta.items()},
        pricing_source="https://mimo.mi.com/static/docs/price/pay-as-you-go.md",
        caveat="12 paired synthetic inputs, assuming one memory/input; output length/model formation decisions can vary. Current formation already emits equivalent topic aliases. No production incremental bill is established.")
    write(path, report)
    print(json.dumps({k: report[k] for k in ("totals", "incremental", "incremental_cny_per_1000_uncached")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
