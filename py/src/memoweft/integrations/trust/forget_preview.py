"""Read-only cascade preview, evaluated by the real erasure code on a RAM copy."""
from __future__ import annotations

import sqlite3

from .command_store import TrustCommandError
from .model import CommandEnvelopeV1
from .revision import coherent_revision_read
from .true_delete import conversation_evidence_ids, delete_evidence, delete_world_item


def preview_forget(db_path: str, subject_id: str, *, target_kind: str | None = None,
                   target_id: str | None = None, conversation_id: str | None = None) -> dict[str, object]:
    # The source connection is mode=ro + query_only. No savepoint on the live
    # database, receipt, revision advance, storage cleanup or model invocation.
    copy = sqlite3.connect(":memory:", isolation_level=None)
    try:
        with coherent_revision_read(db_path) as view:
            view.db.backup(copy)
            revision = view.world_revision
        copy.execute("PRAGMA foreign_keys = ON")
        tables = {"entity": ("entity", "world_id", "canonical_name"),
                  "relationship": ("relationship", "world_id", "content"),
                  "event": ("world_event", "world_id", "content"),
                  "cognition": ("cognition", "subject_id", "content")}
        before = {(kind, str(row[0])): (str(row[1]), str(row[2])) for kind, (table, owner, name) in tables.items()
                  for row in copy.execute(f"SELECT id, {name}, " + ("kind" if kind == "entity" else "content_type" if kind == "cognition" else f"'{kind}'") + f" FROM {table} WHERE {owner} = ?", (subject_id,))}
        if conversation_id is not None:
            targets = [("evidence", value) for value in sorted(conversation_evidence_ids(copy, subject_id, conversation_id))]
        elif target_kind is not None and target_id is not None:
            targets = [(target_kind, target_id)]
        else:
            raise TrustCommandError("invalid_forget_preview")
        affected: set[str] = set()
        copy.execute("BEGIN")
        for kind, item_id in targets:
            command: CommandEnvelopeV1 = {"schema_version": 1, "command_id": "preview-only", "subject_id": subject_id,
                "actor": "owner", "expected_world_revision": revision, "submitted_at": "preview-only",
                "operation": "delete_evidence" if kind == "evidence" else "delete_world_item",
                "target_kind": kind, "target_id": item_id, "payload": {}}  # type: ignore[typeddict-item]
            result = (delete_evidence if kind == "evidence" else delete_world_item)(copy, command, "preview-only", subject_id)
            if result.result_state == "rejected":
                raise TrustCommandError(result.rejection_code or "world_item_not_found")
            affected.update(result.affected_ids)
        items = [{"object_kind": kind, "item_id": item_id, "name": name, "item_type": item_type}
                 for (kind, item_id), (name, item_type) in sorted(before.items())
                 if copy.execute(f"SELECT 1 FROM {tables[kind][0]} WHERE id = ?", (item_id,)).fetchone() is None]
        evidence_ids = [str(row[0]) for row in copy.execute("SELECT id FROM evidence WHERE subject_id = ?", (subject_id,)) if str(row[0]) in affected]
        return {"world_revision": revision, "items": items, "item_count": len(items),
                "evidence_ids": sorted(evidence_ids), "evidence_count": len(evidence_ids)}
    finally:
        copy.close()
