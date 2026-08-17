"""校验便携记忆包的纯函数，与 TypeScript validateBundle 契约保持一致。

消息语言固定 en(TS 默认 lang;shared/parity/bundle-validate.json 的 expected 即 en)。
分级:errors(致命·valid=false)/ warnings(软提示·可导入)。
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any

from ..context_hash import hash_context
from ..types import VisibleTurn
from .model import BUNDLE_FORMAT, BUNDLE_SCHEMA_VERSION

# cognition 字段值校验的运行时全集（与 TS cognition/model.ts 的 CONTENT_TYPES / FORMED_BY_VALUES /
#   CRED_STATUSES 逐一对齐）。content_type 认【完整 8 值】含 hypothesis/trend——导入的是已落库认知，
#   可能由 attribute/trends 产出这两类，不能只认 consolidate 收的那 6 个。
_CONTENT_TYPES = frozenset(
    (
        "fact", "preference", "goal", "project", "state", "trait", "hypothesis", "trend",
        # 2.0 hermes 链的 statement_kind 直接进 content_type
        "attribute", "naming", "relationship", "alias", "event",
    )
)
_FORMED_BY = frozenset(("stated", "observed", "ruled", "confirmed", "inferred"))
_CRED_STATUSES = frozenset(("candidate", "low", "limited", "stable", "conflicted", "contested"))
_SOURCE_KINDS = frozenset(("spoken", "inferred", "observed", "tool"))
_EVIDENCE_RELATIONS = frozenset(("support", "contradict"))
_WORLD_FORMED_BY = frozenset(("stated", "confirmed"))
_VISIBLE_TURN_ROLES = frozenset(("user", "assistant", "tool"))
_RESPONSE_ACTS = frozenset(("affirm", "negate", "select", "elaborate", "ask", "none", "other"))
_PROMPT_ACTS = frozenset(("propose", "ask", "state", "none", "other"))
_PROPOSITION_ORIGINS = frozenset(("user_stated", "assistant_proposed"))
_ASSERTION_STRENGTHS = frozenset(("explicit", "weak", "none"))
_ISO_DATE_TIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$"
)
#: 2.0 world_event.occurred_at 是日期粒度（YYYY-MM-DD，adapter 合同）。
_ISO_DATE_ONLY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


@dataclass(slots=True)
class ValidateResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"valid": self.valid, "errors": self.errors, "warnings": self.warnings}


def _js_stringify(v: Any) -> str:
    """实现 JS JSON.stringify 的单值紧凑格式；缺失键映射为 'undefined'。"""
    if v is _MISSING:
        return "undefined"
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


_MISSING = object()


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and value != ""


def _nullable_string(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _parseable_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    match = _ISO_DATE_TIME.fullmatch(value)
    if match is None:
        return False
    year, month, day, hour, minute, second, zone = match.groups()
    year_i, month_i, day_i = int(year), int(month), int(day)
    hour_i, minute_i = int(hour), int(minute)
    second_i = int(second) if second is not None else 0
    if year_i < 1 or hour_i > 23 or minute_i > 59 or second_i > 59:
        return False
    if zone != "Z":
        offset_hour, offset_minute = (int(part) for part in zone[1:].split(":"))
        if offset_hour > 23 or offset_minute > 59:
            return False
    try:
        datetime(year_i, month_i, day_i, hour_i, minute_i, second_i)
    except ValueError:
        return False
    return True


def _parseable_world_date(value: Any) -> bool:
    """Full ISO datetime OR date-only (YYYY-MM-DD, the world_event contract)."""
    if _parseable_date(value):
        return True
    if not isinstance(value, str):
        return False
    match = _ISO_DATE_ONLY.fullmatch(value)
    if match is None:
        return False
    try:
        datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return False
    return True


def validate_bundle(bundle: Any) -> ValidateResult:
    errors: list[str] = []
    warnings: list[str] = []

    def invalid_field(entity: str, record_id: Any, field: str) -> None:
        errors.append(f"{entity} {record_id} has an invalid or missing {field}")

    if bundle is None or not isinstance(bundle, dict):
        return ValidateResult(False, ["bundle is not an object"], warnings)
    b = bundle

    fmt = b.get("format", _MISSING)
    if fmt != BUNDLE_FORMAT:
        errors.append(f'format should be "{BUNDLE_FORMAT}", but got {_js_stringify(fmt)}')
    sv = b.get("schemaVersion", _MISSING)
    if (
        not isinstance(sv, (int, float))
        or isinstance(sv, bool)
        or sv is _MISSING
        or not math.isfinite(sv)
        or not float(sv).is_integer()
    ):
        errors.append("schemaVersion is missing or not a number")
    elif sv > BUNDLE_SCHEMA_VERSION:
        errors.append(f"schemaVersion={_num(sv)} is higher than the {BUNDLE_SCHEMA_VERSION} supported by this version (upgrade MemoWeft before importing)")
    elif sv < 2:
        # v2 bundle 在 v3 下完全前向兼容（World 段可选），导入行为不变 → 不告警。
        # 只有更早结构（无 interactionContexts/semanticResolutions 语义）才提示。
        warnings.append(f"schemaVersion={_num(sv)} is lower than the current {BUNDLE_SCHEMA_VERSION} (importing with the old structure)")
    sid = b.get("subjectId", _MISSING)
    if not isinstance(sid, str) or sid == "":
        errors.append("subjectId is missing")
    if not _parseable_date(b.get("exportedAt", _MISSING)):
        errors.append("exportedAt is invalid or missing")

    data = b.get("data")
    if data is None or not isinstance(data, dict):
        errors.append("data is missing")
        return ValidateResult(False, errors, warnings)

    arrays = [
        ("evidence", data.get("evidence")),
        ("events", data.get("events")),
        ("eventEvidence", data.get("eventEvidence")),
        ("cognitions", data.get("cognitions")),
        ("cognitionEvidence", data.get("cognitionEvidence")),
    ]
    for name, arr in arrays:
        if not isinstance(arr, list):
            errors.append(f"data.{name} should be an array")
    if errors:
        return ValidateResult(False, errors, warnings)

    ev_list = data["evidence"]
    evt_list = data["events"]
    cog_list = data["cognitions"]
    evev_list = data["eventEvidence"]
    cogev_list = data["cognitionEvidence"]

    def bad_id(x: Any) -> bool:
        i = x.get("id") if isinstance(x, dict) else None
        return not isinstance(i, str) or i == ""

    if any(bad_id(x) for x in ev_list):
        errors.append("data.evidence has an element with a missing id")
    if any(bad_id(x) for x in evt_list):
        errors.append("data.events has an element with a missing id")
    if any(bad_id(x) for x in cog_list):
        errors.append("data.cognitions has an element with a missing id")
    for l in evev_list:
        if not isinstance(l, dict) or not isinstance(l.get("eventId"), str) or not isinstance(l.get("evidenceId"), str):
            errors.append("data.eventEvidence has an invalid endpoint")
            break
    for l in cogev_list:
        if (
            not isinstance(l, dict)
            or not isinstance(l.get("cognitionId"), str)
            or not isinstance(l.get("evidenceId"), str)
            or not isinstance(l.get("relation"), str)
        ):
            errors.append("data.cognitionEvidence has an invalid endpoint")
            break
    if errors:
        return ValidateResult(False, errors, warnings)

    evidence_ids = {e["id"] for e in ev_list}
    event_ids = {e["id"] for e in evt_list}
    cognition_ids = {c["id"] for c in cog_list}

    if len(evidence_ids) != len(ev_list):
        errors.append("data.evidence has duplicate ids")
    if len(event_ids) != len(evt_list):
        errors.append("data.events has duplicate ids")
    if len(cognition_ids) != len(cog_list):
        errors.append("data.cognitions has duplicate ids")

    for link in evev_list:
        if link["eventId"] not in event_ids:
            errors.append(f"eventEvidence references a non-existent event: {link['eventId']}")
        if link["evidenceId"] not in evidence_ids:
            errors.append(f"eventEvidence references a non-existent evidence: {link['evidenceId']}")
    for link in cogev_list:
        if link["cognitionId"] not in cognition_ids:
            errors.append(f"cognitionEvidence references a non-existent cognition: {link['cognitionId']}")
        if link["evidenceId"] not in evidence_ids:
            errors.append(f"cognitionEvidence references a non-existent evidence: {link['evidenceId']}")

    # join 复合键在 bundle 内必须唯一；重复 support 会膨胀 supportCount、confidence 与模型输入。
    event_evidence_tuples: set[tuple[str, str]] = set()
    for link in evev_list:
        event_item = (link["eventId"], link["evidenceId"])
        if event_item in event_evidence_tuples:
            errors.append(f"data.eventEvidence has duplicate link: {event_item[0]}/{event_item[1]}")
        event_evidence_tuples.add(event_item)
    cognition_evidence_tuples: set[tuple[str, str, str]] = set()
    for link in cogev_list:
        cognition_item = (link["cognitionId"], link["evidenceId"], link["relation"])
        if cognition_item in cognition_evidence_tuples:
            errors.append(
                f"data.cognitionEvidence has duplicate link: {cognition_item[0]}/{cognition_item[1]}/{cognition_item[2]}"
            )
        cognition_evidence_tuples.add(cognition_item)

    # Portable bundle 是单 subject 边界，不是混装容器。显式校验实体及 join 归属，
    # 防止 A 的 cognition 经 provenance link 引用 B 的 evidence 而泄露 B 的摘要。
    evidence_subjects = {e["id"]: e.get("subjectId") for e in ev_list}
    event_subjects = {e["id"]: e.get("subjectId") for e in evt_list}
    cognition_subjects = {c["id"]: c.get("subjectId") for c in cog_list}
    for e in ev_list:
        if e.get("subjectId") != sid:
            errors.append(f"evidence {e['id']} subjectId({e.get('subjectId')}) does not match the bundle({sid})")
    for e in evt_list:
        if e.get("subjectId") != sid:
            errors.append(f"event {e['id']} subjectId({e.get('subjectId')}) does not match the bundle")
    for c in cog_list:
        if c.get("subjectId") != sid:
            errors.append(f"cognition {c['id']} subjectId({c.get('subjectId')}) does not match the bundle")
    for link in evev_list:
        event_subject = event_subjects.get(link["eventId"])
        evidence_subject = evidence_subjects.get(link["evidenceId"])
        if event_subject is not None and evidence_subject is not None and event_subject != evidence_subject:
            errors.append(f"eventEvidence crosses subjects: {link['eventId']}/{link['evidenceId']}")
    for link in cogev_list:
        cognition_subject = cognition_subjects.get(link["cognitionId"])
        evidence_subject = evidence_subjects.get(link["evidenceId"])
        if cognition_subject is not None and evidence_subject is not None and cognition_subject != evidence_subject:
            errors.append(f"cognitionEvidence crosses subjects: {link['cognitionId']}/{link['evidenceId']}")

    # cognition 字段值校验（致命）：枚举越界 / confidence 非法。与 validateBundle.ts 逐字符一致。
    #   为什么在这道守门拦：cognition 表 content_type/formed_by 列无 CHECK、confidence 靠 SQLite 类型
    #   亲和性也拦不住字符串，import 又完全信任 valid=True 直插。越界 formed_by 会埋成延迟雷
    #   （下次 compute_confidence 重算得 NaN → 那次重算整体失败）。
    for c in cog_list:
        # 消息里的值一律走 _js_stringify（= JS JSON.stringify），与 TS 逐字符对齐：
        #   非 ASCII 值也不转义（ensure_ascii=False）、缺失键映射 'undefined'。
        if c.get("contentType", _MISSING) not in _CONTENT_TYPES:
            errors.append(f"cognition {c['id']} has an invalid content_type: {_js_stringify(c.get('contentType', _MISSING))}")
        if c.get("formedBy", _MISSING) not in _FORMED_BY:
            errors.append(f"cognition {c['id']} has an invalid formed_by: {_js_stringify(c.get('formedBy', _MISSING))}")
        if c.get("credStatus", _MISSING) not in _CRED_STATUSES:
            errors.append(f"cognition {c['id']} has an invalid cred_status: {_js_stringify(c.get('credStatus', _MISSING))}")
        conf = c.get("confidence", _MISSING)
        # JSON 的 600 与 600.0 在 TS 都是同一个 number；Python 也接受有限、整值的 int/float。
        # bool 是 int 子类，须显式排除；NaN/非整小数/越界一律拒。
        if (
            not isinstance(conf, (int, float))
            or isinstance(conf, bool)
            or not math.isfinite(conf)
            or not float(conf).is_integer()
            or conf < 0
            or conf > 1000
        ):
            errors.append(
                f"cognition {c['id']} has an invalid confidence (must be an integer 0-1000): {_js_stringify(conf)}"
            )

    # 外部 bundle 绕开各 store 写路径；所有会进入下游分支或 SQLite 的字段在这里一次性守门。
    # 日期只要求可解析，不重写旧 v2 bundle 的原始时间字符串。
    for e in ev_list:
        if not _non_empty_string(e.get("subjectId")):
            invalid_field("evidence", e["id"], "subjectId")
        if e.get("sourceKind") not in _SOURCE_KINDS:
            invalid_field("evidence", e["id"], "sourceKind")
        if not _non_empty_string(e.get("hostId")):
            invalid_field("evidence", e["id"], "hostId")
        if not _parseable_date(e.get("occurredAt")):
            invalid_field("evidence", e["id"], "occurredAt")
        if not _parseable_date(e.get("recordedAt")):
            invalid_field("evidence", e["id"], "recordedAt")
        # 稳定写路径允许空消息；便携校验必须接受自身导出的空 rawContent/summary。
        if not isinstance(e.get("rawContent"), str):
            invalid_field("evidence", e["id"], "rawContent")
        if not isinstance(e.get("summary"), str):
            invalid_field("evidence", e["id"], "summary")
        for field in ("allowLocalRead", "allowCloudRead", "allowInference"):
            if not isinstance(e.get(field), bool):
                invalid_field("evidence", e["id"], field)
        for field in ("originId", "correctsEvidenceId"):
            if not _nullable_string(e.get(field)):
                invalid_field("evidence", e["id"], field)
    for e in evt_list:
        if not _non_empty_string(e.get("subjectId")):
            invalid_field("event", e["id"], "subjectId")
        if not isinstance(e.get("summary"), str):
            invalid_field("event", e["id"], "summary")
        for field in ("occurredAt", "createdAt"):
            if not _parseable_date(e.get(field)):
                invalid_field("event", e["id"], field)
    for c in cog_list:
        if not _non_empty_string(c.get("subjectId")):
            invalid_field("cognition", c["id"], "subjectId")
        if not _non_empty_string(c.get("content")):
            invalid_field("cognition", c["id"], "content")
        if not _nullable_string(c.get("scope")):
            invalid_field("cognition", c["id"], "scope")
        for field in ("validAt", "invalidAt", "askedAt", "archivedAt", "mutedAt"):
            value = c.get(field, _MISSING)
            if value is not _MISSING and value is not None and not _parseable_date(value):
                invalid_field("cognition", c["id"], field)
        for field in ("createdAt", "updatedAt"):
            if not _parseable_date(c.get(field)):
                invalid_field("cognition", c["id"], field)
    for link in cogev_list:
        if link.get("relation") not in _EVIDENCE_RELATIONS:
            invalid_field("cognitionEvidence", f"{link['cognitionId']}/{link['evidenceId']}", "relation")

    for e in ev_list:
        cid = e.get("correctsEvidenceId")
        if cid is not None and cid not in evidence_ids:
            warnings.append(f"evidence {e['id']} correctsEvidenceId({cid}) is not in the bundle")

    uncons = data.get("unconsolidatedEventIds")
    if uncons is not None:
        if not isinstance(uncons, list):
            errors.append("data.unconsolidatedEventIds should be an array")
        else:
            for _id in uncons:
                if _id not in event_ids:
                    warnings.append(f"unconsolidatedEventIds contains an unknown event: {_id}")

    for name in ("interactionContexts", "semanticResolutions"):
        arr = data.get(name)
        if arr is None:
            continue
        if not isinstance(arr, list):
            errors.append(f"data.{name} should be an array")
        elif any(not isinstance(x, dict) or not isinstance(x.get("id"), str) or x.get("id") == "" for x in arr):
            errors.append(f"data.{name} has an element with a missing id")

    interaction_contexts = data.get("interactionContexts")
    if isinstance(interaction_contexts, list):
        context_ids: set[str] = set()
        for value in interaction_contexts:
            if not isinstance(value, dict) or not _non_empty_string(value.get("id")):
                continue
            record_id = value["id"]
            if record_id in context_ids:
                errors.append("data.interactionContexts has duplicate ids")
            context_ids.add(record_id)
            for field in ("subjectId", "conversationId", "episodeId", "contextHash"):
                if not _non_empty_string(value.get(field)):
                    invalid_field("interactionContext", record_id, field)
            if value.get("subjectId") != sid:
                errors.append(
                    f"interactionContext {record_id} subjectId({value.get('subjectId')}) does not match the bundle"
                )
            if not _parseable_date(value.get("createdAt")):
                invalid_field("interactionContext", record_id, "createdAt")
            context = value.get("context")
            if not isinstance(context, list):
                invalid_field("interactionContext", record_id, "context")
            else:
                context_valid = True
                for turn in context:
                    if not isinstance(turn, dict) or turn.get("role") not in _VISIBLE_TURN_ROLES:
                        invalid_field("interactionContext", record_id, "context.role")
                        context_valid = False
                    if not isinstance(turn, dict) or not isinstance(turn.get("content"), str):
                        invalid_field("interactionContext", record_id, "context.content")
                        context_valid = False
                if (
                    context_valid
                    and _non_empty_string(value.get("contextHash"))
                    and hash_context(
                        [
                            VisibleTurn(role=turn["role"], content=turn["content"])
                            for turn in context
                        ]
                    )
                    != value["contextHash"]
                ):
                    errors.append(
                        f"interactionContext {record_id} contextHash does not match its context"
                    )

    semantic_resolutions = data.get("semanticResolutions")
    if isinstance(semantic_resolutions, list):
        resolution_ids: set[str] = set()
        evidence_with_resolution: set[str] = set()
        for value in semantic_resolutions:
            if not isinstance(value, dict) or not _non_empty_string(value.get("id")):
                continue
            record_id = value["id"]
            if record_id in resolution_ids:
                errors.append("data.semanticResolutions has duplicate ids")
            resolution_ids.add(record_id)
            evidence_id = value.get("evidenceId")
            if not isinstance(evidence_id, str) or evidence_id == "" or evidence_id not in evidence_ids:
                errors.append(f"semanticResolution {record_id} references a non-existent evidence: {evidence_id}")
            elif evidence_id in evidence_with_resolution:
                errors.append(f"data.semanticResolutions has duplicate evidenceId: {evidence_id}")
            else:
                evidence_with_resolution.add(evidence_id)
            if not isinstance(value.get("resolvedContent"), str):
                invalid_field("semanticResolution", record_id, "resolvedContent")
            if not _non_empty_string(value.get("resolverVersion")):
                invalid_field("semanticResolution", record_id, "resolverVersion")
            if not _parseable_date(value.get("createdAt")):
                invalid_field("semanticResolution", record_id, "createdAt")
            for field, allowed in (
                ("responseAct", _RESPONSE_ACTS),
                ("promptAct", _PROMPT_ACTS),
                ("propositionOrigin", _PROPOSITION_ORIGINS),
                ("assertionStrength", _ASSERTION_STRENGTHS),
            ):
                if value.get(field) is not None and value.get(field) not in allowed:
                    invalid_field("semanticResolution", record_id, field)
            if not _nullable_string(value.get("requiredContext")):
                invalid_field("semanticResolution", record_id, "requiredContext")

    # ── v3：2.0 World 对象段（可选；仅在 schemaVersion == 3 且键存在时校验）──
    # entity/relationship/world_event 是 Python-owned 2.0 对象：世界 ID 必须等于
    # bundle subjectId（单 subject 边界），链接与端点必须能在包内闭包解析。
    _world_section_names = (
        "entities",
        "relationships",
        "relationshipEvidence",
        "worldEvents",
        "worldEventEvidence",
        "cognitionTargets",
    )
    world_sections_present = any(name in data for name in _world_section_names)
    if world_sections_present and sv != BUNDLE_SCHEMA_VERSION:
        warnings.append(
            f"World sections are only supported by schemaVersion {BUNDLE_SCHEMA_VERSION}; ignored"
        )
    elif world_sections_present:
        entity_list = data.get("entities")
        relationship_list = data.get("relationships")
        relationship_evidence_list = data.get("relationshipEvidence")
        world_event_list = data.get("worldEvents")
        world_event_evidence_list = data.get("worldEventEvidence")
        cognition_target_list = data.get("cognitionTargets")
        for name, arr in (
            ("entities", entity_list),
            ("relationships", relationship_list),
            ("relationshipEvidence", relationship_evidence_list),
            ("worldEvents", world_event_list),
            ("worldEventEvidence", world_event_evidence_list),
            ("cognitionTargets", cognition_target_list),
        ):
            if not isinstance(arr, list):
                errors.append(f"data.{name} should be an array")

        entity_ids: set[str] = set()
        if isinstance(entity_list, list):
            for entity in entity_list:
                if not isinstance(entity, dict) or not _non_empty_string(entity.get("id")):
                    errors.append("data.entities has an element with a missing id")
                    continue
                entity_id = entity["id"]
                if entity_id in entity_ids:
                    errors.append("data.entities has duplicate ids")
                entity_ids.add(entity_id)
                if entity.get("worldId") != sid:
                    errors.append(f"entity {entity_id} worldId({_js_stringify(entity.get('worldId', _MISSING))}) does not match the bundle")
                if not _non_empty_string(entity.get("kind")):
                    invalid_field("entity", entity_id, "kind")
                if not _non_empty_string(entity.get("canonicalName")):
                    invalid_field("entity", entity_id, "canonicalName")
                aliases = entity.get("aliases", _MISSING)
                if aliases is not _MISSING and (
                    not isinstance(aliases, list)
                    or any(not isinstance(a, str) or a == "" for a in aliases)
                ):
                    invalid_field("entity", entity_id, "aliases")
                for field in ("createdAt", "updatedAt"):
                    if not _parseable_date(entity.get(field)):
                        invalid_field("entity", entity_id, field)
                invalid_at = entity.get("invalidAt", _MISSING)
                if invalid_at is not _MISSING and invalid_at is not None and not _parseable_date(invalid_at):
                    invalid_field("entity", entity_id, "invalidAt")

        def _world_record_checks(
            record_list: Any, name: str, ids: set[str], entity_index: set[str]
        ) -> None:
            for record in record_list:
                if not isinstance(record, dict) or not _non_empty_string(record.get("id")):
                    errors.append(f"data.{name} has an element with a missing id")
                    continue
                record_id = record["id"]
                if record_id in ids:
                    errors.append(f"data.{name} has duplicate ids")
                ids.add(record_id)
                if record.get("worldId") != sid:
                    errors.append(f"{name[:-1]} {record_id} worldId({_js_stringify(record.get('worldId', _MISSING))}) does not match the bundle")
                if not _non_empty_string(record.get("content")):
                    invalid_field(name[:-1], record_id, "content")
                if record.get("formedBy") not in _WORLD_FORMED_BY:
                    invalid_field(name[:-1], record_id, "formedBy")
                confidence = record.get("confidence", _MISSING)
                if (
                    not isinstance(confidence, (int, float))
                    or isinstance(confidence, bool)
                    or not math.isfinite(confidence)
                    or not float(confidence).is_integer()
                    or confidence < 0
                    or confidence > 1000
                ):
                    invalid_field(name[:-1], record_id, "confidence")
                if record.get("credStatus") not in _CRED_STATUSES:
                    invalid_field(name[:-1], record_id, "credStatus")
                for field in ("createdAt", "updatedAt"):
                    if not _parseable_date(record.get(field)):
                        invalid_field(name[:-1], record_id, field)
                invalid_at = record.get("invalidAt", _MISSING)
                if invalid_at is not _MISSING and invalid_at is not None and not _parseable_date(invalid_at):
                    invalid_field(name[:-1], record_id, "invalidAt")
                if name == "relationships":
                    source = record.get("sourceEntityId")
                    target = record.get("targetEntityId")
                    if not _non_empty_string(source) or not _non_empty_string(target):
                        invalid_field("relationship", record_id, "sourceEntityId/targetEntityId")
                    else:
                        if source not in entity_index or target not in entity_index:
                            errors.append(
                                f"relationship {record_id} references a non-existent entity: {source}/{target}"
                            )
                        if source == target:
                            errors.append(f"relationship {record_id} endpoints must differ")
                    if not _non_empty_string(record.get("relationType")):
                        invalid_field("relationship", record_id, "relationType")
                if name == "worldEvents":
                    occurred_at = record.get("occurredAt", _MISSING)
                    if occurred_at is not _MISSING and occurred_at is not None and not _parseable_world_date(occurred_at):
                        invalid_field("worldEvent", record_id, "occurredAt")
                    if not _nullable_string(record.get("timeExpression")):
                        invalid_field("worldEvent", record_id, "timeExpression")
                    for field in ("participants", "objects"):
                        members = record.get(field, _MISSING)
                        if members is _MISSING:
                            continue
                        if not isinstance(members, list) or any(
                            not isinstance(m, dict)
                            or not _non_empty_string(m.get("canonicalName"))
                            or not _non_empty_string(m.get("kind"))
                            for m in members
                        ):
                            invalid_field("worldEvent", record_id, field)

        relationship_ids: set[str] = set()
        if isinstance(relationship_list, list):
            _world_record_checks(relationship_list, "relationships", relationship_ids, entity_ids)
        world_event_ids: set[str] = set()
        if isinstance(world_event_list, list):
            _world_record_checks(world_event_list, "worldEvents", world_event_ids, entity_ids)

        def _world_link_checks(
            link_list: Any, name: str, id_field: str, index: set[str]
        ) -> None:
            seen: set[tuple[object, ...]] = set()
            for link in link_list:
                if not isinstance(link, dict):
                    errors.append(f"data.{name} has an invalid link")
                    continue
                target_id = link.get(id_field)
                evidence_id = link.get("evidenceId")
                relation = link.get("relation")
                if not _non_empty_string(target_id) or not _non_empty_string(evidence_id):
                    errors.append(f"data.{name} has an invalid endpoint")
                    continue
                if target_id not in index:
                    errors.append(f"{name} references a non-existent {id_field}: {target_id}")
                if evidence_id not in evidence_ids:
                    errors.append(f"{name} references a non-existent evidence: {evidence_id}")
                if relation not in _EVIDENCE_RELATIONS:
                    invalid_field(name, f"{target_id}/{evidence_id}", "relation")
                key = (target_id, evidence_id, str(relation))
                if key in seen:
                    errors.append(f"data.{name} has duplicate link: {target_id}/{evidence_id}/{relation}")
                seen.add(key)

        if isinstance(relationship_evidence_list, list):
            _world_link_checks(
                relationship_evidence_list, "relationshipEvidence", "relationshipId", relationship_ids
            )
        if isinstance(world_event_evidence_list, list):
            _world_link_checks(
                world_event_evidence_list, "worldEventEvidence", "worldEventId", world_event_ids
            )

        if isinstance(cognition_target_list, list):
            seen_targets: set[str] = set()
            for target in cognition_target_list:
                if not isinstance(target, dict) or not _non_empty_string(target.get("cognitionId")):
                    errors.append("data.cognitionTargets has an invalid endpoint")
                    continue
                cognition_id = target["cognitionId"]
                if cognition_id in seen_targets:
                    errors.append("data.cognitionTargets has duplicate cognitionId")
                seen_targets.add(cognition_id)
                if cognition_id not in cognition_ids:
                    errors.append(f"cognitionTarget references a non-existent cognition: {cognition_id}")
                target_entity_id = target.get("targetEntityId")
                if not _non_empty_string(target_entity_id) or target_entity_id not in entity_ids:
                    errors.append(f"cognitionTarget {cognition_id} references a non-existent target entity")
                perspective = target.get("perspectiveEntityId")
                if perspective is not None:
                    if not isinstance(perspective, str) or perspective == "":
                        invalid_field("cognitionTarget", cognition_id, "perspectiveEntityId")
                    elif perspective not in entity_ids:
                        errors.append(f"cognitionTarget {cognition_id} references a non-existent perspective entity")

    return ValidateResult(len(errors) == 0, errors, warnings)


def _num(v: float) -> str:
    """把数字渲染成与 JS `${n}` 一致的串(整数不带 .0)。"""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
