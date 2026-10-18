"""Read-only cascade preview, evaluated by the real erasure code on a RAM copy."""
from __future__ import annotations

import sqlite3
import json

from ...store.interaction_context import erased_context_json

from .command_store import TrustCommandError
from .model import CommandEnvelopeV1
from .revision import coherent_revision_read
from .true_delete import conversation_evidence_ids, delete_evidence, delete_world_item, erase_conversation_commitments, _redact_observed_dependencies


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
        if copy.execute("SELECT 1 FROM sqlite_master WHERE name='interaction_commitment'").fetchone():
            tables["interaction_commitment"] = ("interaction_commitment", "subject_id", "content")
        tables["interaction_context"] = ("interaction_context", "subject_id", "context_json")
        before = {(kind, str(row[0])): (str(row[1]), str(row[2])) for kind, (table, owner, name) in tables.items()
                  for row in copy.execute(f"SELECT id, {name}, " + ("kind" if kind in {"entity", "interaction_commitment"} else "content_type" if kind == "cognition" else f"'{kind}'") + f" FROM {table} WHERE {owner} = ?", (subject_id,))}
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
        if conversation_id is not None:
            affected.update(erase_conversation_commitments(copy, subject_id, conversation_id))
            context_ids = {str(row[0]) for row in copy.execute(
                "SELECT id FROM interaction_context WHERE subject_id=? AND conversation_id=?", (subject_id, conversation_id))}
            affected.update(_redact_observed_dependencies(copy, subject_id, affected | context_ids))
        items = []
        for (kind, item_id), (name, item_type) in sorted(before.items()):
            after = copy.execute(f"SELECT {tables[kind][2]} FROM {tables[kind][0]} WHERE id=?", (item_id,)).fetchone()
            removed = after is None
            if kind == 'interaction_context':
                removed = not erased_context_json(name) and (after is None or erased_context_json(str(after[0])))
            if removed:
                display = '\n'.join(str(turn.get('content', '')) for turn in json.loads(name)) if kind == 'interaction_context' else name
                items.append({"object_kind": kind, "item_id": item_id, "name": display, "item_type": item_type})
        evidence_ids = [str(row[0]) for row in copy.execute("SELECT id FROM evidence WHERE subject_id = ?", (subject_id,)) if str(row[0]) in affected]
        return {"world_revision": revision, "items": items, "item_count": len(items),
                "evidence_ids": sorted(evidence_ids), "evidence_count": len(evidence_ids)}
    finally:
        copy.close()
