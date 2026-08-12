"""Static contract for the owner-facing chat-first Next Lab page."""
from __future__ import annotations

import re
from pathlib import Path


WORKBENCH = Path(__file__).resolve().parents[2] / "next-lab" / "workbench.html"


def test_default_workbench_is_a_chinese_chat_surface_not_a_scripted_scenario_form() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert '<html lang="zh-CN">' in html
    assert "MemoWeft 聊天工作台" in html
    assert 'id="messages"' in html
    assert 'id="messageInput"' in html
    assert 'id="send"' in html
    assert 'id="composer"' in html
    assert "你只需要说自己的话" in html
    assert "/api/chat-session" in html
    assert "/api/chat-turns" in html
    assert "api('/api/chat-turns',{message})" in html
    assert "expectedMemory" not in html
    assert "laterQuestion" not in html
    assert "expectedAnswer" not in html
    assert "addAssistant" not in html


def test_chat_turn_renders_answer_then_compact_memory_work_without_blocking_chat() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert "MemoWeft 本轮工作" in html
    assert "召回" in html and "候选记忆" in html
    assert "接受记忆" in html and "忽略这次" in html
    assert "本轮没有形成候选记忆；聊天仍可继续。" in html
    assert "const failure=result.memoryFailure||proposal?.failure" in html
    assert "AI 已回复，但本轮提取失败；可继续。" in html
    assert "/api/memory-decisions" in html
    assert "renderWorld(result.world)" in html
    assert "/api/memory-world" in html
    assert "旧 Golden 开发者工具" in html and 'href="/legacy"' in html


def test_addition_and_natural_correction_have_distinct_owner_facing_cards() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert 'proposal?.state==="correction-pending"' in html
    assert "本轮候选" in html
    assert "将替换" in html
    assert "改为" in html
    assert "结构提醒" in html
    assert 'class="correction-proposal"' in html
    assert 'class="correction-before"' in html
    assert 'class="correction-after"' in html
    assert "structural-notice" in html
    assert "supersededCognitions" in html
    assert "replacementContent" in html
    assert "structureHints" in html
    assert "structuralNotices" in html


def test_correction_card_escapes_every_backend_supplied_display_value() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert "superseded.map(item=>`<li>${esc(itemText(item))}</li>`)" in html
    assert "<p>${esc(correction.replacementContent||'')}</p>" in html
    assert "notices.map(item=>`<li>${esc(structureText(item))}</li>`)" in html
    assert "hints.map(item=>`<li>${esc(structureText(item))}</li>`)" in html


def test_manual_correction_response_is_rendered_with_the_same_safe_card_as_natural_correction() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert "function currentCognitionById(cognitionId)" in html
    assert "proposal?.candidateCorrection" in html
    assert "proposal?.priorCognitionId" in html
    assert "candidateCorrection?.content" in html
    assert "candidateCorrection?.evidence" in html
    assert "prior||{id:proposal.priorCognitionId}" in html
    assert "evidence:result.evidence||proposal?.evidence||proposal?.candidateCorrection?.evidence||[]" in html


def test_correction_uses_existing_review_decision_contract_and_only_two_actions() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert "runId:proposal?.runId||proposal?.id" in html
    assert "reviewId:proposal?.reviewId" in html
    assert "data-review-id=" in html
    assert "data-decision=\"accept\"" in html
    assert "data-decision=\"reject\"" in html
    assert "接受记忆" in html and "忽略这次" in html
    assert "api('/api/memory-decisions',{runId,resultHash,decision})" in html
    assert "renderWorld(result.world)" in html


def test_chat_pipeline_is_presented_in_conversation_order() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert (
        "const pipelineOrder=['Recall','Answer','Evidence','Correction',"
        "'Extract','Review','Apply']"
    ) in html


def test_send_immediately_keeps_the_user_turn_and_shows_a_replaceable_thinking_placeholder() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert "function appendPendingTurn(message,requestId)" in html
    assert "正在思考" in html
    assert "pending-turn-${++requestSequence}" in html
    assert "appendPendingTurn(message,requestId)" in html
    assert "appendTurnResult(result,requestId)" in html
    assert "function showTurnFailure(requestId,error)" in html
    assert "你的话已保留，可以重试或继续说下一句。" in html


def test_workbench_has_one_inline_script_with_no_external_frontend_dependency() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")
    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.DOTALL)

    assert len(scripts) == 1
    assert "<script src=" not in html
    assert "fetch(" in scripts[0]


def test_refresh_rehydrates_each_chat_turn_with_its_persisted_memory_run() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert "/api/memory-runs" in html
    assert "const runByCurrentUserTurn=new Map()" in html
    assert "bindRunsToTranscript(transcript,runs)" in html
    assert "run.turns" in html and "turn.turnId" in html
    assert "renderHistoricalTurnWork(run)" in html
    assert "candidate-ready" in html and "no-candidate" in html and "failed" in html
    assert "accepted" in html and "rejected" in html


def test_v1_style_shell_keeps_chat_primary_and_acceptance_truth_visible() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert 'id="rail"' in html
    assert 'data-panel="chat-panel"' in html
    assert 'data-panel="memory-panel"' in html
    assert 'id="acceptedMemoryPill"' in html
    assert 'id="memoryDrawer"' in html
    assert "关于我" in html and "我的世界其他对象" in html and "历史" in html
    assert "候选不是已记住" in html
    assert "已接受记忆" in html
    assert "候选绝不叫已记住" not in html  # product rule belongs in behavior, not visible jargon


def test_detail_is_progressively_disclosed_and_backend_display_is_escaped() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert "<summary>本轮详情</summary>" in html
    assert "Evidence" in html and "Recall" in html and "Pipeline" in html
    assert "esc(itemText(item))" in html
    assert "esc(turn.content||turn.text||'')" in html
    assert "esc(failure.kind||'安全失败')" in html


def test_owner_memory_groups_exclude_the_base_owner_but_keep_owner_connected_records() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert "function isBaseOwnerEntity(item)" in html
    assert "function isOwnerEndpointRelationship(item)" in html
    assert "function isOwnerParticipantEvent(item)" in html
    assert "const entities=(memory.entities||[]).filter(item=>!isBaseOwnerEntity(item))" in html
    assert "const ownerRelationships=relationships.filter(isOwnerEndpointRelationship)" in html
    assert "const ownerEvents=events.filter(isOwnerParticipantEvent)" in html
    assert "const acceptedCount=owner.length+ownerRelationships.length+ownerEvents.length+other.length" in html


def test_rehydrated_turn_details_use_the_persisted_run_recall_projection() -> None:
    html = WORKBENCH.read_text(encoding="utf-8")

    assert "recall:run?.recall||{}" in html
