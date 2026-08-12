"""Fresh-process restart proof for the SQLite identity authority."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap


_SRC = Path(__file__).resolve().parents[1] / "src"


def _phase(database: Path, body: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(_SRC)
    return subprocess.run(
        [sys.executable, "-B", "-c", textwrap.dedent(body), str(database)],
        cwd=_SRC.parent,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
    )


_COMMON = """
from pathlib import Path
import sys
from memoweft.world import Entity, MemoryLoop, MemoryWorldGraph, PersonalWorld

def graph():
    value = MemoryWorldGraph(PersonalWorld("world", "owner"))
    value.add_entity(Entity("owner", "world", "person", "Owner"))
    value.add_entity(Entity("ana", "world", "person", "Ana"))
    value.add_entity(Entity("annie", "world", "person", "Annie"))
    return value

database = Path(sys.argv[1])
"""


def test_identity_pending_accept_and_resolution_survive_three_fresh_processes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "shared-main.sqlite"

    _phase(
        database,
        _COMMON
        + """
from memoweft.world import EntityIdentityDelta, IdentityEvidence, PersistentIdentityAuthority

with MemoryLoop(database, graph()) as loop:
    authority = PersistentIdentityAuthority(loop.connection)
    authority.register_evidence(IdentityEvidence(
        "e:ana", "world", "conversation:1", "2026-01-01T00:00:00+00:00", "user", "Ana",
        "continuity:owner",
    ))
    mention = authority.issue_verified_mention("e:ana", 0, 3)
    pending = authority.stage(
        EntityIdentityDelta.bind("world", "ana", mention),
        {"reviewer": "owner", "reason": "accepted identity"},
    )
    assert authority.view().pending[0].result_hash == pending.result_hash
""",
    )

    _phase(
        database,
        _COMMON
        + """
from memoweft.world import PersistentIdentityAuthority

with MemoryLoop(database, graph()) as loop:
    authority = PersistentIdentityAuthority(loop.connection)
    pending = authority.view().pending[0]
    authority.decide(
        pending.review_id,
        pending.result_hash,
        "accept",
        "2026-02-01T00:00:00+00:00",
    )
    assert authority.view().bindings[0].current_entity_id == "ana"
""",
    )

    result = _phase(
        database,
        _COMMON
        + """
import json
from memoweft.world import (
    EntityReferenceResolver,
    IdentityEvidence,
    PersistentIdentityAuthority,
)

with MemoryLoop(database, graph()) as loop:
    authority = PersistentIdentityAuthority(loop.connection)
    before = authority.view()
    assert before.revision == 1
    assert len(before.pending) == 0
    assert len(before.decisions) == 1
    assert before.bindings[0].current_entity_id == "ana"
    authority.register_evidence(IdentityEvidence(
        "e:current", "world", "conversation:2", "2026-03-01T00:00:00+00:00", "user", "她",
        "continuity:owner",
    ))
    current = authority.issue_verified_mention("e:current", 0, 1)
    resolution = EntityReferenceResolver().resolve_context(authority.resolution_context(current))
    assert resolution.state == "resolved"
    assert resolution.entity_id == "ana"
    row = loop.connection.execute(
        "SELECT storage_generation, state_hash FROM identity_state WHERE singleton = 1"
    ).fetchone()
    assert row is not None
    print(json.dumps({
        "entity_id": resolution.entity_id,
        "identity_revision": before.revision,
        "storage_generation": row[0],
        "state_hash": row[1],
    }, sort_keys=True))
""",
    )

    observation = json.loads(result.stdout.strip())
    assert observation["entity_id"] == "ana"
    assert observation["identity_revision"] == 1
    assert observation["storage_generation"] == 7
    assert observation["state_hash"].startswith("sha256:")
