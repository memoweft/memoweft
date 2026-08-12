"""Focused contracts for the local-only MemoWeft Next Lab."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Collection

import pytest

LAB_ROOT = Path(__file__).resolve().parents[2] / "next-lab"
if str(LAB_ROOT) not in sys.path:
    sys.path.insert(0, str(LAB_ROOT))

from next_lab_core import LabService, SCENARIOS, reproducibility_snapshot, safe_model_status  # noqa: E402
from next_lab_server import HTML  # noqa: E402


def test_lab_runs_all_allowlisted_manual_golden_checks(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    run = service.run(list(SCENARIOS))

    assert len(run["scenarios"]) == 5
    checks = [check for scenario in run["scenarios"] for check in scenario["checks"]]
    assert len(checks) == 10
    assert {check["state"] for check in checks} == {"passed"}
    assert all(scenario["manualOracle"] for scenario in run["scenarios"])
    ai_case = next(item for item in run["scenarios"] if item["scenarioId"] == "golden-3-ai-shared-experience")
    artifacts = ai_case["graph"]["provenanceArtifacts"]
    assert artifacts["evidence"][0]["source_kind"] == "spoken"
    assert artifacts["interactionContext"]["context"][0]["role"] == "assistant"
    assert artifacts["semanticResolution"]["proposition_origin"] == "assistant_proposed"
    assert "interaction:planning-interpretation" in {node["id"] for node in ai_case["views"]["provenance"]["nodes"]}
    assert "entity" in {node["kind"] for node in ai_case["views"]["world"]["nodes"]}
    assert "entitie" not in {node["kind"] for node in ai_case["views"]["world"]["nodes"]}


def test_review_is_append_only_and_cannot_change_a_world(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    run = service.run(["golden-1-nanjing"])
    before = run["scenarios"][0]["worldHash"]

    review = service.review({"runId": run["id"], "scenarioId": "golden-1-nanjing", "worldHash": before, "verdict": "needs-discussion", "notes": "Inspect the event boundary."})

    assert review["effect"] == "append-only local review note; world unchanged; diagnostic evidence only; not product acceptance"
    assert service.runs()["runs"][0]["scenarios"][0]["worldHash"] == before
    assert service.review_path.read_text(encoding="utf-8").count("golden-1-nanjing") == 1


def test_nanjing_expansion_is_local_expansion_not_recall(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    expanded = service.expand({"scenarioId": "golden-1-nanjing", "target": {"kind": "event", "id": "event:nanjing-conflict"}, "depth": 1})

    assert expanded["kind"] == "local-expansion"
    assert expanded["label"] == "Local expansion (not Recall)"
    assert "person:friend-x" in expanded["slice"]["entity_ids"]
    assert "event:nanjing-conflict" in expanded["slice"]["event_ids"]


def test_allowlist_pin_compare_and_failed_rerun_behavior(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    first = service.run(["golden-5-dormant-friend"])
    assert service.pin({"runId": first["id"], "scenarioId": "golden-5-dormant-friend"})["pinned"] is True
    assert service.rerun({"runId": first["id"], "onlyFailed": True})["kind"] == "no-failed-checks"
    second = service.run(["golden-5-dormant-friend"])
    # Deterministic manual builders are expected to compare equal across runs;
    # an empty diff is still an explicit, useful comparison result.
    assert service.compare({"leftRunId": first["id"], "rightRunId": second["id"], "scenarioId": "golden-5-dormant-friend"})["changes"] == []
    try:
        service.run(["arbitrary-path-or-command"])
    except ValueError as exc:
        assert "allowlisted" in str(exc)
    else:
        raise AssertionError("unallowlisted scenario was accepted")


def test_review_requires_matching_run_scenario_and_survives_reload(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    run = service.run(["golden-1-nanjing"])
    for body in ({"scenarioId": "golden-1-nanjing", "verdict": "needs-discussion", "notes": "x"}, {"runId": run["id"], "scenarioId": "golden-2-mother-candy", "verdict": "needs-discussion", "notes": "x"}):
        try:
            service.review(body)
        except (ValueError, KeyError):
            pass
        else:
            raise AssertionError("invalid review binding was accepted")
    review = service.review({"runId": run["id"], "scenarioId": "golden-1-nanjing", "worldHash": run["scenarios"][0]["worldHash"], "verdict": "needs-discussion", "notes": "x"})
    assert review["worldHash"] == run["scenarios"][0]["worldHash"]
    reloaded = LabService(tmp_path)
    assert reloaded.scenarios()["scenarios"][0]["latestVerdict"] == "needs-discussion"
    assert reloaded.scenarios()["scenarios"][0]["accepted"] is False


def test_baseline_compare_is_bound_to_one_complete_scenario_and_renders_paths(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    first = service.run(["golden-1-nanjing"])
    service.pin({"runId": first["id"], "scenarioId": "golden-1-nanjing"})
    second = service.run(["golden-1-nanjing"])
    second["scenarios"][0]["graph"]["world"]["world_id"] = "world:changed-for-diff-contract"

    comparison = service.compare({"leftRunId": first["id"], "rightRunId": second["id"], "scenarioId": "golden-1-nanjing"})

    assert comparison["leftWorldHash"] == first["scenarios"][0]["worldHash"]
    assert comparison["rightWorldHash"] == second["scenarios"][0]["worldHash"]
    assert {change["path"] for change in comparison["changes"]} >= {"world.world_id"}
    partial = service.run(["golden-1-nanjing"], only_checks={"golden-1-nanjing": [SCENARIOS["golden-1-nanjing"]["checks"][0]]})
    with pytest.raises(ValueError, match="partial"):
        service.compare({"leftRunId": first["id"], "rightRunId": partial["id"], "scenarioId": "golden-1-nanjing"})
    with pytest.raises(ValueError, match="partial"):
        service.pin({"runId": partial["id"], "scenarioId": "golden-1-nanjing"})


def test_stale_reasons_cover_fixture_payload_and_scenario_contract_drift(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    run = service.run(["golden-1-nanjing"])
    recorded = run["reproducibility"]
    recorded["fixture"]["manifestHash"] = "sha256:changed"
    recorded["fixture"]["payloadActualHashes"]["query.json"] = "sha256:changed"
    run["scenarios"][0]["definitionHash"] = "sha256:changed"
    run["scenarios"][0]["sourceHash"] = "sha256:changed"

    reasons = service.runs()["runs"][0]["staleReasons"]

    assert "fixture-manifest" in reasons
    assert "fixture-payload:query.json" in reasons
    assert "scenario-definition:golden-1-nanjing" in reasons
    assert "scenario-source:golden-1-nanjing" in reasons
    fixture = reproducibility_snapshot(Path(__file__).resolve().parents[2])["fixture"]
    assert fixture["fixtureVersion"] == "1.0.0-synthetic"
    assert fixture["ownerApproved"] is True and fixture["notGate1Evidence"] is True
    assert "manifest.json" not in fixture["payloadActualHashes"]


def test_jsonl_is_canonical_across_reload_and_state_save_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = LabService(tmp_path)
    run = service.run(["golden-1-nanjing"])
    review_body = {"runId": run["id"], "scenarioId": "golden-1-nanjing", "worldHash": run["scenarios"][0]["worldHash"], "verdict": "needs-discussion", "notes": "ledger is canonical"}
    service.review(review_body)
    assert LabService(tmp_path).reviews()["reviews"][0]["notes"] == "ledger is canonical"

    original_save = service._save
    monkeypatch.setattr(service, "_save", lambda: (_ for _ in ()).throw(OSError("simulated state save failure")))
    with pytest.raises(OSError, match="simulated"):
        service.review({**review_body, "notes": "survives stale cache"})
    monkeypatch.setattr(service, "_save", original_save)
    reconciled = LabService(tmp_path).reviews()["reviews"]
    assert [item["notes"] for item in reconciled] == ["ledger is canonical", "survives stale cache"]


def test_corrupt_legacy_review_ledger_tail_is_explicit(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    run = service.run(["golden-1-nanjing"])
    service.review({"runId": run["id"], "scenarioId": "golden-1-nanjing", "worldHash": run["scenarios"][0]["worldHash"], "verdict": "needs-discussion", "notes": "valid"})
    with service.review_path.open("a", encoding="utf-8") as ledger:
        ledger.write('{"torn":')
    reloaded = LabService(tmp_path)
    assert reloaded.reviews()["reviews"][0]["notes"] == "valid"
    assert "owner-verdict-ledger-tail-unreadable" in reloaded.status()["stateWarnings"]


def test_chinese_chat_workbench_is_the_default_and_keeps_legacy_separate() -> None:
    server_source = (LAB_ROOT / "next_lab_server.py").read_text(encoding="utf-8")
    assert '<html lang="zh-CN">' in HTML
    assert "MemoWeft 聊天工作台" in HTML
    assert "你说一句，AI 回一句" in HTML
    assert 'id="messages"' in HTML and 'id="messageInput"' in HTML
    assert "你只需要说自己的话" in HTML
    assert "/api/chat-session" in HTML and "/api/chat-turns" in HTML
    assert "MemoWeft 本轮工作" in HTML
    assert "接受记忆" in HTML and "忽略这次" in HTML
    assert "当前长期记忆" in HTML
    assert "expectedMemory" not in HTML and "expectedAnswer" not in HTML
    assert "const esc=" in HTML
    assert "raw model" not in HTML.lower()
    assert "LEGACY_HTML" in server_source and 'self.path == "/legacy"' in server_source


def test_memory_run_uses_user_allowlist_and_keeps_base_disposable(tmp_path: Path) -> None:
    from memoweft.world import ConversationTurn, Entity, MemoryWorldGraph, WorldDelta

    observed: dict[str, Any] = {}

    def fake(base: MemoryWorldGraph, turns: tuple[ConversationTurn, ...], allowlist: Collection[str]) -> WorldDelta:
        observed["base"] = base
        observed["turns"] = turns
        observed["allowlist"] = allowlist
        return WorldDelta(
            world_id=base.world.world_id,
            source_evidence_ids=(next(iter(allowlist)),),
            new_entities=(Entity(id="person:friend", world_id=base.world.world_id, kind="person", canonical_name="朋友"),),
        )

    class FakeAnswerer:
        def chat(self, messages: list[object]) -> str:
            return "只根据已召回的记忆回答。"

    service = LabService(tmp_path, memory_run_executor=fake, answer_client_factory=FakeAnswerer)
    result = service.memory_run({"title": "测试", "turns": [{"role": "user", "content": "我喜欢开车旅行"}, {"role": "assistant", "content": "我理解了"}]})

    assert result["state"] == "candidate-ready" and result["baseUnchanged"] is True
    assert len(observed["allowlist"]) == 1
    assert result["assistantContext"][0]["note"] == "仅作上下文，不作为用户证据"
    assert result["evidence"][0]["text"] == "我喜欢开车旅行"
    assert result["candidateMemory"]["entities"][0]["canonical_name"] == "朋友"
    assert [item["name"] for item in result["pipeline"]] == ["Evidence", "Extract", "Review", "Apply", "Recall", "Answer", "Correction"]
    with pytest.raises(ValueError, match="resultHash"):
        service.memory_evaluation({"runId": result["id"], "resultHash": "sha256:wrong", "verdict": "correct", "notes": "x"})
    evaluation = service.memory_evaluation({"runId": result["id"], "resultHash": result["resultHash"], "verdict": "partly-correct", "notes": "需要更多例子"})
    assert evaluation["effect"].startswith("仅追加本地评价")
    assert service.memory_evaluations()["evaluations"][0]["resultHash"] == result["resultHash"]
    before = service.memory_world()
    with pytest.raises(ValueError, match="resultHash"):
        service.memory_decision({"runId": result["id"], "resultHash": "sha256:wrong", "decision": "accept"})
    accepted = service.memory_decision({"runId": result["id"], "resultHash": result["resultHash"], "decision": "accept"})
    assert accepted["decision"] == "accept" and accepted["world"]["revision"] == before["revision"] + 1
    assert "person:friend" in {item["id"] for item in accepted["world"]["memory"]["entities"]}
    assert result["state"] == "accepted" and result["pipeline"][3]["state"] == "applied"
    retry = service.memory_decision(
        {"runId": result["id"], "resultHash": result["resultHash"], "decision": "accept"}
    )
    assert retry["idempotent"] is True
    assert retry["world"]["revision"] == accepted["world"]["revision"]
    query = service.memory_query({"query": "朋友"})
    assert query["status"] == "answered" and query["answer"] == "只根据已召回的记忆回答。"
    assert query["recalledEntities"][0]["id"] == "person:friend"
    with pytest.raises(ValueError, match="MemoryLoopError"):
        service.memory_correction({"cognitionId": "missing", "correctionText": "改正"})


@pytest.mark.parametrize(
    ("prior_text", "accidental_name"),
    (
        ("I will travel tomorrow.", "Will"),
        ("I may travel tomorrow.", "May"),
    ),
)
def test_default_lab_extractor_never_uses_raw_prior_text_as_identity_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prior_text: str,
    accidental_name: str,
) -> None:
    """Trusted Stage 2 mode disables the legacy lexical-history fallback."""

    import memoweft.llm.client as llm_client
    import memoweft.world as world_module
    from memoweft.world import (
        ConversationTurn,
        Entity,
        MemoryWorldGraph,
        PersonalWorld,
    )

    class ScriptedExtractorClient:
        def __init__(self) -> None:
            self.calls = 0

        def chat(self, messages: list[object]) -> str:
            self.calls += 1
            return json.dumps({
                "world_id": "world:lab-stage2",
                "new_entities": [],
                "new_relationships": [],
                "new_events": [],
                "new_cognitions": [],
                "unresolved_references": [
                    {"mention": "She", "evidence_ids": ["turn:current"]},
                ],
                "semantic_uncertainties": [],
            })

    observed: dict[str, Any] = {}
    real_extractor = world_module.WorldExtractor

    class RecordingExtractor:
        def __init__(self, client: Any) -> None:
            self._delegate = real_extractor(client)

        def extract(self, *args: Any, **kwargs: Any) -> Any:
            observed["accepted_entity_references"] = kwargs.get(
                "accepted_entity_references",
            )
            return self._delegate.extract(*args, **kwargs)

    graph = MemoryWorldGraph(PersonalWorld("world:lab-stage2", "entity:owner"))
    for entity in (
        Entity("entity:owner", graph.world.world_id, "person", "Owner"),
        Entity("person:will", graph.world.world_id, "person", "Will"),
        Entity("person:may", graph.world.world_id, "person", "May"),
    ):
        graph.add_entity(entity)
    turns = (
        ConversationTurn(
            "turn:prior",
            "conversation:lab-stage2",
            "user",
            prior_text,
            "2026-08-10T09:00:00+08:00",
        ),
        ConversationTurn(
            "turn:current",
            "conversation:lab-stage2",
            "user",
            "She is kind.",
            "2026-08-10T10:00:00+08:00",
        ),
    )
    scripted = ScriptedExtractorClient()
    monkeypatch.setattr(llm_client, "OpenAICompatClient", lambda _config: scripted)
    monkeypatch.setattr(world_module, "WorldExtractor", RecordingExtractor)

    delta = LabService(tmp_path)._execute_memory_run(  # noqa: SLF001 - default Lab/Extractor integration
        graph,
        turns,
        frozenset({"turn:current"}),
    )

    assert scripted.calls == 1
    assert observed == {"accepted_entity_references": ()}
    assert delta.new_cognitions == ()
    assert tuple(
        (reference.mention, reference.evidence_ids)
        for reference in delta.unresolved_references
    ) == (("She", ("turn:current",)),)
    accidental_entity_id = f"person:{accidental_name.casefold()}"
    assert graph.entities[accidental_entity_id].canonical_name == accidental_name


def test_memory_run_safe_failure_and_hash_bound_evaluation(tmp_path: Path) -> None:
    from memoweft.world import WorldExtractionError

    def failure(base: object, turns: tuple[object, ...], allowlist: object) -> object:
        raise WorldExtractionError(("SAFE_CODE@$",), attempts=2)

    service = LabService(tmp_path, memory_run_executor=failure)
    failed = service.memory_run({"title": "失败", "turns": [{"role": "user", "content": "一句话"}]})
    assert failed["state"] == "failed" and failed["failure"] == {"kind": "WorldExtractionError", "codes": ["SAFE_CODE@$"], "attempts": 2}
    with pytest.raises(ValueError, match="candidate-memory"):
        service.memory_evaluation({"runId": failed["id"], "resultHash": "none", "verdict": "incorrect", "notes": "x"})


def test_answer_client_configuration_never_reuses_the_world_delta_schema(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    config = service._answer_model_config()  # noqa: SLF001 - explicit local-model safety contract

    assert config.base_url == "http://127.0.0.1:8012/v1"
    assert config.model == "qwen3-14b-local"
    assert config.temperature == 0.2
    assert config.max_tokens == 1024
    assert config.response_format is None


def test_corrupt_ledger_middle_is_not_treated_as_a_normal_review_history(tmp_path: Path) -> None:
    service = LabService(tmp_path)
    run = service.run(["golden-1-nanjing"])
    review = service.review({"runId": run["id"], "scenarioId": "golden-1-nanjing", "worldHash": run["scenarios"][0]["worldHash"], "verdict": "needs-discussion", "notes": "trusted prefix"})
    with service.review_path.open("a", encoding="utf-8") as ledger:
        ledger.write('{"corrupt":}\n')
        ledger.write(json.dumps({**review, "id": "review-after-corruption", "notes": "must not be trusted"}) + "\n")

    reloaded = LabService(tmp_path)

    assert [item["notes"] for item in reloaded.reviews()["reviews"]] == ["trusted prefix"]
    assert "owner-verdict-ledger-middle-unreadable" in reloaded.status()["stateWarnings"]


def test_model_state_and_tcp_metadata_do_not_claim_verified_managed_health(tmp_path: Path) -> None:
    state_path = tmp_path / ".local" / "state" / "server.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"pid": 12, "port": 1, "bindAddress": "127.0.0.1", "alias": "qwen3-14b-local"}), encoding="utf-8")

    status = safe_model_status(tmp_path)

    assert status["stage0"] == "Not required"
    assert status["managed"] is False
    assert "Unverified" in status["verification"]
