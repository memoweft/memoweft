"""Frozen English precision gate through production snapshots, without models.

The explicit --record-main command records the unmodified Git reference, never
the working implementation. Normal pytest needs no Git history or network.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import asdict
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from typing import Any

from memoweft.integrations.hermes import recall
from memoweft.store import open_db

ROOT = Path(__file__).resolve().parents[3]
HERE = ROOT / "bench/recall-quality"
spec = importlib.util.spec_from_file_location("english_gate_seed", HERE / "evaluate.py")
assert spec is not None and spec.loader is not None
fixture: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def digest(raw: bytes) -> str:
    return sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def cases() -> list[dict[str, Any]]:
    return json.loads((HERE / "english.json").read_text(encoding="utf-8"))["cases"]  # type: ignore[no-any-return]


def seed(path: Path, case: dict[str, Any]) -> None:
    fixture.seed(path, case)
    with closing(open_db(str(path))) as db:
        for memory in case["memories"]:
            mid, state = memory["id"], memory.get("state")
            if state == "assistant":
                db.execute("UPDATE evidence SET raw_content='I collect ceramic bowls.', summary='', "
                           "preceding_ai_context=? WHERE id=?", (memory["content"], "e-" + mid))
                db.execute("UPDATE cognition SET content='I collect ceramic bowls.' WHERE id=?", (mid,))
            elif state == "unformed":
                db.execute("DELETE FROM cognition_evidence WHERE cognition_id=?", (mid,))
                db.execute("DELETE FROM cognition WHERE id=?", (mid,))
        db.commit()


def observe(module: Any, db: Any, case: dict[str, Any], *, lang: str = "en") -> dict[str, Any]:
    snapshot = module.recall_world_snapshot(db, "fictional-owner", case["query"],
                                          model_tier=case.get("tier", "local"), lang=lang)
    assert snapshot is not None, case["id"]
    selected = [pair[1] for pair in snapshot.selected_item_ids]
    expected = set(case["expect"])
    return {"id": case["id"], "selected": selected,
            "rank": next((i + 1 for i, mid in enumerate(selected) if mid in expected), 0),
            "wrong": sorted(set(selected) - expected),
            "snapshot_sha256": digest(json.dumps(asdict(snapshot), sort_keys=True, ensure_ascii=False).encode())}


def run(items: list[dict[str, Any]], module: Any = recall, *, repeats: int = 1) -> list[dict[str, Any]]:
    results = []
    with TemporaryDirectory(prefix="english-recall-gate-") as folder:
        for case in items:
            path = Path(folder) / "world.sqlite3"
            # Different cases must never inherit a prior case's facts.
            path = path.with_name(case["id"] + ".sqlite3")
            seed(path, case)
            with closing(open_db(str(path))) as db:
                before = list(db.iterdump())
                row = observe(module, db, case)
                for _ in range(repeats - 1):
                    assert observe(module, db, case) == row, case["id"]
                assert list(db.iterdump()) == before, case["id"]
            results.append(row)
    return results


def metrics(items: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    positive_n = sum(bool(case["expect"]) for case in items)
    hit_n = sum(bool(row["rank"]) for row in rows)
    returned = sum(len(row["selected"]) for row in rows)
    wrong = sum(len(row["wrong"]) for row in rows)
    return {"queries": len(rows), "positives": positive_n, "hits": hit_n,
            "recall": hit_n / positive_n, "returned": returned, "wrong": wrong,
            "precision": (returned - wrong) / returned if returned else 1.0}


def check(items: list[dict[str, Any]], rows: list[dict[str, Any]], baseline: dict[str, Any]) -> None:
    assert digest((HERE / "english.json").read_bytes()) == baseline["fixture_sha256"], "frozen English fixture changed"
    assert [row["id"] for row in rows] == [row["id"] for row in baseline["results"]]
    for case, row, old in zip(items, rows, baseline["results"], strict=True):
        assert not old["rank"] or row["rank"], f'{case["id"]}: lost main positive'
        assert not (set(row["wrong"]) - set(old["wrong"])), f'{case["id"]}: new false recall {row["wrong"]}'
        if case.get("required"):
            assert row["rank"] == 1 and not row["wrong"], f'{case["id"]}: review regression {row}'
    current, old_metrics = metrics(items, rows), baseline["metrics"]
    assert current["recall"] >= old_metrics["recall"]
    assert current["precision"] >= old_metrics["precision"]
    assert current["wrong"] <= old_metrics["wrong"]


def chinese(module: Any) -> dict[str, str]:
    result = {}
    with TemporaryDirectory(prefix="chinese-byte-gate-") as folder:
        for split in ("dev", "challenge", "holdout", "review"):
            split_cases = json.loads((HERE / f"{split}.json").read_text(encoding="utf-8"))["cases"]
            paths: dict[str, Path] = {}
            for case in split_cases:
                if case["seed"] not in paths:
                    paths[case["seed"]] = Path(folder) / f'{split}-{case["seed"]}.sqlite3'
                    fixture.seed(paths[case["seed"]], case)
                with closing(open_db(str(paths[case["seed"]]))) as db:
                    result[f'{split}/{case["id"]}'] = observe(module, db, case, lang="zh")["snapshot_sha256"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record-main", help="Explicit immutable main reference for baseline creation")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    items = cases()
    if args.record_main:
        assert args.out is not None
        ref = subprocess.check_output(["git", "rev-parse", args.record_main], cwd=ROOT, text=True).strip()
        with fixture.historical_module(ref) as module:
            rows = run(items, module, repeats=args.repeats)
            frozen = {"reference": ref, "fixture_sha256": digest((HERE / "english.json").read_bytes()),
                      "source_sha256": digest(subprocess.check_output(["git", "show", f"{ref}:py/src/memoweft/integrations/hermes/recall.py"], cwd=ROOT)),
                      "metrics": metrics(items, rows), "results": rows, "chinese_snapshots": chinese(module)}
            args.out.write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(frozen["metrics"]))
    else:
        baseline = json.loads((HERE / "english-main-baseline.json").read_text(encoding="utf-8"))
        rows = run(items, repeats=args.repeats)
        report = {"metrics": metrics(items, rows), "results": rows}
        if args.out:
            args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report["metrics"]))
        check(items, rows, baseline)
        assert chinese(recall) == baseline["chinese_snapshots"], "Chinese full snapshots changed"


if __name__ == "__main__":
    main()
