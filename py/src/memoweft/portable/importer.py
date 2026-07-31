"""导入便携记忆包，并保持 TypeScript importBundle 的完整 ImportPlan 语义。

保真 + 幂等 + 不污染:
  - 保真:按【原 id 与时间戳】落库(store.insert),溯源链不丢。
  - 幂等去重:按 id 判重,已存在则跳过(计 duplicates)。
  - 引用完整:evidence 因 originId 撞库中【另一条不同 id】而无法落库时标记悬空,**连带丢弃指向它的 join 行**并告警;
    悬空 correctsEvidenceId 落库前置空——绝不写出悬空引用。
  - 不污染:非法包(validate_bundle 不过)绝不写库;merge 写入包进事务(若传),中途失败整体回滚。
dryRun:只算不写。
"""
from __future__ import annotations

from typing import Any, Optional

from ..config import resolve_lang
from ..store.cognition import SqliteCognitionStore
from ..store.event import SqliteEventStore
from ..store.evidence import SqliteEvidenceStore
from ..store.interaction_context import SqliteInteractionContextStore
from ..store.semantic_resolution import SqliteSemanticResolutionStore
from ..store.transaction import Transaction
from ..store._tombstones import is_evidence_tombstoned
from ..types import (
    Cognition,
    Event,
    Evidence,
    EvidenceLink,
    InteractionContext,
    SemanticResolution,
    VisibleTurn,
)
from .model import ImportCounts, ImportDuplicates, ImportMode, ImportPlan
from .validate import validate_bundle


def _to_evidence(d: dict[str, Any]) -> Evidence:
    return Evidence(
        id=d["id"], subject_id=d["subjectId"], source_kind=d["sourceKind"], host_id=d["hostId"],
        origin_id=d.get("originId"), occurred_at=d["occurredAt"], recorded_at=d["recordedAt"],
        raw_content=d["rawContent"], summary=d["summary"], allow_local_read=bool(d["allowLocalRead"]),
        allow_cloud_read=bool(d["allowCloudRead"]), allow_inference=bool(d["allowInference"]),
        corrects_evidence_id=d.get("correctsEvidenceId"),
    )


def _to_event(d: dict[str, Any]) -> Event:
    return Event(id=d["id"], subject_id=d["subjectId"], summary=d["summary"], occurred_at=d["occurredAt"], created_at=d["createdAt"])


def _to_cognition(d: dict[str, Any]) -> Cognition:
    return Cognition(
        id=d["id"], subject_id=d["subjectId"], content=d["content"], content_type=d["contentType"],
        formed_by=d["formedBy"], confidence=d["confidence"], cred_status=d["credStatus"], scope=d.get("scope"),
        valid_at=d.get("validAt"), invalid_at=d.get("invalidAt"), asked_at=d.get("askedAt"),
        archived_at=d.get("archivedAt"), muted_at=d.get("mutedAt"), created_at=d["createdAt"], updated_at=d["updatedAt"],
    )


def _to_interaction_context(d: dict[str, Any]) -> InteractionContext:
    return InteractionContext(
        id=d["id"], subject_id=d["subjectId"], conversation_id=d["conversationId"], episode_id=d["episodeId"],
        context=[VisibleTurn(role=t["role"], content=t["content"]) for t in d["context"]],
        context_hash=d["contextHash"], created_at=d["createdAt"],
    )


def _to_semantic_resolution(d: dict[str, Any]) -> SemanticResolution:
    return SemanticResolution(
        id=d["id"], evidence_id=d["evidenceId"], resolved_content=d["resolvedContent"],
        response_act=d.get("responseAct"), prompt_act=d.get("promptAct"), proposition_origin=d.get("propositionOrigin"),
        assertion_strength=d.get("assertionStrength"), required_context=d.get("requiredContext"),
        resolver_version=d["resolverVersion"], created_at=d["createdAt"],
    )


def _same_string_multiset(left: list[str], right: list[str]) -> bool:
    return sorted(left) == sorted(right)


def import_bundle(
    bundle: Any,
    *,
    evidence_store: SqliteEvidenceStore,
    event_store: SqliteEventStore,
    cognition_store: SqliteCognitionStore,
    interaction_context_store: SqliteInteractionContextStore,
    semantic_resolution_store: SqliteSemanticResolutionStore,
    transaction: Optional[Transaction] = None,
    mode: ImportMode = "merge",
) -> ImportPlan:
    """按共享便携包契约生成并执行导入计划。"""
    lang = resolve_lang()
    validation = validate_bundle(bundle)
    plan = ImportPlan(
        mode=mode, valid=validation.valid, errors=list(validation.errors), warnings=list(validation.warnings),
        counts=ImportCounts(), duplicates=ImportDuplicates(),
    )
    if not validation.valid:
        return plan  # 结构/引用错 → 绝不写库

    data = bundle["data"]
    unconsolidated_set = set(data.get("unconsolidatedEventIds") or [])

    # 同 id 仅在完整实体及其自有关系完全相同时才是安全幂等。否则把包内派生实体
    # 绑定到目标行会造成跨血缘授权漂白；rc.2 选择整包 fail-closed，等待显式冲突解决。
    event_links: dict[str, list[str]] = {}
    for link in data["eventEvidence"]:
        event_links.setdefault(link["eventId"], []).append(link["evidenceId"])
    cognition_link_keys: dict[str, list[str]] = {}
    for link in data["cognitionEvidence"]:
        cognition_link_keys.setdefault(link["cognitionId"], []).append(
            f"{link['evidenceId']}\0{link['relation']}"
        )

    for evidence in data["evidence"]:
        existing_evidence = evidence_store.get(evidence["id"])
        if existing_evidence is not None and existing_evidence != _to_evidence(evidence):
            plan.errors.append(
                f"evidence {evidence['id']} 与目标库同 id 记录内容或授权不一致，拒绝导入"
                if lang == "zh"
                else f"evidence {evidence['id']} collides with a different target record; import rejected"
            )
    for event in data["events"]:
        existing_event = event_store.get(event["id"])
        if existing_event is None:
            continue
        same_links = _same_string_multiset(
            event_store.evidence_of(event["id"]), event_links.get(event["id"], [])
        )
        target_unconsolidated = any(
            candidate.id == event["id"] for candidate in event_store.unconsolidated(event["subjectId"])
        )
        if (
            existing_event != _to_event(event)
            or not same_links
            or target_unconsolidated != (event["id"] in unconsolidated_set)
        ):
            plan.errors.append(
                f"event {event['id']} 与目标库同 id 事件的内容、证据关系或消化状态不一致，拒绝导入"
                if lang == "zh"
                else f"event {event['id']} collides with different target content, evidence links, or consolidation state; import rejected"
            )
    for cognition in data["cognitions"]:
        existing_cognition = cognition_store.get(cognition["id"])
        if existing_cognition is None:
            continue
        target_sources = [
            f"{link.evidence_id}\0{link.relation}"
            for link in cognition_store.sources_of(cognition["id"])
        ]
        if (
            existing_cognition != _to_cognition(cognition)
            or not _same_string_multiset(
                target_sources, cognition_link_keys.get(cognition["id"], [])
            )
        ):
            plan.errors.append(
                f"cognition {cognition['id']} 与目标库同 id 认知的内容或溯源关系不一致，拒绝导入"
                if lang == "zh"
                else f"cognition {cognition['id']} collides with different target content or provenance links; import rejected"
            )
    if plan.errors:
        plan.valid = False
        return plan

    # ── 判重(evidence:按 id;额外防 originId 唯一约束撞车)──
    unresolved_evidence: set[str] = set()
    new_evidence: list[dict[str, Any]] = []
    for e in data["evidence"]:
        # 删除是单调的：旧备份绝不能让目标库已软删除的同 id 证据复活。
        # get() 刻意不返回墓碑，恢复路径须显式辨认；其关联 join / 解析也一并跳过。
        if is_evidence_tombstoned(evidence_store, e["id"]):
            plan.duplicates.evidence += 1
            unresolved_evidence.add(e["id"])
            plan.warnings.append(
                f"evidence {e['id']} 在目标库已被删除（墓碑），跳过旧备份以保持删除单调性"
                if lang == "zh"
                else f"evidence {e['id']} is tombstoned in the target database; skipping the older backup to preserve deletion monotonicity"
            )
            continue
        if evidence_store.get(e["id"]) is not None:
            plan.duplicates.evidence += 1  # 同 id 已在 → 跳过(join 仍指向它,安全)
            continue
        origin = e.get("originId")
        if origin is not None and evidence_store.find_by_origin(origin) is not None:
            plan.duplicates.evidence += 1
            unresolved_evidence.add(e["id"])  # 无法按原 id 落库 → 指向它的 join 行必须一并丢
            plan.warnings.append(
                f"evidence {e['id']} 的 originId 已被库中另一条占用，跳过（其溯源引用一并丢弃）"
                if lang == "zh"
                else f"evidence {e['id']} originId is already taken by another record in the database; skipping (its provenance links are dropped too)"
            )
            continue
        new_evidence.append(e)

    candidate_events = []
    for ev in data["events"]:
        if event_store.get(ev["id"]) is not None:
            plan.duplicates.events += 1
            continue
        candidate_events.append(ev)

    candidate_cognitions = []
    for c in data["cognitions"]:
        if cognition_store.get(c["id"]) is not None:
            plan.duplicates.cognitions += 1
            continue
        candidate_cognitions.append(c)

    # 删除单调性同样适用于派生层：event.summary / cognition.content 均由 evidence 派生。
    # 任一来源未恢复即整实体 fail-closed，绝不留下脱离被删证据的摘要或画像。
    new_events: list[dict[str, Any]] = []
    for event in candidate_events:
        unresolved = [eid for eid in event_links.get(event["id"], []) if eid in unresolved_evidence]
        if unresolved:
            plan.warnings.append(
                f"event {event['id']} 依赖未恢复的 evidence {', '.join(unresolved)}，跳过以防摘要复活"
                if lang == "zh"
                else f"event {event['id']} depends on unresolved evidence {', '.join(unresolved)}; skipping to prevent derived summary revival"
            )
            continue
        new_events.append(event)

    cognition_links: dict[str, list[str]] = {}
    for link in data["cognitionEvidence"]:
        cognition_links.setdefault(link["cognitionId"], []).append(link["evidenceId"])
    new_cognitions: list[dict[str, Any]] = []
    for cognition in candidate_cognitions:
        unresolved = [eid for eid in cognition_links.get(cognition["id"], []) if eid in unresolved_evidence]
        if unresolved:
            plan.warnings.append(
                f"cognition {cognition['id']} 依赖未恢复的 evidence {', '.join(unresolved)}，跳过以防派生内容复活"
                if lang == "zh"
                else f"cognition {cognition['id']} depends on unresolved evidence {', '.join(unresolved)}; skipping to prevent derived content revival"
            )
            continue
        new_cognitions.append(cognition)

    # 将新建 event 的覆盖证据。实体筛选已 fail-closed，因此这里不会给残缺事件写 join。
    new_event_ids = {e["id"] for e in new_events}
    event_evidence_of: dict[str, list[str]] = {}
    event_evidence_count = 0
    for link in data["eventEvidence"]:
        if link["eventId"] not in new_event_ids:
            continue
        if link["evidenceId"] in unresolved_evidence:
            continue  # 悬空 → 丢
        event_evidence_of.setdefault(link["eventId"], []).append(link["evidenceId"])
        event_evidence_count += 1

    # 将新建 cognition 的溯源链(同理丢弃悬空)。
    new_cognition_ids = {c["id"] for c in new_cognitions}
    cognition_sources_of: dict[str, list[EvidenceLink]] = {}
    cognition_evidence_count = 0
    for link in data["cognitionEvidence"]:
        if link["cognitionId"] not in new_cognition_ids:
            continue
        if link["evidenceId"] in unresolved_evidence:
            continue
        cognition_sources_of.setdefault(link["cognitionId"], []).append(
            EvidenceLink(evidence_id=link["evidenceId"], relation=link["relation"])
        )
        cognition_evidence_count += 1

    # 悬空 correctsEvidenceId 置空:目标库既无、也不在本次新建集 → 落库前置空。
    new_evidence_ids = {e["id"] for e in new_evidence}
    evidence_to_insert: list[dict[str, Any]] = []
    for e in new_evidence:
        cid = e.get("correctsEvidenceId")
        if cid is not None and evidence_store.get(cid) is None and cid not in new_evidence_ids:
            plan.warnings.append(
                f"evidence {e['id']} 的 correctsEvidenceId({cid}) 在目标库无法解析，导入时置空"
                if lang == "zh"
                else f"evidence {e['id']} correctsEvidenceId({cid}) cannot be resolved in the target database; cleared on import"
            )
            evidence_to_insert.append({**e, "correctsEvidenceId": None})
        else:
            evidence_to_insert.append(e)

    # 交互层:按 id 判重;向后兼容 v1 包(无这两段 → 空)。
    new_interaction_contexts = [c for c in (data.get("interactionContexts") or []) if interaction_context_store.get(c["id"]) is None]
    # 一证据一解析：目标库已有解析时保持既有结果；包内重复/孤儿在 validate_bundle 已致命拒绝。
    new_semantic_resolutions: list[dict[str, Any]] = []
    for r in data.get("semanticResolutions") or []:
        if r["evidenceId"] in unresolved_evidence:
            plan.warnings.append(
                f"semanticResolution {r['id']} 指向未恢复的 evidence {r['evidenceId']}，跳过"
                if lang == "zh"
                else f"semanticResolution {r['id']} references unresolved evidence {r['evidenceId']}; skipping"
            )
            continue
        if semantic_resolution_store.get(r["id"]) is not None:
            continue
        if semantic_resolution_store.of_evidence(r["evidenceId"]) is not None:
            plan.warnings.append(
                f"evidence {r['evidenceId']} 在目标库已有 semanticResolution，跳过 {r['id']} 以保持一证据一解析"
                if lang == "zh"
                else f"evidence {r['evidenceId']} already has a semanticResolution in the target database; skipping {r['id']} to preserve one resolution per evidence"
            )
            continue
        new_semantic_resolutions.append(r)

    plan.counts = ImportCounts(
        evidence=len(new_evidence), events=len(new_events), cognitions=len(new_cognitions),
        event_evidence=event_evidence_count, cognition_evidence=cognition_evidence_count,
        interaction_contexts=len(new_interaction_contexts), semantic_resolutions=len(new_semantic_resolutions),
    )

    if mode == "dryRun":
        return plan  # 只算不写

    # ── merge:实际写入。顺序:evidence → event(挂证据)→ cognition(挂溯源)——被引方先落库。──
    def write() -> None:
        for e in evidence_to_insert:
            evidence_store.insert(_to_evidence(e))
        for ev in new_events:
            event_store.insert(
                _to_event(ev), event_evidence_of.get(ev["id"], []), consolidated=ev["id"] not in unconsolidated_set
            )
        for c in new_cognitions:
            cognition_store.insert(_to_cognition(c), cognition_sources_of.get(c["id"], []))
        for c in new_interaction_contexts:
            interaction_context_store.insert(_to_interaction_context(c))
        for r in new_semantic_resolutions:
            semantic_resolution_store.insert(_to_semantic_resolution(r))

    try:
        if transaction is not None:
            transaction(write)
        else:
            write()
    except Exception as e:  # 将写入错误归入 ImportPlan；无事务时同时报告可能存在的部分写入。
        plan.valid = False
        plan.errors.append(f"导入写入失败：{e}" if lang == "zh" else f"Import write failed: {e}")
        if transaction is None:
            plan.warnings.append(
                "未提供 transaction，写入中途失败可能已残留部分数据（建议用 openStores 的 transaction）"
                if lang == "zh"
                else "No transaction provided; a mid-write failure may have left partial data (use the transaction from openStores)"
            )
        plan.counts = ImportCounts()
        return plan

    return plan
