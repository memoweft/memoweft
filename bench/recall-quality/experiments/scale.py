"""CPU scale evidence. Separates vector search from the existing full scan.

Vector sizes use 1,000 genuinely encoded synthetic records, tiled at larger N
to measure flat search bandwidth/top-k (not retrieval quality). Full-chain mode
seeds real World/Evidence records and measures the actual unmodified pipeline.
"""
import argparse
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np
import psutil

from aliases import read, write, usage_report
from models import Embedder
from runner import OUT, CandidateIndex, Stores, percentile


TEMPLATES = [
    "我在虚构项目{n}里每周整理纸质索引卡。", "我在虚构地点{n}固定乘渡轮通勤。",
    "我在虚构课程{n}里学习陶器修补。", "我在虚构任务{n}中偏好机械铅笔。",
    "我的虚构手账{n}每页只写三件事。", "我的虚构房间{n}使用暖色台灯。",
    "我在虚构周末安排{n}中经常练习口琴。", "我在虚构仓库{n}里收藏明信片。",
]


def synthetic(n):
    return [TEMPLATES[i % len(TEMPLATES)].format(n=i) for i in range(n)]


def distribution(values):
    return {"n": len(values), "p50_ms": percentile(values, 50), "p95_ms": percentile(values, 95), "mean_ms": sum(values) / len(values)}


def matrix():
    embedder = Embedder()
    texts = synthetic(1000)
    writes = []
    for text in texts[:100]:
        started = perf_counter()
        embedder.encode([text])
        writes.append((perf_counter() - started) * 1000)
    started = perf_counter()
    vectors = np.concatenate([embedder.encode(texts[start:start + 16]) for start in range(0, 1000, 16)])
    backfill = perf_counter() - started
    query_ms, query_vectors = [], []
    for i in range(60):
        started = perf_counter()
        query_vectors.append(embedder.encode([f"我在虚构项目{i}里用什么方法记录任务？"], query=True)[0])
        query_ms.append((perf_counter() - started) * 1000)
    report = {"embedding": embedder.metadata, "single_write": distribution(writes), "query_encode": distribution(query_ms),
              "batch_backfill": {"measured_records": 1000, "batch": 16, "seconds": backfill, "estimated_seconds": {str(n): backfill * n / 1000 for n in (1000, 10000, 50000)}},
              "vector_payload_bytes_per_record": 384 * 4, "matrix_search": {}, "storage": {}}
    alias_cache = read(OUT / "aliases-dev.json")
    aliases = [e["sidecar"] for e in alias_cache["entries"].values()]
    for n in (1000, 10000, 50000):
        matrix = np.tile(vectors, (n // 1000, 1))
        times = []
        for q in query_vectors:
            started = perf_counter()
            scores = matrix @ q
            top = np.argpartition(scores, -32)[-32:]
            top = top[np.argsort(-scores[top], kind="stable")]
            assert len(top) == 32
            times.append((perf_counter() - started) * 1000)
        report["matrix_search"][str(n)] = {**distribution(times), "matrix_bytes": matrix.nbytes,
            "mean_with_query_encode_ms": sum(times) / len(times) + sum(query_ms) / len(query_ms),
            "scope": "Preloaded float32 matrix, exact cosine dot + top32 order; no World/permission filtering; larger N tiles the 1000 measured vectors, not a quality corpus"}
        with TemporaryDirectory(prefix="mem5-storage-") as folder:
            path = Path(folder) / "synthetic.sqlite3"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE memory(id INTEGER PRIMARY KEY,content TEXT)")
            db.executemany("INSERT INTO memory VALUES(?,?)", [(i, texts[i % 1000]) for i in range(n)])
            db.commit()
            base = path.stat().st_size
            db.execute("CREATE TABLE vector(id INTEGER PRIMARY KEY, embedding BLOB)")
            db.executemany("INSERT INTO vector VALUES(?,?)", [(i, vectors[i % 1000].tobytes()) for i in range(n)])
            db.commit()
            vector_size = path.stat().st_size - base
            db.execute("DROP TABLE vector")
            db.execute("VACUUM")
            db.execute("CREATE TABLE aliases(id INTEGER PRIMARY KEY, sidecar TEXT)")
            import json
            db.executemany("INSERT INTO aliases VALUES(?,?)", [(i, json.dumps(aliases[i % len(aliases)], ensure_ascii=False, separators=(',', ':'))) for i in range(n)])
            db.commit()
            report["storage"][str(n)] = {"base_bytes": base, "vector_extra_bytes": vector_size,
                "alias_extra_bytes": path.stat().st_size - base, "scope": "Isolated SQLite sidecar tables including pages, not a production schema migration; aliases cycle through dev sidecars"}
            db.close()
        print("measured CPU matrix/storage", n, flush=True)
        write(OUT / "scale.json", report)
    report["rss_after_benchmark_bytes"] = psutil.Process().memory_info().rss
    write(OUT / "scale.json", report)


def full_chain():
    embedder = Embedder()
    cache = read(OUT / "aliases-dev.json")
    cache["entries"] = {}  # No A in this capacity measurement.
    texts = [t.format(n=0) for t in TEMPLATES]
    index = CandidateIndex(embedder, cache, texts)
    thresholds = read(OUT / "parameters.json")["thresholds"]
    report = {"scope": "One cold-ish paired observation per size, baseline and B; real seeded World, raw candidate scores then real eligibility/full scan/render. No p95 claim; repeated template content models scan cost only.", "sizes": {}}
    for n in (1000, 10000, 50000):
        case = {"id": "scale", "seed": "scale", "category": "scale", "query": "我用什么本子记录事情？", "tier": "local",
                "memories": [{"id": f"scale-{i:05}", "content": texts[i % len(texts)]} for i in range(n)], "expect": [], "forbid": []}
        with TemporaryDirectory(prefix="mem5-full-scale-") as folder:
            started = perf_counter()
            stores = Stores(folder, [case])
            seed_s = perf_counter() - started
            item = {"seed_seconds": seed_s}
            for arm in ("baseline", "B"):
                print("starting full chain", n, arm, flush=True)
                # No duplicate slow validation at capacity sizes; read-only
                # behavior was already validated on every quality case.
                result = stores.evaluate([case], index, arm, thresholds, validate_readonly=False)
                item[arm] = result["overall"]["latency_ms"]["p50"]
                print("completed full chain", n, arm, item[arm], "ms", flush=True)
                report["sizes"][str(n)] = item
                write(OUT / "full-scale.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--full-chain", action="store_true")
    args = parser.parse_args()
    (full_chain if args.full_chain else matrix)()
