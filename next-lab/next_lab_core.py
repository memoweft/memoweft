"""MemoWeft Next Lab: local diagnostics plus the real 1.x product adapter.

The Lab deliberately runs the existing manual Golden builders.  It is not an
extractor, a storage prototype, or a substitute for Owner semantic review.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import socket
import sys
import subprocess
import unicodedata
import uuid
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Collection, Literal, cast


if TYPE_CHECKING:
    from memoweft.world import ConversationTurn


REPO_ROOT = Path(__file__).resolve().parent.parent
PY_ROOT = REPO_ROOT / "py"
TEST_ROOT = PY_ROOT / "tests"
for _path in (PY_ROOT / "src", TEST_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


_META_REFERENCE_ONLY = re.compile(
    r"^(?:我)?(?:刚才|刚刚|之前|前面)(?:我)?(?:已经|都|就)?(?:跟你)?"
    r"(?:告诉(?:过)?你|说(?:过)?|提过|讲过)(?:了|啦|呀|啊)?$"
)

# Legacy imports deliberately accept a narrow, replayable subset of the 1.x
# conversation ledger.  The timestamp grammar is kept explicit instead of
# relying on ``datetime.fromisoformat``'s permissive implementation details:
# imported Evidence must have an unambiguous timezone and no hidden suffix.
_RFC3339_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


SCENARIOS: dict[str, dict[str, Any]] = {
    "golden-1-nanjing": {
        "title": "Golden #1 · Nanjing conflict",
        "builder": ("test_world_model_golden_nanjing", "build_nanjing_world"),
        "checks": (
            "test_nanjing_case_forms_a_personal_world_not_only_a_user_profile",
            "test_event_anchor_expands_the_local_memory_needed_to_answer_why_they_argued",
            "test_perspective_and_provenance_are_separate_dimensions",
        ),
        "module": "test_world_model_golden_nanjing",
        "summary": "Event, relationship, independent Friend_X cognition, target, perspective and provenance.",
        "boundaryQuestion": "Does this local structure feel like a lived event, rather than a User Profile paragraph?",
    },
    "golden-2-mother-candy": {
        "title": "Golden #2 · Mother gives candy",
        "builder": ("test_world_model_golden_mother_candy", "build_mother_candy_world"),
        "checks": (
            "test_manual_oracle_forms_a_third_party_relationship_pattern",
            "test_manual_oracle_does_not_infer_that_mother_likes_candy",
        ),
        "module": "test_world_model_golden_mother_candy",
        "summary": "Repeated care belongs to a relationship pattern; it is not mind-reading.",
        "boundaryQuestion": "Is the relationship pattern useful without inferring a private preference for Mother?",
    },
    "golden-3-ai-shared-experience": {
        "title": "Golden #3 · AI shared experience",
        "builder": ("test_world_model_golden_ai_shared_experience", "build_confirmed_ai_interpretation_world"),
        "checks": (
            "test_manual_oracle_uses_real_user_evidence_for_a_confirmed_ai_interpretation",
            "test_manual_oracle_keeps_assistant_turn_as_non_evidence_context",
            "test_manual_oracle_uses_user_perspective_for_ai_confirmed_cognition",
        ),
        "module": "test_world_model_golden_ai_shared_experience",
        "summary": "The agent participates in an event; the confirmed cognition defaults to the user's perspective and only user confirmation is evidence.",
        "boundaryQuestion": "Does the user's confirmed cognition remain distinct from the agent's proposal and event participation?",
    },
    "golden-4-relationship-repair": {
        "title": "Golden #4 · Conflict, apology, repair",
        "builder": ("test_world_model_golden_relationship_repair", "build_repaired_friendship_world"),
        "checks": ("test_manual_oracle_preserves_history_and_targets_repair_cognition",),
        "module": "test_world_model_golden_relationship_repair",
        "summary": "Three evidence-referenced events remain queryable; repair is a relationship-targeted cognition, not a status overwrite.",
        "semanticStatus": "owner-review-required",
        "boundaryQuestion": "Does repair remain evidence-backed cognition or derived projection while the three-event history stays intact?",
    },
    "golden-5-dormant-friend": {
        "title": "Golden #5 · Long-dormant friend",
        "builder": ("test_world_model_golden_dormant_friend", "build_dormant_friend_world"),
        "checks": ("test_manual_oracle_low_current_snapshot_keeps_identity_and_history",),
        "module": "test_world_model_golden_dormant_friend",
        "summary": "Identity, relationship and history persist; salience remains a later query/time-derived signal.",
        "semanticStatus": "owner-review-required",
        "boundaryQuestion": "Does the world retain identity, relationship and history without a long-lived status or active_salience field?",
    },
}


def _json_value(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_json_value(item) for item in value]
    return value


def graph_payload(graph: Any, provenance_artifacts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Serialize a real manual-builder graph, retaining only Evidence references.

    The Stage 0 builders contain Evidence IDs, not frozen raw transcript text.
    This serializer deliberately does not invent or recover raw content.
    """
    payload = {
        "world": _json_value(graph.world),
        "entities": [_json_value(item) for item in graph.entities.values()],
        "relationships": [_json_value(item) for item in graph.relationships.values()],
        "events": [_json_value(item) for item in graph.events.values()],
        "cognitions": [_json_value(item) for item in graph.cognitions.values()],
        "evidencePolicy": {
            "kind": "reference-only",
            "message": "Evidence IDs are references only. Most have no frozen raw content in this repository; the Lab never supplements it.",
        },
    }
    if provenance_artifacts is not None:
        # Golden #3 actually constructs these typed audit artifacts.  No
        # artificial transcript or provenance is added for the other cases.
        payload["provenanceArtifacts"] = provenance_artifacts
    return payload


def canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def file_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def scenario_definition_hash(scenario_id: str) -> str:
    """Hash the allowlisted contract, separately from its Python source."""
    metadata = SCENARIOS[scenario_id]
    return canonical_hash({
        "id": scenario_id,
        "title": metadata["title"],
        "builder": metadata["builder"],
        "checks": metadata["checks"],
        "module": metadata["module"],
        "summary": metadata["summary"],
        "semanticStatus": metadata.get("semanticStatus", "manual-oracle-awaiting-owner"),
        "boundaryQuestion": metadata["boundaryQuestion"],
    })


def scenario_reproducibility() -> dict[str, dict[str, str | None]]:
    snapshot: dict[str, dict[str, str | None]] = {}
    for scenario_id, metadata in SCENARIOS.items():
        source = TEST_ROOT / f"{metadata['module']}.py"
        snapshot[scenario_id] = {
            "definitionHash": scenario_definition_hash(scenario_id),
            "sourceHash": file_hash(source) if source.is_file() else None,
        }
    return snapshot


def fixture_reproducibility(repo_root: Path) -> dict[str, Any]:
    """Read the declared fixture contract and hash only the payloads it names.

    The manifest is an index, not a payload: including it in ``payloadActualHashes``
    would create the recursive integrity claim that the Gate explicitly avoids.
    """
    fixture_root = repo_root / "py" / "tests" / "fixtures" / "next" / "golden-001-nanjing"
    manifest_path = fixture_root / "manifest.json"
    empty: dict[str, Any] = {
        "manifestHash": None,
        "fixtureId": None,
        "fixtureVersion": None,
        "status": None,
        "synthetic": None,
        "ownerApproved": None,
        "notGate1Evidence": None,
        "declaredPayloadHashes": {},
        "payloadActualHashes": {},
        "payloadHashMismatches": [],
    }
    if not manifest_path.is_file():
        return {**empty, "error": "manifest-missing"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest is not an object")
        payloads = manifest.get("payloads", {})
        if not isinstance(payloads, dict) or not all(isinstance(name, str) and isinstance(value, str) for name, value in payloads.items()):
            raise ValueError("manifest payloads are invalid")
        actual = {
            name: file_hash(fixture_root / name) if (fixture_root / name).is_file() else None
            for name in payloads
        }
        mismatches = sorted(name for name, expected in payloads.items() if actual[name] != expected.lower())
        return {
            "manifestHash": file_hash(manifest_path),
            "fixtureId": manifest.get("fixture_id"),
            "fixtureVersion": manifest.get("fixture_version"),
            "status": manifest.get("status"),
            "synthetic": manifest.get("synthetic"),
            "ownerApproved": manifest.get("owner_approved"),
            "notGate1Evidence": manifest.get("not_gate_1_evidence"),
            "declaredPayloadHashes": dict(sorted(payloads.items())),
            "payloadActualHashes": actual,
            "payloadHashMismatches": mismatches,
        }
    except (OSError, ValueError, json.JSONDecodeError):
        return {**empty, "manifestHash": file_hash(manifest_path), "error": "manifest-unreadable"}


def reproducibility_snapshot(repo_root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False).stdout.strip()
    tracked_diff = subprocess.run(
        ["git", "-C", str(repo_root), "diff", "--binary", "HEAD"],
        capture_output=True,
        check=False,
    ).stdout
    untracked_names = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=False,
    ).stdout.decode("utf-8", errors="surrogateescape").split("\0")
    untracked_hashes = {
        name: file_hash(repo_root / name)
        for name in sorted(name for name in untracked_names if name and (repo_root / name).is_file())
    }
    dirty_fingerprint = canonical_hash({
        "trackedDiffHash": "sha256:" + hashlib.sha256(tracked_diff).hexdigest(),
        "untrackedHashes": untracked_hashes,
    })
    return {
        "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"),
        "dirtyFingerprint": dirty_fingerprint,
        # Historical key retained for run-comparison compatibility; this is
        # a diagnostic snapshot label, not a product gate.
        "stageGate": "Capability 1 · legacy diagnostic snapshot",
        "fixture": fixture_reproducibility(repo_root),
        "scenarios": scenario_reproducibility(),
    }


def _module(name: str) -> Any:
    return importlib.import_module(name)


def build_scenario(scenario_id: str) -> tuple[Any, dict[str, Any] | None]:
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        raise KeyError("unknown allowlisted scenario")
    module_name, builder_name = scenario["builder"]
    builder = getattr(_module(module_name), builder_name)
    built = builder()
    # Golden #3 also returns its evidence/context/resolution audit artifacts.
    # The World Graph is still the actual first tuple item, never a synthetic
    # reconstruction. The corresponding provenance artifacts remain typed.
    if not isinstance(built, tuple):
        return built, None
    graph, evidence_by_id, interaction_context, resolution = built
    evidence = []
    for item in evidence_by_id.values():
        raw = _json_value(item)
        evidence.append(raw)
    # Preserve every typed InteractionContext field for provenance audit.
    context = _json_value(interaction_context)
    artifacts = {"evidence": evidence, "interactionContext": context, "semanticResolution": _json_value(resolution)}
    return graph, artifacts


def build_graph(scenario_id: str) -> Any:
    return build_scenario(scenario_id)[0]


def graph_nodes(payload: dict[str, Any], view: str) -> list[dict[str, str]]:
    nodes: list[dict[str, str]] = []
    if view == "world":
        nodes.append({"id": payload["world"]["world_id"], "kind": "world", "label": "Personal world"})
        node_kinds = {"entities": "entity", "relationships": "relationship", "events": "event"}
        for kind in ("entities", "relationships", "events"):
            for item in payload[kind]:
                nodes.append({"id": item["id"], "kind": node_kinds[kind], "label": item.get("canonical_name") or item.get("summary") or item["id"]})
    else:
        for cognition in payload["cognitions"]:
            nodes.append({"id": cognition["id"], "kind": "cognition", "label": cognition["content"]})
        typed_evidence = {item["id"]: item for item in payload.get("provenanceArtifacts", {}).get("evidence", [])}
        evidence_ids = sorted({link["evidence_id"] for c in payload["cognitions"] for link in c["sources"]} | {eid for event in payload["events"] for eid in event["evidence_ids"]})
        nodes.extend({"id": evidence_id, "kind": "spoken-evidence" if evidence_id in typed_evidence else "evidence-reference", "label": f"{evidence_id} · spoken Evidence" if evidence_id in typed_evidence else f"{evidence_id} · reference only"} for evidence_id in evidence_ids)
        artifacts = payload.get("provenanceArtifacts")
        if artifacts:
            nodes.append({"id": artifacts["interactionContext"]["id"], "kind": "interaction-context", "label": "Assistant InteractionContext · non-evidence"})
            nodes.append({"id": artifacts["semanticResolution"]["id"], "kind": "semantic-resolution", "label": "SemanticResolution · user affirmation"})
    return nodes


def graph_edges(payload: dict[str, Any], view: str) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    if view == "world":
        world_id = payload["world"]["world_id"]
        edges.extend({"from": world_id, "to": item["id"], "kind": "contains"} for item in payload["entities"])
        for relationship in payload["relationships"]:
            edges.extend((
                {"from": relationship["id"], "to": relationship["source_entity_id"], "kind": relationship["relation_type"]},
                {"from": relationship["id"], "to": relationship["target_entity_id"], "kind": relationship["relation_type"]},
            ))
        for event in payload["events"]:
            for participant in event["participants"]:
                edges.append({"from": event["id"], "to": participant["entity_id"], "kind": participant.get("role") or "participant"})
            for relationship_id in event["relationship_ids"]:
                edges.append({"from": event["id"], "to": relationship_id, "kind": "event relationship"})
    else:
        for cognition in payload["cognitions"]:
            for source in cognition["sources"]:
                edges.append({"from": source["evidence_id"], "to": cognition["id"], "kind": source["relation"]})
        artifacts = payload.get("provenanceArtifacts")
        if artifacts:
            context_id = artifacts["interactionContext"]["id"]
            resolution_id = artifacts["semanticResolution"]["id"]
            edges.append({"from": context_id, "to": resolution_id, "kind": "context only; non-evidence"})
            edges.append({"from": artifacts["semanticResolution"]["evidence_id"], "to": resolution_id, "kind": "resolved from spoken Evidence"})
            for cognition in payload["cognitions"]:
                if artifacts["semanticResolution"]["evidence_id"] in {item["evidence_id"] for item in cognition["sources"]}:
                    edges.append({"from": resolution_id, "to": cognition["id"], "kind": "resolved support for cognition"})
    return edges


def safe_model_status(repo_root: Path) -> dict[str, Any]:
    """Expose a minimal local-model status without reading keys or logs."""
    state_path = repo_root / ".local" / "state" / "server.json"
    result: dict[str, Any] = {
        "required": False,
        "stage0": "Not required",
        "managed": False,
        "state": "not-configured",
        "verification": "Not verified; the legacy diagnostic console does not require a local model.",
    }
    if not state_path.is_file():
        return result
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pid = int(state.get("pid", 0))
        port = int(state.get("port", 0))
        host = str(state.get("bindAddress", "127.0.0.1"))
        alias = str(state.get("alias", "local-model"))
        alive = False
        if host == "127.0.0.1" and 0 < port < 65536:
            try:
                with socket.create_connection((host, port), timeout=0.25):
                    alive = True
            except OSError:
                alive = False
        result.update({
            "stateFilePresent": True,
            "pid": pid,
            "alias": alias,
            "endpoint": f"http://{host}:{port}/v1",
            "state": "reachable-but-unverified" if alive else "stale-or-unreachable",
            "verification": "Unverified: state file and TCP reachability do not establish managed-model health.",
        })
    except (OSError, ValueError, json.JSONDecodeError):
        result["state"] = "state-unreadable"
    return result


def json_diff(left: Any, right: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Small, deterministic structural diff suitable for a review workbench."""
    if type(left) is not type(right):
        return [{"path": prefix or "$", "before": left, "after": right}]
    if isinstance(left, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right)):
            path = f"{prefix}.{key}" if prefix else key
            if key not in left:
                changes.append({"path": path, "before": None, "after": right[key]})
            elif key not in right:
                changes.append({"path": path, "before": left[key], "after": None})
            else:
                changes.extend(json_diff(left[key], right[key], path))
        return changes
    if isinstance(left, list):
        changes = []
        for index in range(max(len(left), len(right))):
            path = f"{prefix}[{index}]"
            if index >= len(left):
                changes.append({"path": path, "before": None, "after": right[index]})
            elif index >= len(right):
                changes.append({"path": path, "before": left[index], "after": None})
            else:
                changes.extend(json_diff(left[index], right[index], path))
        return changes
    return [] if left == right else [{"path": prefix or "$", "before": left, "after": right}]


class LabService:
    """State boundary for legacy diagnostics and the capability-1 adapter."""

    def __init__(
        self,
        state_dir: Path,
        *,
        repo_root: Path = REPO_ROOT,
        retention: int = 50,
        memory_run_executor: Callable[[Any, tuple[Any, ...], Collection[str]], Any] | None = None,
        answer_client_factory: Callable[[], Any] | None = None,
        chat_client_factory: Callable[[], Any] | None = None,
        correction_client_factory: Callable[[], Any] | None = None,
        meaning_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.state_dir = state_dir
        self.repo_root = repo_root
        self.retention = retention
        self.state_path = state_dir / "state.json"
        self.review_path = state_dir / "owner-verdicts.jsonl"
        self.memory_evaluation_path = state_dir / "memory-evaluations.jsonl"
        self.memory_world_path = state_dir / "memory-world.sqlite3"
        # This is deliberately a narrow test seam.  The production path below
        # always constructs the fixed local-model extractor; tests inject a
        # deterministic executor and never need a running model.
        self._memory_run_executor = memory_run_executor
        self._answer_client_factory = answer_client_factory
        self._chat_client_factory = chat_client_factory
        self._correction_client_factory = correction_client_factory
        self._meaning_client_factory = meaning_client_factory
        self._persistent_memory_loop: Any | None = None
        self._persistent_identity_authority: Any | None = None
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()
        self._reload_reviews_from_ledger()

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {
                "schemaVersion": 2,
                "runs": [],
                "reviews": [],
                "memoryRuns": [],
                # The chat transcript is deliberately Lab state, not memory
                # evidence.  It gives the workbench one normal conversation
                # without turning the experiment into a multi-user product.
                "chatSession": {"id": "chat:next-lab-owner", "turns": []},
                "updatedAt": utc_now(),
            }
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or not isinstance(loaded.get("runs"), list):
                raise ValueError("invalid lab state")
            loaded.setdefault("reviews", [])
            loaded.setdefault("memoryRuns", [])
            chat_session = loaded.setdefault("chatSession", {"id": "chat:next-lab-owner", "turns": []})
            if not isinstance(chat_session, dict) or not isinstance(chat_session.get("id"), str) or not isinstance(chat_session.get("turns"), list):
                raise ValueError("invalid lab chat session")
            return loaded
        except (OSError, ValueError, json.JSONDecodeError):
            # Preserve the unreadable file for audit rather than overwriting it.
            return {
                "schemaVersion": 2,
                "runs": [],
                "reviews": [],
                "memoryRuns": [],
                "chatSession": {"id": "chat:next-lab-owner", "turns": []},
                "updatedAt": utc_now(),
                "stateWarning": "previous-state-unreadable",
            }

    def _save(self) -> None:
        self.state["updatedAt"] = utc_now()
        temporary = self.state_path.with_name(f"{self.state_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(self.state_path)
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)

    def _add_state_warning(self, warning: str) -> None:
        warnings = self.state.setdefault("stateWarnings", [])
        if warning not in warnings:
            warnings.append(warning)
        # Keep the original single-warning field available to existing callers.
        self.state["stateWarning"] = "; ".join(warnings)

    def _read_review_ledger(self) -> tuple[list[dict[str, Any]], list[str]]:
        """Read the canonical append-only JSONL ledger.

        A torn final write is explicitly reported and ignored. A malformed
        middle line stops reconciliation, because records after it cannot be
        treated as a trustworthy contiguous ledger.
        """
        if not self.review_path.is_file():
            return [], []
        lines = self.review_path.read_text(encoding="utf-8").splitlines()
        nonempty = [index for index, line in enumerate(lines) if line.strip()]
        last_record = nonempty[-1] if nonempty else -1
        records: list[dict[str, Any]] = []
        warnings: list[str] = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("review record is not an object")
                records.append(record)
            except (ValueError, json.JSONDecodeError):
                if index == last_record:
                    warnings.append("owner-verdict-ledger-tail-unreadable")
                else:
                    warnings.append("owner-verdict-ledger-middle-unreadable")
                break
        return records, warnings

    def _reload_reviews_from_ledger(self) -> list[dict[str, Any]]:
        records, warnings = self._read_review_ledger()
        self.state["reviews"] = records
        for warning in warnings:
            self._add_state_warning(warning)
        return records

    def scenarios(self) -> dict[str, Any]:
        self._reload_reviews_from_ledger()
        latest = {item["scenarioId"]: item for item in self.state["reviews"]}
        baselines = {item.get("baselineFor"): item["id"] for item in self.state["runs"] if item.get("baselineFor")}
        return {"scenarios": [{"id": scenario_id, "checkCount": len(meta["checks"]), "manualOracle": True, "semanticStatus": meta.get("semanticStatus", "manual-oracle-awaiting-owner"), "latestVerdict": latest.get(scenario_id, {}).get("verdict"), "accepted": False, "needsDiscussion": latest.get(scenario_id, {}).get("verdict") == "needs-discussion", "baselineRunId": baselines.get(scenario_id), **{key: value for key, value in meta.items() if key in {"title", "summary", "boundaryQuestion"}}} for scenario_id, meta in SCENARIOS.items()]}

    def status(self) -> dict[str, Any]:
        self._reload_reviews_from_ledger()
        return {
            "stage": "Capability 1 · Continue same object",
            "host": "127.0.0.1",
            "workbench": "MemoWeft Next Lab",
            "pipeline": [
                {"name": "Evidence", "state": "active", "detail": "Only the current user turn may become new Evidence."},
                {"name": "Meaning", "state": "active", "detail": "The model proposes spans, handles and statement kind; the program validates them."},
                {"name": "Identity", "state": "active", "detail": "Accepted exact mentions bind to stable entities without kind-specific word patches."},
                {"name": "Review", "state": "active", "detail": "Owner acceptance is the only accepted-world write boundary."},
                {"name": "Apply", "state": "active", "detail": "World, Evidence and identity binding commit in one SQLite transaction."},
                {"name": "Recall", "state": "paused", "detail": "No recall-ranking expansion before capability 1 Owner dogfood."},
                {"name": "Legacy fixtures", "state": "diagnostic-only", "detail": "Old Golden scenarios remain evidence tools, not product stages or gates."},
            ],
            "model": safe_model_status(self.repo_root),
            "runCount": len(self.state["runs"]),
            "reviewCount": len(self.state["reviews"]),
            "stale": bool(self.state.get("stateWarning")),
            "stateWarnings": self.state.get("stateWarnings", []),
            "routeDocument": "docs/next/product-vertical-route.md",
            "reproducibility": reproducibility_snapshot(self.repo_root),
            "environment": {"python": sys.version.split()[0], "stateDir": ".local/next-lab", "retention": self.retention},
        }

    def _assert_scenario_ids(self, scenario_ids: Any) -> list[str]:
        if not isinstance(scenario_ids, list) or not scenario_ids or not all(isinstance(item, str) and item in SCENARIOS for item in scenario_ids):
            raise ValueError("scenarioIds must be a non-empty list of allowlisted scenario IDs")
        return list(dict.fromkeys(scenario_ids))

    def run(self, scenario_ids: Any, *, only_checks: dict[str, list[str]] | None = None, rerun_of: str | None = None) -> dict[str, Any]:
        ids = self._assert_scenario_ids(scenario_ids)
        scenario_runs: list[dict[str, Any]] = []
        for scenario_id in ids:
            metadata = SCENARIOS[scenario_id]
            try:
                graph, artifacts = build_scenario(scenario_id)
                payload = graph_payload(graph, artifacts)
            except Exception as exc:
                scenario_runs.append({"scenarioId": scenario_id, "state": "failed", "error": f"{type(exc).__name__}: {exc}", "checks": []})
                continue
            module = _module(metadata["module"])
            selected = set(only_checks.get(scenario_id, []) if only_checks else metadata["checks"])
            check_runs: list[dict[str, Any]] = []
            for check_name in metadata["checks"]:
                if check_name not in selected:
                    check_runs.append({"id": check_name, "state": "not-run"})
                    continue
                try:
                    getattr(module, check_name)()
                    check_runs.append({"id": check_name, "state": "passed"})
                except Exception as exc:  # the run must remain inspectable if a Golden fails
                    check_runs.append({"id": check_name, "state": "failed", "error": f"{type(exc).__name__}: {exc}"})
            source = TEST_ROOT / f"{metadata['module']}.py"
            scenario_runs.append({"scenarioId": scenario_id, "title": metadata["title"], "manualOracle": True, "semanticStatus": metadata.get("semanticStatus", "manual-oracle-awaiting-owner"), "boundaryQuestion": metadata["boundaryQuestion"], "definitionHash": scenario_definition_hash(scenario_id), "sourceHash": file_hash(source), "provenanceArtifactsHash": canonical_hash(payload["provenanceArtifacts"]) if "provenanceArtifacts" in payload else None, "graph": payload, "worldHash": canonical_hash(payload), "checks": check_runs, "views": {"world": {"nodes": graph_nodes(payload, "world"), "edges": graph_edges(payload, "world")}, "provenance": {"nodes": graph_nodes(payload, "provenance"), "edges": graph_edges(payload, "provenance")}}})
        created = utc_now()
        reproducibility = reproducibility_snapshot(self.repo_root)
        run = {"id": f"run-{uuid.uuid4()}", "createdAt": created, "kind": "manual-golden-check", "partial": only_checks is not None, "executedScope": {"scenarios": ids, "checks": only_checks}, "rerunOf": rerun_of, "scenarios": scenario_runs, "pinned": False, "environment": self.status()["environment"], "reproducibility": reproducibility}
        run["identity"] = canonical_hash({"scope": run["executedScope"], "scenarioHashes": [(item["scenarioId"], item.get("definitionHash"), item.get("sourceHash"), item.get("worldHash"), item.get("provenanceArtifactsHash")) for item in scenario_runs], "repro": reproducibility})
        self.state["runs"].append(run)
        self._prune_runs()
        self._save()
        return run

    def _find_run(self, run_id: Any) -> dict[str, Any]:
        if not isinstance(run_id, str):
            raise ValueError("runId must be a string")
        for run in self.state["runs"]:
            if run["id"] == run_id:
                return cast(dict[str, Any], run)
        raise KeyError("unknown retained run ID")

    def _prune_runs(self) -> None:
        reviewed = {item["runId"] for item in self.state["reviews"]}
        removable = [run for run in self.state["runs"] if not run.get("pinned") and run["id"] not in reviewed]
        while len(self.state["runs"]) > self.retention and removable:
            target = removable.pop(0)
            self.state["runs"].remove(target)

    def runs(self) -> dict[str, Any]:
        current = reproducibility_snapshot(self.repo_root)
        decorated = []
        for run in self.state["runs"]:
            reasons = [key for key in ("branch", "head", "dirtyFingerprint", "stageGate") if run.get("reproducibility", {}).get(key) != current.get(key)]
            recorded_fixture = run.get("reproducibility", {}).get("fixture", {})
            current_fixture = current["fixture"]
            if recorded_fixture.get("manifestHash") != current_fixture.get("manifestHash"):
                reasons.append("fixture-manifest")
            for name in sorted(set(recorded_fixture.get("payloadActualHashes", {})) | set(current_fixture.get("payloadActualHashes", {}))):
                if recorded_fixture.get("payloadActualHashes", {}).get(name) != current_fixture.get("payloadActualHashes", {}).get(name):
                    reasons.append(f"fixture-payload:{name}")
            current_scenarios = current["scenarios"]
            for scenario in run.get("scenarios", []):
                scenario_id = scenario.get("scenarioId")
                expected = current_scenarios.get(scenario_id, {})
                if scenario.get("definitionHash") != expected.get("definitionHash"):
                    reasons.append(f"scenario-definition:{scenario_id}")
                if scenario.get("sourceHash") != expected.get("sourceHash"):
                    reasons.append(f"scenario-source:{scenario_id}")
            decorated.append({**run, "stale": bool(reasons), "staleReasons": reasons})
        return {"runs": decorated}

    def reviews(self) -> dict[str, Any]:
        return {"reviews": self._reload_reviews_from_ledger(), "stateWarnings": self.state.get("stateWarnings", [])}

    @staticmethod
    def _memory_model_config() -> Any:
        """The only real-model configuration exposed by the Stage-1 lab path."""
        from memoweft.llm.client import LLMConfig
        from memoweft.world.extractor import world_delta_response_format

        return LLMConfig(
            base_url="http://127.0.0.1:8012/v1",
            api_key="local-only",
            model="qwen3-14b-local",
            temperature=0.0,
            tier="local",
            enable_thinking=False,
            max_tokens=8192,
            response_format=world_delta_response_format(),
        )

    @staticmethod
    def _owner_only_base(world_id: str) -> Any:
        """Create a disposable base: the Owner exists, but nothing is retained."""
        from memoweft.world import Entity, MemoryWorldGraph, PersonalWorld

        owner = Entity(id="entity:owner", world_id=world_id, kind="person", canonical_name="Owner")
        return MemoryWorldGraph(world=PersonalWorld(world_id=world_id, owner_entity_id=owner.id), entities={owner.id: owner})

    def _memory_loop(self) -> Any:
        """Open the one intentionally local, durable experiment world lazily."""
        if self._persistent_memory_loop is None:
            from memoweft.world.loop import MemoryLoop

            self._persistent_memory_loop = MemoryLoop(
                self.memory_world_path,
                self._owner_only_base("world:next-lab-owner"),
            )
        if self._persistent_identity_authority is None:
            # The identity row is not an optional diagnostic any more.  It is
            # bootstrapped against this exact SQLite connection before the
            # product adapter can form a candidate, so acceptance can persist
            # the world change and the reviewed mention binding together.
            from memoweft.world.identity_store import PersistentIdentityAuthority

            self._persistent_identity_authority = PersistentIdentityAuthority(
                self._persistent_memory_loop.connection
            )
        return self._persistent_memory_loop

    def _identity_authority(self) -> Any:
        """Return a fresh identity view on the same connection as ``MemoryLoop``."""
        self._memory_loop()
        assert self._persistent_identity_authority is not None
        return self._persistent_identity_authority.reload()

    def _answer_client(self) -> Any:
        if self._answer_client_factory is not None:
            return self._answer_client_factory()
        from memoweft.llm.client import OpenAICompatClient

        return OpenAICompatClient(self._answer_model_config())

    def _chat_client(self) -> Any:
        """Create a normal-chat client without changing the recall-answer path."""
        if self._chat_client_factory is not None:
            return self._chat_client_factory()
        # Preserve the pre-chat deterministic test seam.  The real path below
        # is deliberately separate from memory_query's answer configuration.
        if self._answer_client_factory is not None:
            return self._answer_client_factory()
        from memoweft.llm.client import OpenAICompatClient

        return OpenAICompatClient(self._chat_model_config())

    def _correction_client(self) -> Any:
        """Create the independent, schema-bound natural-correction classifier."""
        if self._correction_client_factory is not None:
            return self._correction_client_factory()
        from memoweft.llm.client import OpenAICompatClient

        return OpenAICompatClient(self._correction_model_config())

    def _meaning_client(self) -> Any:
        """Create the strict, interpretation-only client for product writes."""
        if self._meaning_client_factory is not None:
            return self._meaning_client_factory()
        from memoweft.llm.client import OpenAICompatClient

        return OpenAICompatClient(self._meaning_model_config())

    @staticmethod
    def _answer_model_config() -> Any:
        """Keep answer generation separate from the WorldDelta extraction schema."""
        from memoweft.llm.client import LLMConfig

        return LLMConfig(
            base_url="http://127.0.0.1:8012/v1",
            api_key="local-only",
            model="qwen3-14b-local",
            temperature=0.2,
            tier="local",
            enable_thinking=True,
            max_tokens=1024,
            response_format=None,
        )

    @staticmethod
    def _chat_model_config() -> Any:
        """Fast, no-thinking configuration for normal conversational replies."""
        from memoweft.llm.client import LLMConfig

        return LLMConfig(
            base_url="http://127.0.0.1:8012/v1",
            api_key="local-only",
            model="qwen3-14b-local",
            temperature=0.3,
            tier="local",
            enable_thinking=False,
            max_tokens=512,
            response_format=None,
        )

    @staticmethod
    def _correction_model_config() -> Any:
        """Strict local classifier config; it cannot author replacement memory."""
        from memoweft.llm.client import LLMConfig
        from memoweft.world.correction import NATURAL_CORRECTION_RESPONSE_FORMAT

        return LLMConfig(
            base_url="http://127.0.0.1:8012/v1",
            api_key="local-only",
            model="qwen3-14b-local",
            temperature=0.0,
            tier="local",
            enable_thinking=False,
            max_tokens=1024,
            response_format=NATURAL_CORRECTION_RESPONSE_FORMAT,
        )

    @staticmethod
    def _meaning_model_config() -> Any:
        """Strict schema for language interpretation, never WorldDelta authoring."""
        from memoweft.llm.client import LLMConfig
        from memoweft.world.turn_meaning import product_turn_response_format

        return LLMConfig(
            base_url="http://127.0.0.1:8012/v1",
            api_key="local-only",
            model="qwen3-14b-local",
            temperature=0.0,
            tier="local",
            enable_thinking=False,
            max_tokens=2048,
            response_format=product_turn_response_format(),
        )

    def _chat_session(self) -> dict[str, Any]:
        """Return the one server-owned Lab transcript.

        The public chat endpoint never receives role-labelled turns.  Keeping
        this small seam here makes that ownership rule explicit and lets a
        browser refresh resume the same local experiment without treating the
        transcript itself as durable world memory.
        """
        session = self.state.get("chatSession")
        if not isinstance(session, dict) or not isinstance(session.get("id"), str) or not isinstance(session.get("turns"), list):
            raise ValueError("stored Lab chat session is invalid")
        return session

    @staticmethod
    def _chat_turn_payload(turn: dict[str, Any]) -> dict[str, str]:
        return {
            "turnId": cast(str, turn["turnId"]),
            "role": cast(str, turn["role"]),
            "content": cast(str, turn["content"]),
            "occurredAt": cast(str, turn["occurredAt"]),
        }

    def _chat_transcript(self) -> list[dict[str, str]]:
        session = self._chat_session()
        transcript: list[dict[str, str]] = []
        for index, raw_turn in enumerate(session["turns"]):
            if not isinstance(raw_turn, dict) or raw_turn.get("role") not in {"user", "assistant"}:
                raise ValueError(f"stored Lab chat turn {index} is invalid")
            if not isinstance(raw_turn.get("turnId"), str) or not isinstance(raw_turn.get("content"), str) or not isinstance(raw_turn.get("occurredAt"), str):
                raise ValueError(f"stored Lab chat turn {index} is invalid")
            transcript.append(self._chat_turn_payload(raw_turn))
        return transcript

    @staticmethod
    def _validate_chat_message(body: dict[str, Any]) -> str:
        if set(body) != {"message"}:
            raise ValueError("chat turns accept only a user message; roles and assistant text are server-owned")
        message = body.get("message")
        if not isinstance(message, str) or not message.strip() or len(message) > 4000:
            raise ValueError("message must be a non-empty string of at most 4000 characters")
        return message.strip()

    @staticmethod
    def _is_meta_reference_only(message: str) -> bool:
        """Recognize only a narrow, content-free reference to an earlier turn."""
        normalized = unicodedata.normalize("NFKC", message).casefold()
        start, end = 0, len(normalized)
        while start < end and (
            normalized[start].isspace() or unicodedata.category(normalized[start]).startswith("P")
        ):
            start += 1
        while end > start and (
            normalized[end - 1].isspace() or unicodedata.category(normalized[end - 1]).startswith("P")
        ):
            end -= 1
        return _META_REFERENCE_ONLY.fullmatch(normalized[start:end]) is not None

    @staticmethod
    def _extraction_window(transcript: list[dict[str, str]]) -> list[dict[str, str]]:
        """Keep a role-preserving recent history inside the extractor limit.

        The window is selected from the server-owned transcript, never from a
        caller-provided role list.  It retains whole turns (rather than slicing
        a sentence) so source spans and assistant context remain inspectable.
        """
        selected: list[dict[str, str]] = []
        characters = 0
        for turn in reversed(transcript):
            content = turn["content"]
            if selected and (len(selected) >= 20 or characters + len(content) > 24000):
                break
            selected.append(turn)
            characters += len(content)
        return list(reversed(selected))

    @staticmethod
    def _recall_projection(answer: Any) -> dict[str, Any]:
        projection = {
            "status": answer.status,
            "recalledEntities": [_json_value(item) for item in answer.recalled_entities],
            "recalledRelationships": [_json_value(item) for item in answer.recalled_relationships],
            "recalledEvents": [_json_value(item) for item in answer.recalled_events],
            "recalledCognitions": [_json_value(item) for item in answer.recalled_cognitions],
            "evidence": [_json_value(item) for item in answer.evidence_context],
            "historyCognitionIds": list(answer.history_cognition_ids),
        }
        if answer.reconstruction is not None and answer.reconstruction.status == "resolved":
            projection["recalledHistoryCognitions"] = [
                _json_value(item) for item in answer.recalled_history_cognitions
            ]
            projection["reconstruction"] = _json_value(answer.reconstruction)
        return projection

    @staticmethod
    def _chat_memory_context(recall: dict[str, Any]) -> str:
        """Render selected world claims for chat without raw Evidence or ids."""
        if recall["status"] == "no_memory":
            return "（当前没有已接受、可召回的长期记忆。）"
        if recall["status"] == "ambiguous":
            return "（存在多个同等相关的已接受经历，本轮无法可靠确定指的是哪一个。）"
        entity_names = {
            item.get("id"): item.get("canonical_name")
            for item in recall.get("recalledEntities", [])
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("canonical_name"), str)
        }
        lines: list[str] = []
        for relationship in recall.get("recalledRelationships", []):
            if not isinstance(relationship, dict):
                continue
            source = entity_names.get(relationship.get("source_entity_id"), "相关人物")
            target = entity_names.get(relationship.get("target_entity_id"), "相关人物")
            relation_type = relationship.get("relation_type")
            if isinstance(relation_type, str):
                lines.append(f"- 关系：{source} 与 {target}（{relation_type}）")
        for event in recall.get("recalledEvents", []):
            if not isinstance(event, dict):
                continue
            summary = event.get("summary")
            occurred_at = event.get("occurred_at")
            if isinstance(summary, str) and summary.strip():
                suffix = f"（{occurred_at}）" if isinstance(occurred_at, str) else ""
                lines.append(f"- 事件：{summary.strip()}{suffix}")
            for facet in event.get("facets", []):
                if not isinstance(facet, dict):
                    continue
                key, value = facet.get("key"), facet.get("value")
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                about = entity_names.get(facet.get("about_entity_id"))
                label = f"{key}（{about}）" if isinstance(about, str) else key
                lines.append(f"  - {label}：{value}")
        for heading, key in (
            ("当前认知", "recalledCognitions"),
            ("相关历史认知", "recalledHistoryCognitions"),
        ):
            for cognition in recall.get(key, []):
                if not isinstance(cognition, dict):
                    continue
                content = cognition.get("content")
                if isinstance(content, str) and content.strip():
                    lines.append(f"- {heading}：{content.strip()}")
        if not lines:
            return "（本轮没有可直接用于回答的已接受认知。）"
        return "\n".join(lines)

    def _chat_reply(self, transcript: list[dict[str, str]], recall: dict[str, Any]) -> str:
        from memoweft.llm.client import ChatMessage

        recent_turns = self._extraction_window(transcript)
        messages = [
            ChatMessage(
                "system",
                "你是 MemoWeft 本地聊天工作台里的 AI。直接对“你”说话，"
                "不得称呼对方为“用户”，也不得用第三人称描述对方。"
                "除非对方明确询问，否则不得提及内部召回、长期记忆、候选、提取、记录或是否需要记录；"
                "这些状态由界面单独展示。只输出回复正文，不输出解释、标签、思考过程或工作台状态。"
                "不得使用“用户提到”、“根据当前记忆”、“记忆库”、“长期记忆”或“是否需要记录”等短语。"
                "先回应最后一条消息本身，再自然、简洁地继续对话。"
                "不要把候选记忆、模型推断或未被对方说出的内容说成事实。"
                "先前 assistant 的话只表示它当时说过什么，并不证明其中的事实；"
                "如果它与对方后来所说的内容冲突，以对方当前的表述为准。"
                "不得因为两个对象属于同一类别就声称它们相似，也不得补写未经明确陈述的比较。"
                "当最后一句用“刚才、刚刚、之前、前面已经告诉、说过、提过或讲过”等方式指代上下文时，"
                "必须从最近真实 user 消息中找出具体内容，明确复述那条内容后再回应；"
                "不得只泛泛确认或重复追问已经回答的问题。",
            ),
            ChatMessage(
                "system",
                "以下是本轮召回的已接受记忆背景。它只可用于直接相关的事实，"
                "不得扩写、类比或与别的对象进行未经陈述的比较：\n"
                f"{self._chat_memory_context(recall)}",
            ),
        ]
        messages.extend(
            ChatMessage(cast(Literal["user", "assistant"], turn["role"]), turn["content"])
            for turn in recent_turns
        )
        reply = self._chat_client().chat(messages)
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError("local chat model returned an empty reply")
        return reply.strip()

    def _meta_reference_no_candidate(
        self,
        *,
        run_id: str,
        turns: tuple[ConversationTurn, ...],
        view: Any,
    ) -> dict[str, Any]:
        """Retain an inspectable no-candidate run without calling extraction.

        The current user turn stays in the chat transcript, but a pure pointer
        such as “刚才告诉你了” carries no standalone proposition that should be
        written to the Evidence ledger or staged as long-term memory.
        """
        empty_memory: dict[str, list[Any]] = {
            "entities": [], "relationships": [], "events": [], "cognitions": [],
        }
        result = {
            "id": run_id,
            "createdAt": utc_now(),
            "title": "聊天式工作台 · 当前回合上下文指代",
            "state": "no-candidate",
            "filter": "meta-reference-only",
            "baseRevision": view.revision,
            "baseWorldHash": view.snapshot_hash,
            "previewWorldHash": view.snapshot_hash,
            "baseUnchanged": True,
            "turns": [
                {
                    "turnId": turn.turn_id,
                    "role": turn.role,
                    "content": turn.content,
                    "occurredAt": turn.occurred_at,
                }
                for turn in turns
            ],
            "assistantContext": [
                {"turnId": turn.turn_id, "text": turn.content, "note": "仅作上下文，不作为用户证据"}
                for turn in turns
                if turn.role == "assistant"
            ],
            "evidencePolicy": "纯上下文指代只保留在聊天记录中，不进入 Evidence ledger。",
            "candidateMemory": empty_memory,
            "evidence": [],
            "formation": [],
            "unresolvedReferences": [],
            "semanticUncertainties": [],
            "pipeline": [
                {"name": "Evidence", "state": "context-only", "detail": "当前话语只指向先前上下文，不含独立记忆内容。"},
                {"name": "Correction", "state": "not-correction", "detail": "本轮没有形成对当前认知的替代计划。"},
                {"name": "Extract", "state": "skipped", "detail": "可信预过滤已阻止把纯元话语写成 stated cognition。"},
                {"name": "Review", "state": "not-needed", "detail": "没有候选记忆可复核。"},
                {"name": "Apply", "state": "not-applied", "detail": "当前长期世界未改变。"},
            ],
        }
        self.state["memoryRuns"].append(result)
        self._save()
        return result

    def _stage_natural_correction(
        self,
        *,
        run_id: str,
        plan: Any,
        current_user_turn: ConversationTurn,
        view: Any,
    ) -> dict[str, Any]:
        """Stage a trusted replacement bundle while preserving every prior."""
        from memoweft.world.loop import EvidenceRecord

        current_by_id = {item.id: item for item in view.current_cognitions}
        priors = tuple(current_by_id[item_id] for item_id in plan.prior_cognition_ids)
        notices = [
            "接受后，所选旧认知会保留在历史中，并由一条新的当前认知替代。",
            "结构提示只供你复核；本阶段不会自动修改实体类别。",
        ]
        pending = self._memory_loop().stage_correction_bundle(
            plan.prior_cognition_ids,
            plan.replacement_text,
            EvidenceRecord(plan.evidence_id, plan.replacement_text),
            review_payload={
                "runId": run_id,
                "kind": "correction",
                "structureHints": list(plan.structure_hints),
            },
        )
        replacement_preview = {
            "previewOnly": True,
            "content": plan.replacement_text,
            "content_type": priors[0].content_type,
            "formed_by": "stated",
            "target": _json_value(plan.target),
            "perspective": _json_value(plan.perspective),
            "sources": [{"evidence_id": plan.evidence_id, "relation": "support"}],
            "supersedes": list(plan.prior_cognition_ids),
        }
        result = {
            "id": run_id,
            "createdAt": utc_now(),
            "title": "聊天式工作台 · 当前回合自然纠正",
            "state": "correction-pending",
            "reviewId": pending.id,
            "resultHash": pending.result_hash,
            "baseRevision": pending.base_revision,
            "baseWorldHash": view.snapshot_hash,
            "baseUnchanged": True,
            "turns": [{
                "turnId": current_user_turn.turn_id,
                "role": current_user_turn.role,
                "content": current_user_turn.content,
                "occurredAt": current_user_turn.occurred_at,
            }],
            "evidencePolicy": "只有当前 user turn 是本次纠正 Evidence；assistant 仅作上下文。",
            "candidateMemory": {
                "entities": [],
                "relationships": [],
                "events": [],
                "cognitions": [replacement_preview],
            },
            "evidence": [{"evidenceId": plan.evidence_id, "text": plan.replacement_text}],
            "correction": {
                "supersededCognitions": [_json_value(item) for item in priors],
                "replacementContent": plan.replacement_text,
                "structureHints": list(plan.structure_hints),
                "structuralNotices": notices,
            },
            "pipeline": [
                {"name": "Evidence", "state": "ready", "detail": "当前 user turn 是这次纠正的唯一 Evidence。"},
                {"name": "Correction", "state": "awaiting-owner", "detail": "自然纠正已形成替代候选，等待接受或拒绝。"},
                {"name": "Extract", "state": "skipped", "detail": "本轮走纠正边界，不再运行普通新增提取。"},
                {"name": "Review", "state": "awaiting-owner", "detail": "当前长期世界保持不变。"},
                {"name": "Apply", "state": "awaiting-owner", "detail": "接受后才会原子替换，并保留历史。"},
            ],
        }
        self.state["memoryRuns"].append(result)
        self._save()
        return result

    def _natural_correction_failure(
        self,
        *,
        run_id: str,
        code: str,
        current_user_turn: ConversationTurn,
        view: Any,
    ) -> dict[str, Any]:
        """Retain one safe failure and explicitly block addition fallback."""
        result = {
            "id": run_id,
            "createdAt": utc_now(),
            "title": "聊天式工作台 · 当前回合自然纠正",
            "state": "failed",
            "baseRevision": view.revision,
            "baseWorldHash": view.snapshot_hash,
            "baseUnchanged": True,
            "turns": [{
                "turnId": current_user_turn.turn_id,
                "role": current_user_turn.role,
                "content": current_user_turn.content,
                "occurredAt": current_user_turn.occurred_at,
            }],
            "failure": {"kind": "NaturalCorrectionError", "codes": [code], "attempts": 1},
            "pipeline": [
                {"name": "Evidence", "state": "ready", "detail": "当前 user turn 已被识别为潜在纠正来源。"},
                {"name": "Correction", "state": "failed", "detail": "纠正声明未通过本地验证。"},
                {"name": "Extract", "state": "blocked", "detail": "为避免把非法纠正误当新增记忆，本轮不回退普通提取。"},
                {"name": "Review", "state": "blocked", "detail": "没有安全候选可复核。"},
                {"name": "Apply", "state": "not-applied", "detail": "当前长期世界未改变。"},
            ],
        }
        self.state["memoryRuns"].append(result)
        self._save()
        return result

    @staticmethod
    def _proposal_projection(proposal: dict[str, Any]) -> dict[str, Any] | None:
        if proposal.get("state") not in {"candidate-ready", "correction-pending"}:
            return None
        return {
            key: proposal[key]
            for key in (
                "id", "state", "reviewId", "resultHash", "baseRevision", "candidateMemory", "evidence",
                "formation", "unresolvedReferences", "semanticUncertainties", "correction", "pipeline",
                "carryForward", "meaning", "identityBindings", "target", "statementKind", "ownerPerspective",
                "claims", "transitionIntents",
            )
            if key in proposal
        }

    @staticmethod
    def _product_claim_review_projection(
        claim_bundle: dict[str, Any],
        *,
        focal_entity_id: str | None,
        owner_entity_id: str,
        evidence_id: str,
    ) -> dict[str, Any]:
        """Add only presentation/audit fields to the compiler's claim bundle.

        ``ProductClaimBundle`` is intentionally language-level material, not a
        storage instruction.  The adapter therefore makes the target,
        perspective, exact Evidence span, and current writer boundary visible
        beside every claim before it is hash-bound into the SQLite proposal.
        In particular, naming and alias remain classified language material:
        this vertical writer does not accept them as independent name/alias
        mutations, even when an introduced entity itself has a canonical name.
        """
        raw_claims = claim_bundle.get("claims")
        if not isinstance(raw_claims, list):
            raise ValueError("product claim bundle claims must be a list")
        projected_claims: list[dict[str, Any]] = []
        first_legacy_attribute_lowered = False
        for raw in raw_claims:
            if not isinstance(raw, dict):
                raise ValueError("product claim bundle claim must be an object")
            item = dict(raw)
            kind = item.get("kind")
            if not isinstance(kind, str):
                raise ValueError("product claim bundle claim kind must be a string")
            start, end = item.get("start"), item.get("end")
            if type(start) is not int or type(end) is not int:
                raise ValueError("product claim bundle claim span must be integer offsets")
            if kind in {"naming", "alias"}:
                write_state = "unsupported"
                structured_status = "not-lowered"
            elif (
                kind in {"attribute", "relationship", "event", "evaluation"}
                and item.get("disposition") == "assert"
                and item.get("polarity") == "affirm"
                and item.get("epistemic_status") == "stated"
            ):
                write_state = "candidate"
                # The first compatible attribute is deliberately lowered by
                # the legacy safe writer inside the v2 compiler.  It is still
                # part of this same reviewed SQLite bundle, but it has no
                # StructuredClaim yet; do not misrepresent it to the Owner.
                if kind == "attribute" and not first_legacy_attribute_lowered:
                    first_legacy_attribute_lowered = True
                    structured_status = "legacy-unstructured"
                else:
                    structured_status = "candidate"
            else:
                write_state = "not-written"
                structured_status = "not-lowered"
            item.update({
                # "object" is a semantic target descriptor, not a model
                # supplied record identifier.  SQLite still validates the
                # actual delta independently when the bundle is staged.
                "object": {
                    "kind": "entity",
                    "entityId": focal_entity_id,
                },
                "perspective": {
                    "kind": "entity",
                    "holderEntityIds": [owner_entity_id],
                },
                "evidence": {
                    "evidenceId": evidence_id,
                    "span": {"start": start, "end": end},
                },
                "structuredStatus": structured_status,
                "writeState": write_state,
            })
            projected_claims.append(item)
        return {**claim_bundle, "claims": projected_claims}

    def chat_session(self) -> dict[str, Any]:
        """Read the persisted single-owner Lab conversation and current world."""
        session = self._chat_session()
        return {
            "sessionId": session["id"],
            "transcript": self._chat_transcript(),
            "world": self.memory_world(),
        }

    def _stage_current_user_memory(
        self,
        *,
        run_id: str,
        title: str,
        turns: tuple[ConversationTurn, ...],
        carry_forward_evidence_ids: Collection[str] = (),
    ) -> dict[str, Any]:
        """Use explicitly authorized user Evidence for chat/adapter staging.

        Both chat entry points retain real typed history for model context, but
        neither grants old user turns or assistant turns Evidence authority.
        The adapter may separately authorize prior unresolved user Evidence;
        those IDs are caller-visible, state-validated, and never inferred from
        message wording.
        Keeping correction classification and ordinary addition together here
        prevents the legacy-console bridge from drifting into a second, weaker
        memory semantics.
        """
        from memoweft.world.correction import NaturalCorrectionError, NaturalCorrectionProposer

        if not turns or turns[-1].role != "user":
            raise ValueError("current typed memory staging requires a final user turn")
        current_user_turn = turns[-1]
        carried = frozenset(carry_forward_evidence_ids)
        if carried:
            # An explicit unresolved continuation is an addition/extraction
            # bundle, not a natural correction of the accepted world.  Running
            # it directly also guarantees that every authorized source reaches
            # the extractor instead of being silently ignored by a classifier.
            return self._memory_run_from_typed_turns(
                run_id=run_id,
                title=title,
                turns=turns,
                evidence_allowlist=carried | {current_user_turn.turn_id},
                owner_notes={},
            )
        current_view = self._memory_loop().view()
        preceding_assistant_turn = turns[-2] if len(turns) >= 2 and turns[-2].role == "assistant" else None
        try:
            correction_plan = NaturalCorrectionProposer(self._correction_client()).propose(
                current_view,
                current_user_turn,
                preceding_assistant_turn,
            )
        except NaturalCorrectionError as exc:
            return self._natural_correction_failure(
                run_id=run_id,
                code=exc.code,
                current_user_turn=current_user_turn,
                view=current_view,
            )
        except Exception:
            return self._natural_correction_failure(
                run_id=run_id,
                code="correction_boundary_failed",
                current_user_turn=current_user_turn,
                view=current_view,
            )
        if correction_plan is not None:
            try:
                return self._stage_natural_correction(
                    run_id=run_id,
                    plan=correction_plan,
                    current_user_turn=current_user_turn,
                    view=current_view,
                )
            except Exception:
                return self._natural_correction_failure(
                    run_id=run_id,
                    code="correction_stage_rejected",
                    current_user_turn=current_user_turn,
                    view=current_view,
                )
        if self._is_meta_reference_only(current_user_turn.content):
            return self._meta_reference_no_candidate(
                run_id=run_id,
                turns=turns,
                view=current_view,
            )
        return self._memory_run_from_typed_turns(
            run_id=run_id,
            title=title,
            turns=turns,
            evidence_allowlist=frozenset({current_user_turn.turn_id}),
            owner_notes={},
        )

    def chat_turn(self, body: dict[str, Any]) -> dict[str, Any]:
        """Append one user message, generate a reply, then stage (never apply) memory.

        The sequencing is intentional: ordinary chat works with no accepted
        memory, and extraction is an inspectable secondary operation.  An
        extraction failure is returned as a safe proposal failure after the
        assistant reply, so it never turns into a broken chat session.
        """
        from memoweft.world import ConversationTurn

        message = self._validate_chat_message(body)
        session = self._chat_session()
        history_before = self._chat_transcript()
        loop = self._memory_loop()
        recall = self._recall_projection(loop.ask(message))
        user_turn = {
            "turnId": f"turn:{uuid.uuid4()}",
            "role": "user",
            "content": message,
            "occurredAt": utc_now(),
        }
        assistant_text = self._chat_reply([*history_before, user_turn], recall)
        assistant_turn = {
            "turnId": f"turn:{uuid.uuid4()}",
            "role": "assistant",
            "content": assistant_text,
            "occurredAt": utc_now(),
        }
        session["turns"].extend((user_turn, assistant_turn))
        # Extraction/classification belongs to the current user turn.  The
        # assistant answer generated above is retained in the server transcript
        # for the next round, but cannot steer this round's memory proposal.
        extraction_turns = self._extraction_window([*history_before, user_turn])
        typed_turns = tuple(
            ConversationTurn(
                turn_id=turn["turnId"],
                conversation_id=cast(str, session["id"]),
                role=cast(Literal["user", "assistant"], turn["role"]),
                content=turn["content"],
                occurred_at=turn["occurredAt"],
            )
            for turn in extraction_turns
        )
        run_id = f"memory-run-{uuid.uuid4()}"
        proposal = self._stage_current_user_memory(
            run_id=run_id,
            title="聊天式工作台 · 当前回合候选记忆",
            turns=typed_turns,
        )
        proposal_failure = proposal.get("failure") if proposal.get("state") == "failed" else None
        proposal_state = proposal.get("state")
        pipeline_prefix = [
            {"name": "Recall", "state": recall["status"], "detail": "已从当前接受世界读取相关记忆。" if recall["status"] != "no_memory" else "当前没有可召回的已接受长期记忆；仍正常聊天。"},
            {"name": "Answer", "state": "succeeded", "detail": "本地 AI 已基于对话与已接受记忆回复。"},
        ]
        if proposal_state == "correction-pending" or (
            proposal_state == "failed" and proposal_failure is not None
            and proposal_failure.get("kind") == "NaturalCorrectionError"
        ):
            pipeline = [*pipeline_prefix, *proposal["pipeline"]]
        elif proposal.get("filter") == "meta-reference-only":
            pipeline = [*pipeline_prefix, *proposal["pipeline"]]
        else:
            extraction_state = "succeeded" if proposal_state == "candidate-ready" else "no-candidate" if proposal_state == "no-candidate" else "failed"
            review_state = "awaiting-owner" if extraction_state == "succeeded" else "not-needed" if extraction_state == "no-candidate" else "blocked"
            pipeline = [
                *pipeline_prefix,
                {"name": "Evidence", "state": "ready", "detail": "只有服务器保存的当前 user turn 可作为 Evidence；历史与 assistant 只作上下文。"},
                {"name": "Correction", "state": "not-correction", "detail": "本轮不是对已有认知的自然纠正，继续检查新增记忆。"},
                {"name": "Extract", "state": extraction_state, "detail": "已形成等待复核的候选记忆。" if extraction_state == "succeeded" else "本轮没有候选记忆。" if extraction_state == "no-candidate" else "提取失败；聊天回复仍可继续。"},
                {"name": "Review", "state": review_state, "detail": "候选尚未写入长期世界。" if extraction_state == "succeeded" else "没有候选，无需复核。" if extraction_state == "no-candidate" else "没有候选可复核。"},
                {"name": "Apply", "state": "awaiting-owner" if extraction_state == "succeeded" else "not-applied", "detail": "必须单独接受，绝不自动写入。"},
            ]
        proposal["pipeline"] = pipeline
        # Recall is created before the user turn is staged, so it is a
        # read-only observation of the accepted base world.  Persist that
        # exact structured projection with the inspection run as well as the
        # transient chat response; otherwise a browser refresh loses the
        # Recall stage for this turn even though every later pipeline stage is
        # retained.  This does not alter the proposal, Evidence, or world.
        proposal["recall"] = recall
        self._save()
        return {
            "sessionId": session["id"],
            "userTurn": user_turn,
            "assistantTurn": assistant_turn,
            "recall": recall,
            "memoryProposal": self._proposal_projection(proposal),
            "memoryFailure": proposal_failure,
            "world": self.memory_world(),
            "pipeline": pipeline,
        }

    @staticmethod
    def _world_snapshot(graph: Any) -> dict[str, Any]:
        return {
            "world": _json_value(graph.world),
            "entities": [_json_value(item) for item in sorted(graph.entities.values(), key=lambda item: item.id)],
            "relationships": [_json_value(item) for item in sorted(graph.relationships.values(), key=lambda item: item.id)],
            "events": [_json_value(item) for item in sorted(graph.events.values(), key=lambda item: item.id)],
            "cognitions": [_json_value(item) for item in sorted(graph.cognitions.values(), key=lambda item: item.id)],
        }

    @staticmethod
    def _validate_memory_scenario(body: dict[str, Any]) -> tuple[str, list[tuple[Literal["user", "assistant"], str]], dict[str, str]]:
        title = body.get("title", "未命名情景")
        if not isinstance(title, str) or not title.strip() or len(title) > 160:
            raise ValueError("title must be a non-empty string of at most 160 characters")
        raw_turns = body.get("turns")
        if not isinstance(raw_turns, list) or not 1 <= len(raw_turns) <= 20:
            raise ValueError("turns must contain 1 through 20 user/assistant turns")
        turns: list[tuple[Literal["user", "assistant"], str]] = []
        total = 0
        for index, item in enumerate(raw_turns):
            if not isinstance(item, dict) or set(item) != {"role", "content"}:
                raise ValueError(f"turns[{index}] must contain only role and content")
            role, content = item.get("role"), item.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip() or len(content) > 4000:
                raise ValueError(f"turns[{index}] requires user/assistant and 1..4000 non-blank characters")
            total += len(content)
            turns.append((cast(Literal["user", "assistant"], role), content))
        if total > 24000:
            raise ValueError("turn content exceeds the 24000-character local experiment limit")
        owner_notes: dict[str, str] = {}
        for key in ("expectedMemory", "laterQuestion", "expectedAnswer"):
            value = body.get(key, "")
            if not isinstance(value, str) or len(value) > 4000:
                raise ValueError(f"{key} must be a string of at most 4000 characters")
            if value.strip():
                owner_notes[key] = value
        return title, turns, owner_notes

    @staticmethod
    def _candidate_projection(delta: Any, ledger: dict[str, dict[str, str]]) -> dict[str, Any]:
        cognitions: list[dict[str, Any]] = []
        for cognition in delta.new_cognitions:
            item = cast(dict[str, Any], _json_value(cognition))
            item["evidence"] = [
                {"evidenceId": link.evidence_id, "text": ledger[link.evidence_id]["content"]}
                for link in cognition.sources
                if link.evidence_id in ledger
            ]
            cognitions.append(item)
        records = {
            "entities": [_json_value(item) for item in delta.new_entities],
            "relationships": [_json_value(item) for item in delta.new_relationships],
            "events": [_json_value(item) for item in delta.new_events],
            "cognitions": cognitions,
        }
        referenced = set(delta.source_evidence_ids)
        for event in delta.new_events:
            referenced.update(event.evidence_ids)
        for cognition in delta.new_cognitions:
            referenced.update(link.evidence_id for link in cognition.sources)
        for trace in delta.formation_traces:
            referenced.update(source.evidence_id for source in trace.sources)
        evidence = [
            {"evidenceId": evidence_id, "text": ledger[evidence_id]["content"]}
            for evidence_id in sorted(referenced)
            if evidence_id in ledger
        ]
        return {
            "candidateMemory": records,
            "evidence": evidence,
            "formation": [_json_value(item) for item in delta.formation_traces],
            "unresolvedReferences": [_json_value(item) for item in delta.unresolved_references],
            "semanticUncertainties": [_json_value(item) for item in delta.semantic_uncertainties],
        }

    def _execute_memory_run(self, base: Any, turns: tuple[Any, ...], allowlist: Collection[str]) -> Any:
        if self._memory_run_executor is not None:
            return self._memory_run_executor(base, turns, allowlist)
        from memoweft.llm.client import OpenAICompatClient
        from memoweft.world import WorldExtractor

        return WorldExtractor(OpenAICompatClient(self._memory_model_config())).extract(
            base,
            turns,
            allowlist,
            accepted_entity_references=(),
        )

    @staticmethod
    def _validate_adapter_memory_turns(
        body: dict[str, Any],
    ) -> tuple[str, str, str, tuple[str, ...], tuple[ConversationTurn, ...], str]:
        """Validate the local Node-to-Python bridge without widening chat input.

        This is intentionally separate from ``memory_run``: the legacy server
        already owns turn IDs, roles, timestamps, and session history.  We can
        accept that typed ledger only when its complete, bounded shape proves
        which final user turn is newly eligible as Evidence.
        """
        from memoweft.world import ConversationTurn

        expected = {
            "operationId", "sessionId", "currentUserTurnId",
            "carryForwardEvidenceIds", "turns",
        }
        if set(body) != expected:
            raise ValueError(
                "adapter memory turns require only operationId, sessionId, "
                "currentUserTurnId, carryForwardEvidenceIds, and turns"
            )
        operation_id = body.get("operationId")
        session_id = body.get("sessionId")
        current_user_turn_id = body.get("currentUserTurnId")
        for name, value in (
            ("operationId", operation_id),
            ("sessionId", session_id),
            ("currentUserTurnId", current_user_turn_id),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 200:
                raise ValueError(f"{name} must be a trimmed non-empty string of at most 200 characters")
        raw_carried = body.get("carryForwardEvidenceIds")
        if not isinstance(raw_carried, list) or len(raw_carried) > 8:
            raise ValueError("carryForwardEvidenceIds must be a list of at most 8 Evidence IDs")
        carried_ids: list[str] = []
        seen_carried: set[str] = set()
        for index, evidence_id in enumerate(raw_carried):
            if (
                not isinstance(evidence_id, str)
                or not evidence_id.strip()
                or evidence_id != evidence_id.strip()
                or len(evidence_id) > 200
            ):
                raise ValueError(
                    f"carryForwardEvidenceIds[{index}] must be a trimmed non-empty string of at most 200 characters"
                )
            if evidence_id in seen_carried:
                raise ValueError("carryForwardEvidenceIds must contain unique Evidence IDs")
            seen_carried.add(evidence_id)
            carried_ids.append(evidence_id)
        carried = tuple(sorted(carried_ids))
        if current_user_turn_id in seen_carried:
            raise ValueError("currentUserTurnId cannot be carried forward")
        raw_turns = body.get("turns")
        if not isinstance(raw_turns, list) or not 1 <= len(raw_turns) <= 20:
            raise ValueError("adapter turns must contain 1 through 20 typed user/assistant turns")
        turns: list[ConversationTurn] = []
        seen_ids: set[str] = set()
        total_characters = 0
        for index, item in enumerate(raw_turns):
            if not isinstance(item, dict) or set(item) != {"turnId", "role", "content", "occurredAt"}:
                raise ValueError(f"adapter turns[{index}] must contain only turnId, role, content, and occurredAt")
            turn_id, role, content, occurred_at = item.get("turnId"), item.get("role"), item.get("content"), item.get("occurredAt")
            if not isinstance(turn_id, str) or not turn_id.strip() or turn_id != turn_id.strip() or len(turn_id) > 200:
                raise ValueError(f"adapter turns[{index}].turnId must be a trimmed non-empty string of at most 200 characters")
            if turn_id in seen_ids:
                raise ValueError("adapter turns require unique turnId values")
            if role not in {"user", "assistant"}:
                raise ValueError(f"adapter turns[{index}].role must be user or assistant")
            if not isinstance(content, str) or not content.strip() or len(content) > 4000:
                raise ValueError(f"adapter turns[{index}].content must be a non-empty string of at most 4000 characters")
            if not isinstance(occurred_at, str) or not occurred_at.strip() or len(occurred_at) > 64:
                raise ValueError(f"adapter turns[{index}].occurredAt must be a non-empty ISO timestamp")
            try:
                parsed_time = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"adapter turns[{index}].occurredAt must be an ISO timestamp") from exc
            if parsed_time.tzinfo is None:
                raise ValueError(f"adapter turns[{index}].occurredAt must include a timezone")
            seen_ids.add(turn_id)
            total_characters += len(content)
            turns.append(ConversationTurn(
                turn_id=turn_id,
                conversation_id=cast(str, session_id),
                role=cast(Literal["user", "assistant"], role),
                content=content,
                occurred_at=occurred_at,
            ))
        if total_characters > 24000:
            raise ValueError("adapter turn content exceeds the 24000-character local experiment limit")
        if turns[-1].turn_id != current_user_turn_id or turns[-1].role != "user":
            raise ValueError("currentUserTurnId must reference the final user turn")
        turn_by_id = {turn.turn_id: turn for turn in turns}
        for evidence_id in carried:
            carried_turn = turn_by_id.get(evidence_id)
            if carried_turn is None:
                raise ValueError("carryForwardEvidenceIds must reference supplied typed turns")
            if carried_turn.role != "user":
                raise ValueError("only prior user turns may be carried forward")
            if carried_turn is turns[-1]:
                raise ValueError("carryForwardEvidenceIds must be earlier than currentUserTurnId")
        request_hash = canonical_hash({
            "sessionId": session_id,
            "currentUserTurnId": current_user_turn_id,
            "carryForwardEvidenceIds": list(carried),
            "turns": [
                {"turnId": turn.turn_id, "role": turn.role, "content": turn.content, "occurredAt": turn.occurred_at}
                for turn in turns
            ],
        })
        return (
            cast(str, operation_id),
            cast(str, session_id),
            cast(str, current_user_turn_id),
            carried,
            tuple(turns),
            request_hash,
        )

    def _authorize_adapter_carry_forward(
        self,
        *,
        session_id: str,
        turns: tuple[ConversationTurn, ...],
        evidence_ids: tuple[str, ...],
    ) -> tuple[dict[str, str], ...]:
        """Bind carry requests to prior unresolved, no-candidate adapter runs.

        Typed history alone is context and never grants Evidence authority.
        Each carried ID must be the exact current user Evidence of an earlier
        run in the same adapter session, must be cited by that run's unresolved
        references, and must not already have formed another candidate.
        """
        supplied_by_id = {turn.turn_id: turn for turn in turns}
        memory_runs = self.state.get("memoryRuns", [])
        if not isinstance(memory_runs, list):
            raise ValueError("stored memory runs are invalid")
        authorizations: list[dict[str, str]] = []
        for evidence_id in evidence_ids:
            matching: list[tuple[int, dict[str, Any]]] = []
            for run_index, raw_run in enumerate(memory_runs):
                if not isinstance(raw_run, dict):
                    continue
                adapter = raw_run.get("adapter")
                if (
                    isinstance(adapter, dict)
                    and adapter.get("sessionId") == session_id
                    and adapter.get("currentUserTurnId") == evidence_id
                ):
                    matching.append((run_index, raw_run))
            if len(matching) != 1:
                raise ValueError(
                    "carried Evidence must belong to exactly one prior adapter run in the same session"
                )
            run_index, source_run = matching[0]
            if source_run.get("state") != "no-candidate":
                raise ValueError("carried Evidence source run must be no-candidate")
            unresolved_ids = {
                unresolved_evidence_id
                for reference in source_run.get("unresolvedReferences", [])
                if isinstance(reference, dict)
                for unresolved_evidence_id in reference.get("evidence_ids", [])
                if isinstance(unresolved_evidence_id, str)
            }
            if evidence_id not in unresolved_ids:
                raise ValueError("carried Evidence must be cited by the source run's unresolvedReferences")
            if isinstance(source_run.get("carryForwardConsumption"), dict):
                raise ValueError("carried Evidence has already been consumed")

            source_turns = source_run.get("turns")
            if not isinstance(source_turns, list):
                raise ValueError("carried Evidence source run has no auditable typed turn")
            source_turn = next(
                (
                    item for item in source_turns
                    if isinstance(item, dict) and item.get("turnId") == evidence_id
                ),
                None,
            )
            supplied_turn = supplied_by_id[evidence_id]
            expected_turn = {
                "turnId": supplied_turn.turn_id,
                "role": supplied_turn.role,
                "content": supplied_turn.content,
                "occurredAt": supplied_turn.occurred_at,
            }
            if source_turn != expected_turn or supplied_turn.role != "user":
                raise ValueError("carried Evidence must exactly match its prior user turn")
            authorizations.append({
                "evidenceId": evidence_id,
                "sourceRunId": cast(str, source_run["id"]),
                "sessionId": session_id,
                "sourceState": "no-candidate",
                "reason": "unresolved-reference",
                "sourceRunOrdinal": str(run_index),
            })
        return tuple(authorizations)

    @staticmethod
    def _adapter_projection(run: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
        proposal = LabService._proposal_projection(run)
        return {
            "memoryProposal": proposal,
            "memoryFailure": run.get("failure") if run.get("state") == "failed" else None,
            "pipeline": run.get("pipeline", []),
            "world": world,
            "run": run,
        }

    def _stage_product_adapter_memory(
        self,
        *,
        run_id: str,
        session_id: str,
        title: str,
        turns: tuple[ConversationTurn, ...],
        operation_id: str,
    ) -> dict[str, Any]:
        """Compile one normal adapter turn through the product write boundary.

        This deliberately does not reuse ``_memory_run_from_typed_turns``.
        That helper accepts a model-authored WorldDelta and remains a direct-Lab
        diagnostic surface.  The production adapter accepts only a model's
        closed, span-bound interpretation and lets program code compile the
        small capability-1 candidate.
        """
        from memoweft.world.loop import EvidenceRecord
        from memoweft.world.turn_meaning import (
            TurnMeaningError,
            TurnMeaningInterpreter,
            build_accepted_entity_handles,
            compile_product_turn,
        )

        if not turns or turns[-1].role != "user":
            raise ValueError("product adapter staging requires a final user turn")
        current = turns[-1]
        loop = self._memory_loop()
        view = loop.view()
        identity_view = self._identity_authority()
        handles = build_accepted_entity_handles(
            identity_view,
            conversation_id=session_id,
            world_hash=view.snapshot_hash,
        )
        base = view.graph
        base_before = canonical_hash(self._world_snapshot(base))
        context_turns = [
            {
                "turnId": turn.turn_id,
                "role": turn.role,
                "text": turn.content,
                "note": "仅作上下文，不在本次 Evidence allowlist 中",
            }
            for turn in turns[:-1]
        ]
        common = {
            "id": run_id,
            "createdAt": utc_now(),
            "title": title,
            "turns": [
                {
                    "turnId": turn.turn_id,
                    "role": turn.role,
                    "content": turn.content,
                    "occurredAt": turn.occurred_at,
                }
                for turn in turns
            ],
            "assistantContext": [
                {"turnId": turn.turn_id, "text": turn.content, "note": "仅作上下文，不作为用户证据"}
                for turn in turns
                if turn.role == "assistant"
            ],
            "contextTurns": context_turns,
            "evidencePolicy": "只有当前 user turn 是本次 Evidence；历史与 assistant 只作上下文。",
            "baseRevision": view.revision,
            "baseWorldHash": view.snapshot_hash,
            "baseUnchanged": True,
        }
        try:
            meaning = TurnMeaningInterpreter(self._meaning_client()).interpret(
                turns,
                current,
                handles,
            )
            plan = compile_product_turn(
                proposal=meaning,
                current_user_turn=current,
                world_id=base.world.world_id,
                owner_entity_id=base.world.owner_entity_id,
                base_graph=base,
                handles=handles,
                identity_view=identity_view,
                operation_key=operation_id,
            )
        except TurnMeaningError as exc:
            result = {
                **common,
                "state": "failed",
                "failure": {"kind": "TurnMeaningError", "codes": [exc.code], "attempts": 1},
                "pipeline": [
                    {"name": "Evidence", "state": "ready", "detail": "当前 user turn 是唯一可写 Evidence。"},
                    {"name": "Interpret", "state": "failed", "detail": "结构化解释未通过本地校验。"},
                    *[{"name": name, "state": "blocked", "detail": "没有可验证的候选。"} for name in ("Identity", "Review", "Apply")],
                ],
            }
        else:
            meaning_projection = {
                "act": meaning.act,
                "mention": _json_value(meaning.mention),
                "statement": _json_value(meaning.statement),
                "mentions": [_json_value(item) for item in meaning.mentions],
                "claims": [_json_value(item) for item in meaning.claims],
                "code": plan.code,
            }
            # Legacy correction classification is intentionally absent here.
            # Capability 1 recognizes only introduction and simple attribute
            # candidates; letting a second model reinterpret either candidate
            # as a correction would widen the reviewed product boundary.  The
            # retained direct-Lab diagnostic and explicit correction endpoint
            # continue to exercise correction independently.
            if plan.state == "no_candidate":
                result = {
                    **common,
                    "state": "no-candidate",
                    "filter": plan.code,
                    "meaning": meaning_projection,
                    "previewWorldHash": view.snapshot_hash,
                    "candidateMemory": {"entities": [], "relationships": [], "events": [], "cognitions": []},
                    "evidence": [],
                    "formation": [],
                    "unresolvedReferences": [],
                    "semanticUncertainties": [],
                    "pipeline": [
                        {"name": "Evidence", "state": "read-only", "detail": "纯查询或非世界陈述不进入 Evidence ledger。"},
                        {"name": "Interpret", "state": "no-candidate", "detail": "本轮不形成长期记忆候选。"},
                        *[{"name": name, "state": "not-needed", "detail": "没有候选可复核或写入。"} for name in ("Identity", "Review", "Apply")],
                    ],
                }
            elif plan.state == "clarification_required":
                result = {
                    **common,
                    "state": "clarification-required",
                    "meaning": meaning_projection,
                    "clarification": {
                        "code": plan.code,
                        "candidateEntityNames": list(plan.candidate_entity_names),
                        "message": "当前指代不能唯一绑定到已接受对象，请先说明你指的是哪一个。",
                    },
                    "previewWorldHash": view.snapshot_hash,
                    "candidateMemory": {"entities": [], "relationships": [], "events": [], "cognitions": []},
                    "evidence": [],
                    "formation": [],
                    "unresolvedReferences": [],
                    "semanticUncertainties": [],
                    "pipeline": [
                        {"name": "Evidence", "state": "held", "detail": "未唯一绑定的指代不会作为新增事实写入。"},
                        {"name": "Interpret", "state": "clarification-required", "detail": "模型列出可能对象，程序拒绝猜测。"},
                        {"name": "Identity", "state": "unresolved", "detail": "需要用户澄清指代。"},
                        *[{"name": name, "state": "not-needed", "detail": "没有候选可复核或写入。"} for name in ("Review", "Apply")],
                    ],
                }
            elif plan.state == "out_of_scope":
                result = {
                    **common,
                    "state": "out-of-scope",
                    "filter": plan.code,
                    "meaning": meaning_projection,
                    "previewWorldHash": view.snapshot_hash,
                    "candidateMemory": {"entities": [], "relationships": [], "events": [], "cognitions": []},
                    "evidence": [],
                    "formation": [],
                    "unresolvedReferences": [],
                    "semanticUncertainties": [],
                    "pipeline": [
                        {"name": "Evidence", "state": "held", "detail": "该陈述类别尚不属于第一能力块。"},
                        {"name": "Interpret", "state": "out-of-scope", "detail": "不会回退到旧 extractor 猜测写入。"},
                        *[{"name": name, "state": "not-needed", "detail": "本轮不会形成候选。"} for name in ("Identity", "Review", "Apply")],
                    ],
                }
            else:
                assert plan.delta is not None
                from memoweft.world.loop import CognitionTransitionIntent

                ledger = {current.turn_id: {"role": "user", "content": current.content}}
                preview = plan.delta.apply_to(base, frozenset(ledger))
                if canonical_hash(self._world_snapshot(base)) != base_before:
                    raise RuntimeError("BASE_MUTATED@$")
                projection = self._candidate_projection(plan.delta, ledger)
                claim_bundle = plan.claim_bundle.to_data() if plan.claim_bundle is not None else {
                    "version": 0,
                    "focal_entity_id": plan.resolved_entity_id,
                    "evidence_id": current.turn_id,
                    "perspective_holder_entity_id": base.world.owner_entity_id,
                    "claims": [],
                }
                claim_bundle = self._product_claim_review_projection(
                    claim_bundle,
                    focal_entity_id=plan.resolved_entity_id,
                    owner_entity_id=base.world.owner_entity_id,
                    evidence_id=current.turn_id,
                )
                # v2 correction claims may describe a retraction of unaccepted
                # chat context.  They deliberately carry no SQLite transition:
                # only a compiler-supplied accepted prior can become a
                # transition intent.  This adapter does not invent a prior ID
                # from model text or a handle, so the current bundle remains
                # safe until the cognition-selection catalog is wired.
                transition_intents: tuple[CognitionTransitionIntent, ...] = ()
                bundle_claim_items = claim_bundle.get("claims")
                if not isinstance(bundle_claim_items, list):
                    raise ValueError("product claim review projection is invalid")
                statement_kinds = [
                    item.get("kind")
                    for item in bundle_claim_items
                    if isinstance(item, dict) and isinstance(item.get("kind"), str)
                ]
                legacy_statement_kind = (
                    meaning.statement.kind
                    if meaning.statement is not None
                    else "naming"
                )
                display_metadata = {
                    "title": title,
                    "candidateMemory": projection["candidateMemory"],
                    "evidence": projection["evidence"],
                    "formation": projection["formation"],
                    "unresolvedReferences": projection["unresolvedReferences"],
                    "semanticUncertainties": projection["semanticUncertainties"],
                    "meaning": meaning_projection,
                    "identityBindings": [_json_value(item) for item in plan.identity_bindings],
                    "target": {
                        "entityId": plan.resolved_entity_id,
                        "entityNames": list(plan.candidate_entity_names),
                    },
                    "statementKind": (
                        statement_kinds[0]
                        if len(statement_kinds) == 1
                        else legacy_statement_kind if not statement_kinds else None
                    ),
                    "ownerPerspective": {"kind": "entity", "entityIds": [base.world.owner_entity_id]},
                    "claims": claim_bundle,
                    "transitionIntents": [_json_value(item) for item in transition_intents],
                }
                pending = loop.stage_product_bundle(
                    plan.delta,
                    (
                        EvidenceRecord(
                            current.turn_id,
                            current.content,
                            metadata={
                                "conversation_id": current.conversation_id,
                                "occurred_at": current.occurred_at,
                                "continuity_scope": current.conversation_id,
                            },
                        ),
                    ),
                    # This is the authority-side recovery payload.  The JSON
                    # state file may lose its UI projection; it must never be
                    # the only place where a pending Owner decision can be
                    # understood or completed.  ``stage_addition`` includes
                    # this entire mapping in ``resultHash``.
                    review_payload={
                        "runId": run_id,
                        "kind": "product-bundle",
                        "operationId": operation_id,
                        "sessionId": session_id,
                        "currentEvidenceId": current.turn_id,
                        "createdAt": common["createdAt"],
                        "baseWorldHash": view.snapshot_hash,
                        "productDisplay": display_metadata,
                    },
                    identity_bindings=plan.identity_bindings,
                    transition_intents=transition_intents,
                )
                result = {
                    **common,
                    "state": "candidate-ready",
                    "reviewId": pending.id,
                    "resultHash": pending.result_hash,
                    "previewWorldHash": canonical_hash(self._world_snapshot(preview)),
                    "meaning": display_metadata["meaning"],
                    "identityBindings": display_metadata["identityBindings"],
                    "target": display_metadata["target"],
                    "statementKind": display_metadata["statementKind"],
                    "ownerPerspective": display_metadata["ownerPerspective"],
                    "claims": display_metadata["claims"],
                    "transitionIntents": display_metadata["transitionIntents"],
                    "pipeline": [
                        {"name": "Evidence", "state": "ready", "detail": "当前 user turn 已进入候选 Evidence。"},
                        {"name": "Interpret", "state": "succeeded", "detail": "模型只提供 span、引用和陈述类别。"},
                        {"name": "Identity", "state": "awaiting-owner", "detail": "候选包含经验证的实体绑定意图。"},
                        {"name": "Review", "state": "awaiting-owner", "detail": "等待 Owner 接受或拒绝。"},
                        {"name": "Apply", "state": "awaiting-owner", "detail": "接受后才会同事务写入世界和身份绑定。"},
                    ],
                    **projection,
                }
        self.state["memoryRuns"].append(result)
        self._save()
        return result

    def adapter_memory_turns(self, body: dict[str, Any]) -> dict[str, Any]:
        """Stage the Node-owned current user turn without generating another reply.

        ``operationId`` is the retry key from the original 1.x testbench.  Its
        stable hashed run ID and retained request hash make a delivery retry a
        read of the prior run, while refusing a different turn ledger under the
        same operation key.  The adapter performs no chat/Answer call.
        """
        (
            operation_id,
            session_id,
            current_user_turn_id,
            carry_forward_evidence_ids,
            turns,
            request_hash,
        ) = self._validate_adapter_memory_turns(body)
        run_id = "memory-run-adapter-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:32]
        prior = next((item for item in self.state.get("memoryRuns", []) if item.get("id") == run_id), None)
        if prior is not None:
            adapter = prior.get("adapter")
            if not isinstance(adapter, dict) or adapter.get("operationId") != operation_id or prior.get("adapterRequestHash") != request_hash:
                raise ValueError("operationId already belongs to a different adapter memory turn request")
            return self._adapter_projection(prior, self.memory_world())
        carry_authorizations = self._authorize_adapter_carry_forward(
            session_id=session_id,
            turns=turns,
            evidence_ids=carry_forward_evidence_ids,
        )
        if self._memory_run_executor is not None:
            # Existing deterministic extractor tests exercise the retained
            # diagnostic seam.  The real adapter has no executor and never
            # reaches this WorldDelta-authoring path.
            result = self._stage_current_user_memory(
                run_id=run_id,
                title="1.0 控制台 · 当前回合候选记忆",
                turns=turns,
                carry_forward_evidence_ids=carry_forward_evidence_ids,
            )
        elif carry_forward_evidence_ids:
            # Carry-forward was an old unresolved-extractor protocol.  It is
            # intentionally not interpreted as a free-form identity patch in
            # capability 1; preserving its authorization record makes the
            # rejected boundary auditable without falling back to extraction.
            result = {
                "id": run_id,
                "createdAt": utc_now(),
                "title": "1.0 控制台 · 当前回合能力边界",
                "state": "out-of-scope",
                "filter": "carry_forward_not_supported_in_capability_1",
                "turns": [{
                    "turnId": turn.turn_id,
                    "role": turn.role,
                    "content": turn.content,
                    "occurredAt": turn.occurred_at,
                } for turn in turns],
                "baseUnchanged": True,
                "candidateMemory": {"entities": [], "relationships": [], "events": [], "cognitions": []},
                "evidence": [],
                "formation": [],
                "unresolvedReferences": [],
                "semanticUncertainties": [],
                "pipeline": [
                    {"name": "Evidence", "state": "held", "detail": "旧 carry-forward 不构成第一能力块的身份绑定证据。"},
                    {"name": "Interpret", "state": "out-of-scope", "detail": "不会回退到旧 extractor。"},
                    *[{"name": name, "state": "not-needed", "detail": "本轮没有候选。"} for name in ("Identity", "Review", "Apply")],
                ],
            }
            self.state["memoryRuns"].append(result)
            self._save()
        else:
            result = self._stage_product_adapter_memory(
                run_id=run_id,
                session_id=session_id,
                title="1.0 控制台 · 当前回合候选记忆",
                turns=turns,
                operation_id=operation_id,
            )
        consumes_carried_evidence = result.get("state") in {"candidate-ready", "correction-pending"}
        carry_status = "consumed" if consumes_carried_evidence else "retained"
        result["carryForward"] = {
            "requestedEvidenceIds": list(carry_forward_evidence_ids),
            "sources": [dict(item) for item in carry_authorizations],
            "status": carry_status if carry_forward_evidence_ids else "not-requested",
        }
        result["adapter"] = {
            "operationId": operation_id,
            "sessionId": session_id,
            "currentUserTurnId": current_user_turn_id,
            "carryForwardEvidenceIds": list(carry_forward_evidence_ids),
        }
        result["adapterRequestHash"] = request_hash
        if consumes_carried_evidence:
            consumed_at = utc_now()
            source_run_ids = {item["sourceRunId"]: item["evidenceId"] for item in carry_authorizations}
            for source_run in self.state.get("memoryRuns", []):
                evidence_id = source_run_ids.get(source_run.get("id"))
                if evidence_id is not None:
                    source_run["carryForwardConsumption"] = {
                        "evidenceId": evidence_id,
                        "byRunId": run_id,
                        "consumedAt": consumed_at,
                    }
        self._save()
        return self._adapter_projection(result, self.memory_world())

    @staticmethod
    def _validate_legacy_evidence_imports(body: dict[str, Any]) -> tuple[str, tuple[ConversationTurn, ...], str]:
        """Accept only raw, typed 1.x *user* Evidence for a one-time replay.

        This endpoint is intentionally not a compatibility parser for 1.x
        cognition/profile output.  Its exact body shape makes it impossible to
        supply model-derived memory text, confidence, traits, or a claimed
        prior 1.x decision.  Every imported turn is a fresh, Owner-reviewable
        Evidence source; nothing is applied during the replay.
        """
        from memoweft.world import ConversationTurn

        if set(body) != {"operationId", "turns"}:
            raise ValueError("legacy evidence imports require only operationId and turns")
        operation_id = body.get("operationId")
        if not isinstance(operation_id, str) or not operation_id.strip() or operation_id != operation_id.strip() or len(operation_id) > 200:
            raise ValueError("operationId must be a trimmed non-empty string of at most 200 characters")
        raw_turns = body.get("turns")
        if not isinstance(raw_turns, list) or not 1 <= len(raw_turns) <= 20:
            raise ValueError("legacy evidence imports require 1 through 20 typed user turns")

        turns: list[ConversationTurn] = []
        seen_ids: set[str] = set()
        total_characters = 0
        for index, item in enumerate(raw_turns):
            if not isinstance(item, dict) or set(item) != {"turnId", "role", "content", "occurredAt"}:
                raise ValueError(f"legacy evidence imports[{index}] must contain only turnId, role, content, and occurredAt")
            turn_id, role, content, occurred_at = item.get("turnId"), item.get("role"), item.get("content"), item.get("occurredAt")
            if not isinstance(turn_id, str) or not turn_id.strip() or turn_id != turn_id.strip() or len(turn_id) > 200:
                raise ValueError(f"legacy evidence imports[{index}].turnId must be a trimmed non-empty string of at most 200 characters")
            if turn_id in seen_ids:
                raise ValueError("legacy evidence imports require unique turnId values")
            if role != "user":
                raise ValueError(f"legacy evidence imports[{index}].role must be user")
            if not isinstance(content, str) or not content.strip() or len(content) > 4000:
                raise ValueError(f"legacy evidence imports[{index}].content must be a non-empty string of at most 4000 characters")
            if not isinstance(occurred_at, str) or not _RFC3339_TIMESTAMP.fullmatch(occurred_at):
                raise ValueError(f"legacy evidence imports[{index}].occurredAt must be an RFC3339 timestamp with timezone")
            try:
                datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"legacy evidence imports[{index}].occurredAt must be a valid RFC3339 timestamp") from exc
            seen_ids.add(turn_id)
            total_characters += len(content)
            turns.append(ConversationTurn(
                turn_id=turn_id,
                conversation_id="legacy-evidence-import",
                role="user",
                content=content,
                occurred_at=occurred_at,
            ))
        if total_characters > 24000:
            raise ValueError("legacy evidence import content exceeds the 24000-character local experiment limit")
        request_hash = canonical_hash({
            "turns": [
                {"turnId": turn.turn_id, "role": turn.role, "content": turn.content, "occurredAt": turn.occurred_at}
                for turn in turns
            ],
        })
        return operation_id, tuple(turns), request_hash

    def adapter_legacy_memory_imports(self, body: dict[str, Any]) -> dict[str, Any]:
        """Replay 1.x raw Owner turns as a pending 2.0 memory candidate.

        Unlike ``adapter_memory_turns``, migration does not select a current
        turn and does not run natural-correction classification.  Each supplied
        raw user statement enters the explicit Evidence allowlist, while the
        normal 2.0 extractor and Owner decision boundary remain unchanged.
        """
        operation_id, turns, request_hash = self._validate_legacy_evidence_imports(body)
        run_id = "memory-run-legacy-import-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:32]
        prior = next((item for item in self.state.get("memoryRuns", []) if item.get("id") == run_id), None)
        if prior is not None:
            replay = prior.get("legacyEvidenceReplay")
            if not isinstance(replay, dict) or replay.get("operationId") != operation_id or prior.get("legacyEvidenceImportRequestHash") != request_hash:
                raise ValueError("operationId already belongs to a different legacy evidence import")
            return self._adapter_projection(prior, self.memory_world())

        # Retrying an already-retained delivery above is read-only and must
        # remain available.  A *new* replay, however, may not silently queue
        # behind another undecided Owner candidate: its base could become stale
        # before the Owner decides.  Keep the externally visible error content
        # fixed so the loopback HTTP boundary cannot disclose turn text.
        if self._memory_loop().view().pending_reviews:
            raise ValueError("LEGACY_IMPORT_PENDING_REVIEW_EXISTS")

        result = self._memory_run_from_typed_turns(
            run_id=run_id,
            title="1.x 原始 Evidence 回放 · 待审核候选记忆",
            turns=turns,
            evidence_allowlist=frozenset(turn.turn_id for turn in turns),
            owner_notes={},
        )
        result["legacyEvidenceReplay"] = {
            "operationId": operation_id,
            "kind": "raw-user-evidence-only",
            "evidencePolicy": "所有导入 turn 都是原始 user Evidence；不接收或保留 1.x cognition 派生文本。",
        }
        result["legacyEvidenceImportRequestHash"] = request_hash
        self._save()
        return self._adapter_projection(result, self.memory_world())

    def memory_run(self, body: dict[str, Any]) -> dict[str, Any]:
        """Run one user-created scenario as a disposable candidate-memory preview.

        Extraction reads the current persistent experiment world, but staging
        leaves that world untouched until the Owner later decides this exact
        proposal.  The preview remains isolated from the canonical snapshot.
        """
        from memoweft.world import ConversationTurn

        title, supplied_turns, owner_notes = self._validate_memory_scenario(body)
        run_id = f"memory-run-{uuid.uuid4()}"
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        turns = tuple(
            ConversationTurn(
                turn_id=f"turn:{uuid.uuid4()}",
                conversation_id=run_id,
                role=role,
                content=content,
                occurred_at=now,
            )
            for role, content in supplied_turns
        )
        return self._memory_run_from_typed_turns(
            run_id=run_id,
            title=title,
            turns=turns,
            evidence_allowlist=frozenset(turn.turn_id for turn in turns if turn.role == "user"),
            owner_notes=owner_notes,
        )

    def _memory_run_from_typed_turns(
        self,
        *,
        run_id: str,
        title: str,
        turns: tuple[ConversationTurn, ...],
        evidence_allowlist: Collection[str],
        owner_notes: dict[str, str],
    ) -> dict[str, Any]:
        """Extract and stage from caller-owned typed turns and an explicit Evidence set.

        Scripted scenarios make every user turn eligible before entering this
        helper.  Chat instead supplies its persisted turn IDs and marks only the
        current user turn eligible; earlier user and assistant turns remain
        role-preserving model context but cannot enter the Evidence ledger.
        """
        from memoweft.world import WorldExtractionError
        from memoweft.world.loop import EvidenceRecord

        turn_by_id = {turn.turn_id: turn for turn in turns}
        if len(turn_by_id) != len(turns):
            raise ValueError("typed memory turns require unique turn IDs")
        allowlist = frozenset(evidence_allowlist)
        if not allowlist.issubset(turn_by_id):
            raise ValueError("Evidence allowlist must reference supplied typed turns")
        if any(turn_by_id[turn_id].role != "user" for turn_id in allowlist):
            raise ValueError("only user turns may enter the Evidence allowlist")

        ledger = {
            turn_id: {"role": "user", "content": turn_by_id[turn_id].content}
            for turn_id in allowlist
        }
        assistant_context = [
            {"turnId": turn.turn_id, "text": turn.content, "note": "仅作上下文，不作为用户证据"}
            for turn in turns
            if turn.role == "assistant"
        ]
        context_turns = [
            {
                "turnId": turn.turn_id,
                "role": turn.role,
                "text": turn.content,
                "note": "仅作上下文，不在本次 Evidence allowlist 中",
            }
            for turn in turns
            if turn.turn_id not in allowlist
        ]
        loop = self._memory_loop()
        current_view = loop.view()
        base = current_view.graph
        base_before = canonical_hash(self._world_snapshot(base))
        common = {
            "id": run_id,
            "createdAt": utc_now(),
            "title": title,
            "ownerReference": owner_notes,
            "turns": [
                {
                    "turnId": turn.turn_id,
                    "role": turn.role,
                    "content": turn.content,
                    "occurredAt": turn.occurred_at,
                }
                for turn in turns
            ],
            "assistantContext": assistant_context,
            "contextTurns": context_turns,
            "evidencePolicy": "只有显式列入本次 allowlist 的 user turn 进入 Evidence；其余 turn 仅作上下文。",
            "baseWorldHash": base_before,
        }
        try:
            delta = self._execute_memory_run(base, turns, allowlist)
            base_after_extract = canonical_hash(self._world_snapshot(base))
            if base_after_extract != base_before:
                raise RuntimeError("BASE_MUTATED@$")
            has_candidate_records = any((
                delta.new_entities,
                delta.new_relationships,
                delta.new_events,
                delta.new_cognitions,
            ))
            if not has_candidate_records:
                # A syntactically valid model response which proposes no world
                # records is not a reviewable memory proposal.  In particular,
                # a normal greeting must not manufacture an empty pending
                # review or advance the persistent world revision on accept.
                result = {
                    **common,
                    "state": "no-candidate",
                    "baseUnchanged": True,
                    "previewWorldHash": base_before,
                    "pipeline": [
                        {"name": "Evidence", "state": "ready", "detail": "用户 turn 已作为候选证据目录。"},
                        {"name": "Extract", "state": "no-candidate", "detail": "本地模型未提出任何实体、关系、事件或认知。"},
                        {"name": "Review", "state": "not-needed", "detail": "没有候选记忆可供接受或拒绝。"},
                        {"name": "Apply", "state": "not-applied", "detail": "没有候选，当前实验世界未改变。"},
                        *[{"name": name, "state": "unchanged", "detail": "本轮没有形成候选记忆。"} for name in ("Recall", "Answer", "Correction")],
                    ],
                    **self._candidate_projection(delta, ledger),
                }
            else:
                preview = delta.apply_to(base, frozenset(ledger))
                base_after_preview = canonical_hash(self._world_snapshot(base))
                if base_after_preview != base_before:
                    raise RuntimeError("BASE_MUTATED@$")
                projection = self._candidate_projection(delta, ledger)
                pending = loop.stage_addition(
                    delta,
                    tuple(EvidenceRecord(turn_id, item["content"]) for turn_id, item in ledger.items()),
                    review_payload={"runId": run_id, "kind": "addition"},
                )
                result = {
                    **common,
                    "state": "candidate-ready",
                    "reviewId": pending.id,
                    "resultHash": pending.result_hash,
                    "baseRevision": pending.base_revision,
                    "previewWorldHash": canonical_hash(self._world_snapshot(preview)),
                    "baseUnchanged": True,
                    "pipeline": [
                        {"name": "Evidence", "state": "ready", "detail": "用户 turn 已作为候选证据目录。"},
                        {"name": "Extract", "state": "succeeded", "detail": "本地模型已提出候选记忆。"},
                        {"name": "Review", "state": "awaiting-owner", "detail": "等待 Owner 评价与接受或拒绝。"},
                        {"name": "Apply", "state": "awaiting-owner", "detail": "尚未接受，不会写入当前实验世界。"},
                        *[{"name": name, "state": "waiting-for-accept", "detail": "接受候选后才会读取当前长期记忆。"} for name in ("Recall", "Answer", "Correction")],
                    ],
                    **projection,
                }
        except WorldExtractionError as exc:
            result = {
                **common,
                "state": "failed",
                "baseUnchanged": canonical_hash(self._world_snapshot(base)) == base_before,
                "failure": {"kind": "WorldExtractionError", "codes": list(exc.codes), "attempts": exc.attempts},
                "pipeline": [
                    {"name": "Evidence", "state": "ready", "detail": "用户 turn 已作为候选证据目录。"},
                    {"name": "Extract", "state": "failed", "detail": "提取失败；这不是空记忆成功。"},
                    *[{"name": name, "state": "blocked", "detail": "提取失败，未进入此步骤。"} for name in ("Review", "Apply", "Recall", "Answer", "Correction")],
                ],
            }
        except Exception as exc:
            result = {
                **common,
                "state": "failed",
                "baseUnchanged": canonical_hash(self._world_snapshot(base)) == base_before,
                "failure": {"kind": "memory-run-failed", "codes": [str(exc) if str(exc) == "BASE_MUTATED@$" else type(exc).__name__], "attempts": 0},
                "pipeline": [
                    {"name": "Evidence", "state": "ready", "detail": "用户 turn 已作为候选证据目录。"},
                    {"name": "Extract", "state": "failed", "detail": "提取失败；这不是空记忆成功。"},
                    *[{"name": name, "state": "blocked", "detail": "提取失败，未进入此步骤。"} for name in ("Review", "Apply", "Recall", "Answer", "Correction")],
                ],
            }
        self.state["memoryRuns"].append(result)
        self._save()
        return result

    def memory_runs(self) -> dict[str, Any]:
        return {"runs": list(self.state.get("memoryRuns", []))}

    @staticmethod
    def _memory_view_projection(view: Any) -> dict[str, Any]:
        graph = view.graph
        return {
            "worldId": graph.world.world_id,
            "revision": view.revision,
            "worldHash": view.snapshot_hash,
            "memory": {
                "entities": [_json_value(item) for item in sorted(graph.entities.values(), key=lambda item: item.id)],
                "relationships": [_json_value(item) for item in sorted(graph.relationships.values(), key=lambda item: item.id)],
                "events": [_json_value(item) for item in sorted(graph.events.values(), key=lambda item: item.id)],
                "cognitions": [_json_value(item) for item in view.current_cognitions],
            },
            "supersededCognitionIds": sorted(view.superseded_cognition_ids),
            "transitions": [_json_value(item) for item in view.transitions],
            "pendingReviews": [_json_value(item) for item in view.pending_reviews],
        }

    def memory_world(self) -> dict[str, Any]:
        return self._memory_view_projection(self._memory_loop().view())

    def _proposal_authority(self, review_id: str) -> dict[str, Any] | None:
        """Read the SQLite review record that authorizes an Owner decision.

        ``memoryRuns`` is intentionally absent from this lookup: it is a
        convenient workbench projection, not a second source of truth for the
        decision state or reviewed hash.
        """
        row = self._memory_loop().connection.execute(
            "SELECT id, kind, base_revision, result_hash, review_payload_json, status "
            "FROM proposals WHERE id = ?",
            (review_id,),
        ).fetchone()
        if row is None:
            return None
        raw_payload = row["review_payload_json"]
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else None
        except json.JSONDecodeError as exc:
            raise ValueError("SQLite proposal display payload is invalid") from exc
        if payload is not None and not isinstance(payload, dict):
            raise ValueError("SQLite proposal display payload is invalid")
        return {
            "reviewId": row["id"],
            "kind": row["kind"],
            "baseRevision": int(row["base_revision"]),
            "resultHash": row["result_hash"],
            "status": row["status"],
            "reviewPayload": payload,
        }

    @staticmethod
    def _memory_run_for_review(state: dict[str, Any], review_id: str) -> dict[str, Any] | None:
        memory_runs = state.get("memoryRuns", [])
        if not isinstance(memory_runs, list):
            raise ValueError("stored memory runs are invalid")
        return next(
            (
                item for item in memory_runs
                if isinstance(item, dict) and item.get("reviewId") == review_id
            ),
            None,
        )

    def _restore_product_run_projection(self, authority: dict[str, Any]) -> dict[str, Any] | None:
        """Recreate the smallest pending product projection from SQLite.

        A restart may lose only ``state.json``'s ``memoryRuns`` projection.
        The signed review payload contains the fields needed to present and
        decide the proposal again, so this recovery never re-extracts a turn
        or invents a new candidate.
        """
        payload = authority.get("reviewPayload")
        if authority.get("kind") != "product_bundle" or not isinstance(payload, dict):
            return None
        if payload.get("kind") != "product-bundle":
            return None
        run_id = payload.get("runId")
        operation_id = payload.get("operationId")
        session_id = payload.get("sessionId")
        current_evidence_id = payload.get("currentEvidenceId")
        display = payload.get("productDisplay")
        if not all(isinstance(item, str) and item for item in (run_id, operation_id, session_id, current_evidence_id)):
            raise ValueError("SQLite product proposal recovery payload is incomplete")
        if not isinstance(display, dict):
            raise ValueError("SQLite product proposal display metadata is invalid")
        candidate_memory = display.get("candidateMemory")
        evidence = display.get("evidence")
        if not isinstance(candidate_memory, dict) or not isinstance(evidence, list):
            raise ValueError("SQLite product proposal display metadata is invalid")
        run: dict[str, Any] = {
            "id": run_id,
            "createdAt": payload.get("createdAt") if isinstance(payload.get("createdAt"), str) else utc_now(),
            "title": display.get("title") if isinstance(display.get("title"), str) else "恢复的候选记忆",
            "state": "candidate-ready",
            "reviewId": authority["reviewId"],
            "resultHash": authority["resultHash"],
            "baseRevision": authority["baseRevision"],
            "baseWorldHash": payload.get("baseWorldHash"),
            "baseUnchanged": True,
            "candidateMemory": candidate_memory,
            "evidence": evidence,
            "formation": display.get("formation", []),
            "unresolvedReferences": display.get("unresolvedReferences", []),
            "semanticUncertainties": display.get("semanticUncertainties", []),
            "meaning": display.get("meaning"),
            "identityBindings": display.get("identityBindings", []),
            "target": display.get("target"),
            "statementKind": display.get("statementKind"),
            "ownerPerspective": display.get("ownerPerspective"),
            "claims": display.get("claims", {}),
            "transitionIntents": display.get("transitionIntents", []),
            "adapter": {
                "operationId": operation_id,
                "sessionId": session_id,
                "currentUserTurnId": current_evidence_id,
                "carryForwardEvidenceIds": [],
            },
            "recoveredFromSqlite": True,
            "pipeline": [
                {"name": "Evidence", "state": "ready", "detail": "已从 SQLite proposal 恢复当前 Evidence。"},
                {"name": "Interpret", "state": "succeeded", "detail": "已恢复经程序验证的候选解释。"},
                {"name": "Identity", "state": "awaiting-owner", "detail": "身份绑定仍待 Owner 决定。"},
                {"name": "Review", "state": "awaiting-owner", "detail": "SQLite proposal 仍待 Owner 决定。"},
                {"name": "Apply", "state": "awaiting-owner", "detail": "接受后才会写入当前世界。"},
            ],
        }
        self.state["memoryRuns"].append(run)
        self._save()
        return run

    def memory_decision(self, body: dict[str, Any]) -> dict[str, Any]:
        from memoweft.world.loop import MemoryLoopError

        review_id = body.get("reviewId")
        supplied_run_id = body.get("runId")
        result_hash = body.get("resultHash")
        decision = body.get("decision")
        if not isinstance(result_hash, str) or not result_hash:
            raise ValueError("resultHash must reference a candidate-memory proposal")
        if decision not in {"accept", "reject"}:
            raise ValueError("decision must be accept or reject")
        # ``runId`` is retained only to locate an old projection.  New callers
        # must send the SQLite review ID that they actually reviewed.
        if review_id is None:
            if not isinstance(supplied_run_id, str):
                raise ValueError("reviewId is required for a candidate-memory decision")
            legacy_run = next(
                (item for item in self.state.get("memoryRuns", []) if isinstance(item, dict) and item.get("id") == supplied_run_id),
                None,
            )
            review_id = legacy_run.get("reviewId") if isinstance(legacy_run, dict) else None
        if not isinstance(review_id, str) or not review_id:
            raise ValueError("reviewId must reference a SQLite candidate-memory proposal")
        authority = self._proposal_authority(review_id)
        if authority is None:
            raise ValueError("reviewId must reference a SQLite candidate-memory proposal")
        if result_hash != authority["resultHash"]:
            raise ValueError("resultHash must match the SQLite candidate-memory proposal")
        run = self._memory_run_for_review(self.state, review_id)
        if run is None and authority["status"] == "pending":
            run = self._restore_product_run_projection(authority)
        if run is not None and isinstance(supplied_run_id, str) and supplied_run_id != run.get("id"):
            raise ValueError("runId does not match the SQLite candidate-memory proposal")
        if authority["status"] not in {"pending", "accept", "reject"}:
            raise ValueError("SQLite candidate-memory proposal has an invalid decision state")
        if authority["status"] != "pending":
            if authority["status"] != decision:
                raise ValueError("SQLite candidate-memory proposal already has the opposite decision")
            view = self._memory_loop().view()
            return {
                "runId": run["id"] if run is not None else supplied_run_id,
                "reviewId": review_id,
                "resultHash": result_hash,
                "decision": decision,
                "idempotent": True,
                "world": self._memory_view_projection(view),
            }
        try:
            view = self._memory_loop().decide(review_id, result_hash, decision)
        except MemoryLoopError as exc:
            # A concurrent retry can observe a proposal that was decided after
            # our read.  Re-read SQLite and return the same decision only.
            after = self._proposal_authority(review_id)
            if after is not None and after["status"] == decision and after["resultHash"] == result_hash:
                view = self._memory_loop().view()
                return {
                    "runId": run["id"] if run is not None else supplied_run_id,
                    "reviewId": review_id,
                    "resultHash": result_hash,
                    "decision": decision,
                    "idempotent": True,
                    "world": self._memory_view_projection(view),
                }
            raise ValueError(f"memory decision rejected: {type(exc).__name__}") from None
        if run is None:
            # Non-product legacy reviews remain decidable by SQLite authority;
            # they simply have no recoverable 1.x workbench card to project.
            self._save()
            return {
                "runId": supplied_run_id,
                "reviewId": review_id,
                "resultHash": result_hash,
                "decision": decision,
                "world": self._memory_view_projection(view),
            }
        run["state"] = "accepted" if decision == "accept" else "rejected"
        run["decision"] = {"decision": decision, "decidedAt": utc_now(), "revision": view.revision, "worldHash": view.snapshot_hash}
        memory_available = view.revision > 0
        if isinstance(run.get("correction"), dict):
            availability = [
                {
                    "name": name,
                    "state": "available" if memory_available else "waiting-for-memory",
                    "detail": "可以继续使用当前实验世界。" if memory_available else "当前还没有已接受的长期记忆。",
                }
                for name in ("Recall", "Answer")
            ]
            run["pipeline"] = [
                *availability,
                {"name": "Evidence", "state": "ready", "detail": "当前 user turn 是这次纠正的唯一 Evidence。"},
                {"name": "Correction", "state": decision, "detail": "Owner 已决定这条自然纠正。"},
                {"name": "Extract", "state": "skipped", "detail": "自然纠正没有运行普通新增提取。"},
                {"name": "Review", "state": decision, "detail": "Owner 已作出决定。"},
                {"name": "Apply", "state": "applied" if decision == "accept" else "not-applied", "detail": "已原子替换当前认知并保留旧认知历史。" if decision == "accept" else "已拒绝；当前实验世界未改变。"},
            ]
        else:
            run["pipeline"] = [
                {"name": "Evidence", "state": "ready", "detail": "用户 Evidence 已保留在本地实验账本。"},
                {"name": "Extract", "state": "succeeded", "detail": "候选记忆已形成。"},
                {"name": "Review", "state": decision, "detail": "Owner 已作出决定。"},
                {"name": "Apply", "state": "applied" if decision == "accept" else "not-applied", "detail": "已写入当前实验世界。" if decision == "accept" else "已拒绝；当前实验世界未改变。"},
                *[{
                    "name": name,
                    "state": "available" if memory_available else "waiting-for-memory",
                    "detail": "可以继续使用当前实验世界。" if memory_available else "当前还没有已接受的长期记忆。",
                } for name in ("Recall", "Answer", "Correction")],
            ]
        self._save()
        return {
            "runId": run["id"],
            "reviewId": review_id,
            "resultHash": result_hash,
            "decision": decision,
            "world": self._memory_view_projection(view),
        }

    def memory_query(self, body: dict[str, Any]) -> dict[str, Any]:
        query = body.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 1200:
            raise ValueError("query must be a non-empty string of at most 1200 characters")
        answer = self._memory_loop().ask(query, self._answer_client())
        return {
            "query": query,
            "status": answer.status,
            "answer": answer.answer,
            "recalledEntities": [_json_value(item) for item in answer.recalled_entities],
            "recalledRelationships": [_json_value(item) for item in answer.recalled_relationships],
            "recalledEvents": [_json_value(item) for item in answer.recalled_events],
            "recalledCognitions": [_json_value(item) for item in answer.recalled_cognitions],
            "evidence": [_json_value(item) for item in answer.evidence_context],
            "historyCognitionIds": list(answer.history_cognition_ids),
            "recalledHistoryCognitions": [
                _json_value(item) for item in answer.recalled_history_cognitions
            ],
            "reconstruction": _json_value(answer.reconstruction),
            "world": self._memory_view_projection(self._memory_loop().view()),
        }

    def memory_recall(self, body: dict[str, Any]) -> dict[str, Any]:
        """Read a small, accepted-memory-only recall bundle for the 1.x chat bridge.

        This deliberately does not share ``memory_query``: the bridge must not
        create an answer client, return raw provenance, or expose Lab state to
        the cloud-chat prompt assembly path.
        """
        if set(body) != {"query"}:
            raise ValueError("memory recalls accept only query")
        query = body.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 1200:
            raise ValueError("query must be a non-empty string of at most 1200 characters")
        answer = self._memory_loop().ask(query)
        return {
            "status": answer.status,
            "memories": [
                {
                    "content": cognition.content,
                    "confidence": cognition.confidence,
                    "credStatus": cognition.cred_status,
                }
                for cognition in answer.recalled_cognitions[:8]
            ],
        }

    def memory_correction(self, body: dict[str, Any]) -> dict[str, Any]:
        from memoweft.world.loop import EvidenceRecord, MemoryLoopError

        cognition_id, correction_text = body.get("cognitionId"), body.get("correctionText")
        if not isinstance(cognition_id, str) or not cognition_id or not isinstance(correction_text, str) or not correction_text.strip() or len(correction_text) > 4000:
            raise ValueError("cognitionId and a non-empty correctionText of at most 4000 characters are required")
        run_id = f"memory-correction-{uuid.uuid4()}"
        evidence_id = f"turn:{uuid.uuid4()}"
        try:
            pending = self._memory_loop().stage_correction(
                cognition_id,
                correction_text,
                EvidenceRecord(evidence_id, correction_text),
                review_payload={"runId": run_id, "kind": "correction"},
            )
        except MemoryLoopError as exc:
            raise ValueError(f"memory correction rejected: {type(exc).__name__}") from None
        result = {
            "id": run_id,
            "createdAt": utc_now(),
            "state": "correction-pending",
            "reviewId": pending.id,
            "resultHash": pending.result_hash,
            "baseRevision": pending.base_revision,
            "priorCognitionId": cognition_id,
            "candidateCorrection": {"content": correction_text, "evidence": [{"evidenceId": evidence_id, "text": correction_text}]},
            "pipeline": [
                {"name": "Evidence", "state": "ready", "detail": "这条纠错作为新的用户 Evidence。"},
                {"name": "Correction", "state": "awaiting-owner", "detail": "纠错候选等待接受或拒绝。"},
                {"name": "Apply", "state": "awaiting-owner", "detail": "尚未接受，当前实验世界未改变。"},
            ],
        }
        self.state["memoryRuns"].append(result)
        self._save()
        return result

    def _read_memory_evaluations(self) -> list[dict[str, Any]]:
        if not self.memory_evaluation_path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in self.memory_evaluation_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("memory evaluation ledger contains a non-object record")
            records.append(item)
        return records

    def memory_evaluation(self, body: dict[str, Any]) -> dict[str, Any]:
        run = next((item for item in self.state.get("memoryRuns", []) if item.get("id") == body.get("runId")), None)
        verdict, notes = body.get("verdict"), body.get("notes", "")
        if run is None or not isinstance(run.get("resultHash"), str):
            raise ValueError("runId must reference a candidate-memory run")
        if body.get("resultHash") != run.get("resultHash"):
            raise ValueError("resultHash must match the retained candidate-memory result")
        if verdict not in {"correct", "partly-correct", "incorrect"} or not isinstance(notes, str) or len(notes) > 4000:
            raise ValueError("verdict must be correct, partly-correct, or incorrect; notes <= 4000 chars")
        record = {"id": f"memory-evaluation-{uuid.uuid4()}", "recordedAt": utc_now(), "runId": run["id"], "resultHash": run["resultHash"], "verdict": verdict, "notes": notes, "effect": "仅追加本地评价；候选记忆未持久化，base/preview 均不变"}
        with self.memory_evaluation_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def memory_evaluations(self) -> dict[str, Any]:
        return {"evaluations": self._read_memory_evaluations()}

    def expand(self, body: dict[str, Any]) -> dict[str, Any]:
        scenario_id = body.get("scenarioId")
        if scenario_id not in SCENARIOS:
            raise ValueError("scenarioId is not allowlisted")
        target = body.get("target")
        if not isinstance(target, dict) or target.get("kind") not in {"world", "entity", "relationship", "event"} or not isinstance(target.get("id"), str):
            raise ValueError("target must contain an allowed kind and ID")
        depth = body.get("depth", 1)
        if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0 or depth > 2:
            raise ValueError("depth must be an integer from 0 through 2")
        from memoweft.world import MemoryTarget
        graph = build_graph(scenario_id)
        slice_ = graph.expand(MemoryTarget(target["kind"], target["id"]), depth=depth)
        return {"kind": "local-expansion", "label": "Local expansion (not Recall)", "scenarioId": scenario_id, "anchor": target, "depth": depth, "slice": _json_value(slice_), "worldHash": canonical_hash(graph_payload(graph))}

    def review(self, body: dict[str, Any]) -> dict[str, Any]:
        _, ledger_warnings = self._read_review_ledger()
        if ledger_warnings:
            self._reload_reviews_from_ledger()
            raise ValueError("owner verdict ledger must be repaired before appending another review")
        scenario_id, verdict, notes = body.get("scenarioId"), body.get("verdict"), body.get("notes", "")
        if scenario_id not in SCENARIOS or verdict not in {"accept-structure", "needs-discussion", "reject-structure"} or not isinstance(notes, str) or len(notes) > 4000:
            raise ValueError("invalid review; scenario and verdict must be allowlisted and notes <= 4000 chars")
        run = self._find_run(body.get("runId"))
        scenario = next((item for item in run["scenarios"] if item["scenarioId"] == scenario_id and item.get("worldHash")), None)
        if scenario is None:
            raise ValueError("runId must contain the reviewed scenario with a world hash")
        if body.get("worldHash") != scenario["worldHash"]:
            raise ValueError("worldHash must match the reviewed scenario in the retained run")
        record = {"id": f"review-{uuid.uuid4()}", "recordedAt": utc_now(), "runId": run["id"], "runIdentity": run["identity"], "scenarioId": scenario_id, "worldHash": scenario["worldHash"], "checks": scenario["checks"], "verdict": verdict, "notes": notes, "effect": "append-only local review note; world unchanged; diagnostic evidence only; not product acceptance"}
        with self.review_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._reload_reviews_from_ledger()
        self._save()
        return record

    def pin(self, body: dict[str, Any]) -> dict[str, Any]:
        run = self._find_run(body.get("runId"))
        scenario_id = body.get("scenarioId")
        scenario = next((item for item in run["scenarios"] if item.get("scenarioId") == scenario_id), None)
        if scenario is None:
            raise ValueError("scenarioId must be present in the pinned run")
        if run.get("partial") or any(check.get("state") == "not-run" for check in scenario.get("checks", [])):
            raise ValueError("a partial or incomplete scenario run cannot become a baseline")
        for older in self.state["runs"]:
            if older.get("baselineFor") == scenario_id:
                older.pop("baselineFor")
        run["pinned"] = True
        run["baselineFor"] = scenario_id
        run["pinnedAt"] = utc_now()
        self._save()
        return {"runId": run["id"], "scenarioId": scenario_id, "pinned": True}

    def compare(self, body: dict[str, Any]) -> dict[str, Any]:
        left, right = self._find_run(body.get("leftRunId")), self._find_run(body.get("rightRunId"))
        scenario_id = body.get("scenarioId")
        if scenario_id not in SCENARIOS:
            raise ValueError("compare requires an allowlisted scenarioId")
        if left.get("partial") or right.get("partial"):
            raise ValueError("compare rejects partial runs; select two complete runs for this scenario")
        left_scenario = next((item for item in left["scenarios"] if item.get("scenarioId") == scenario_id), None)
        right_scenario = next((item for item in right["scenarios"] if item.get("scenarioId") == scenario_id), None)
        if left_scenario is None or right_scenario is None:
            raise ValueError("compare requires both retained runs to contain the selected scenario")
        if any(check.get("state") == "not-run" for item in (left_scenario, right_scenario) for check in item.get("checks", [])):
            raise ValueError("compare rejects incomplete scenario scope")
        if left_scenario.get("definitionHash") != right_scenario.get("definitionHash"):
            raise ValueError("compare rejects incompatible scenario definition scope")
        if [check.get("id") for check in left_scenario.get("checks", [])] != [check.get("id") for check in right_scenario.get("checks", [])]:
            raise ValueError("compare rejects incompatible selected-scenario check scope")
        return {
            "scenarioId": scenario_id,
            "leftRunId": left["id"],
            "rightRunId": right["id"],
            "before": left["identity"],
            "after": right["identity"],
            "leftWorldHash": left_scenario.get("worldHash"),
            "rightWorldHash": right_scenario.get("worldHash"),
            "changes": json_diff(left_scenario["graph"], right_scenario["graph"]),
        }

    def rerun(self, body: dict[str, Any]) -> dict[str, Any]:
        source = self._find_run(body.get("runId"))
        only_failed = body.get("onlyFailed", False)
        if not isinstance(only_failed, bool):
            raise ValueError("onlyFailed must be boolean")
        ids = [item["scenarioId"] for item in source["scenarios"]]
        checks: dict[str, list[str]] | None = None
        if only_failed:
            checks = {item["scenarioId"]: [check["id"] for check in item["checks"] if check["state"] == "failed"] for item in source["scenarios"]}
            ids = [scenario_id for scenario_id in ids if checks[scenario_id]]
            if not ids:
                return {"kind": "no-failed-checks", "sourceRunId": source["id"], "message": "No failed Golden checks are available to rerun."}
        return self.run(ids, only_checks=checks, rerun_of=source["id"])
