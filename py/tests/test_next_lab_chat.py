"""Deterministic contracts for the chat-first Next Lab boundary."""
from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Collection

import pytest


LAB = Path(__file__).resolve().parents[2] / "next-lab"
sys.path.insert(0, str(LAB))
from next_lab_core import LabService  # noqa: E402
from next_lab_server import Handler, NextLabHTTPServer  # noqa: E402


class _FakeAnswerer:
    def __init__(self) -> None:
        self.calls: list[list[object]] = []

    def chat(self, messages: list[object]) -> str:
        self.calls.append(messages)
        return f"本地回复 #{len(self.calls)}"


class _FakeCorrectionClassifier:
    def __init__(self, reply: dict[str, object] | None = None) -> None:
        self.reply = reply or {
            "is_correction": False,
            "prior_cognition_ids": [],
            "structure_hints": [],
        }
        self.calls: list[list[object]] = []

    def chat(self, messages: list[object]) -> str:
        self.calls.append(messages)
        return json.dumps(self.reply, ensure_ascii=False)


class _FakeTurnMeaning:
    """Return a schema-valid interpretation based only on the current user text."""

    def __init__(self, responder: Callable[[str, list[object]], dict[str, object]]) -> None:
        self._responder = responder
        self.calls: list[list[object]] = []

    def chat(self, messages: list[object]) -> str:
        self.calls.append(messages)
        current = messages[-1].content  # type: ignore[attr-defined]
        assert isinstance(current, str)
        return json.dumps(self._responder(current, messages), ensure_ascii=False)


def _chat_service(state_dir: Path, **kwargs: Any) -> LabService:
    kwargs.setdefault("correction_client_factory", _FakeCorrectionClassifier)
    return LabService(state_dir, **kwargs)


def _adapter_body(
    *,
    operation_id: str,
    session_id: str,
    turns: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "operationId": operation_id,
        "sessionId": session_id,
        "currentUserTurnId": turns[-1]["turnId"],
        "carryForwardEvidenceIds": [],
        "turns": turns,
    }


def _install_current_doubao_memories(service: LabService) -> tuple[str, str]:
    from memoweft.world import Entity, MemoryTarget, Perspective, WorldCognition
    from memoweft.world.loop import MemoryLoop

    graph = service._owner_only_base("world:next-lab-owner")  # noqa: SLF001 - deterministic canonical test world
    graph.add_entity(Entity("entity:doubao", graph.world.world_id, "animal", "豆包"))
    prior_ids = ("cognition:doubao-cat", "cognition:doubao-fake")
    for cognition_id, content in zip(prior_ids, ("我的猫叫豆包。", "豆包其实是假的。"), strict=True):
        graph.add_cognition(WorldCognition(
            cognition_id,
            graph.world.world_id,
            MemoryTarget("entity", "entity:doubao"),
            content,
            "fact",
            "inferred",
            440,
            "low",
            Perspective("entity", (graph.world.owner_entity_id,)),
        ))
    service._persistent_memory_loop = MemoryLoop(service.memory_world_path, graph)  # noqa: SLF001
    return prior_ids


def _recording_delta_executor(observed: dict[str, Any]) -> Callable[[object, tuple[object, ...], Collection[str]], Any]:
    from memoweft.world import Entity, WorldDelta

    def executor(base: object, turns: tuple[object, ...], allowlist: Collection[str]) -> WorldDelta:
        observed.setdefault("calls", []).append({"turns": turns, "allowlist": frozenset(allowlist)})
        source = next(iter(allowlist))
        world_id = base.world.world_id  # type: ignore[attr-defined]
        return WorldDelta(
            world_id=world_id,
            source_evidence_ids=(source,),
            new_entities=(Entity(id=f"person:chat-{len(observed['calls'])}", world_id=world_id, kind="person", canonical_name="聊天朋友"),),
        )

    return executor


def test_chat_turn_replies_without_memory_and_persists_one_server_owned_session(tmp_path: Path) -> None:
    answerer = _FakeAnswerer()
    observed: dict[str, Any] = {}
    service = _chat_service(tmp_path, memory_run_executor=_recording_delta_executor(observed), answer_client_factory=lambda: answerer)

    result = service.chat_turn({"message": "你好，我想聊聊旅行。"})

    assert result["recall"]["status"] == "no_memory"
    assert result["assistantTurn"] == {
        "turnId": result["assistantTurn"]["turnId"],
        "role": "assistant",
        "content": "本地回复 #1",
        "occurredAt": result["assistantTurn"]["occurredAt"],
    }
    assert result["memoryProposal"] is not None
    assert result["memoryFailure"] is None
    assert [step["name"] for step in result["pipeline"]] == ["Recall", "Answer", "Evidence", "Correction", "Extract", "Review", "Apply"]
    reloaded = _chat_service(tmp_path, memory_run_executor=_recording_delta_executor({}), answer_client_factory=lambda: answerer)
    session = reloaded.chat_session()
    assert session["sessionId"] == "chat:next-lab-owner"
    assert [(turn["role"], turn["content"]) for turn in session["transcript"]] == [
        ("user", "你好，我想聊聊旅行。"),
        ("assistant", "本地回复 #1"),
    ]


def test_chat_reply_prompt_keeps_internal_memory_mechanics_out_of_normal_conversation(tmp_path: Path) -> None:
    answerer = _FakeAnswerer()
    service = _chat_service(tmp_path, memory_run_executor=_recording_delta_executor({}), answer_client_factory=lambda: answerer)

    service.chat_turn({"message": "今天有点累。"})

    system_message = answerer.calls[0][0]
    assert system_message.role == "system"  # type: ignore[attr-defined]
    prompt = system_message.content  # type: ignore[attr-defined]
    assert "直接对“你”说话" in prompt
    assert "不得称呼对方为“用户”" in prompt
    assert "除非对方明确询问" in prompt
    assert "不得提及内部召回、长期记忆、候选、提取、记录或是否需要记录" in prompt
    assert "只输出回复正文" in prompt
    assert "不得使用“用户提到”、“根据当前记忆”、“记忆库”、“长期记忆”或“是否需要记录”等短语" in prompt
    assert "先回应最后一条消息本身" in prompt

    memory_context = answerer.calls[0][1]
    assert memory_context.role == "system"  # type: ignore[attr-defined]
    assert "已接受" in memory_context.content  # type: ignore[attr-defined]


def test_real_adapter_product_path_binds_an_open_kind_then_updates_that_same_entity(
    tmp_path: Path,
) -> None:
    """Capability 1 crosses the actual adapter, not the extractor test seam."""

    def meaning(text: str, messages: list[object]) -> dict[str, object]:
        if text == "我在做一个叫星港的项目。":
            mention = "星港"
            return {
                "act": "assertion",
                "mention": {
                    "text": mention,
                    "start": text.index(mention),
                    "end": text.index(mention) + len(mention),
                    "mode": "introduce",
                    "kind_hint": "project",
                    "accepted_handles": [],
                },
                "statement": {
                    "kind": "naming",
                    "text": text,
                    "start": 0,
                    "end": len(text),
                    "value": None,
                },
            }
        assert text == "这个项目已经启动了。"
        contract = json.loads(messages[0].content)  # type: ignore[attr-defined]
        handles = contract["accepted_entity_catalog"]
        assert len(handles) == 1
        mention = "这个项目"
        value = "已经启动了"
        return {
            "act": "assertion",
            "mention": {
                "text": mention,
                "start": text.index(mention),
                "end": text.index(mention) + len(mention),
                "mode": "refer",
                "kind_hint": "project",
                "accepted_handles": [handles[0]["handle"]],
            },
            "statement": {
                "kind": "attribute",
                "text": text,
                "start": 0,
                "end": len(text),
                "value": {
                    "text": value,
                    "start": text.index(value),
                    "end": text.index(value) + len(value),
                },
            },
        }

    service = _chat_service(
        tmp_path,
        meaning_client_factory=lambda: _FakeTurnMeaning(meaning),
    )
    first_turns = [{
        "turnId": "evidence:introduce",
        "role": "user",
        "content": "我在做一个叫星港的项目。",
        "occurredAt": "2026-08-11T10:00:00Z",
    }]
    first = service.adapter_memory_turns(_adapter_body(
        operation_id="product-introduce",
        session_id="product-session",
        turns=first_turns,
    ))

    assert first["run"]["state"] == "candidate-ready"
    assert first["memoryProposal"]["target"]["entityNames"] == ["星港"]
    assert first["memoryProposal"]["statementKind"] == "naming"
    assert first["memoryProposal"]["ownerPerspective"] == {
        "kind": "entity", "entityIds": ["entity:owner"],
    }
    assert len(first["memoryProposal"]["identityBindings"]) == 1
    accepted_first = service.memory_decision({
        "runId": first["run"]["id"],
        "resultHash": first["run"]["resultHash"],
        "decision": "accept",
    })
    entity_id = first["memoryProposal"]["target"]["entityId"]
    assert entity_id in {
        item["id"] for item in accepted_first["world"]["memory"]["entities"]
    }

    second_turns = [
        *first_turns,
        {
            "turnId": "assistant:context",
            "role": "assistant",
            "content": "收到。",
            "occurredAt": "2026-08-11T10:00:01Z",
        },
        {
            "turnId": "evidence:attribute",
            "role": "user",
            "content": "这个项目已经启动了。",
            "occurredAt": "2026-08-11T10:00:02Z",
        },
    ]
    second = service.adapter_memory_turns(_adapter_body(
        operation_id="product-attribute",
        session_id="product-session",
        turns=second_turns,
    ))

    proposal = second["memoryProposal"]
    assert second["run"]["state"] == "candidate-ready"
    assert proposal["target"]["entityId"] == entity_id
    assert proposal["candidateMemory"]["entities"] == []
    assert proposal["candidateMemory"]["cognitions"][0]["target"] == {"kind": "entity", "id": entity_id}
    assert proposal["candidateMemory"]["cognitions"][0]["perspective"] == {
        "kind": "entity", "holder_entity_ids": ["entity:owner"],
    }
    service.memory_decision({
        "runId": second["run"]["id"],
        "resultHash": second["run"]["resultHash"],
        "decision": "accept",
    })
    world = service.memory_world()
    assert {item["id"] for item in world["memory"]["entities"]} == {"entity:owner", entity_id}
    assert world["memory"]["cognitions"][0]["target"]["id"] == entity_id


def test_real_adapter_stages_multi_claim_relationship_attribute_and_evaluation_bundle(
    tmp_path: Path,
) -> None:
    """One real adapter turn is reviewed and accepted as one SQLite bundle."""

    content = "我喜欢一个女孩，短发样子很可爱"

    def meaning(text: str, _: list[object]) -> dict[str, object]:
        assert text == content
        girl = "一个女孩"
        relation = "我喜欢一个女孩"
        predicate = "喜欢"
        attribute = "短发样子很可爱"
        short_hair = "短发"
        evaluation = "样子很可爱"
        return {
            "act": "assertion",
            "mentions": [{
                "text": girl,
                "start": text.index(girl),
                "end": text.index(girl) + len(girl),
                "mode": "introduce",
                "kind_hint": "person",
                "accepted_handles": [],
            }],
            "claims": [
                {
                    "id": "claim:relationship",
                    "kind": "relationship",
                    "subject": 0,
                    "text": relation,
                    "start": text.index(relation),
                    "end": text.index(relation) + len(relation),
                    "value": None,
                    "predicate": {"text": predicate, "start": text.index(predicate), "end": text.index(predicate) + len(predicate)},
                    "occurred_at": None,
                    "relationship_direction": "owner_to_focal",
                    "polarity": "affirm",
                    "epistemic_status": "stated",
                    "disposition": "assert",
                    "related_mentions": [],
                    "accepted_entity_handles": [],
                    "prior_cognition_handles": [],
                },
                {
                    "id": "claim:attribute",
                    "kind": "attribute",
                    "subject": 0,
                    "text": attribute,
                    "start": text.index(attribute),
                    "end": text.index(attribute) + len(attribute),
                    "value": {"text": short_hair, "start": text.index(short_hair), "end": text.index(short_hair) + len(short_hair)},
                    "predicate": None,
                    "occurred_at": None,
                    "relationship_direction": None,
                    "polarity": "affirm",
                    "epistemic_status": "stated",
                    "disposition": "assert",
                    "related_mentions": [],
                    "accepted_entity_handles": [],
                    "prior_cognition_handles": [],
                },
                {
                    "id": "claim:evaluation",
                    "kind": "evaluation",
                    "subject": 0,
                    "text": evaluation,
                    "start": text.index(evaluation),
                    "end": text.index(evaluation) + len(evaluation),
                    "value": None,
                    "predicate": None,
                    "occurred_at": None,
                    "relationship_direction": None,
                    "polarity": "affirm",
                    "epistemic_status": "stated",
                    "disposition": "assert",
                    "related_mentions": [],
                    "accepted_entity_handles": [],
                    "prior_cognition_handles": [],
                },
            ],
        }

    service = _chat_service(tmp_path, meaning_client_factory=lambda: _FakeTurnMeaning(meaning))
    staged = service.adapter_memory_turns(_adapter_body(
        operation_id="v2-bundle-relationship-attribute-evaluation",
        session_id="v2-bundle-session",
        turns=[{
            "turnId": "evidence:v2-bundle",
            "role": "user",
            "content": content,
            "occurredAt": "2026-08-12T01:00:00Z",
        }],
    ))

    proposal = staged["memoryProposal"]
    assert staged["run"]["state"] == "candidate-ready"
    assert [item["kind"] for item in proposal["claims"]["claims"]] == [
        "relationship", "attribute", "evaluation",
    ]
    for claim in proposal["claims"]["claims"]:
        assert claim["writeState"] == "candidate"
        assert claim["structuredStatus"] == (
            "legacy-unstructured" if claim["kind"] == "attribute" else "candidate"
        )
        assert claim["object"] == {
            "kind": "entity",
            "entityId": proposal["target"]["entityId"],
        }
        assert claim["perspective"] == {
            "kind": "entity", "holderEntityIds": ["entity:owner"],
        }
        assert claim["evidence"]["evidenceId"] == "evidence:v2-bundle"
        assert claim["evidence"]["span"] == {
            "start": claim["start"], "end": claim["end"],
        }
    assert proposal["claims"]["focal_entity_id"] == proposal["target"]["entityId"]
    assert proposal["transitionIntents"] == []
    authority = service._proposal_authority(proposal["reviewId"])  # noqa: SLF001 - SQLite is the decision authority
    assert authority is not None and authority["kind"] == "product_bundle"
    display = authority["reviewPayload"]["productDisplay"]
    assert [item["kind"] for item in display["claims"]["claims"]] == [
        "relationship", "attribute", "evaluation",
    ]

    accepted = service.memory_decision({
        "reviewId": proposal["reviewId"],
        "resultHash": proposal["resultHash"],
        "decision": "accept",
    })
    world = accepted["world"]["memory"]
    assert len(world["relationships"]) == 1
    assert {
        item["structured_claim"]["statement_kind"]
        for item in world["cognitions"]
        if item["structured_claim"] is not None
    } == {
        "relationship_statement", "evaluation",
    }
    # The first supported attribute still uses the pre-v2 compatible writer;
    # it is present in the accepted bundle but does not yet expose a
    # StructuredClaim.  The review projection calls that distinction out.
    assert any("短发" in item["content"] for item in world["cognitions"])


def test_product_claim_review_projection_marks_naming_and_alias_as_not_writable(
    tmp_path: Path,
) -> None:
    """A classified name is not presented as a currently writable claim."""

    service = _chat_service(tmp_path)
    projected = service._product_claim_review_projection(  # noqa: SLF001 - product review payload contract
        {
            "version": 1,
            "focal_entity_id": "entity:project",
            "evidence_id": "evidence:name",
            "perspective_holder_entity_id": "entity:owner",
            "claims": [
                {
                    "id": "claim:naming", "kind": "naming", "text": "它叫星港",
                    "start": 0, "end": 4, "value": None, "predicate": None,
                    "occurred_at": None, "relationship_direction": None,
                    "polarity": "affirm", "epistemic_status": "stated",
                    "disposition": "assert",
                },
                {
                    "id": "claim:alias", "kind": "alias", "text": "也叫港口",
                    "start": 0, "end": 4, "value": None, "predicate": None,
                    "occurred_at": None, "relationship_direction": None,
                    "polarity": "affirm", "epistemic_status": "stated",
                    "disposition": "assert",
                },
            ],
        },
        focal_entity_id="entity:project",
        owner_entity_id="entity:owner",
        evidence_id="evidence:name",
    )

    assert [claim["writeState"] for claim in projected["claims"]] == [
        "unsupported", "unsupported",
    ]
    assert [claim["structuredStatus"] for claim in projected["claims"]] == [
        "not-lowered", "not-lowered",
    ]


def test_sqlite_review_is_decision_authority_when_memory_run_projection_is_missing(
    tmp_path: Path,
) -> None:
    """A pending product review remains decidable after only state.json loses it."""

    def meaning(text: str, messages: list[object]) -> dict[str, object]:
        assert text == "我在做一个叫晨星的项目。"
        mention = "晨星"
        return {
            "act": "assertion",
            "mention": {
                "text": mention,
                "start": text.index(mention),
                "end": text.index(mention) + len(mention),
                "mode": "introduce",
                "kind_hint": "project",
                "accepted_handles": [],
            },
            "statement": {
                "kind": "naming",
                "text": text,
                "start": 0,
                "end": len(text),
                "value": None,
            },
        }

    service = _chat_service(tmp_path, meaning_client_factory=lambda: _FakeTurnMeaning(meaning))
    staged = service.adapter_memory_turns(_adapter_body(
        operation_id="sqlite-authority-product",
        session_id="sqlite-authority-session",
        turns=[{
            "turnId": "evidence:sqlite-authority",
            "role": "user",
            "content": "我在做一个叫晨星的项目。",
            "occurredAt": "2026-08-11T10:00:00Z",
        }],
    ))
    proposal = staged["memoryProposal"]
    review_id = proposal["reviewId"]
    result_hash = proposal["resultHash"]
    authority = service._proposal_authority(review_id)  # noqa: SLF001 - exact SQLite authority contract
    assert authority is not None
    assert authority["reviewPayload"]["operationId"] == "sqlite-authority-product"
    assert authority["reviewPayload"]["sessionId"] == "sqlite-authority-session"
    assert authority["reviewPayload"]["currentEvidenceId"] == "evidence:sqlite-authority"
    assert authority["reviewPayload"]["productDisplay"]["target"] == proposal["target"]

    # Simulate a restart whose state projection lost only memoryRuns.  SQLite
    # still contains the proposal, hash, Evidence and display reconstruction.
    service.state["memoryRuns"] = []  # noqa: SLF001 - state projection loss simulation
    service._save()  # noqa: SLF001 - state projection loss simulation
    restarted = _chat_service(tmp_path, meaning_client_factory=lambda: _FakeTurnMeaning(meaning))

    accepted = restarted.memory_decision({
        "reviewId": review_id,
        "resultHash": result_hash,
        "decision": "accept",
    })
    assert accepted["reviewId"] == review_id
    assert accepted["resultHash"] == result_hash
    assert accepted["decision"] == "accept"
    assert accepted["world"]["revision"] == 1
    restored = restarted.memory_runs()["runs"]
    assert len(restored) == 1
    assert restored[0]["id"] == staged["run"]["id"]
    assert restored[0]["state"] == "accepted"
    assert restored[0]["recoveredFromSqlite"] is True

    retry = restarted.memory_decision({
        "reviewId": review_id,
        "resultHash": result_hash,
        "decision": "accept",
    })
    assert retry["idempotent"] is True
    with pytest.raises(ValueError, match="opposite decision"):
        restarted.memory_decision({
            "reviewId": review_id,
            "resultHash": result_hash,
            "decision": "reject",
        })


def test_real_adapter_product_queries_and_ambiguous_references_do_not_stage_candidates(
    tmp_path: Path,
) -> None:
    def meaning(text: str, messages: list[object]) -> dict[str, object]:
        if text == "那个项目现在怎么样？":
            mention = "那个项目"
            return {
                "act": "query",
                "mention": {
                    "text": mention,
                    "start": text.index(mention),
                    "end": text.index(mention) + len(mention),
                    "mode": "refer",
                    "kind_hint": "project",
                    "accepted_handles": [],
                },
                "statement": None,
            }
        contract = json.loads(messages[0].content)  # type: ignore[attr-defined]
        handles = contract["accepted_entity_catalog"]
        mention = "那个项目"
        value = "很顺利"
        return {
            "act": "assertion",
            "mention": {
                "text": mention,
                "start": text.index(mention),
                "end": text.index(mention) + len(mention),
                "mode": "refer",
                "kind_hint": "project",
                "accepted_handles": [item["handle"] for item in handles],
            },
            "statement": {
                "kind": "attribute",
                "text": text,
                "start": 0,
                "end": len(text),
                "value": {
                    "text": value,
                    "start": text.index(value),
                    "end": text.index(value) + len(value),
                },
            },
        }

    service = _chat_service(tmp_path, meaning_client_factory=lambda: _FakeTurnMeaning(meaning))
    query_turns = [{
        "turnId": "evidence:query",
        "role": "user",
        "content": "那个项目现在怎么样？",
        "occurredAt": "2026-08-11T10:00:00Z",
    }]
    query = service.adapter_memory_turns(_adapter_body(
        operation_id="product-query",
        session_id="product-session",
        turns=query_turns,
    ))

    assert query["run"]["state"] == "no-candidate"
    assert query["memoryProposal"] is None
    assert query["run"]["evidence"] == []
    assert service.memory_world()["revision"] == 0

    from memoweft.world import Entity
    from memoweft.world.loop import MemoryLoop

    ambiguous_service = _chat_service(
        tmp_path / "ambiguous",
        meaning_client_factory=lambda: _FakeTurnMeaning(meaning),
    )
    graph = ambiguous_service._owner_only_base("world:next-lab-owner")  # noqa: SLF001 - accepted base for resolver boundary
    graph.add_entity(Entity("entity:project-a", graph.world.world_id, "project", "甲计划"))
    graph.add_entity(Entity("entity:project-b", graph.world.world_id, "project", "乙计划"))
    ambiguous_service._persistent_memory_loop = MemoryLoop(  # noqa: SLF001 - exact product adapter setup
        ambiguous_service.memory_world_path,
        graph,
    )
    ambiguous_turns = [{
        "turnId": "evidence:ambiguous",
        "role": "user",
        "content": "那个项目很顺利。",
        "occurredAt": "2026-08-11T10:00:00Z",
    }]
    ambiguous = ambiguous_service.adapter_memory_turns(_adapter_body(
        operation_id="product-ambiguous",
        session_id="product-session",
        turns=ambiguous_turns,
    ))

    assert ambiguous["run"]["state"] == "clarification-required"
    assert ambiguous["memoryProposal"] is None
    # Two opaque model handles do not become identity authority.  Neither
    # project has an accepted binding in this session, so the adapter asks for
    # clarification without inventing a candidate list from model selection.
    assert ambiguous["run"]["clarification"]["candidateEntityNames"] == []
    assert ambiguous_service.memory_world()["revision"] == 0


def test_chat_receives_reconstructed_nanjing_event_facets_without_raw_evidence_or_ids(
    tmp_path: Path,
) -> None:
    from memoweft.world.loop import MemoryLoop
    from test_world_model_golden_nanjing import build_nanjing_world

    answerer = _FakeAnswerer()
    service = _chat_service(
        tmp_path,
        memory_run_executor=_recording_delta_executor({}),
        answer_client_factory=lambda: answerer,
    )
    service._persistent_memory_loop = MemoryLoop(  # noqa: SLF001 - exact accepted-world chat contract
        service.memory_world_path,
        build_nanjing_world(),
    )

    result = service.chat_turn(
        {"message": "你还记得我和 Friend_X 为什么因为南京旅行吵起来吗？"}
    )

    assert result["recall"]["status"] == "recalled"
    assert result["recall"]["reconstruction"]["status"] == "resolved"
    memory_context = answerer.calls[0][1].content  # type: ignore[attr-defined]
    assert "They disagreed about how much a trip should be planned in advance." in memory_context
    assert "position（User）" in memory_context
    assert "position（Friend_X）" in memory_context
    assert "Prefers driving without a fixed itinerary" in memory_context
    assert "Prefers making an itinerary and following a travel guide" in memory_context
    assert "event:nanjing-conflict" not in memory_context
    assert "person:friend-x" not in memory_context
    assert "e1" not in memory_context and "e2" not in memory_context and "e3" not in memory_context
    assert "我和 Friend_X 是朋友，最近一起计划去南京旅行。" not in memory_context


def test_chat_model_configuration_disables_thinking_without_changing_memory_answer_config(tmp_path: Path) -> None:
    service = _chat_service(tmp_path)

    chat = service._chat_model_config()  # noqa: SLF001 - explicit local-chat contract
    answer = service._answer_model_config()  # noqa: SLF001 - separate memory-query contract
    correction = service._correction_model_config()  # noqa: SLF001 - separate natural-correction contract

    assert chat.base_url == "http://127.0.0.1:8012/v1"
    assert chat.model == "qwen3-14b-local"
    assert chat.temperature == 0.3
    assert chat.enable_thinking is False
    assert chat.max_tokens == 512
    assert chat.response_format is None
    assert answer.enable_thinking is True
    assert answer.max_tokens == 1024
    assert correction.base_url == "http://127.0.0.1:8012/v1"
    assert correction.model == "qwen3-14b-local"
    assert correction.temperature == 0.0
    assert correction.enable_thinking is False
    assert correction.max_tokens == 1024
    from memoweft.world.correction import NATURAL_CORRECTION_RESPONSE_FORMAT
    assert correction.response_format == NATURAL_CORRECTION_RESPONSE_FORMAT


def test_explicit_chat_client_factory_takes_precedence_over_legacy_answer_factory(tmp_path: Path) -> None:
    answerer, chatter = _FakeAnswerer(), _FakeAnswerer()
    service = _chat_service(
        tmp_path,
        memory_run_executor=_recording_delta_executor({}),
        answer_client_factory=lambda: answerer,
        chat_client_factory=lambda: chatter,
    )

    service.chat_turn({"message": "只走聊天客户端。"})

    assert len(chatter.calls) == 1
    assert answerer.calls == []


def test_second_chat_turn_carries_prior_ai_context_but_never_uses_ai_as_evidence(tmp_path: Path) -> None:
    answerer = _FakeAnswerer()
    observed: dict[str, Any] = {}
    service = _chat_service(tmp_path, memory_run_executor=_recording_delta_executor(observed), answer_client_factory=lambda: answerer)

    first = service.chat_turn({"message": "我最近在学做饭。"})
    second = service.chat_turn({"message": "你还记得我刚才说什么吗？"})

    answer_messages = answerer.calls[-1]
    assert [message.role for message in answer_messages] == [  # type: ignore[attr-defined]
        "system",
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert [message.content for message in answer_messages[-3:]] == [  # type: ignore[attr-defined]
        "我最近在学做饭。",
        "本地回复 #1",
        "你还记得我刚才说什么吗？",
    ]
    assert answer_messages[-1].role == "user"  # type: ignore[attr-defined]

    extraction = observed["calls"][-1]
    transcript = service.chat_session()["transcript"][:-1]
    assert [
        (turn.turn_id, turn.role, turn.content, turn.occurred_at)
        for turn in extraction["turns"]
    ] == [
        (turn["turnId"], turn["role"], turn["content"], turn["occurredAt"])
        for turn in transcript
    ]
    role_by_id = {turn.turn_id: turn.role for turn in extraction["turns"]}
    assert any(turn.role == "assistant" and turn.content == "本地回复 #1" for turn in extraction["turns"])
    assert all(turn.turn_id != second["assistantTurn"]["turnId"] for turn in extraction["turns"])
    assert extraction["allowlist"] == frozenset({second["userTurn"]["turnId"]})
    assert {role_by_id[evidence_id] for evidence_id in extraction["allowlist"]} == {"user"}
    assert first["memoryProposal"]["evidence"] == [
        {"evidenceId": first["userTurn"]["turnId"], "text": "我最近在学做饭。"},
    ]
    assert second["memoryProposal"]["evidence"] == [
        {"evidenceId": second["userTurn"]["turnId"], "text": "你还记得我刚才说什么吗？"},
    ]


def test_adapter_memory_turns_stages_only_the_current_user_turn_and_is_idempotent(tmp_path: Path) -> None:
    """The legacy-console bridge owns its IDs; old turns stay context-only."""
    observed: dict[str, Any] = {}
    service = _chat_service(tmp_path, memory_run_executor=_recording_delta_executor(observed))
    body: dict[str, Any] = {
        "operationId": "legacy-operation-001",
        "sessionId": "legacy-session-001",
        "currentUserTurnId": "legacy-turn-003",
        "carryForwardEvidenceIds": [],
        "turns": [
            {"turnId": "legacy-turn-001", "role": "user", "content": "我以前学过摄影。", "occurredAt": "2026-08-09T08:00:00Z"},
            {"turnId": "legacy-turn-002", "role": "assistant", "content": "这只是之前的 AI 回复。", "occurredAt": "2026-08-09T08:00:01Z"},
            {"turnId": "legacy-turn-003", "role": "user", "content": "我现在开始学做饭。", "occurredAt": "2026-08-09T08:00:02Z"},
        ],
    }

    first = service.adapter_memory_turns(body)
    retry = service.adapter_memory_turns(body)

    assert len(observed["calls"]) == 1
    extraction = observed["calls"][0]
    assert [(turn.turn_id, turn.role, turn.content) for turn in extraction["turns"]] == [
        ("legacy-turn-001", "user", "我以前学过摄影。"),
        ("legacy-turn-002", "assistant", "这只是之前的 AI 回复。"),
        ("legacy-turn-003", "user", "我现在开始学做饭。"),
    ]
    assert extraction["allowlist"] == frozenset({"legacy-turn-003"})
    assert first["memoryProposal"] is not None
    assert first["memoryProposal"]["evidence"] == [{"evidenceId": "legacy-turn-003", "text": "我现在开始学做饭。"}]
    assert first["run"]["contextTurns"] == [
        {"turnId": "legacy-turn-001", "role": "user", "text": "我以前学过摄影。", "note": "仅作上下文，不在本次 Evidence allowlist 中"},
        {"turnId": "legacy-turn-002", "role": "assistant", "text": "这只是之前的 AI 回复。", "note": "仅作上下文，不在本次 Evidence allowlist 中"},
    ]
    assert first["run"]["adapter"] == {
        "operationId": "legacy-operation-001",
        "sessionId": "legacy-session-001",
        "currentUserTurnId": "legacy-turn-003",
        "carryForwardEvidenceIds": [],
    }
    assert first["run"]["carryForward"] == {
        "requestedEvidenceIds": [],
        "sources": [],
        "status": "not-requested",
    }
    assert retry["run"]["id"] == first["run"]["id"]
    assert retry["memoryProposal"] == first["memoryProposal"]
    assert len(service.memory_runs()["runs"]) == 1

    conflicting = {**body, "turns": [*body["turns"]]}
    conflicting["turns"][-1] = {**conflicting["turns"][-1], "content": "同一个 operationId 不能偷偷替换内容。"}
    with pytest.raises(ValueError, match="operationId already belongs"):
        service.adapter_memory_turns(conflicting)
    assert len(observed["calls"]) == 1


@pytest.mark.parametrize(
    ("prior_text", "clarification_text"),
    [
        ("他把旧相机放在工作室了。", "这里的他是我大学室友。"),
        ("那位医生建议我早点休息。", "我指的是上周门诊见到的林医生。"),
        ("她每逢周末都会带点心来。", "她是住在楼上的邻居，姓名我还不知道。"),
    ],
)
def test_adapter_explicitly_carries_prior_unresolved_user_evidence_without_wording_rules(
    tmp_path: Path,
    prior_text: str,
    clarification_text: str,
) -> None:
    from memoweft.world import Entity, UnresolvedReference, WorldDelta

    observed: list[dict[str, Any]] = []

    def executor(base: Any, turns: tuple[Any, ...], allowlist: Collection[str]) -> WorldDelta:
        evidence_ids = tuple(sorted(allowlist))
        observed.append({"turns": turns, "allowlist": frozenset(allowlist)})
        if len(observed) == 1:
            return WorldDelta(
                world_id=base.world.world_id,
                source_evidence_ids=evidence_ids,
                unresolved_references=(UnresolvedReference("待确认对象", evidence_ids),),
            )
        return WorldDelta(
            world_id=base.world.world_id,
            source_evidence_ids=evidence_ids,
            new_entities=(
                Entity("entity:resolved-description", base.world.world_id, "person", "已补充身份的人"),
            ),
        )

    state_dir = tmp_path / str(abs(hash((prior_text, clarification_text))))
    service = _chat_service(state_dir, memory_run_executor=executor)
    first_body = {
        "operationId": "carry-source-operation",
        "sessionId": "carry-session",
        "currentUserTurnId": "evidence:source",
        "carryForwardEvidenceIds": [],
        "turns": [
            {
                "turnId": "evidence:source",
                "role": "user",
                "content": prior_text,
                "occurredAt": "2026-08-11T08:00:00Z",
            },
        ],
    }
    first = service.adapter_memory_turns(first_body)
    assert first["run"]["state"] == "no-candidate"
    assert first["run"]["unresolvedReferences"] == [
        {"mention": "待确认对象", "evidence_ids": ["evidence:source"]},
    ]

    second_body = {
        "operationId": "carry-consumer-operation",
        "sessionId": "carry-session",
        "currentUserTurnId": "evidence:clarification",
        "carryForwardEvidenceIds": ["evidence:source"],
        "turns": [
            first_body["turns"][0],
            {
                "turnId": "assistant:context",
                "role": "assistant",
                "content": "这句话只作上下文，不能成为 Evidence。",
                "occurredAt": "2026-08-11T08:00:01Z",
            },
            {
                "turnId": "evidence:clarification",
                "role": "user",
                "content": clarification_text,
                "occurredAt": "2026-08-11T08:00:02Z",
            },
        ],
    }
    second = service.adapter_memory_turns(second_body)
    retry = service.adapter_memory_turns(second_body)

    assert len(observed) == 2, "消费标记不得破坏相同 operationId 的幂等重试"
    assert observed[1]["allowlist"] == frozenset({"evidence:source", "evidence:clarification"})
    assert {
        turn.turn_id: (turn.role, turn.content)
        for turn in observed[1]["turns"]
    } == {
        "evidence:source": ("user", prior_text),
        "assistant:context": ("assistant", "这句话只作上下文，不能成为 Evidence。"),
        "evidence:clarification": ("user", clarification_text),
    }
    assert second["memoryProposal"]["evidence"] == [
        {"evidenceId": "evidence:clarification", "text": clarification_text},
        {"evidenceId": "evidence:source", "text": prior_text},
    ]
    assert second["memoryProposal"]["carryForward"] == {
        "requestedEvidenceIds": ["evidence:source"],
        "sources": [{
            "evidenceId": "evidence:source",
            "sourceRunId": first["run"]["id"],
            "sessionId": "carry-session",
            "sourceState": "no-candidate",
            "reason": "unresolved-reference",
            "sourceRunOrdinal": "0",
        }],
        "status": "consumed",
    }
    assert second["run"]["adapterRequestHash"] == retry["run"]["adapterRequestHash"]
    source_run = service.memory_runs()["runs"][0]
    assert source_run["carryForwardConsumption"]["evidenceId"] == "evidence:source"
    assert source_run["carryForwardConsumption"]["byRunId"] == second["run"]["id"]

    conflicting_retry = {**second_body, "carryForwardEvidenceIds": []}
    with pytest.raises(ValueError, match="operationId already belongs"):
        service.adapter_memory_turns(conflicting_retry)

    reloaded = _chat_service(state_dir, memory_run_executor=executor)
    with pytest.raises(ValueError, match="already been consumed"):
        reloaded.adapter_memory_turns({
            "operationId": "carry-reuse-operation",
            "sessionId": "carry-session",
            "currentUserTurnId": "evidence:later",
            "carryForwardEvidenceIds": ["evidence:source"],
            "turns": [
                first_body["turns"][0],
                {
                    "turnId": "evidence:later",
                    "role": "user",
                    "content": "再补充一条描述。",
                    "occurredAt": "2026-08-11T08:00:03Z",
                },
            ],
        })


def test_adapter_carry_forward_rejects_cross_session_assistant_and_tampered_sources(tmp_path: Path) -> None:
    from memoweft.world import UnresolvedReference, WorldDelta

    def unresolved(base: Any, turns: tuple[Any, ...], allowlist: Collection[str]) -> WorldDelta:
        evidence_ids = tuple(sorted(allowlist))
        return WorldDelta(
            world_id=base.world.world_id,
            source_evidence_ids=evidence_ids,
            unresolved_references=(UnresolvedReference("待确认对象", evidence_ids),),
        )

    service = _chat_service(tmp_path, memory_run_executor=unresolved)
    source_turn = {
        "turnId": "evidence:source",
        "role": "user",
        "content": "那个地方的夜景很漂亮。",
        "occurredAt": "2026-08-11T09:00:00Z",
    }
    service.adapter_memory_turns({
        "operationId": "source-operation",
        "sessionId": "source-session",
        "currentUserTurnId": "evidence:source",
        "carryForwardEvidenceIds": [],
        "turns": [source_turn],
    })

    def continuation(*, session_id: str = "source-session") -> dict[str, Any]:
        return {
            "operationId": f"continuation-{session_id}",
            "sessionId": session_id,
            "currentUserTurnId": "evidence:current",
            "carryForwardEvidenceIds": ["evidence:source"],
            "turns": [
                source_turn,
                {
                    "turnId": "evidence:current",
                    "role": "user",
                    "content": "我说的是河东旧桥旁边。",
                    "occurredAt": "2026-08-11T09:00:02Z",
                },
            ],
        }

    with pytest.raises(ValueError, match="same session"):
        service.adapter_memory_turns(continuation(session_id="different-session"))

    tampered = continuation()
    tampered["turns"] = [{**source_turn, "content": "被替换的历史文本。"}, tampered["turns"][1]]
    with pytest.raises(ValueError, match="exactly match"):
        service.adapter_memory_turns(tampered)

    assistant = continuation()
    assistant["operationId"] = "assistant-carry-operation"
    assistant["carryForwardEvidenceIds"] = ["assistant:context"]
    assistant["turns"].insert(1, {
        "turnId": "assistant:context",
        "role": "assistant",
        "content": "AI 自己说的话。",
        "occurredAt": "2026-08-11T09:00:01Z",
    })
    with pytest.raises(ValueError, match="only prior user turns"):
        service.adapter_memory_turns(assistant)


def test_adapter_carry_forward_requires_an_unresolved_no_candidate_source(tmp_path: Path) -> None:
    from memoweft.world import WorldDelta, WorldExtractionError

    calls = 0

    def source_outcomes(base: Any, turns: tuple[Any, ...], allowlist: Collection[str]) -> WorldDelta:
        nonlocal calls
        calls += 1
        if calls == 1:
            return WorldDelta(world_id=base.world.world_id, source_evidence_ids=tuple(allowlist))
        raise WorldExtractionError(("TEST_FAILURE@$",), attempts=1)

    service = _chat_service(tmp_path, memory_run_executor=source_outcomes)

    def source(operation_id: str, evidence_id: str, content: str, occurred_at: str) -> None:
        service.adapter_memory_turns({
            "operationId": operation_id,
            "sessionId": "eligibility-session",
            "currentUserTurnId": evidence_id,
            "carryForwardEvidenceIds": [],
            "turns": [{
                "turnId": evidence_id,
                "role": "user",
                "content": content,
                "occurredAt": occurred_at,
            }],
        })

    source("plain-no-candidate", "evidence:plain", "普通问候。", "2026-08-11T10:00:00Z")
    source("failed-source", "evidence:failed", "这一轮提取失败。", "2026-08-11T10:00:01Z")

    def carry(evidence_id: str, source_text: str, occurred_at: str) -> dict[str, Any]:
        return {
            "operationId": f"carry-{evidence_id}",
            "sessionId": "eligibility-session",
            "currentUserTurnId": "evidence:current",
            "carryForwardEvidenceIds": [evidence_id],
            "turns": [
                {
                    "turnId": evidence_id,
                    "role": "user",
                    "content": source_text,
                    "occurredAt": occurred_at,
                },
                {
                    "turnId": "evidence:current",
                    "role": "user",
                    "content": "补充说明。",
                    "occurredAt": "2026-08-11T10:00:02Z",
                },
            ],
        }

    with pytest.raises(ValueError, match="unresolvedReferences"):
        service.adapter_memory_turns(carry("evidence:plain", "普通问候。", "2026-08-11T10:00:00Z"))
    with pytest.raises(ValueError, match="must be no-candidate"):
        service.adapter_memory_turns(carry("evidence:failed", "这一轮提取失败。", "2026-08-11T10:00:01Z"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update({"unexpected": True}),
        lambda body: body.update({"currentUserTurnId": "legacy-turn-002"}),
        lambda body: body["turns"].__setitem__(2, {"turnId": "legacy-turn-003", "role": "assistant", "content": "伪造 Evidence", "occurredAt": "2026-08-09T08:00:02Z"}),
        lambda body: body["turns"].append({"turnId": "legacy-turn-001", "role": "user", "content": "重复 ID", "occurredAt": "2026-08-09T08:00:03Z"}),
        lambda body: body["turns"].__setitem__(1, {"turnId": "legacy-turn-003", "role": "user", "content": "当前 turn 不是最后一个", "occurredAt": "2026-08-09T08:00:01Z"}),
    ],
)
def test_adapter_memory_turns_rejects_untrusted_or_ambiguous_typed_history(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    service = _chat_service(tmp_path, memory_run_executor=_recording_delta_executor({}))
    body: dict[str, Any] = {
        "operationId": "legacy-operation-invalid",
        "sessionId": "legacy-session-invalid",
        "currentUserTurnId": "legacy-turn-003",
        "carryForwardEvidenceIds": [],
        "turns": [
            {"turnId": "legacy-turn-001", "role": "user", "content": "旧 user 上下文。", "occurredAt": "2026-08-09T08:00:00Z"},
            {"turnId": "legacy-turn-002", "role": "assistant", "content": "旧 assistant 上下文。", "occurredAt": "2026-08-09T08:00:01Z"},
            {"turnId": "legacy-turn-003", "role": "user", "content": "唯一当前 Evidence。", "occurredAt": "2026-08-09T08:00:02Z"},
        ],
    }
    mutate(body)

    with pytest.raises(ValueError):
        service.adapter_memory_turns(body)

    assert service.memory_runs()["runs"] == []


def test_adapter_memory_turns_reuses_the_natural_correction_stage(tmp_path: Path) -> None:
    classifier = _FakeCorrectionClassifier({
        "is_correction": True,
        "prior_cognition_ids": ["cognition:doubao-cat", "cognition:doubao-fake"],
        "structure_hints": [],
    })
    observed: dict[str, Any] = {}
    service = _chat_service(
        tmp_path,
        memory_run_executor=_recording_delta_executor(observed),
        correction_client_factory=lambda: classifier,
    )
    _install_current_doubao_memories(service)

    result = service.adapter_memory_turns({
        "operationId": "legacy-correction-001",
        "sessionId": "legacy-session-correction",
        "currentUserTurnId": "legacy-correction-turn",
        "carryForwardEvidenceIds": [],
        "turns": [
            {"turnId": "legacy-prior-ai", "role": "assistant", "content": "之前说豆包是猫。", "occurredAt": "2026-08-09T08:00:00Z"},
            {"turnId": "legacy-correction-turn", "role": "user", "content": "豆包不是我的猫。", "occurredAt": "2026-08-09T08:00:01Z"},
        ],
    })

    assert observed.get("calls", []) == []
    assert result["memoryFailure"] is None
    assert result["memoryProposal"]["state"] == "correction-pending"
    assert result["run"]["correction"]["replacementContent"] == "豆包不是我的猫。"
    assert {step["name"]: step["state"] for step in result["pipeline"]}["Extract"] == "skipped"


def test_legacy_evidence_import_replays_every_raw_user_turn_as_pending_owner_review(tmp_path: Path) -> None:
    """Migration accepts raw Owner Evidence only and cannot mutate the base world."""
    observed: dict[str, Any] = {}
    service = _chat_service(tmp_path, memory_run_executor=_recording_delta_executor(observed))
    body: dict[str, Any] = {
        "operationId": "legacy-import-operation-001",
        "turns": [
            {"turnId": "legacy-import-turn-001", "role": "user", "content": "我以前学过摄影。", "occurredAt": "2026-08-10T08:00:00+08:00"},
            {"turnId": "legacy-import-turn-002", "role": "user", "content": "我现在开始学做饭。", "occurredAt": "2026-08-10T08:01:00+08:00"},
        ],
    }
    base = service.memory_world()

    first = service.adapter_legacy_memory_imports(body)
    retry = service.adapter_legacy_memory_imports(body)

    assert len(observed["calls"]) == 1
    extraction = observed["calls"][0]
    assert [(turn.turn_id, turn.role, turn.content) for turn in extraction["turns"]] == [
        ("legacy-import-turn-001", "user", "我以前学过摄影。"),
        ("legacy-import-turn-002", "user", "我现在开始学做饭。"),
    ]
    assert extraction["allowlist"] == frozenset({"legacy-import-turn-001", "legacy-import-turn-002"})
    assert set(first) == {"memoryProposal", "memoryFailure", "pipeline", "world", "run"}
    assert first["memoryFailure"] is None
    assert first["memoryProposal"]["state"] == "candidate-ready"
    assert first["run"]["legacyEvidenceReplay"] == {
        "operationId": "legacy-import-operation-001",
        "kind": "raw-user-evidence-only",
        "evidencePolicy": "所有导入 turn 都是原始 user Evidence；不接收或保留 1.x cognition 派生文本。",
    }
    assert "cognition" not in first["run"]["legacyEvidenceReplay"]
    assert first["world"]["revision"] == base["revision"] == 0
    assert first["world"]["worldHash"] == base["worldHash"]
    assert first["world"]["memory"] == base["memory"]
    assert len(first["world"]["pendingReviews"]) == 1
    assert retry["run"]["id"] == first["run"]["id"]
    assert retry["memoryProposal"] == first["memoryProposal"]
    assert service.memory_world()["revision"] == 0

    conflicting = {
        "operationId": body["operationId"],
        "turns": [
            *body["turns"][:-1],
            {**body["turns"][-1], "content": "同一个 operationId 不能替换原始 Evidence。"},
        ],
    }
    with pytest.raises(ValueError, match="operationId already belongs"):
        service.adapter_legacy_memory_imports(conflicting)
    assert len(observed["calls"]) == 1

    accepted = service.memory_decision({
        "runId": first["run"]["id"],
        "resultHash": first["run"]["resultHash"],
        "decision": "accept",
    })
    assert accepted["world"]["revision"] == 1


def test_legacy_evidence_import_retains_no_candidate_and_safe_failure_without_world_pollution(tmp_path: Path) -> None:
    from memoweft.world import WorldDelta, WorldExtractionError

    body = {
        "operationId": "legacy-import-no-candidate",
        "turns": [{"turnId": "legacy-import-empty", "role": "user", "content": "你好。", "occurredAt": "2026-08-10T00:00:00Z"}],
    }

    def no_candidate(base: Any, turns: tuple[Any, ...], allowlist: Collection[str]) -> WorldDelta:
        return WorldDelta(world_id=base.world.world_id, source_evidence_ids=tuple(allowlist))

    no_candidate_service = _chat_service(tmp_path / "no-candidate", memory_run_executor=no_candidate)
    no_candidate_result = no_candidate_service.adapter_legacy_memory_imports(body)
    assert no_candidate_result["memoryProposal"] is None
    assert no_candidate_result["memoryFailure"] is None
    assert no_candidate_result["run"]["state"] == "no-candidate"
    assert no_candidate_result["world"]["revision"] == 0

    def safe_failure(base: Any, turns: tuple[Any, ...], allowlist: Collection[str]) -> WorldDelta:
        raise WorldExtractionError(("LEGACY_IMPORT_SAFE_FAILURE@$",), attempts=1)

    failure_service = _chat_service(tmp_path / "failure", memory_run_executor=safe_failure)
    failure_result = failure_service.adapter_legacy_memory_imports({**body, "operationId": "legacy-import-safe-failure"})
    assert failure_result["memoryProposal"] is None
    assert failure_result["memoryFailure"] == {
        "kind": "WorldExtractionError", "codes": ["LEGACY_IMPORT_SAFE_FAILURE@$"], "attempts": 1,
    }
    assert failure_result["run"]["state"] == "failed"
    assert failure_result["world"]["revision"] == 0


def test_legacy_evidence_import_rejects_a_new_operation_while_owner_review_is_pending(tmp_path: Path) -> None:
    observed: dict[str, Any] = {}
    service = _chat_service(tmp_path, memory_run_executor=_recording_delta_executor(observed))
    first_body: dict[str, Any] = {
        "operationId": "legacy-import-pending-first",
        "turns": [{"turnId": "legacy-import-pending-turn", "role": "user", "content": "这是第一条迁移 Evidence。", "occurredAt": "2026-08-10T00:00:00Z"}],
    }

    first = service.adapter_legacy_memory_imports(first_body)
    retained_world = service.memory_world()
    retry = service.adapter_legacy_memory_imports(first_body)

    assert retry["run"]["id"] == first["run"]["id"]
    assert len(observed["calls"]) == 1
    with pytest.raises(ValueError, match="^LEGACY_IMPORT_PENDING_REVIEW_EXISTS$"):
        service.adapter_legacy_memory_imports({
            "operationId": "legacy-import-pending-blocked",
            "turns": [{"turnId": "legacy-import-blocked-turn", "role": "user", "content": "这条不得排队进入新的迁移。", "occurredAt": "2026-08-10T00:01:00Z"}],
        })
    assert len(observed["calls"]) == 1
    assert len(service.memory_runs()["runs"]) == 1
    assert service.memory_world() == retained_world


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update({"cognition": "1.x 派生文本绝不在迁移入口接受"}),
        lambda body: body["turns"][0].update({"derivedCognition": "也不能藏在 turn 中"}),
        lambda body: body["turns"][0].update({"role": "assistant"}),
        lambda body: body["turns"].append({"turnId": "legacy-import-turn", "role": "user", "content": "重复 ID", "occurredAt": "2026-08-10T00:01:00Z"}),
        lambda body: body["turns"][0].update({"occurredAt": "2026-08-10T00:00:00"}),
        lambda body: body["turns"][0].update({"occurredAt": "2026-08-10 00:00:00Z"}),
    ],
)
def test_legacy_evidence_import_rejects_derived_or_non_raw_payloads(tmp_path: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    service = _chat_service(tmp_path, memory_run_executor=_recording_delta_executor({}))
    body: dict[str, Any] = {
        "operationId": "legacy-import-invalid",
        "turns": [{"turnId": "legacy-import-turn", "role": "user", "content": "原始 Owner Evidence。", "occurredAt": "2026-08-10T00:00:00Z"}],
    }
    mutate(body)

    with pytest.raises(ValueError):
        service.adapter_legacy_memory_imports(body)
    assert service.memory_runs()["runs"] == []


def test_memory_recall_is_read_only_current_accepted_cognitions_without_an_answerer(tmp_path: Path) -> None:
    """The legacy-chat bridge may read accepted memory, but never run an LLM or expose provenance."""
    from memoweft.world.loop import EvidenceRecord

    answer_factory_calls = 0

    def answer_factory() -> _FakeAnswerer:
        nonlocal answer_factory_calls
        answer_factory_calls += 1
        return _FakeAnswerer()

    service = _chat_service(tmp_path, answer_client_factory=answer_factory)
    prior_ids = _install_current_doubao_memories(service)
    loop = service._memory_loop()  # noqa: SLF001 - exact read-only boundary contract
    pending = loop.stage_correction_bundle(
        prior_ids,
        "豆包是 AI；我的猫叫二五。",
        EvidenceRecord("evidence:replacement", "豆包是 AI；我的猫叫二五。"),
    )
    loop.decide(pending.id, pending.result_hash, "accept")
    before_world = service.memory_world()
    before_runs = service.memory_runs()

    recalled = service.memory_recall({"query": "豆包是什么？"})
    absent = service.memory_recall({"query": "今天天气如何？"})

    assert recalled == {
        "status": "recalled",
        "memories": [{"content": "豆包是 AI；我的猫叫二五。", "confidence": 600, "credStatus": "limited"}],
    }
    assert absent == {"status": "no_memory", "memories": []}
    assert answer_factory_calls == 0
    assert service.memory_world() == before_world
    assert service.memory_runs() == before_runs
    assert set(recalled["memories"][0]) == {"content", "confidence", "credStatus"}
    assert all(prior not in {item.id for item in loop.view().current_cognitions} for prior in prior_ids)
    assert "我的猫叫豆包。" not in str(recalled)
    assert "豆包其实是假的。" not in str(recalled)

    for invalid in ({}, {"query": ""}, {"query": "x", "unexpected": True}, {"query": "x" * 1201}):
        with pytest.raises(ValueError):
            service.memory_recall(invalid)


def test_context_only_just_told_you_turn_uses_real_roles_and_never_enters_addition(tmp_path: Path) -> None:
    from memoweft.world import WorldDelta

    answerer = _FakeAnswerer()
    observed: dict[str, Any] = {"calls": []}

    def empty(base: object, turns: tuple[object, ...], allowlist: Collection[str]) -> WorldDelta:
        observed["calls"].append({"turns": turns, "allowlist": frozenset(allowlist)})
        return WorldDelta(
            world_id=base.world.world_id,  # type: ignore[attr-defined]
            source_evidence_ids=(next(iter(allowlist)),),
        )

    service = _chat_service(
        tmp_path,
        memory_run_executor=empty,
        answer_client_factory=lambda: answerer,
    )
    previous = "豆包其实是AI不是我的猫，我的猫叫二五"
    service.chat_turn({"message": previous})
    observed["calls"].clear()

    result = service.chat_turn({"message": "刚才告诉你了"})

    messages = answerer.calls[-1]
    assert [message.role for message in messages] == ["system", "system", "user", "assistant", "user"]  # type: ignore[attr-defined]
    assert [message.content for message in messages[-3:]] == [  # type: ignore[attr-defined]
        previous,
        "本地回复 #1",
        "刚才告诉你了",
    ]
    system_prompt = messages[0].content  # type: ignore[attr-defined]
    assert "必须从最近真实 user 消息中找出具体内容" in system_prompt
    assert "明确复述那条内容后再回应" in system_prompt
    assert "不得只泛泛确认或重复追问" in system_prompt
    assert observed["calls"] == []
    assert result["memoryProposal"] is None
    assert result["memoryFailure"] is None
    assert result["world"]["pendingReviews"] == []
    assert service.chat_session()["transcript"][-2:] == [result["userTurn"], result["assistantTurn"]]
    retained = service.memory_runs()["runs"][-1]
    assert retained["state"] == "no-candidate"
    assert retained["filter"] == "meta-reference-only"
    assert retained["candidateMemory"] == {"entities": [], "relationships": [], "events": [], "cognitions": []}
    stages = {step["name"]: step["state"] for step in result["pipeline"]}
    assert stages["Evidence"] == "context-only"
    assert stages["Extract"] == "skipped"
    assert stages["Review"] == "not-needed"
    assert stages["Apply"] == "not-applied"


def test_meta_reference_only_filter_is_narrow_and_keeps_confirmation_carriers_eligible() -> None:
    assert LabService._is_meta_reference_only("  「刚才告诉你了！」 ")  # noqa: SLF001
    assert LabService._is_meta_reference_only("之前已经说过了。")  # noqa: SLF001
    assert LabService._is_meta_reference_only("前面我提过")  # noqa: SLF001
    assert not LabService._is_meta_reference_only("对")  # noqa: SLF001
    assert not LabService._is_meta_reference_only("是的")  # noqa: SLF001
    assert not LabService._is_meta_reference_only("刚才告诉你豆包不是我的猫")  # noqa: SLF001
    assert not LabService._is_meta_reference_only("之前提过南京旅行")  # noqa: SLF001


def test_natural_correction_stages_two_current_cognitions_without_running_addition_extractor(tmp_path: Path) -> None:
    answerer = _FakeAnswerer()
    classifier = _FakeCorrectionClassifier({
        "is_correction": True,
        "prior_cognition_ids": ["cognition:doubao-cat", "cognition:doubao-fake"],
        "structure_hints": ["entity_reclassification"],
    })
    observed: dict[str, Any] = {}
    service = _chat_service(
        tmp_path,
        memory_run_executor=_recording_delta_executor(observed),
        answer_client_factory=lambda: answerer,
        correction_client_factory=lambda: classifier,
    )
    prior_ids = _install_current_doubao_memories(service)
    service._chat_session()["turns"].append({  # noqa: SLF001 - exact preceding-assistant contract
        "turnId": "turn:preceding-assistant",
        "role": "assistant",
        "content": "所以豆包是你的猫。",
        "occurredAt": "2026-08-09T02:00:00Z",
    })
    before = service.memory_world()

    result = service.chat_turn({"message": "豆包其实是AI，不是我的猫；我的猫叫二五。"})

    proposal = result["memoryProposal"]
    assert proposal["state"] == "correction-pending"
    assert result["memoryFailure"] is None
    assert result["world"]["revision"] == before["revision"]
    assert {item["id"] for item in result["world"]["memory"]["cognitions"]} == set(prior_ids)
    assert observed.get("calls", []) == []
    assert [message.role for message in classifier.calls[0]] == ["system", "assistant", "user"]  # type: ignore[attr-defined]
    assert classifier.calls[0][1].content == "所以豆包是你的猫。"  # type: ignore[attr-defined]
    assert classifier.calls[0][-1].content == result["userTurn"]["content"]  # type: ignore[attr-defined]
    assert classifier.calls[0][-1].content != result["assistantTurn"]["content"]  # type: ignore[attr-defined]
    assert proposal["evidence"] == [{
        "evidenceId": result["userTurn"]["turnId"],
        "text": result["userTurn"]["content"],
    }]
    assert proposal["candidateMemory"]["cognitions"][0]["content"] == result["userTurn"]["content"]
    correction = proposal["correction"]
    assert {item["id"] for item in correction["supersededCognitions"]} == set(prior_ids)
    assert correction["replacementContent"] == result["userTurn"]["content"]
    assert correction["structureHints"] == ["entity_reclassification"]
    assert correction["structuralNotices"] == [
        "接受后，所选旧认知会保留在历史中，并由一条新的当前认知替代。",
        "结构提示只供你复核；本阶段不会自动修改实体类别。",
    ]
    stages = {step["name"]: step["state"] for step in result["pipeline"]}
    assert stages["Correction"] == "awaiting-owner"
    assert stages["Extract"] == "skipped"

    accepted = service.memory_decision({
        "runId": proposal["id"],
        "resultHash": proposal["resultHash"],
        "decision": "accept",
    })
    assert accepted["world"]["revision"] == before["revision"] + 1
    view = service._memory_loop().view()  # noqa: SLF001 - history acceptance contract
    assert set(view.superseded_cognition_ids) == set(prior_ids)
    assert set(prior_ids).issubset(view.graph.cognitions)
    assert len(view.current_cognitions) == 1
    replacement = view.current_cognitions[0]
    assert replacement.content == result["userTurn"]["content"]
    assert {item.prior_cognition_id for item in view.transitions} == set(prior_ids)
    assert {item.replacement_cognition_id for item in view.transitions} == {replacement.id}
    accepted_run = next(item for item in service.memory_runs()["runs"] if item["id"] == proposal["id"])
    accepted_stages = {step["name"]: step["state"] for step in accepted_run["pipeline"]}
    assert accepted_stages["Correction"] == "accept"
    assert accepted_stages["Extract"] == "skipped"


def test_rejected_natural_correction_does_not_change_world_or_history(tmp_path: Path) -> None:
    classifier = _FakeCorrectionClassifier({
        "is_correction": True,
        "prior_cognition_ids": ["cognition:doubao-cat", "cognition:doubao-fake"],
        "structure_hints": [],
    })
    service = _chat_service(
        tmp_path,
        memory_run_executor=_recording_delta_executor({}),
        answer_client_factory=_FakeAnswerer,
        correction_client_factory=lambda: classifier,
    )
    prior_ids = _install_current_doubao_memories(service)
    before = service.memory_world()
    result = service.chat_turn({"message": "豆包不是我的猫。"})
    proposal = result["memoryProposal"]

    rejected = service.memory_decision({
        "runId": proposal["id"],
        "resultHash": proposal["resultHash"],
        "decision": "reject",
    })

    assert rejected["world"]["revision"] == before["revision"]
    view = service._memory_loop().view()  # noqa: SLF001 - rejection isolation contract
    assert {item.id for item in view.current_cognitions} == set(prior_ids)
    assert not view.superseded_cognition_ids
    assert not view.transitions
    assert not view.pending_reviews


def test_invalid_declared_correction_fails_closed_without_falling_back_to_addition(tmp_path: Path) -> None:
    classifier = _FakeCorrectionClassifier({
        "is_correction": True,
        "prior_cognition_ids": ["cognition:not-current"],
        "structure_hints": [],
    })
    observed: dict[str, Any] = {}
    service = _chat_service(
        tmp_path,
        memory_run_executor=_recording_delta_executor(observed),
        answer_client_factory=_FakeAnswerer,
        correction_client_factory=lambda: classifier,
    )
    _install_current_doubao_memories(service)
    before = service.memory_world()

    result = service.chat_turn({"message": "豆包不是我的猫。"})

    assert result["memoryProposal"] is None
    assert result["memoryFailure"] == {
        "kind": "NaturalCorrectionError",
        "codes": ["unknown_cognition"],
        "attempts": 1,
    }
    assert observed.get("calls", []) == []
    assert result["world"] == before
    stages = {step["name"]: step["state"] for step in result["pipeline"]}
    assert stages["Correction"] == "failed"
    assert stages["Extract"] == "blocked"
    assert service.memory_world()["pendingReviews"] == []


def test_false_correction_classifier_uses_current_turn_addition_and_narrow_recalled_cognition_context(tmp_path: Path) -> None:
    answerer = _FakeAnswerer()
    classifier = _FakeCorrectionClassifier()
    observed: dict[str, Any] = {}
    service = _chat_service(
        tmp_path,
        memory_run_executor=_recording_delta_executor(observed),
        answer_client_factory=lambda: answerer,
        correction_client_factory=lambda: classifier,
    )
    _install_current_doubao_memories(service)

    new_message = "豆包不是我的朋友，我只是新增一句测试。"
    result = service.chat_turn({"message": new_message})

    assert len(classifier.calls) == 1
    assert len(observed["calls"]) == 1
    extraction = observed["calls"][0]
    assert [(turn.role, turn.content) for turn in extraction["turns"]] == [("user", new_message)]
    assert extraction["allowlist"] == frozenset({result["userTurn"]["turnId"]})
    assert result["memoryProposal"]["state"] == "candidate-ready"
    assert all(turn.turn_id != result["assistantTurn"]["turnId"] for turn in extraction["turns"])

    recalled_context = answerer.calls[0][1].content  # type: ignore[attr-defined]
    assert "我的猫叫豆包。" in recalled_context
    assert "豆包其实是假的。" in recalled_context
    assert "animal" not in recalled_context
    assert "entity:doubao" not in recalled_context
    assert "evidence" not in recalled_context.lower()


def test_scripted_memory_run_keeps_all_user_turns_eligible(tmp_path: Path) -> None:
    answerer = _FakeAnswerer()
    observed: dict[str, Any] = {}
    service = _chat_service(
        tmp_path,
        memory_run_executor=_recording_delta_executor(observed),
        answer_client_factory=lambda: answerer,
    )

    service.memory_run({
        "title": "脚本情景仍是整批用户证据",
        "turns": [
            {"role": "user", "content": "第一条用户事实。"},
            {"role": "assistant", "content": "只作上下文。"},
            {"role": "user", "content": "第二条用户事实。"},
        ],
    })

    extraction = observed["calls"][0]
    user_ids = {turn.turn_id for turn in extraction["turns"] if turn.role == "user"}
    assistant_ids = {turn.turn_id for turn in extraction["turns"] if turn.role == "assistant"}
    assert extraction["allowlist"] == frozenset(user_ids)
    assert len(user_ids) == 2
    assert extraction["allowlist"].isdisjoint(assistant_ids)


def test_chat_extraction_failure_is_safe_and_does_not_break_the_reply(tmp_path: Path) -> None:
    from memoweft.world import WorldExtractionError

    def fail(base: object, turns: tuple[object, ...], allowlist: Collection[str]) -> object:
        raise WorldExtractionError(("CHAT_EXTRACT_FAILED@$",), attempts=2)

    answerer = _FakeAnswerer()
    service = _chat_service(tmp_path, memory_run_executor=fail, answer_client_factory=lambda: answerer)

    result = service.chat_turn({"message": "还是先正常聊天。"})

    assert result["assistantTurn"]["content"] == "本地回复 #1"
    assert result["memoryProposal"] is None
    assert result["memoryFailure"] == {"kind": "WorldExtractionError", "codes": ["CHAT_EXTRACT_FAILED@$"], "attempts": 2}
    assert next(step for step in result["pipeline"] if step["name"] == "Extract")["state"] == "failed"
    assert len(service.chat_session()["transcript"]) == 2


def test_chat_run_persists_structured_recall_across_reload_for_every_terminal_extraction_state(tmp_path: Path) -> None:
    """The chat response and retained inspection run must expose the same Recall.

    A chat turn has three ordinary terminal extraction states.  The transient
    response already showed Recall, but the persisted run did not, which made
    a browser refresh lose the first step of that turn's observable pipeline.
    """
    from memoweft.world import WorldDelta, WorldExtractionError

    def empty(base: object, turns: tuple[object, ...], allowlist: Collection[str]) -> WorldDelta:
        return WorldDelta(
            world_id=base.world.world_id,  # type: ignore[attr-defined]
            source_evidence_ids=(next(iter(allowlist)),),
        )

    def fail(base: object, turns: tuple[object, ...], allowlist: Collection[str]) -> object:
        raise WorldExtractionError(("CHAT_EXTRACT_FAILED@$",), attempts=2)

    cases: tuple[tuple[str, Callable[[object, tuple[object, ...], Collection[str]], object]], ...] = (
        ("candidate-ready", _recording_delta_executor({})),
        ("no-candidate", empty),
        ("failed", fail),
    )
    for expected_state, executor in cases:
        state_dir = tmp_path / expected_state
        service = _chat_service(
            state_dir,
            memory_run_executor=executor,
            answer_client_factory=_FakeAnswerer,
        )

        result = service.chat_turn({"message": f"验证 {expected_state} 的召回持久化。"})

        assert result["recall"] == {
            "status": "no_memory",
            "recalledEntities": [],
            "recalledRelationships": [],
            "recalledEvents": [],
            "recalledCognitions": [],
            "evidence": [],
            "historyCognitionIds": [],
        }
        reloaded = _chat_service(state_dir, answer_client_factory=_FakeAnswerer)
        retained = reloaded.memory_runs()["runs"][-1]
        assert retained["state"] == expected_state
        assert retained["recall"] == result["recall"]


def test_empty_world_delta_is_not_staged_as_an_acceptable_memory_proposal(tmp_path: Path) -> None:
    from memoweft.world import WorldDelta

    def empty(base: object, turns: tuple[object, ...], allowlist: Collection[str]) -> WorldDelta:
        return WorldDelta(world_id=base.world.world_id, source_evidence_ids=(next(iter(allowlist)),))  # type: ignore[attr-defined]

    answerer = _FakeAnswerer()
    service = _chat_service(tmp_path, memory_run_executor=empty, answer_client_factory=lambda: answerer)
    before = service.memory_world()

    run = service.memory_run({"title": "普通问候", "turns": [{"role": "user", "content": "你好"}]})
    assert run["state"] == "no-candidate"
    assert "reviewId" not in run and "resultHash" not in run
    assert run["candidateMemory"] == {"entities": [], "relationships": [], "events": [], "cognitions": []}
    after_run = service.memory_world()
    assert after_run["revision"] == before["revision"]
    assert after_run["pendingReviews"] == []

    chat = service.chat_turn({"message": "再打个招呼"})
    assert chat["memoryProposal"] is None and chat["memoryFailure"] is None
    stages = {step["name"]: step["state"] for step in chat["pipeline"]}
    assert stages["Extract"] == "no-candidate"
    assert stages["Review"] == "not-needed"
    assert stages["Apply"] == "not-applied"
    after_chat = service.memory_world()
    assert after_chat["revision"] == before["revision"]
    assert after_chat["pendingReviews"] == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"message": ""},
        {"message": "hi", "assistantText": "forbidden"},
        {"message": "hi", "turns": [{"role": "assistant", "content": "forbidden"}]},
    ],
)
def test_chat_turn_rejects_caller_owned_roles_and_assistant_text(tmp_path: Path, body: dict[str, object]) -> None:
    service = _chat_service(tmp_path, answer_client_factory=_FakeAnswerer)

    with pytest.raises(ValueError, match="message|server-owned"):
        service.chat_turn(body)


def test_chat_routes_keep_loopback_origin_and_json_body_boundary(tmp_path: Path) -> None:
    answerer = _FakeAnswerer()
    Handler.service, Handler.bind_port, Handler.instance_token = _chat_service(
        tmp_path,
        memory_run_executor=_recording_delta_executor({}),
        answer_client_factory=lambda: answerer,
    ), 0, "test-token"
    server = NextLabHTTPServer(("127.0.0.1", 0), Handler)
    Handler.bind_port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        def request(method: str, path: str, body: object | None = None, *, request_origin: str | None = None) -> http.client.HTTPResponse:
            headers = {"Host": f"127.0.0.1:{server.server_port}"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            if request_origin is not None:
                headers["Origin"] = request_origin
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            encoded = json.dumps(body).encode() if body is not None else None
            connection.request(method, path, body=encoded, headers=headers)
            return connection.getresponse()

        session_response = request("GET", "/api/chat-session")
        assert session_response.status == 200 and json.loads(session_response.read())["transcript"] == []
        accepted = request("POST", "/api/chat-turns", {"message": "你好"}, request_origin=origin)
        assert accepted.status == 200 and json.loads(accepted.read())["assistantTurn"]["role"] == "assistant"
        assert request("POST", "/api/chat-turns", {"message": "你好"}, request_origin="http://evil.invalid").status == 403
        assert request("POST", "/api/chat-turns", {"message": "你好", "assistantText": "伪造"}, request_origin=origin).status == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
