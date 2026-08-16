"""Static contract for the 1.x shell's automatic Next-memory observation UI."""
from __future__ import annotations

import re
from pathlib import Path


TESTBENCH = Path(__file__).resolve().parents[2] / "testbench" / "index.html"


def test_original_v1_chat_and_management_entrypoints_survive_the_next_bridge() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert 'id="text"' in html
    assert "async function send()" in html
    assert "async function loadSessions()" in html
    assert "async function openSessionUI(id, opts)" in html
    assert "async function pollChat()" in html
    assert "function userSoftReset()" in html
    assert 'id="memPanel"' in html and 'id="mmList"' in html


def test_formal_world_is_the_only_world_surface_and_legacy_is_read_only_migration_source() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert 'id="nextWorldCard"' in html
    assert "个人记忆世界" in html
    assert html.index('id="nextWorldCard"') < html.index('id="legacyMigrationSource"')
    assert 'id="legacyMigrationSource"' in html
    assert "1.0 整理出的迁移线索（尚未成为正式记忆）" in html
    assert "用原话处理迁移" in html
    assert "function stageLegacyCognitionMigration(cognition, buttonEl)" in html
    assert "function hasNextPendingDecision()" not in html
    assert 'id="nextWorldPending"' not in html
    assert "请先在“待决定”里接受或忽略当前候选" not in html
    assert 'fetch(\'/api/next/legacy-cognition-migrations\'' in html


def test_automatic_apply_ui_has_no_owner_decision_route_or_buttons() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "/api/next/memory-decisions" not in html
    assert "function decideNextMemory" not in html
    assert "接受记忆" not in html
    assert "忽略这次" not in html
    assert "等待你决定" not in html
    assert "不会要求你批准才能形成记忆" in html


def test_terminal_outcomes_are_observable_and_applied_is_evidence_backed() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "processing: '正在根据这轮原话验证并形成个人记忆世界；聊天可以继续。'" in html
    assert "applied: '已通过验证并自动写入当前 2.0 记忆世界。'" in html
    assert "'no-change': '本轮没有符合长期形成条件的 World Change。'" in html
    assert "'no-candidate': '本轮没有符合长期形成条件的 World Change。'" in html
    assert "'clarification-required'" in html and "failed:" in html
    assert "function appendNextClaimObservation(host, view)" in html
    assert "appendNextCandidateContext(bubble, view);" in html
    assert "appendNextClaimObservation(bubble, view);" in html
    assert "Provenance：${provenanceIds.join('、')}" in html
    assert "Evidence（本轮证据）" in html
    assert "对象：${names.join('、')" in html
    assert "视角：Owner" in html
    assert "const claims = proposal.claims?.claims || proposal.claims || [];" in html
    assert "row.textContent = nextClaimObservationText(claim, entityIndex);" in html
    assert "nextText(claim)" not in html


def test_current_world_is_rendered_from_the_authoritative_route_not_history_snapshots() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    register = re.search(
        r"function registerNextMemory\(nextMemory\) \{(?P<body>[\s\S]*?)\n      \}", html
    )
    assert register is not None
    assert "renderNextWorld" not in register.group("body")
    assert "async function loadNextWorld(context = {})" in html
    assert "fetch('/api/next/memory-world')" in html
    assert "async function refreshNextWorldAfterApplied(operationId, sessionId, attempt = 0)" in html
    assert "const refreshed = await loadNextWorld({ sessionId });" in html
    assert "const NEXT_WORLD_REFRESH_MAX_ATTEMPTS = 3;" in html
    assert "void refreshNextWorldAfterApplied(operationId, sessionId);" in html


def test_formal_event_world_cards_show_time_participants_and_related_objects_safely() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "event.occurred_at ? `发生于 ${event.occurred_at}` : null" in html
    assert "...(event?.participants || []).map" in html
    assert "...(event?.related_entity_ids || [])" in html
    assert "const relatedEntities = (event.related_entity_ids || [])" in html
    assert "`参与：${participants.join('、')}`" in html
    assert "`相关对象：${relatedEntities.join('、')}`" in html
    assert "section.appendChild(domEl('div', 'evmeta', detail.join(' · ')))" in html


def test_processing_card_polls_with_operation_and_session_identity_checks() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "const _nextOperationRows = new Map()" in html
    assert "const _nextOperationFlights = new Map()" in html
    assert "const _nextOperationTerminal = new Set()" in html
    assert "function nextOperationMarker(raw)" in html
    assert "operationId" in html and "currentEvidenceId" in html
    assert "function renderNextMemoryTurn(row, nextMemory)" in html
    assert "function appendNextMemoryTurn(nextMemory, context = {})" in html
    assert "async function pollNextMemoryOperation(marker)" in html
    assert "/api/next/memory-operations?" in html
    assert "result.operationId !== operationId" in html
    assert "result.sessionId !== sessionId" in html
    assert "_currentSessionId !== sessionId" in html
    assert "result.state === 'ready'" in html and "result.state === 'failed'" in html
    assert "_nextOperationTerminal.add(operationId)" in html
    assert "const _nextWorldRefreshTimers = new Map()" in html


def test_history_hydration_is_session_scoped_and_dynamic_content_is_text_only() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "/api/next/memory-runs" in html
    assert "const _nextRuns = new Map()" in html
    assert "async function loadNextMemoryRuns()" in html
    assert "const liveRun = _nextRuns.get(runId)" in html
    assert "const requestedSessionId = _currentSessionId;" in html
    assert "if (_currentSessionId !== requestedSessionId) return false;" in html
    assert "nextSessionId(run) === requestedSessionId" in html
    assert "function nextText(value)" in html
    assert ".textContent =" in html
    assert not re.search(r"fetch\(\s*['\"]https?://", html)


def test_correction_remains_an_explicit_evidence_entry_not_a_review_decision() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "function requestNextMemoryCorrection(cognition, buttonEl)" in html
    assert 'fetch(\'/api/next/memory-corrections\'' in html
    assert "function nextCorrectionOperation(cognitionId, correctionText)" in html
    assert "const _nextCorrectionOperations = new Map()" in html
    assert "testbench-correction:${globalThis.crypto.randomUUID()}" in html
    assert "const { operationId } = nextCorrectionOperation(cognition.id, text);" in html
    assert "body: JSON.stringify({ operationId, cognitionId: cognition.id, correctionText })" in html
    assert "纠正结果暂不明确；可以用相同原话重试。" in html
    assert "await loadNextWorld();" in html
    assert "纠正已通过验证并自动应用到当前世界。" in html
    assert "正式删除" not in html


def test_conflicted_cognition_provenance_is_visible_in_formal_world_and_review() -> None:
    """The formal product shell keeps conflict semantics and raw Evidence observable."""

    html = TESTBENCH.read_text(encoding="utf-8")
    world_surface = html[
        html.index("function appendAcceptedCognitionDetail") :
        html.index("function appendAcceptedEntitySection")
    ]
    review_surface = html[
        html.index("function appendNextClaimObservation") :
        html.index("function registerNextMemory")
    ]

    for surface in (world_surface, review_surface):
        assert "cred_status" in surface
        assert "confidence" in surface
        assert "sources" in surface
        assert "relation" in surface

    # Formal review uses DOM text nodes for the exact current-turn Evidence;
    # this is the safe equivalent of HTML escaping and must not regress to
    # interpolated innerHTML.
    assert "nextText(item.text || item.content || item)" in review_surface
    assert "detail.appendChild(domEl('div', 'evmeta'," in review_surface


def test_cognition_evidence_change_is_rendered_as_text_only_review_history() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")
    review_surface = html[
        html.index("function appendNextClaimObservation") :
        html.index("function appendNextCandidateContext")
    ]

    assert "proposal.cognitionEvidenceChanges || view.run.cognitionEvidenceChanges || []" in review_surface
    assert "同一 cognition 的 Evidence 变化" in review_surface
    assert "change?.relation" in review_surface
    assert "change?.evidenceId" in review_surface
    assert "before?.confidence" in review_surface
    assert "before?.cred_status" in review_surface
    assert "after?.confidence" in review_surface
    assert "after?.cred_status" in review_surface
    assert "此前（仅作为变化对照，不是当前值）" in review_surface
    assert "本轮应用后" in review_surface
    assert ".textContent =" in review_surface
    assert "innerHTML" not in review_surface


def test_typed_cognition_replacement_uses_text_nodes_and_precedes_legacy_correction() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")
    review_surface = html[
        html.index("function appendNextClaimObservation") :
        html.index("function appendNextCandidateContext")
    ]

    assert "proposal.cognitionReplacements || view.run.cognitionReplacements || []" in review_surface
    assert "纠正前（历史，只作对照，不是当前值）" in review_surface
    assert "纠正后（本轮应用后的当前值）" in review_surface
    assert "structured_claim" in review_surface
    assert "target" in review_surface
    assert "Evidence" in review_surface
    assert "domEl(" in review_surface
    assert ".textContent =" in review_surface
    assert "innerHTML" not in review_surface
    assert review_surface.index("cognitionReplacements.length") < review_surface.index(
        "proposal.correction || manual.content"
    )


def test_relationship_targeted_owner_evaluation_is_text_only_and_not_unbound() -> None:
    """Compiler-owned claim.object is shown without exposing its opaque handle."""

    html = TESTBENCH.read_text(encoding="utf-8")
    review_surface = html[
        html.index("function nextClaimObservationText") :
        html.index("function appendNextCandidateContext")
    ]
    world_surface = html[
        html.index("function buildAcceptedEntityIndex") :
        html.index("function appendNextWorldItem")
    ]

    assert "function nextRelationshipObjectText" in html
    assert "function nextOwnerRelationshipEvaluationText" in html
    assert "function nextClaimObservationText" in html
    assert "claim?.object?.kind === 'relationship'" in review_surface
    assert "claim?.kind === 'evaluation'" in review_surface
    assert "row.textContent = nextClaimObservationText(claim, entityIndex);" in review_surface
    assert "nextText(claim)" not in review_surface
    assert "accepted_object_handles" not in review_surface
    assert "relationshipCognitions" in world_surface
    assert "cognition?.target?.kind === 'relationship'" in world_surface
    assert "Owner 对当前关系的评价" in world_surface
    assert "nextOwnerRelationshipEvaluationText(" in world_surface
    assert "textContent" in world_surface


def test_event_targeted_owner_evaluation_is_text_only_and_not_unbound() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")
    review_surface = html[
        html.index("function nextClaimObservationText") :
        html.index("function appendNextCandidateContext")
    ]
    world_surface = html[
        html.index("function buildAcceptedEntityIndex") :
        html.index("function appendNextWorldItem")
    ]

    assert "function nextEventObjectText" in html
    assert "function nextOwnerEventEvaluationText" in html
    assert "claim?.object?.kind === 'event'" in review_surface
    assert "nextOwnerEventEvaluationText(" in review_surface
    assert "accepted_object_handles" not in review_surface
    assert "eventCognitions" in world_surface
    assert "cognition?.target?.kind === 'event'" in world_surface
    assert "Owner 对当前事件的评价" in world_surface
    assert "nextOwnerEventEvaluationText(" in world_surface
    assert "textContent" in world_surface
