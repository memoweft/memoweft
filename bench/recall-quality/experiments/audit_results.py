"""Post-final accounting only; reads saved rows, never invokes recall/models."""
from collections import Counter
from aliases import read, write
from runner import HERE, OUT, load


def main():
    assert read(OUT / "final-once.json")["status"] == "completed"
    dev, final = read(OUT / "development.json"), read(OUT / "final.json")
    sources = {"dev": {"baseline": dev["baseline"], "arms": dev["arms"]}, **final["splits"]}
    report = {"scope": "Derived from saved final results only; no rerun, relabeling or threshold changes",
        "lifecycle_metric_note": "The frozen runner's lifecycle_leaks counts ANY return on a lifecycle-category query. It therefore includes unrelated active distractors. Actual blocked-memory returns are audited separately below; original rows remain unchanged.", "splits": {}}
    for split, arms in sources.items():
        cases = {c["id"]: c for c in load(split)}
        baseline = {r["id"]: r for r in arms["baseline"]["results"]}
        current = {}
        for arm in ("baseline", "A", "B", "AB"):
            result = arms["baseline"] if arm == "baseline" else arms["arms"][arm]
            blocked, gains, positive_wrong = [], 0, 0
            for row in result["results"]:
                case = cases[row["id"]]
                by_id = {m["id"]: m for m in case["memories"]}
                for selected in row["selected"]:
                    if by_id[selected].get("state", "active") != "active":
                        blocked.append({"case_id": row["id"], "id": selected, "state": by_id[selected]["state"]})
                gains += bool(row["rank"] and not baseline[row["id"]]["rank"])
                positive_wrong += bool(row["expect"] and row["wrong"])
            current[arm] = {"actual_blocked_returns": blocked, "new_hits_over_baseline": gains,
                "baseline_misses": sum(bool(r["expect"] and not r["rank"]) for r in baseline.values()),
                "positive_queries_with_wrong_memory": positive_wrong,
                "false_queries": sum(bool(r["wrong"]) for r in result["results"]),
                "review_failures": [{"id": r["id"], "query": cases[r["id"]]["query"], "selected": r["selected"], "wrong": r["wrong"]}
                                    for r in result["results"] if split == "review" and r["wrong"]]}
        report["splits"][split] = current
    write(OUT / "derived-audit.json", report)
    print({split: {arm: {"actual_blocked_return_count": len(v["actual_blocked_returns"]), "new_hits": v["new_hits_over_baseline"], "positive_with_wrong": v["positive_queries_with_wrong_memory"]} for arm, v in arms.items()} for split, arms in report["splits"].items()})


if __name__ == "__main__":
    main()
