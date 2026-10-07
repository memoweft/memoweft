from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Mapping


APP_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_ROOT))

from routes import MemoryExperienceRoutes  # noqa: E402


class TaskClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def call(self, method: str, params: Mapping[str, object] | None = None) -> dict[str, object]:
        params = dict(params or {})
        self.calls.append((method, params))
        if method == "submit_command":
            return {"receipt": {"command_id": "receipt-command", "after_revision": 8, "result_state": "applied"}}
        if method == "answer_clarification":
            return {"receipt": {"clarification_id": "clar-1", "answer_evidence_id": "ev-2", "follow_up_job_id": "job-2", "after_revision": 8}}
        if method == "portable_import":
            return {"receipt_id": "portable-receipt", "after_revision": 8, "result_state": "applied"}
        if method == "preview_recall":
            recall_query = params.get("query")
            if not isinstance(recall_query, str) or not recall_query.strip():
                raise AssertionError("N4 preview_recall rejects blank query")
            return {"world_revision": 8, "preview": "re-read at revision 8"}
        return {"schema_version": 1, "subject_id": "subject-1", "world_revision": 8, "items": []}


def test_user_mutations_show_receipts_then_refresh_at_receipt_revision() -> None:
    client = TaskClient()
    routes = MemoryExperienceRoutes(client)

    response = routes.handle(
        "POST",
        "/api/commands",
        {"command": {"operation": "forget_evidence", "target_id": "ev-1"}, "recall_query": "coffee"},
    )

    assert response.status == 200
    payload = json.loads(response.body)
    assert payload["data"]["receipt"]["command_id"] == "receipt-command"
    assert payload["data"]["refresh"]["world_revision"] == 8
    assert ("query_world", {"operation": "list"}) in client.calls
    assert ("query_evidence", {"operation": "list"}) in client.calls
    assert ("preview_recall", {"query": "coffee"}) in client.calls


def test_clarification_and_portable_apply_keep_formal_receipts() -> None:
    client = TaskClient()
    routes = MemoryExperienceRoutes(client)

    clarification = routes.handle(
        "POST", "/api/clarifications/clar-1/answer", {"result_session_id": "session-1", "answer": "At home."}
    )
    portable = routes.handle(
        "POST", "/api/portable/apply", {"bundle": {"schema_version": 4}, "plan_hash": "plan-1"}
    )

    assert json.loads(clarification.body)["data"]["receipt"]["follow_up_job_id"] == "job-2"
    assert json.loads(portable.body)["data"]["receipt"]["receipt_id"] == "portable-receipt"
    assert json.loads(clarification.body)["data"]["refresh"]["world_revision"] == 8
    assert json.loads(portable.body)["data"]["refresh"]["world_revision"] == 8
    assert ("preview_recall", {"query": "memory"}) in client.calls
    assert any(method == "answer_clarification" for method, _params in client.calls)
    assert any(method == "portable_import" for method, _params in client.calls)


def test_correct_ui_uses_n5_payload_contract_and_excludes_entity() -> None:
    source = (APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    compact_source = "".join(source.split())

    assert "command('correct_world_item',kind,id,{correction_text:text})" in compact_source
    assert "['relationship','event','cognition'].includes(kind)" in compact_source
    assert "Entity correction and retraction are unavailable." in source


def test_ui_exposes_evidence_tombstone_clarification_identities_and_conflicts() -> None:
    source = (APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "tombstone deleted_at" in source
    assert "lifecycle.deleted_at" in source
    assert "currentness" in source
    assert "receipt.answer_evidence_id" in source
    assert "receipt.follow_up_job_id" in source
    assert "Conflict preview" in source
    assert "plan.conflicts" in source


def test_ui_uses_accessible_in_page_controls_not_native_prompts() -> None:
    source = (APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    assert "prompt(" not in source
    assert "confirm(" not in source
    assert 'for="correction-text"' in source
    assert 'id="correction-text"' in source
    assert 'id="correct-submit"' in source
    assert "Evidence permissions" in source
    assert "data-permission-local" in source
    assert "data-permission-cloud" in source
    assert "data-permission-inference" in source
    assert "Save permissions" in source
    assert "data-answer-text" in source
    assert "confirmAction" in source
    assert "Confirm ${button.dataset.originalLabel}" in source


def test_portable_plan_and_apply_preserve_one_original_bundle_text() -> None:
    source = (APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    compact_source = "".join(source.split())

    assert "bundleText:null" in compact_source
    assert (
        "constbundleText=awaitevent.target.files[0].text(),"
        "bundle=JSON.parse(bundleText);"
    ) in compact_source
    assert "state.bundleText=bundleText" in compact_source
    assert 'return`{"bundle":${state.bundleText}${suffix}}`' in compact_source
    assert "body:portableBundleBody()" in compact_source
    assert "body:portableBundleBody(state.plan.plan_hash)" in compact_source
    assert "JSON.stringify({bundle:state.bundle})" not in compact_source
    assert (
        "JSON.stringify({bundle:state.bundle,plan_hash:state.plan.plan_hash})"
        not in compact_source
    )


def test_ui_declares_all_six_formal_command_operations() -> None:
    source = (APP_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    for operation in (
        "update_evidence_permissions",
        "correct_world_item",
        "retract_world_item",
        "forget_evidence",
        "archive_world_item",
        "mute_world_item",
    ):
        assert operation in source


def test_start_cmd_uses_direct_start_quoting_without_nested_cmd() -> None:
    command = (APP_ROOT.parents[2] / "memory-web" / "start-memory-web.cmd").read_text(encoding="utf-8")

    assert 'for %%I in ("%~dp0..")' in command
    assert 'start "MemoWeft-MemoryExperience" /min "%MW_PY%" "%MW_APP%"' in command
    assert 'start "MemoWeft-MemoryExperience" /min uv run --project "%MW_PROJECT%" python "%MW_APP%"' in command
    assert "cmd /d /c" not in command
