"""导入便携记忆包 —— 移植自 src/portable/importBundle.ts(P2-旁 完整 ImportPlan 语义)。

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
    """对齐 importBundle.ts:45-193。"""
    lang = resolve_lang()
    validation = validate_bundle(bundle)
    plan = ImportPlan(
        mode=mode, valid=validation.valid, errors=list(validation.errors), warnings=list(validation.warnings),
        counts=ImportCounts(), duplicates=ImportDuplicates(),
    )
    if not validation.valid:
        return plan  # 结构/引用错 → 绝不写库

    data = bundle["data"]

    # ── 判重(evidence:按 id;额外防 originId 唯一约束撞车)──
    unresolved_evidence: set[str] = set()
    new_evidence: list[dict[str, Any]] = []
    for e in data["evidence"]:
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

    new_events = []
    for ev in data["events"]:
        if event_store.get(ev["id"]) is not None:
            plan.duplicates.events += 1
            continue
        new_events.append(ev)

    new_cognitions = []
    for c in data["cognitions"]:
        if cognition_store.get(c["id"]) is not None:
            plan.duplicates.cognitions += 1
            continue
        new_cognitions.append(c)

    # 将新建 event 的覆盖证据(丢弃指向悬空 evidence 的链)。
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
    new_semantic_resolutions = [r for r in (data.get("semanticResolutions") or []) if semantic_resolution_store.get(r["id"]) is None]

    plan.counts = ImportCounts(
        evidence=len(new_evidence), events=len(new_events), cognitions=len(new_cognitions),
        event_evidence=event_evidence_count, cognition_evidence=cognition_evidence_count,
        interaction_contexts=len(new_interaction_contexts), semantic_resolutions=len(new_semantic_resolutions),
    )

    if mode == "dryRun":
        return plan  # 只算不写

    # ── merge:实际写入。顺序:evidence → event(挂证据)→ cognition(挂溯源)——被引方先落库。──
    unconsolidated_set = set(data.get("unconsolidatedEventIds") or [])

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
    except Exception as e:  # 无事务无法回滚 → 收进 errors + 提示,不把裸异常抛给调用方
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
