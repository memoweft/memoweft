"""Deterministic contract checks for the frozen synthetic Nanjing input."""

import hashlib
import json
from pathlib import Path
from typing import Any, cast


FIXTURE = Path(__file__).parent / "fixtures" / "next" / "golden-001-nanjing"


def _json(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURE / name).read_text(encoding="utf-8")))


def test_nanjing_frozen_synthetic_contract_and_hashes() -> None:
    manifest = _json("manifest.json")
    assert manifest["fixture_version"] == "1.0.0-synthetic"
    assert manifest["status"] == "frozen"
    assert manifest["synthetic"] is True
    assert manifest["owner_approved"] is True
    assert manifest["not_gate_1_evidence"] is True

    turns = [json.loads(line) for line in (FIXTURE / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    assert turns and all(set(t) == {"turn_id", "conversation_id", "role", "content", "occurred_at"} for t in turns)
    assert len({t["turn_id"] for t in turns}) == len(turns)
    assert len({t["conversation_id"] for t in turns}) == 1
    assert all(t["role"] in {"user", "assistant", "tool"} for t in turns)

    by_id = {t["turn_id"]: t for t in turns}
    allowlist = manifest["evidence_allowlist"]["turn_ids"]
    assert manifest["evidence_allowlist"]["user_or_authoritative_tool_only"] is True
    assert set(allowlist) <= set(by_id)
    assert all(by_id[turn_id]["role"] in {"user", "tool"} for turn_id in allowlist)
    assert not (set(allowlist) & {t["turn_id"] for t in turns if t["role"] == "assistant"})

    predicates = _json("expected-predicates.json")
    required_refs = {
        p["evidence_id"]
        for p in predicates["required"]
        if p["predicate"] == "evidence_allowlist_contains"
    }
    assert required_refs <= set(allowlist)
    assert all(p.get("evidence_id") in by_id for p in predicates["forbidden"] if p["predicate"] == "evidence_allowlist_contains")

    correction = _json("correction.json")
    assert correction["role"] == "user"
    assert correction["conversation_id"] == turns[0]["conversation_id"]
    query = _json("query.json")
    assert set(query["required_evidence_ids"]) <= set(by_id)
    assert set(query["required_evidence_ids"]) <= set(allowlist)
    assert set(query["forbidden_evidence_ids"]) <= set(by_id)

    for name, expected in manifest["payloads"].items():
        digest = hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest().upper()
        assert expected == f"sha256:{digest}"

    # Frozen input is approved for the experiment, but remains explicitly outside Gate 1 evidence.
    assert manifest["status"] == "frozen" and manifest["owner_approved"] is True
