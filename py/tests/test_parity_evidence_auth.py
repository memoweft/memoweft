"""evidence.put 授权分流 parity:Python 与 TS(shared/parity/evidence-auth.json)逐例一致。

验证「按 source_kind 补保守默认 + 显式优先 + cloud_read_default 跟随 privacy_mode」的跨语言一致性。
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from conftest import parity

from memoweft.config import CONFIG
from memoweft.store import open_db
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.types import EvidenceInput


def _fixed() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)  # 授权不吃时间,固定只为确定


def test_evidence_auth_matches_ts() -> None:
    data: Any = parity("evidence-auth.json")
    for case in data["cases"]:
        inp = case["input"]
        cfg = replace(CONFIG, privacy_mode=True) if inp["privacyMode"] else CONFIG
        db = open_db(":memory:")
        try:
            store = SqliteEvidenceStore(db, cfg, _fixed)
            ex = inp["explicit"]
            ev = store.put(
                EvidenceInput(
                    subject_id="owner",
                    source_kind=inp["sourceKind"],
                    host_id="local",
                    raw_content="x",
                    allow_local_read=ex.get("allowLocalRead"),
                    allow_cloud_read=ex.get("allowCloudRead"),
                    allow_inference=ex.get("allowInference"),
                )
            )
            got = {
                "allowLocalRead": ev.allow_local_read,
                "allowCloudRead": ev.allow_cloud_read,
                "allowInference": ev.allow_inference,
            }
            assert got == case["expected"], f"授权分流分叉 input={inp} got={got} want={case['expected']}"
        finally:
            db.close()
