"""privacy 读取门 + perceive + ingest 内部行为单测。"""
from __future__ import annotations

from memoweft.config import CONFIG
from memoweft.ingest import Observation, ingest_observations
from memoweft.perceive import PerceiveOptions, perceive
from memoweft.privacy import filter_readable_by_tier
from memoweft.store import open_db
from memoweft.store.evidence import SqliteEvidenceStore
from memoweft.types import Evidence


def _ev(id: str, *, cloud: bool, local: bool) -> Evidence:
    return Evidence(
        id=id, subject_id="owner", source_kind="spoken", host_id="local", origin_id=None,
        occurred_at="2026-01-01T00:00:00.000Z", recorded_at="2026-01-01T00:00:00.000Z",
        raw_content=id, summary=id, allow_local_read=local, allow_cloud_read=cloud, allow_inference=True,
        corrects_evidence_id=None,
    )


def test_filter_readable_by_tier() -> None:
    items = [_ev("a", cloud=True, local=False), _ev("b", cloud=False, local=True), _ev("c", cloud=True, local=True)]
    assert [e.id for e in filter_readable_by_tier(items)] == ["a", "c"]  # 缺省 cloud
    assert [e.id for e in filter_readable_by_tier(items, "cloud")] == ["a", "c"]
    assert [e.id for e in filter_readable_by_tier(items, "local")] == ["b", "c"]  # 顺序保留


def test_perceive_defaults() -> None:
    inp = perceive("我喜欢爬山")
    assert inp.subject_id == CONFIG.identity.subject_id  # 'owner'
    assert inp.host_id == CONFIG.identity.host_id  # 'local'
    assert inp.source_kind == "spoken"
    assert inp.origin_id is None
    # 显式 opts 覆盖
    inp2 = perceive("x", PerceiveOptions(subject_id="u2", source_kind="observed", origin_id="o1"))
    assert inp2.subject_id == "u2" and inp2.source_kind == "observed" and inp2.origin_id == "o1"


def test_ingest_observations() -> None:
    db = open_db(":memory:")
    try:
        store = SqliteEvidenceStore(db)
        obs = [
            Observation(kind="active_window", occurred_at="2026-01-01T00:00:00.000Z", content="在 VS Code 停留 40 分钟", origin_id="w1"),
            Observation(kind="active_window", occurred_at="2026-01-01T00:01:00.000Z", content="切到浏览器", origin_id="w1"),  # 撞 origin → skip
        ]
        res = ingest_observations("owner", obs, store)
        assert len(res.stored) == 1 and res.skipped == 1
        ev = res.stored[0]
        assert ev.source_kind == "observed"
        # observed 保守默认:local✓/cloud✗/infer✓
        assert ev.allow_local_read is True and ev.allow_cloud_read is False and ev.allow_inference is True
        # 显式授权覆盖
        res2 = ingest_observations(
            "owner",
            [Observation(kind="x", occurred_at="2026-01-02T00:00:00.000Z", content="y", allow_cloud_read=True)],
            store,
        )
        assert res2.stored[0].allow_cloud_read is True
    finally:
        db.close()
