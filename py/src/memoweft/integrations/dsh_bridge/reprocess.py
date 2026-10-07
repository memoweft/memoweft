"""Explicitly reprocess one existing source job after an interpreter repair.

Creates an auditable new job through the normal boundary store, reusing the
original Evidence and preserving both the old terminal and chat records.
No model is called here. The caller must explicitly wake or drain its configured
worker; an idle worker in another process does not poll externally queued jobs.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from ...store import open_db
from ..hermes.boundary_store import (
    HermesBoundaryStore, HermesBoundaryEvidenceCandidate,
    HermesBoundaryFormalTarget, ValidatedHermesBoundary,
)
from ..trust.currentness import evidence_state
from .interactions import _eligible


def reprocess_job(db_path: Path, *, subject_id: str, job_id: str, request_id: str) -> dict[str, object]:
    if not db_path.is_file():
        raise ValueError("database_not_found")
    if not request_id.strip() or request_id != request_id.strip() or len(request_id) > 128:
        raise ValueError("invalid_reprocess_request_id")
    db = open_db(str(db_path))
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN IMMEDIATE")
        source = db.execute(
            "SELECT * FROM memory_world_job WHERE job_id=? AND subject_id=?", (job_id, subject_id),
        ).fetchone()
        if source is None or source["state"] not in ("no_change", "dead"):
            raise ValueError("source_job_not_reprocessable")
        if not _eligible(db, subject_id, source["boundary_event_id"]):
            raise ValueError("source_evidence_not_eligible")
        target = HermesBoundaryFormalTarget(**json.loads(source["formal_target_json"]))
        candidates = []
        evidence_ids = json.loads(source["evidence_ids_json"])
        if not evidence_ids:
            raise ValueError("source_job_has_no_evidence")
        for evidence_id in evidence_ids:
            row = db.execute("SELECT * FROM evidence WHERE id=?", (evidence_id,)).fetchone()
            if (
                row is None or row["subject_id"] != subject_id
                or row["host_id"] != target.host_id or row["source_kind"] != "spoken"
                or row["corrects_evidence_id"] is not None
                or evidence_state(row, surface="formation", model_tier="local") is not None
            ):
                raise ValueError("source_evidence_not_eligible")
            candidates.append(HermesBoundaryEvidenceCandidate(
                origin_id=row["origin_id"], raw_content=row["raw_content"],
                occurred_at=row["occurred_at"], preceding_ai_context=row["preceding_ai_context"],
            ))
        identity = json.dumps([subject_id, job_id, request_id], ensure_ascii=True, separators=(",", ":"))
        payload_hash = sha256(identity.encode()).hexdigest()
        receipt = HermesBoundaryStore(db).accept_in_transaction(ValidatedHermesBoundary(
            event_id=f"weftmate-reprocess-v1:{job_id}:{payload_hash}", payload_hash=payload_hash,
            formal_target=target, evidence=tuple(candidates),
        ))
        db.commit()
        return {**receipt.as_dict(), "original_job_id": job_id}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--request-id", required=True, help="Reuse the same ID when retrying this request")
    parser.add_argument("--apply", action="store_true", help="Explicitly enqueue reprocessing")
    args = parser.parse_args()
    if not args.apply:
        parser.error("--apply is required to enqueue reprocessing")
    print(json.dumps(reprocess_job(
        args.database, subject_id=args.subject_id, job_id=args.job_id, request_id=args.request_id,
    ), ensure_ascii=False))


if __name__ == "__main__":
    main()
