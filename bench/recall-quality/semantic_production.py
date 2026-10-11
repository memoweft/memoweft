"""MEM-6 production-path synthetic evidence; no new blind set or model generation.

Use an existing verified model cache. Run under the machine's heavy-work slot.
Frozen MEM-4/5 data/results are read only; all new evidence has its own folder.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import threading
from time import perf_counter, sleep

os.environ["MEMOWEFT_SEMANTIC_ENABLED"] = "0"
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "py/src"))
from evaluate import seed, aggregate, percentile, QueryService
from memoweft.semantic.config import SemanticConfig
from memoweft.semantic.model import E5Encoder, FILES, MODEL_ID
from memoweft.semantic.runtime import configure_runtime, close_runtime

OUT = Path(__file__).parent / "semantic-results"


def write(name, payload):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wait_ready(runtime, seconds=120):
    deadline = perf_counter() + seconds
    while not runtime.backfill_complete:
        status = runtime.status()
        if status["state"] == "unavailable" and status["reason"] != "not_loaded":
            raise RuntimeError(status["reason"])
        if perf_counter() > deadline:
            raise TimeoutError("synthetic backfill deadline")
        sleep(0.01)


def result(case, preview, timings):
    selected = [pair[1] for pair in preview["selected_item_ids"]]
    rank = next((i + 1 for i, item in enumerate(selected) if item in case["expect"]), 0)
    return {"id": case["id"], "category": case["category"], "expect": case["expect"],
            "selected": selected, "rank": rank, "wrong": sorted(set(selected) & set(case["forbid"])),
            "miss_cause": None if rank else "candidate_or_filter", "latency_ms": percentile(timings, 50),
            "call_latency_ms": timings, "must_hit": case.get("must_hit", False), "must_abstain": case.get("must_abstain", False)}


def quality(encoder, cache):
    report = {"model_id": MODEL_ID, "threshold": 0.87, "max_additions": 3,
              "protocol": "Parameters chosen from existing MEM-5 development curve before this run; exposed blind is reference only. No retuning.", "splits": {}}
    for split in ("dev", "challenge", "holdout", "review", "blind"):
        fixture = Path(__file__).parent / ("experiments/blind.json" if split == "blind" else split + ".json")
        cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
        baselines, current = [], []
        with TemporaryDirectory(prefix="mem6-quality-") as folder:
            paths, runtimes = {}, {}
            try:
                for case in cases:
                    if case["seed"] not in paths:
                        path = Path(folder) / (case["seed"] + ".sqlite3")
                        seed(path, case)
                        paths[case["seed"]] = path
                    service = QueryService(paths[case["seed"]], subject_id="fictional-owner")
                    start = perf_counter()
                    preview = service.preview_recall(case["query"], model_tier=case["tier"])["preview"]
                    baselines.append(result(case, preview, [(perf_counter() - start) * 1000]))
                for key, path in paths.items():
                    runtime = configure_runtime(path, SemanticConfig(cache_dir=cache))
                    runtime._factory = lambda folder, stop: encoder
                    runtime.start()
                    wait_ready(runtime)
                    runtimes[key] = runtime
                for case, baseline in zip(cases, baselines):
                    service = QueryService(paths[case["seed"]], subject_id="fictional-owner")
                    timings, first = [], None
                    for repeat in range(3):
                        start = perf_counter()
                        preview = service.preview_recall(case["query"], model_tier=case["tier"])["preview"]
                        timings.append((perf_counter() - start) * 1000)
                        if first is None:
                            first = preview
                        assert first == preview, "warm recall changed for a fixed index/revision"
                    row = result(case, first, timings)
                    assert row["selected"][:len(baseline["selected"])] == baseline["selected"], "literal order changed"
                    assert len(row["selected"]) - len(baseline["selected"]) <= 3
                    current.append(row)
                protected = {"lost_baseline_hit5": sum(0 < old["rank"] <= 5 and not 0 < new["rank"] <= 5 for old, new in zip(baselines, current)),
                             "new_false_queries": sum(bool(set(new["wrong"]) - set(old["wrong"])) for old, new in zip(baselines, current)),
                             "required_positive_pass": sum(r["must_hit"] and 0 < r["rank"] <= 5 for r in current),
                             "required_positive_total": sum(r["must_hit"] for r in current),
                             "required_negative_pass": sum(r["must_abstain"] and not r["selected"] for r in current),
                             "required_negative_total": sum(r["must_abstain"] for r in current)}
                assert protected["lost_baseline_hit5"] == 0
                assert protected["required_positive_pass"] == protected["required_positive_total"]
                assert protected["required_negative_pass"] == protected["required_negative_total"]
                report["splits"][split] = {"fixture_sha256_lf": sha256(fixture.read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest(),
                    "baseline": {"overall": aggregate(baselines), "results": baselines},
                    "semantic": {"overall": aggregate(current), "protections": protected, "results": current}}
                print(split, json.dumps({**aggregate(current), **protected}), flush=True)
                write("quality.json", report)
            finally:
                for path in paths.values():
                    close_runtime(path)


TEMPLATES = ["我在虚构项目{}里每周整理纸质索引卡。", "我在虚构地点{}固定乘渡轮通勤。",
             "我在虚构课程{}里学习陶器修补。", "我在虚构任务{}中偏好机械铅笔。",
             "我的虚构手账{}每页只写三件事。", "我的虚构房间{}使用暖色台灯。",
             "我在虚构周末安排{}中经常练习口琴。", "我在虚构仓库{}里收藏明信片。"]


def distribution(times):
    return {"n": len(times), "p50_ms": percentile(times, 50), "p95_ms": percentile(times, 95), "per_call_ms": times}


def scale(encoder, cache, repeats, sizes):
    report = {"model_id": MODEL_ID, "threshold": 0.87, "max_additions": 3,
              "scope": "Full QueryService.preview_recall; real synthetic SQLite rows, real encoded vectors. Cold means schema installed but model loading held in background, so every foreground call falls back immediately. Warm includes query encoding, cosine matrix, original gates and rendering. No query vector cache.", "sizes": {}}
    for size in sizes:
        with TemporaryDirectory(prefix="mem6-scale-") as folder:
            path = Path(folder) / "synthetic.sqlite3"
            case = {"memories": [{"id": f"scale-{i:06}", "content": TEMPLATES[i % len(TEMPLATES)].format(i)} for i in range(size)]}
            started = perf_counter()
            seed(path, case)
            setup_s = perf_counter() - started
            runtime = configure_runtime(path, SemanticConfig(cache_dir=cache))
            release = threading.Event()
            def held(folder, stop):
                while not stop.is_set() and not release.wait(0.01):
                    pass
                return encoder
            runtime._factory = held
            service = QueryService(path, subject_id="fictional-owner")
            queries = ["我用什么本子记录事情？", "我怎么去上班？", "我有哪些业余爱好？"]
            times = []
            for repeat in range(repeats):
                started = perf_counter()
                service.preview_recall(queries[repeat % len(queries)])
                times.append((perf_counter() - started) * 1000)
            cold = distribution(times)
            print("cold", size, cold["p50_ms"], cold["p95_ms"], flush=True)
            started = perf_counter()
            release.set()
            wait_ready(runtime, seconds=1200)
            backfill_s = perf_counter() - started
            # First warm query separately exposes lazy matrix materialisation.
            started = perf_counter()
            service.preview_recall(queries[0])
            matrix_first_ms = (perf_counter() - started) * 1000
            times = []
            for repeat in range(repeats):
                started = perf_counter()
                service.preview_recall(queries[repeat % len(queries)])
                times.append((perf_counter() - started) * 1000)
            with closing(sqlite3.connect(path)) as db:
                vectors = db.execute("SELECT count(*) FROM semantic_vector").fetchone()[0]
                assert vectors == size
            report["sizes"][str(size)] = {"seed_seconds": setup_s, "backfill_seconds": backfill_s,
                "actual_encoded_records": size, "vector_rows": vectors, "cold": cold,
                "warm": distribution(times), "first_matrix_recall_ms": matrix_first_ms}
            print("warm", size, json.dumps(report["sizes"][str(size)]), flush=True)
            write("scale.json", report)
            close_runtime(path)


def dependencies():
    # Installed RECORD-owned file bytes; includes runtime transitive packages.
    names = ["numpy", "onnxruntime", "tokenizers", "flatbuffers", "protobuf", "sympy", "mpmath", "huggingface-hub", "filelock", "fsspec", "hf-xet", "pyyaml", "tqdm", "click", "packaging", "colorama"]
    result = {}
    for name in names:
        dist = importlib.metadata.distribution(name)
        files = [dist.locate_file(f) for f in dist.files or []]
        result[name] = {"version": dist.version, "installed_file_bytes": sum(f.stat().st_size for f in files if f.is_file()),
                        "license": dist.metadata.get("License-Expression") or dist.metadata.get("License"),
                        "license_classifiers": [c for c in dist.metadata.get_all("Classifier", []) if "License" in c]}
    write("dependencies.json", result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["quality", "scale", "dependencies"])
    parser.add_argument("--cache", type=Path, default=ROOT / ".local/mem5/model")
    parser.add_argument("--repeats", type=int, default=21)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 50000])
    args = parser.parse_args()
    if args.phase == "dependencies":
        dependencies()
    else:
        started = perf_counter()
        encoder = E5Encoder(args.cache, threading.Event())
        print("verified model loaded", (perf_counter() - started) * 1000, "ms", flush=True)
        if args.phase == "quality": quality(encoder, args.cache)
        else: scale(encoder, args.cache, args.repeats, args.sizes)
