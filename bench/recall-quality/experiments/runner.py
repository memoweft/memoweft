"""MEM-5 bench-only candidate experiment over the real Trust recall pipeline.

Commands: tune (dev only), final (single-use blind + exposed review), scale.
The real snapshot, currentness, person anchoring, rendering and QueryService
remain in charge. No product files or frozen regression contracts are changed.
"""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

import numpy as np
import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from evaluate import aggregate, fixture_hash, percentile, recall, seed, QueryService
from aliases import generate, key, read, usage_report, write
from models import Embedder

OUT = HERE / "results"
ORIGINAL_ROWS = recall._current_world_rows
ORIGINAL_MATCH = recall._match_world_rows


def load(split):
    path = HERE / "blind.json" if split == "blind" else HERE.parent / f"{split}.json"
    return read(path)["cases"]


def memories(cases):
    return [m for c in cases for m in c["memories"]]


class CandidateIndex:
    def __init__(self, embedder, aliases, texts):
        self.embedder, self.aliases = embedder, aliases["entries"]
        self.vectors = {}
        ordered = sorted(set(texts))
        for start in range(0, len(ordered), 16):
            batch = ordered[start:start + 16]
            vectors = embedder.encode(batch)
            self.vectors.update(zip(batch, vectors))
        self.precomputed = None

    def scores(self, query, rows, compute_b=True):
        if self.precomputed is not None:
            return self.precomputed
        qtokens = recall._bigrams(recall._query_signal(query)) - recall._GENERIC_PREDICATE_BIGRAMS
        query_vector = self.embedder.encode([query], query=True)[0] if compute_b else None
        scored = {}
        for row in rows:
            text = str(row["content"])
            sidecar = self.aliases.get(key(text), {}).get("sidecar", {})
            variants = [sidecar.get("theme", ""), *sidecar.get("aliases", [])]
            # Pure multilingual lexical Dice; no handwritten synonym/domain list.
            a = max((2 * len(qtokens & tokens) / (len(qtokens) + len(tokens))
                     for phrase in variants if (tokens := recall._bigrams(phrase)) and qtokens), default=0.)
            b = float(query_vector @ self.vectors[text]) if compute_b else 0.
            scored[(str(row["kind"]), str(row["id"]))] = (a, b)
        return scored


def raw_rows(db):
    # Deliberately includes blocked and other-account synthetic rows so the final
    # authoritative gates are exercised, rather than trusting sidecar metadata.
    rows = []
    for kind, table in (("cognition", "cognition"), ("relationship", "relationship"), ("event", "world_event")):
        rows.extend({"kind": kind, "id": str(r[0]), "content": str(r[1])} for r in db.execute(f"SELECT id,content FROM {table} ORDER BY id"))
    return rows


@contextmanager
def candidate_route(index, arm, thresholds, query):
    state = {}

    def rows(db, subject_id, model_tier="local"):
        # Candidate scores exist BEFORE permissions/currentness filtering.
        state.update(db=db, subject=subject_id, tier=model_tier,
                     scores=index.scores(query, raw_rows(db), compute_b=arm in {"B", "AB"}))
        return ORIGINAL_ROWS(db, subject_id, model_tier)

    def match(text, eligible):
        eligible = list(eligible)
        baseline = ORIGINAL_MATCH(text, eligible)
        if arm == "baseline":
            return baseline
        # Reuse the actual matcher to apply query-specific current successor,
        # historical and name restrictions to semantic additions. Synthetic
        # match_text forces candidacy; it never changes returned claim content.
        synthetic = [{**row, "match_text": text, "predecessor_match_texts": ()} for row in eligible]
        with patch.object(recall, "MAX_ITEMS", max(5, len(synthetic))):
            admitted = {(str(r["kind"]), str(r["id"])) for r in ORIGINAL_MATCH(text, synthetic)}
        extras = []
        for row in eligible:
            item_key = str(row["kind"]), str(row["id"])
            a, b = state["scores"][item_key]
            accepted_a = arm in {"A", "AB"} and a >= thresholds["A"]
            accepted_b = arm in {"B", "AB"} and b >= thresholds["B"]
            if item_key in admitted and (accepted_a or accepted_b):
                score = max(a if accepted_a else 0, b if accepted_b else 0)
                extras.append({**row, "score": score})
        extras.sort(key=lambda r: (-r["score"], -r.get("confidence", 600), str(r["id"]), str(r["kind"])))
        selected, seen = [], set()
        for item in [*baseline, *extras]:
            item_key = str(item["kind"]), str(item["id"])
            if item_key in seen:
                continue
            # Last gate: same product predicate, connection, subject and tier.
            if not recall.world_item_visible(state["db"], state["subject"], item_key[0], item_key[1],
                    surface="model_cloud" if state["tier"] == "cloud" else "recall"):
                continue
            selected.append(item)
            seen.add(item_key)
            if len(selected) == 5:
                break
        return selected

    if arm == "baseline":
        yield
    else:
        with patch.object(recall, "_current_world_rows", rows), patch.object(recall, "_match_world_rows", match):
            yield


class Stores:
    def __init__(self, folder, cases):
        self.paths = {}
        for case in cases:
            if case["seed"] not in self.paths:
                path = Path(folder) / (case["seed"] + ".sqlite3")
                seed(path, case)
                self.paths[case["seed"]] = path

    def evaluate(self, cases, index, arm, thresholds, repeats=1, validate_readonly=True):
        results = []
        for case in cases:
            path = self.paths[case["seed"]]
            service = QueryService(path, subject_id="fictional-owner")
            latencies, first = [], None
            for _ in range(repeats):
                with candidate_route(index, arm, thresholds, case["query"]):
                    started = perf_counter()
                    result = service.preview_recall(case["query"], model_tier=case["tier"])["preview"]
                    latencies.append((perf_counter() - started) * 1000)
                if first is None:
                    first = result
                assert first == result, "Non-deterministic experiment"
            selected = [pair[1] for pair in first["selected_item_ids"]]
            rank = next((i + 1 for i, mid in enumerate(selected) if mid in case["expect"]), 0)
            if validate_readonly:
                with closing(sqlite3.connect(path)) as db:
                    before = db.total_changes
                    with candidate_route(index, arm, thresholds, case["query"]):
                        assert recall.recall_world_snapshot(db, "fictional-owner", case["query"], model_tier=case["tier"]) is not None
                    assert db.total_changes == before, "Recall wrote to World"
            results.append({"id": case["id"], "category": case["category"], "expect": case["expect"], "selected": selected,
                            "rank": rank, "wrong": sorted(set(selected) & set(case["forbid"])), "miss_cause": None if rank else "candidate_or_filter",
                            "latency_ms": percentile(latencies, 50), "call_latency_ms": latencies,
                            "must_hit": case.get("must_hit", False), "must_abstain": case.get("must_abstain", False)})
        return {"overall": aggregate(results), "categories": {category: aggregate([r for r in results if r["category"] == category]) for category in sorted({r["category"] for r in results})}, "results": results}


def protections(current, baseline):
    previous = {r["id"]: r for r in baseline["results"]}
    rows = current["results"]
    return {
        "lost_baseline_hit5": sum(0 < previous[r["id"]]["rank"] <= 5 and not 0 < r["rank"] <= 5 for r in rows),
        "new_false_queries": sum(bool(set(r["wrong"]) - set(previous[r["id"]]["wrong"])) for r in rows),
        "new_pure_negative_false_queries": sum(not r["expect"] and bool(set(r["wrong"]) - set(previous[r["id"]]["wrong"])) for r in rows),
        "lifecycle_leaks": sum(r["category"] == "lifecycle" and bool(r["selected"]) for r in rows),
        "required_positive_pass": sum(r["must_hit"] and 0 < r["rank"] <= 5 for r in rows),
        "required_positive_total": sum(r["must_hit"] for r in rows),
        "required_negative_pass": sum(r["must_abstain"] and not r["selected"] for r in rows),
        "required_negative_total": sum(r["must_abstain"] for r in rows),
    }


def precalculate(cases, index, stores):
    values = {}
    for case in cases:
        with closing(sqlite3.connect(stores.paths[case["seed"]])) as db:
            values[case["id"]] = index.scores(case["query"], raw_rows(db))
    return values


def tune():
    if (OUT / "parameters.json").exists():
        raise SystemExit("Parameters already frozen; do not silently retune")
    cases = load("dev")
    cache = generate(memories(cases), OUT / "aliases-dev.json")
    embedder = Embedder()
    index = CandidateIndex(embedder, cache, [m["content"] for m in memories(cases)])
    with TemporaryDirectory(prefix="mem5-dev-") as folder:
        stores = Stores(folder, cases)
        baseline = stores.evaluate(cases, index, "baseline", {})
        scores = precalculate(cases, index, stores)
        curves, selected = {}, {}
        for arm, grid in [("A", [round(i / 20, 3) for i in range(1, 21)] + [1.001]), ("B", [round(i / 100, 3) for i in range(65, 100)] + [1.001])]:
            curve = []
            for threshold in grid:
                rows = []
                for case in cases:
                    index.precomputed = scores[case["id"]]
                    rows.extend(stores.evaluate([case], index, arm, {"A": threshold, "B": threshold})["results"])
                current = {"overall": aggregate(rows), "results": rows}
                protected = protections(current, baseline)
                curve.append({"threshold": threshold, **current["overall"], **protected})
            eligible = [p for p in curve if p["new_false_queries"] <= 3 and p["new_pure_negative_false_queries"] == 0 and p["lost_baseline_hit5"] == 0 and p["lifecycle_leaks"] == 0]
            chosen = max(eligible, key=lambda p: (p["hit5"], -p["new_false_queries"], p["threshold"]))
            selected[arm] = chosen["threshold"]
            curves[arm] = curve
            print("dev selected", arm, json.dumps(chosen, ensure_ascii=False), flush=True)
        index.precomputed = None
        report = {"split": "dev", "baseline": baseline, "arms": {}, "threshold_curves": curves,
                  "embedding": embedder.metadata, "alias_cost": usage_report(cache)}
        for arm in ("A", "B", "AB"):
            current = stores.evaluate(cases, index, arm, selected, repeats=3)
            current["protections"] = protections(current, baseline)
            report["arms"][arm] = current
    write(OUT / "development.json", report)
    write(OUT / "parameters.json", {"thresholds": selected,
          "selection_rule": "Dev only: maximize Hit@5 with <=3/360 new false queries, zero new pure-negative false queries, zero baseline Hit@5 loss and zero lifecycle leaks; ties fewer false queries then higher threshold. AB unions independently chosen A/B, no extra tuning.",
          "blind_freeze_commit": "c30f14b", "blind_sha256_lf": read(HERE / "blind-manifest.json")["sha256_lf"],
          "dev_sha256_lf": fixture_hash((HERE.parent / "dev.json").read_bytes()),
          "source_sha256": {file: key((HERE / file).read_text(encoding="utf-8")) for file in ("runner.py", "aliases.py", "models.py")}})


def final():
    parameters = read(OUT / "parameters.json")
    assert fixture_hash((HERE / "blind.json").read_bytes()) == parameters["blind_sha256_lf"]
    assert all(key((HERE / file).read_text(encoding="utf-8")) == digest for file, digest in parameters["source_sha256"].items()), "Implementation changed since parameter freeze"
    marker = OUT / "final-once.json"
    with marker.open("x", encoding="utf-8") as handle:
        json.dump({"status": "reserved", "parameters_sha256": sha256((OUT / "parameters.json").read_bytes()).hexdigest()}, handle)
    # First access to blind cases by an experiment, AFTER reservation/freeze.
    cases = {split: load(split) for split in ("blind", "review")}
    cache = generate(memories(cases["blind"]) + memories(cases["review"]), OUT / "aliases-final.json")
    cache["entries"].update(read(OUT / "aliases-dev.json")["entries"])
    embedder = Embedder()
    report = {"protocol": "Single final invocation; one semantic evaluation per query/arm, deterministic validation repeat inside it. No blind curve or per-case stdout. Review never used to tune.", "thresholds": parameters["thresholds"], "splits": {}, "embedding": embedder.metadata,
              "alias_cost_final": usage_report(read(OUT / "aliases-final.json"))}
    for split, collection in cases.items():
        index = CandidateIndex(embedder, cache, [m["content"] for m in memories(collection)])
        with TemporaryDirectory(prefix="mem5-final-") as folder:
            stores = Stores(folder, collection)
            baseline = stores.evaluate(collection, index, "baseline", {})
            report["splits"][split] = {"baseline": baseline, "arms": {}}
            for arm in ("A", "B", "AB"):
                current = stores.evaluate(collection, index, arm, parameters["thresholds"], repeats=1)
                current["protections"] = protections(current, baseline)
                report["splits"][split]["arms"][arm] = current
                print(split, arm, json.dumps({**current["overall"], **current["protections"]}, ensure_ascii=False), flush=True)
        write(OUT / "final.json", report)
    write(marker, {"status": "completed", "parameters_sha256": sha256((OUT / "parameters.json").read_bytes()).hexdigest(),
                   "final_sha256": sha256((OUT / "final.json").read_bytes()).hexdigest()})


def refresh_dev():
    """Pre-blind timing repair, keeping selected thresholds and scores fixed."""
    assert not (OUT / "final-once.json").exists(), "Cannot refresh after blind access"
    parameters = read(OUT / "parameters.json")
    previous = read(OUT / "development.json")
    cases = load("dev")
    cache = read(OUT / "aliases-dev.json")
    embedder = Embedder()
    index = CandidateIndex(embedder, cache, [m["content"] for m in memories(cases)])
    with TemporaryDirectory(prefix="mem5-dev-timing-") as folder:
        stores = Stores(folder, cases)
        for arm in ("A", "B", "AB"):
            current = stores.evaluate(cases, index, arm, parameters["thresholds"], repeats=3)
            assert [[r[k] for k in ("selected", "rank", "wrong")] for r in current["results"]] == [[r[k] for k in ("selected", "rank", "wrong")] for r in previous["arms"][arm]["results"]], "Timing repair changed quality"
            current["protections"] = protections(current, previous["baseline"])
            previous["arms"][arm] = current
    previous["timing_repair"] = "Before blind: avoid computing an unused embedding in A. Thresholds and every dev selection/rank/error were asserted identical. Only final chosen-arm timing was remeasured; no retuning."
    write(OUT / "development.json", previous)
    parameters["source_sha256"] = {file: key((HERE / file).read_text(encoding="utf-8")) for file in ("runner.py", "aliases.py", "models.py")}
    parameters["preblind_repair"] = previous["timing_repair"]
    write(OUT / "parameters.json", parameters)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("tune", "final", "refresh-dev"))
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    {"tune": tune, "final": final, "refresh-dev": refresh_dev}[args.command]()


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as error:
        raise SystemExit("Network failure: " + type(error).__name__) from None
