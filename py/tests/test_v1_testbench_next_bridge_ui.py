"""Static contract: the 1.x testbench grows a same-origin Next-memory bridge."""
from __future__ import annotations

import re
from pathlib import Path


TESTBENCH = Path(__file__).resolve().parents[2] / "testbench" / "index.html"


def test_original_v1_chat_and_management_entrypoints_survive_the_next_bridge() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert 'id="text"' in html
    assert "onkeydown=\"if (event.key === 'Enter') send();\"" in html
    assert "function addMsg(role, text)" in html
    assert "async function send()" in html
    assert "async function loadSessions()" in html
    assert "async function openSessionUI(id, opts)" in html
    assert "async function pollChat()" in html
    assert "function onEnterMemory()" in html
    assert "function userSoftReset()" in html
    assert "function renderXray(r)" in html
    assert "id=\"memPanel\"" in html and "id=\"mmList\"" in html
    assert "id=\"mode-wizard\"" in html and "id=\"settingsPanel\"" in html


def test_drawer_has_one_formal_next_memory_area_and_legacy_is_read_only_migration_source() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert 'id="nextWorldCard"' in html
    assert "正式记忆" in html
    assert html.index('id="nextWorldCard"') < html.index('id="legacyMigrationSource"')
    assert 'id="legacyMigrationSource"' in html
    assert "1.0 整理出的迁移线索（尚未成为正式记忆）" in html
    assert "用原话生成正式候选" in html
    assert "function stageLegacyCognitionMigration(cognition, buttonEl)" in html
    assert "function hasNextPendingDecision()" in html
    assert "请先在“待决定”里接受或忽略当前候选" in html
    assert 'fetch(\'/api/next/legacy-cognition-migrations\'' in html
    assert "body: JSON.stringify({ cognitionId: cognition.id })" in html
    legacy_loader = re.search(
        r"async function loadCognitionFriendly\(\) \{(?P<body>[\s\S]*?)\n      \}", html
    )
    assert legacy_loader is not None
    assert "softEditCog(c.id" not in legacy_loader.group("body")
    assert "softDelCog(c.id" not in legacy_loader.group("body")


def test_formal_world_count_and_correction_use_only_next_routes() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    pill = re.search(
        r"function updateMemPillCount\(\) \{(?P<body>[\s\S]*?)\n      \}", html
    )
    assert pill is not None
    assert "memory.entities" in pill.group("body")
    assert "entity:owner" in pill.group("body")
    assert "_cog" not in pill.group("body")
    assert "function requestNextMemoryCorrection(cognition, buttonEl)" in html
    assert 'fetch(\'/api/next/memory-corrections\'' in html
    assert "body: JSON.stringify({ cognitionId: cognition.id, correctionText })" in html
    assert "纠正" in html
    assert "正式删除" not in html


def test_legacy_copy_does_not_claim_persistence_or_full_reset() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    weave = re.search(r"function weaveMemNote\(c\) \{(?P<body>[\s\S]*?)\n      \}", html)
    assert weave is not None
    assert "1.0 整理出待迁移线索" in weave.group("body")
    assert "还不是正式记忆" in weave.group("body")
    assert "记住了" not in weave.group("body")
    assert "softEditCog" not in weave.group("body")
    assert "softDelCog" not in weave.group("body")
    assert "1.0 兼容 / 调试来源" in html
    assert "不含正式记忆" in html
    assert "清空全部数据" not in html


def test_next_memory_bridge_is_same_origin_and_has_candidate_and_world_surfaces() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "正式记忆" in html
    assert "关于我" in html and "其他实体、关系、事件与认知" in html and "待决定" in html
    assert 'id="nextWorldOwner"' in html
    assert 'id="nextWorldOther"' in html
    assert 'id="nextWorldPending"' in html
    assert 'id="nextMemoryQuery"' in html
    assert "/api/next/memory-world" in html
    assert "/api/next/memory-queries" in html
    assert "/api/next/memory-decisions" in html
    assert "d.nextMemory || d.record.nextMemory" in html
    assert "candidate-ready" in html and "correction-pending" in html
    assert "no-candidate" in html and "clarification-required" in html and "out-of-scope" in html
    assert "failed" in html and "unavailable" in html
    assert not re.search(r"fetch\(\s*['\"]https?://", html)


def test_next_memory_dynamic_content_is_text_only_and_pending_is_the_only_decidable_state() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "function nextText(value)" in html
    assert "function appendNextMemoryTurn(nextMemory)" in html
    assert ".textContent =" in html
    assert re.search(
        r"\['candidate-ready',\s*'correction-pending'\]\.includes\(nextMemory\?\.state\)",
        html,
    )
    assert "button.disabled = true" in html
    assert "d.record.nextMemory" in html
    assert "t.nextMemory" in html


def test_next_product_candidate_and_clarification_metadata_are_visible_but_only_candidates_are_decidable() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "function appendNextCandidateContext(host, view)" in html
    assert "target.entityNames" in html and "target.entityId" in html
    assert "proposal.statementKind" in html and "proposal.ownerPerspective" in html
    assert "function appendNextClarification(host, view)" in html
    assert "clarification.message" in html and "clarification.candidateEntityNames" in html
    assert "view.state === 'clarification-required'" in html
    assert "view.proposal.identityBindings || view.run.identityBindings" in html
    assert "start_codepoint" in html and "end_codepoint" in html


def test_accepted_world_is_rendered_as_entity_centered_cards_without_candidate_leakage() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "function buildAcceptedEntityIndex(memory)" in html
    assert "cognition?.target?.kind === 'entity'" in html
    assert "target.id" in html and "bundle.cognitions.push(cognition)" in html
    assert "relationship?.source_entity_id" in html and "relationship?.target_entity_id" in html
    assert "event?.participants" in html and "event?.related_entity_ids" in html
    assert "function renderAcceptedEntityCard(bundle, entityIndex)" in html
    assert "canonical_name" in html and "稳定实体 ID：" in html and "别名：" in html
    assert "formatNextPerspective(cognition?.perspective, entityIndex)" in html
    assert "Evidence：${evidence.join('、')}" in html
    assert "尚无已接受属性。" in html
    assert "function renderUnboundAcceptedContent(unbound, entityIndex)" in html
    assert "未绑定实体的认知" in html
    renderer = re.search(r"function renderNextWorld\(world\) \{(?P<body>[\s\S]*?)\n      \}", html)
    assert renderer is not None
    assert "buildAcceptedEntityIndex(memory)" in renderer.group("body")
    assert ".candidateMemory" not in renderer.group("body")


def test_historical_next_memory_is_hydrated_from_the_live_run_state_before_rendering() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert "/api/next/memory-runs" in html
    assert "const _nextRuns = new Map()" in html
    assert "async function loadNextMemoryRuns()" in html
    assert "function hydrateNextMemory(raw)" in html
    assert "const liveRun = _nextRuns.get(runId)" in html
    assert "const hydrated = hydrateNextMemory(nextMemory)" in html
    assert "await loadNextMemoryRuns();" in html
    assert "await loadNextMemoryRuns();" in html
    assert "liveRun.state" in html


def test_historical_next_memory_snapshot_never_overwrites_the_live_accepted_world() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    register = re.search(
        r"function registerNextMemory\(nextMemory\) \{(?P<body>[\s\S]*?)\n      \}",
        html,
    )
    assert register is not None
    assert "renderNextWorld(view.world)" not in register.group("body")
    assert "async function loadNextWorld()" in html
    assert "if (result.world) renderNextWorld(result.world);" in html


def test_decided_history_keeps_candidate_content_but_only_pending_history_has_actions() -> None:
    html = TESTBENCH.read_text(encoding="utf-8")

    assert re.search(
        r"\['candidate-ready',\s*'correction-pending',\s*'accepted',\s*'rejected'\]\.includes\(view\.state\)",
        html,
    )
    assert "if (isNextPending(view))" in html
    assert "appendNextCandidateContent(bubble, view);" in html
    assert "const reviewId = view.proposal.reviewId || view.run.reviewId;" in html
    assert "body: JSON.stringify({ reviewId, runId, resultHash, decision })" in html
