"""Resolve exact assistant references recorded by the formation compiler."""
import json
import sqlite3


def assistant_sources(db: sqlite3.Connection, subject_id: str, item_id: str,
                      evidence_id: str | None = None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for content, payload in db.execute("SELECT content, payload_json FROM evidence_ledger WHERE id LIKE 'confirmed-source-%'"):
        data, detail = json.loads(content), json.loads(payload)
        if data.get('cognition_id') != item_id or evidence_id is not None and data.get('evidence_id') != evidence_id:
            continue
        source = detail.get('assistant_source')
        if not isinstance(source, dict):
            continue
        row = db.execute('SELECT context_json, conversation_id, created_at FROM interaction_context WHERE id=? AND subject_id=?',
                         (source.get('interaction_id'), subject_id)).fetchone()
        if row is None:
            continue
        for turn in json.loads(row[0]):
            if turn.get('role') != 'assistant' or turn.get('message_id') != source.get('message_id'):
                continue
            key = (str(source['interaction_id']), str(turn['message_id']))
            if key in seen:
                continue
            seen.add(key)
            result.append({'message_id': str(turn['message_id']), 'content': str(turn['content']),
                           'conversation_id': str(row[1]), 'recorded_at': str(row[2])})
    return result
