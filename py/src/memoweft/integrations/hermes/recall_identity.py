"""Read-only name associations from accepted, permission-eligible World facts.

An explicit self-naming claim can link its existing person entity to a
directly sourced first-person fact. This is retrieval metadata only: no
entity merge, new claim, Evidence search, or stored label is performed.
"""
from __future__ import annotations

import re
import sqlite3

from ...types import ModelTier
from ..trust.currentness import CurrentnessSurface, current_entity_aliases, linked_evidence, world_item_visible


def _direct_self_fact(db: sqlite3.Connection, subject_id: str, cognition_id: str) -> bool:
    """Recognize supported self descriptions, never the speaker's perspective.

    Be conservative where the existing target is absent or only a topic. A
    direct first-person birth/preference clause is sufficient; possession of a
    relative, reported opinions and omitted subjects are not. Other wording
    keeps its existing recall paths rather than guessing a new person link.
    """
    from .batch_adapter import _stated_normalize

    for content, raw in db.execute(
        "SELECT c.content, e.raw_content FROM cognition c "
        "JOIN cognition_evidence ce ON ce.cognition_id=c.id AND ce.relation='support' "
        "JOIN evidence e ON e.id=ce.evidence_id AND e.subject_id=c.subject_id "
        "WHERE c.id=? AND c.subject_id=? AND e.source_kind='spoken'", (cognition_id, subject_id),
    ).fetchall():
        source = str(raw).strip()
        direct = re.match(
            r"^I\s+(?:(?:am|was)\s+born\b|(?:(?:really|usually|always)\s+)*"
            r"(?:like|prefer|enjoy|dislike|love|hate)\b)"
            r"|^(?:我|本人)(?:更|最|很|平时|通常|一直|也|不|特别)*"
            r"(?:出生|喜欢(?!的)|偏好)", source, re.IGNORECASE,
        )
        # A self clause elsewhere in a shared source must not qualify a
        # different claim. Compare only; never change Evidence or proposition.
        if direct and _stated_normalize(source).startswith(str(content)):
            return True
    return False


def _names_self(content: str, name: str) -> bool:
    quoted_name = r'[“"\']?' + re.escape(name) + r'[”"\']?'
    return bool(re.match(
        r"^(?:(?:my name is|i am called|i['’]m called|call me)\s+" + quoted_name
        + r"(?=\s*(?:[,;.!?]|$)|\s+and\b)"
        + r"|(?:用户|我)(?:叫|名叫|的?名字(?:是|叫)|(?:偏好)?称呼自己为|以后叫我)"
        + quoted_name + r"(?=[，,。；;！？!?]|$))",
        content.strip(), re.IGNORECASE,
    ))


def associate_owner_names(
    db: sqlite3.Connection, subject_id: str, rows: list[dict[str, object]], model_tier: ModelTier,
) -> list[dict[str, object]]:
    # Local import keeps the existing identity derivation authoritative without
    # creating a module-import cycle (batch_adapter also uses recall).
    from .batch_adapter import owner_entity_id_for

    owner_id = owner_entity_id_for(subject_id)
    surface: CurrentnessSurface = "model_cloud" if model_tier == "cloud" else "recall"
    entity_names: dict[str, tuple[str, ...]] = {}
    self_ids: set[str] = {owner_id}
    for entity_id, canonical_name in db.execute(
        "SELECT id, canonical_name FROM entity WHERE world_id=? AND kind='person' "
        "AND invalid_at IS NULL ORDER BY id", (subject_id,),
    ).fetchall():
        entity_id, canonical_name = str(entity_id), str(canonical_name)
        if not world_item_visible(db, subject_id, "entity", entity_id, surface=surface):
            continue
        # A legacy entity without formation provenance cannot establish an owner
        # identity. Its independently sourced alias ledger can still do so.
        aliases = current_entity_aliases(db, subject_id, entity_id, surface=surface)
        has_source = bool(linked_evidence(db, "entity", entity_id))
        names = (canonical_name, *aliases) if has_source else aliases
        if not names:
            continue
        entity_names[entity_id] = names
        if has_source and any(
            row.get("statement_kind") == "naming" and not row.get("is_superseded")
            # The accepted self-name may have become a permission-eligible
            # alias when the earlier-formed neutral name stayed canonical.
            and any(_names_self(str(row["content"]), name) for name in names)
            for row in rows
        ):
            self_ids.add(entity_id)

    # A separate person explicitly related to the owner under the same name is
    # not proof that they are the owner. Keep the collision unresolved.
    conflicting_ids: set[str] = set()
    for row in rows:
        if row["kind"] != "relationship" or row.get("is_superseded"):
            continue
        endpoints = db.execute(
            "SELECT source_entity_id, target_entity_id FROM relationship WHERE id=? AND world_id=?",
            (str(row["id"]), subject_id),
        ).fetchone()
        if endpoints is not None and str(endpoints[0]) == owner_id and str(endpoints[1]) in self_ids:
            conflicting_ids.add(str(endpoints[1]))
    self_ids -= conflicting_ids

    holders: dict[str, set[str]] = {}
    spelling: dict[str, str] = {}
    for entity_id, names in entity_names.items():
        for name in names:
            holders.setdefault(name.casefold(), set()).add(entity_id)
            spelling.setdefault(name.casefold(), name)
    ambiguous = tuple(spelling[name] for name, ids in holders.items()
                      if len(ids - self_ids) > 1 or (ids & self_ids and ids - self_ids)
                      or ids & conflicting_ids)
    owner_names = tuple(dict.fromkeys(
        name for entity_id, names in entity_names.items() if entity_id in self_ids
        for name in names if holders[name.casefold()] <= self_ids and name not in ambiguous
    ))
    if not owner_names and not ambiguous:
        return rows

    result: list[dict[str, object]] = []
    for row in rows:
        updated = {**row, "ambiguous_anchors": ambiguous}
        if row["kind"] == "cognition" and row.get("statement_kind") in {"attribute", "preference"}:
            target = db.execute(
                "SELECT ct.target_entity_id, ct.perspective_entity_id, e.kind "
                "FROM cognition_target ct LEFT JOIN entity e ON e.id=ct.target_entity_id "
                "AND e.world_id=? WHERE ct.cognition_id=?", (subject_id, str(row["id"])),
            ).fetchone()
            # Missing targets, topic labels and owner perspective alone do not
            # establish the fact's subject. Require its own direct self source.
            owner_target = target is None or (
                target[1] is None and (str(target[0]) == owner_id or str(target[2]) == "topic")
            )
            if owner_target and _direct_self_fact(db, subject_id, str(row["id"])):
                anchors = row.get("anchors", ())
                assert isinstance(anchors, tuple)
                updated["anchors"] = tuple(dict.fromkeys((*anchors, *owner_names)))
        result.append(updated)
    return result
