"""Production Trust preview quality gate. Offline, temporary synthetic stores.

Run from py: uv run python ../bench/recall-quality/evaluate.py --check
An optional --reference-ref loads the historical recall module from Git for a
paired same-process latency comparison. No historical database is read.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack, closing, contextmanager
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "py/src"))
from memoweft.integrations.hermes import recall  # noqa: E402
from memoweft.integrations.trust.query_service import QueryService  # noqa: E402
from memoweft.integrations.trust.revision import advance_world_revision  # noqa: E402
from memoweft.store.driver import open_db  # noqa: E402
from memoweft.store.schema import SCHEMA_VERSION  # noqa: E402


def seed(path, case, padding=0):
    db = open_db(str(path))
    # Fixture setup is outside latency measurement. Seed atomically instead of
    # fsyncing each synthetic insert, keeping the offline CI gate short.
    db.execute("BEGIN")
    memories = case["memories"] + [{"id": f"padding-{i:04}", "content": f"我收藏虚构邮票编号{i:04}。"} for i in range(padding)]
    for memory in memories:
        mid, text, state = memory["id"], memory["content"], memory.get("state", "active")
        subject = "fictional-other" if state == "other_account" else "fictional-owner"
        ev_subject = "fictional-other" if state == "cross_evidence" else subject
        db.execute("INSERT INTO evidence(id,subject_id,source_kind,host_id,occurred_at,recorded_at,raw_content,summary,allow_local_read,allow_cloud_read,allow_inference,deleted_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("e-" + mid, ev_subject, "spoken", "synthetic-host", "2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00.000Z", text, text, int(state != "local_denied"), int(state != "cloud_denied"), 1, "2026-01-02" if state == "forgotten" else None))
        db.execute("INSERT INTO cognition(id,subject_id,content,content_type,formed_by,confidence,cred_status,invalid_at,archived_at,muted_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (mid, subject, text, "preference", "stated", 600, "stable", "2026-01-02" if state == "invalid" else None, "2026-01-02" if state == "archived" else None, "2026-01-02" if state == "muted" else None, "2026-01-01", "2026-01-01"))
        db.execute("INSERT INTO cognition_evidence VALUES(?,?,?)", (mid, "e-" + mid, "support"))
        if memory.get("predecessor"):
            db.execute("INSERT INTO cognition_transitions VALUES(?,?,?,?,?)", ("t-" + mid, memory["predecessor"], mid, "corrects", 1))
        if state in {"expired", "future"}:
            db.execute("INSERT INTO observed_source(subject_id,host_id,source_hash,evidence_id,version,payload_hash,permission_version,valid_at,valid_until) VALUES(?,?,?,?,?,?,?,?,?)", (subject, "synthetic-host", mid, "e-" + mid, "1", "synthetic", "1", "9999-01-01T00:00:00.000Z" if state == "future" else "2000-01-01T00:00:00.000Z", "2000-01-02T00:00:00.000Z" if state == "expired" else None))
    if case.get("person_name"):
        name = case["person_name"]
        text = case["memories"][0]["content"]
        db.execute("INSERT INTO entity(id,world_id,kind,canonical_name,invalid_at,created_at,updated_at,aliases_json) VALUES('synthetic-person','fictional-owner','person',?,NULL,'2026-01-01','2026-01-01','[]')", (name,))
        db.execute("INSERT INTO cognition_target(cognition_id,target_entity_id) VALUES('person-fact','synthetic-person')")
        db.execute("INSERT INTO evidence_ledger(id,content,payload_json) VALUES(?,?,?)", ('naming-support', json.dumps({'relation': 'support', 'entity_id': 'synthetic-person', 'evidence_id': 'e-person-fact'}), json.dumps({'schema_version': 1, 'start': 0, 'end': len(text)})))
    advance_world_revision(db)
    db.commit()
    db.close()


def percentile(values, p):
    values = sorted(values)
    return values[max(0, (len(values) * p + 99) // 100 - 1)] if values else 0


def aggregate(rows):
    positives = [row for row in rows if row["expect"]]
    negatives = [row for row in rows if not row["expect"]]
    mean = lambda values: sum(values) / len(values) if values else 0
    return {"n": len(rows), "positive_n": len(positives), "negative_query_n": len(negatives), **{f"hit{k}": mean([int(0 < row["rank"] <= k) for row in positives]) for k in (1, 3, 5)}, "mrr5": mean([1 / row["rank"] if 0 < row["rank"] <= 5 else 0 for row in positives]), "negative_false_recall_rate": mean([bool(row["wrong"]) for row in rows]), "negative_query_false_recall_rate": mean([bool(row["wrong"]) for row in negatives]), "negative_returned_count": sum(len(row["wrong"]) for row in rows), "latency_ms": {"p50": percentile([row["latency_ms"] for row in rows], 50), "p95": percentile([row["latency_ms"] for row in rows], 95)}, "returned_mean": mean([len(row["selected"]) for row in rows]), "returned_p95": percentile([len(row["selected"]) for row in rows], 95), "miss_causes": dict(Counter(row["miss_cause"] for row in positives if row["rank"] == 0))}


def diagnose(db, case, selected):
    expected = set(case["expect"])
    if not expected or expected.intersection(selected):
        return None
    rows = recall._current_world_rows(db, "fictional-owner", case["tier"])
    if not expected.intersection(str(row["id"]) for row in rows):
        return "filter"
    with patch.object(recall, "MAX_ITEMS", 10000):
        uncut = recall._match_world_rows(case["query"], rows)
    if expected.intersection(str(row["id"]) for row in uncut):
        return "rank_cutoff"
    return "candidate"


@contextmanager
def historical_module(ref):
    # Exact version, including all gates. Kept out of the public product API.
    code = subprocess.check_output(["git", "show", f"{ref}:py/src/memoweft/integrations/hermes/recall.py"], cwd=ROOT)
    name = "memoweft.integrations.hermes._quality_reference"
    with TemporaryDirectory(prefix="memoweft-quality-reference-") as folder:
        path = Path(folder) / "recall.py"
        path.write_bytes(code)
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        try:
            yield module
        finally:
            del sys.modules[name]


def run(cases, repeats=7, reference=None, padding=0):
    results = []
    reference_results = []
    with TemporaryDirectory(prefix="memoweft-recall-quality-") as folder:
        paths = {}
        for case in cases:
            path = Path(folder) / (case["seed"] + ".sqlite3")
            if case["seed"] not in paths:
                seed(path, case, padding)
                paths[case["seed"]] = path
            service = QueryService(path, subject_id="fictional-owner")
            first = service.preview_recall(case["query"], model_tier=case["tier"])["preview"]
            selected = [pair[1] for pair in first["selected_item_ids"]]
            latencies, reference_latencies = [], []
            with closing(sqlite3.connect(path)) as db:
                for iteration in range(repeats):
                    # Interleave order to avoid systematic cache/order advantage.
                    for arm in (["current", "reference"] if iteration % 2 == 0 else ["reference", "current"]):
                        if arm == "reference" and reference is None:
                            continue
                        if arm == "reference":
                            with patch.object(recall, "recall_world_snapshot", reference.recall_world_snapshot):
                                started = perf_counter()
                                previous = service.preview_recall(case["query"], model_tier=case["tier"])["preview"]
                                reference_latencies.append((perf_counter() - started) * 1000)
                        else:
                            started = perf_counter()
                            result = service.preview_recall(case["query"], model_tier=case["tier"])["preview"]
                            latencies.append((perf_counter() - started) * 1000)
                            assert result == first, case["id"] + ": nondeterministic"
                changes = db.total_changes
                assert recall.recall_world_snapshot(db, "fictional-owner", case["query"], model_tier=case["tier"]) is not None
                assert db.total_changes == changes, "recall wrote to World"
                cause = diagnose(db, case, selected)
            expected = set(case["expect"])
            rank = next((i + 1 for i, mid in enumerate(selected) if mid in expected), 0)
            row = {"id": case["id"], "category": case["category"], "expect": case["expect"], "selected": selected, "rank": rank, "wrong": sorted(set(selected) & set(case["forbid"])), "miss_cause": cause, "latency_ms": percentile(latencies, 50), "call_latency_ms": latencies}
            if reference is not None:
                row["reference_latency_ms"] = percentile(reference_latencies, 50)
                row["reference_call_latency_ms"] = reference_latencies
                row["reference_selected"] = [pair[1] for pair in previous["selected_item_ids"]]
                reference_selected = row["reference_selected"]
                reference_results.append({**row, "selected": reference_selected,
                    "rank": next((i + 1 for i, mid in enumerate(reference_selected) if mid in expected), 0),
                    "wrong": sorted(set(reference_selected) & set(case["forbid"])),
                    "latency_ms": row["reference_latency_ms"], "miss_cause": None})
            results.append(row)
    return {"reference": {"overall": aggregate(reference_results), "categories": {category: aggregate([row for row in reference_results if row["category"] == category]) for category in sorted({row["category"] for row in reference_results})}, "results": reference_results} if reference else None, "overall": aggregate(results), "per_call_latency_ms": {"p50": percentile([t for row in results for t in row["call_latency_ms"]], 50), "p95": percentile([t for row in results for t in row["call_latency_ms"]], 95)}, "reference_per_call_latency_ms": {"p50": percentile([t for row in results for t in row.get("reference_call_latency_ms", [])], 50), "p95": percentile([t for row in results for t in row.get("reference_call_latency_ms", [])], 95)}, "categories": {category: aggregate([row for row in results if row["category"] == category]) for category in sorted({row["category"] for row in results})}, "results": results}


def fixture_hash(raw):
    """Hash UTF-8 fixture content with CRLF/CR normalized to LF."""
    return sha256(raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def check_fixtures(hashes, baseline):
    for split, digest in hashes.items():
        if baseline["fixture_hashes"].get(split) != digest:
            raise SystemExit(f"{split}: frozen fixture changed")


def check(report, baseline, exact=False):
    failures = []
    for split, current in report["splits"].items():
        prior = {row["id"]: row for row in baseline["splits"][split]["results"]}
        ids = [row["id"] for row in current["results"]]
        if len(ids) != len(set(ids)) or set(ids) != set(prior):
            failures.append(split + ": missing, duplicate or unexpected cases")
        for row in current["results"]:
            if row["id"] not in prior:
                continue
            old = prior[row["id"]]
            if set(row["wrong"]) - set(old["wrong"]):
                failures.append(row["id"] + ": new false recall")
            if 0 < old["rank"] <= 5 and not 0 < row["rank"] <= 5:
                failures.append(row["id"] + ": lost baseline positive")
            if old.get("must_hit") and not 0 < row["rank"] <= 5:
                failures.append(row["id"] + ": required review positive missing")
            if old.get("must_abstain") and row["selected"]:
                failures.append(row["id"] + ": required review abstention failed")
            if row["category"] == "lifecycle" and row["selected"]:
                failures.append(row["id"] + ": lifecycle leak")
            if exact and any(row[key] != old[key] for key in ("selected", "rank", "wrong")):
                failures.append(row["id"] + ": differs from recorded baseline")
    # Small fixture stores and shared-machine scheduling produce unstable p95.
    # Timings remain in reports, but are not an acceptance gate.
    if failures:
        raise SystemExit("Quality regressions:\n" + "\n".join(failures))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "challenge", "holdout", "review", "all"], default="all")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--reference-ref")
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--padding", type=int, default=0)
    parser.add_argument("--exact-baseline", action="store_true", help="Require identical per-case selection, hit and false-recall results")
    args = parser.parse_args()
    if args.repeats < 1 or args.padding < 0:
        parser.error("repeats must be positive and padding nonnegative")
    started = perf_counter()
    splits = ["dev", "challenge", "holdout", "review"] if args.split == "all" else [args.split]
    fixtures = {split: json.loads((HERE / f"{split}.json").read_text(encoding="utf-8"))["cases"] for split in splits}
    report = {"version": 2, "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "product_recall_sha256": fixture_hash((ROOT / "py/src/memoweft/integrations/hermes/recall.py").read_bytes()), "schema_version": SCHEMA_VERSION, "fixture_hashes": {split: fixture_hash((HERE / f"{split}.json").read_bytes()) for split in splits}, "measurement": "Trust QueryService.preview_recall, median of interleaved warm calls per query; includes connection, currentness, recent evidence, pending corrections and rendering", "latency_policy": "Report only: small synthetic stores and shared-machine timing are not a stable relative performance gate", "repeats": args.repeats, "padding": args.padding, "splits": {}}
    baseline = json.loads((HERE / "regression-baseline.json").read_text(encoding="utf-8")) if args.check else None
    if baseline is not None:
        check_fixtures(report["fixture_hashes"], baseline)
    with ExitStack() as stack:
        reference = stack.enter_context(historical_module(args.reference_ref)) if args.reference_ref else None
        report["splits"] = {split: run(fixtures[split], args.repeats, reference, args.padding) for split in splits}
    report["duration_seconds"] = perf_counter() - started
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for split, result in report["splits"].items():
        print(split, json.dumps(result["overall"], ensure_ascii=False))
    if args.check:
        check(report, baseline, args.exact_baseline)
        print("Quality gate passed")


if __name__ == "__main__":
    main()
