"""校验便携记忆包的纯函数，与 TypeScript validateBundle 契约保持一致。

消息语言固定 en(TS 默认 lang;shared/parity/bundle-validate.json 的 expected 即 en)。
分级:errors(致命·valid=false)/ warnings(软提示·可导入)。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .model import BUNDLE_FORMAT, BUNDLE_SCHEMA_VERSION

# cognition 字段值校验的运行时全集（与 TS cognition/model.ts 的 CONTENT_TYPES / FORMED_BY_VALUES /
#   CRED_STATUSES 逐一对齐）。content_type 认【完整 8 值】含 hypothesis/trend——导入的是已落库认知，
#   可能由 attribute/trends 产出这两类，不能只认 consolidate 收的那 6 个。
_CONTENT_TYPES = frozenset(
    ("fact", "preference", "goal", "project", "state", "trait", "hypothesis", "trend")
)
_FORMED_BY = frozenset(("stated", "observed", "ruled", "confirmed", "inferred"))
_CRED_STATUSES = frozenset(("candidate", "low", "limited", "stable", "conflicted", "contested"))


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


def validate_bundle(bundle: Any) -> ValidateResult:
    errors: list[str] = []
    warnings: list[str] = []

    if bundle is None or not isinstance(bundle, dict):
        return ValidateResult(False, ["bundle is not an object"], warnings)
    b = bundle

    fmt = b.get("format", _MISSING)
    if fmt != BUNDLE_FORMAT:
        errors.append(f'format should be "{BUNDLE_FORMAT}", but got {_js_stringify(fmt)}')
    sv = b.get("schemaVersion", _MISSING)
    if not isinstance(sv, (int, float)) or isinstance(sv, bool) or sv is _MISSING:
        errors.append("schemaVersion is missing or not a number")
    elif sv > BUNDLE_SCHEMA_VERSION:
        errors.append(f"schemaVersion={_num(sv)} is higher than the {BUNDLE_SCHEMA_VERSION} supported by this version (upgrade MemoWeft before importing)")
    elif sv < BUNDLE_SCHEMA_VERSION:
        warnings.append(f"schemaVersion={_num(sv)} is lower than the current {BUNDLE_SCHEMA_VERSION} (importing with the old structure)")
    sid = b.get("subjectId", _MISSING)
    if not isinstance(sid, str) or sid == "":
        errors.append("subjectId is missing")

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
        if not isinstance(l.get("eventId"), str) or not isinstance(l.get("evidenceId"), str):
            errors.append("data.eventEvidence has an invalid endpoint")
            break
    for l in cogev_list:
        if not isinstance(l.get("cognitionId"), str) or not isinstance(l.get("evidenceId"), str):
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
        # 必须是 0~1000 的整数：非数字/NaN/小数/越界一律拒。bool 是 int 子类，须显式排除。
        if (
            not isinstance(conf, int)
            or isinstance(conf, bool)
            or conf < 0
            or conf > 1000
        ):
            errors.append(
                f"cognition {c['id']} has an invalid confidence (must be an integer 0-1000): {_js_stringify(conf)}"
            )

    for e in ev_list:
        if e.get("subjectId") != sid:
            warnings.append(f"evidence {e['id']} subjectId({e.get('subjectId')}) does not match the bundle({sid})")
    for e in evt_list:
        if e.get("subjectId") != sid:
            warnings.append(f"event {e['id']} subjectId({e.get('subjectId')}) does not match the bundle")
    for c in cog_list:
        if c.get("subjectId") != sid:
            warnings.append(f"cognition {c['id']} subjectId({c.get('subjectId')}) does not match the bundle")

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
        elif any(not isinstance(x.get("id"), str) or x.get("id") == "" for x in arr):
            errors.append(f"data.{name} has an element with a missing id")

    return ValidateResult(len(errors) == 0, errors, warnings)


def _num(v: float) -> str:
    """把数字渲染成与 JS `${n}` 一致的串(整数不带 .0)。"""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
