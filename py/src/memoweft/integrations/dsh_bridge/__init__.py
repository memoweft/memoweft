"""WeftMate (DSH host) integration for MemoWeft 2.0 — thin stdio-facing bridge.

Design rules (WeftMate R7, Owner-approved integration plan):

* Zero edits to the MemoWeft core or the Hermes integration.  This package only
  imports host-agnostic pieces (``..hermes.boundary_store``, ``world_worker``,
  ``batch_adapter``, ``recall``, and the read-only database probe) and adapts
  the closed boundary envelope to the WeftMate/DSH host.
* Same durability discipline as Hermes: user messages become ``spoken``
  Evidence only inside a committed DSH compression boundary; assistant text is
  only ``preceding_ai_context`` and never becomes Evidence by itself.
* Same budget invariant (AUTHORITY §6.6): ordinary turns make zero
  ``memory_world`` calls; a truly committed boundary makes exactly 0 or 1
  one-shot interpretation call; Recall makes zero model calls and zero writes.
* Transport is stdio JSON-Lines (``python -m memoweft.integrations.dsh_bridge``),
  one JSON request/response object per line, driven by the WeftMate host plugin.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Callable, Mapping, Sequence, cast

from ..hermes import _assert_existing_database_is_current  # host-agnostic probe
from ..hermes.batch_adapter import HermesBatchAdapterProcessor
from ..hermes.boundary_store import (
    HermesBoundaryEvidenceCandidate,
    HermesBoundaryFormalTarget,
    HermesBoundaryStore,
    ValidatedHermesBoundary,
)
from ..hermes.recall import recall_world_text
from ..hermes.world_worker import WorldJobWorker
from ..trust.currentness import evidence_state, linked_evidence, world_item_visible
from ...store import open_db

logger = logging.getLogger(__name__)

_BOUNDARY_EVENT_PREFIX = "weftmate-compression-boundary-v1"
_BOUNDARY_PROVIDER_NAME = "memoweft"
_BOUNDARY_MODES = frozenset({"in_place", "rotation"})
_BOUNDARY_KEYS = frozenset(
    {
        "event_id",
        "schema_version",
        "provider_name",
        "parent_session_id",
        "result_session_id",
        "mode",
        "source_messages",
        "payload_hash",
    }
)
_BOUNDARY_MESSAGE_KEYS = frozenset(
    {
        "role",
        "content",
        "timestamp",
        "platform_message_id",
        "message_id",
        "observed",
        "display_kind",
        "synthetic",
        "source_ref",
    }
)

_SUMMARY_PREFIXES = (
    "[CONTEXT COMPACTION — REFERENCE ONLY]",
    "[CONTEXT SUMMARY]:",
)
_CONTINUATION_MARKERS = frozenset(
    {
        "Continue from the compressed conversation context above. "
        "This marker exists because no human user turn was available.",
        "Continue from the compressed conversation context above. "
        "This marker exists because the compacted transcript contained "
        "no preserved user turn.",
    }
)


class DshBoundaryError(ValueError):
    """A WeftMate committed boundary does not satisfy the closed envelope."""


def _text_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def _is_synthetic_user_message(message: Mapping[str, object], content: str) -> bool:
    if (
        bool(message.get("synthetic"))
        or bool(message.get("observed"))
        or bool(message.get("_compressed_summary"))
    ):
        return True
    for key, value in message.items():
        if key.startswith("_") and "synthetic" in key and bool(value):
            return True
    stripped = content.strip()
    return stripped.startswith(_SUMMARY_PREFIXES) or stripped in _CONTINUATION_MARKERS


def _occurred_at(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _boundary_payload_hash(boundary: Mapping[str, object]) -> str:
    hash_input = {
        key: boundary[key]
        for key in (
            "schema_version",
            "provider_name",
            "parent_session_id",
            "result_session_id",
            "mode",
            "source_messages",
        )
    }
    canonical = json.dumps(
        hash_input,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _origin_id(
    *,
    message: Mapping[str, object],
    content: str,
    session_id: str,
    message_index: int,
    subject_id: str,
    host_id: str,
    boundary_id: str,
) -> str:
    message_id = message.get("message_id") or message.get("platform_message_id")
    source_ref = message.get("source_ref")
    row_id = message.get("_row_id")
    timestamp = message.get("timestamp")
    if isinstance(message_id, (str, int)) and not isinstance(message_id, bool):
        identity: tuple[object, ...] = ("platform-message", str(message_id))
    elif isinstance(source_ref, (str, int)) and not isinstance(source_ref, bool):
        identity = ("durable-boundary-source", boundary_id, str(source_ref))
    elif isinstance(row_id, int) and not isinstance(row_id, bool):
        identity = ("row", row_id)
    elif isinstance(timestamp, (str, int, float)) and not isinstance(timestamp, bool):
        identity = ("timestamp-content", str(timestamp), sha256(content.encode()).hexdigest())
    else:
        identity = (
            "session-position-content",
            session_id,
            message_index,
            sha256(content.encode()).hexdigest(),
        )
    canonical = json.dumps(
        {"host": host_id, "subject": subject_id, "identity": identity},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "weftmate:" + sha256(canonical.encode()).hexdigest()


def _candidates(
    messages: Sequence[Mapping[str, object]],
    *,
    session_id: str,
    subject_id: str,
    host_id: str,
    boundary_id: str,
) -> tuple[HermesBoundaryEvidenceCandidate, ...]:
    candidates: list[HermesBoundaryEvidenceCandidate] = []
    preceding_assistant: str | None = None
    for index, message in enumerate(messages):
        role = message.get("role")
        content = _text_content(message.get("content"))
        if bool(message.get("synthetic")):
            continue
        if role == "assistant":
            if content.strip():
                preceding_assistant = content
            continue
        if role != "user" or not content.strip():
            continue
        if _is_synthetic_user_message(message, content):
            continue
        candidates.append(
            HermesBoundaryEvidenceCandidate(
                raw_content=content,
                origin_id=_origin_id(
                    message=message,
                    content=content,
                    session_id=session_id,
                    message_index=index,
                    subject_id=subject_id,
                    host_id=host_id,
                    boundary_id=boundary_id,
                ),
                occurred_at=_occurred_at(message.get("timestamp")),
                preceding_ai_context=preceding_assistant,
            )
        )
    return tuple(candidates)


def _validate_boundary_envelope(boundary: Mapping[str, object]) -> tuple[str, list[Mapping[str, object]]]:
    """Validate the WeftMate boundary envelope (hermes contract with DSH prefix)."""

    unexpected_keys = set(boundary) - _BOUNDARY_KEYS
    if unexpected_keys:
        raise DshBoundaryError("boundary contains unsupported fields")
    schema_version = boundary.get("schema_version")
    event_id = boundary.get("event_id")
    provider_name = boundary.get("provider_name")
    parent_session_id = boundary.get("parent_session_id")
    result_session_id = boundary.get("result_session_id")
    mode = boundary.get("mode")
    source_messages = boundary.get("source_messages")
    payload_hash = boundary.get("payload_hash")
    if schema_version != 1:
        raise DshBoundaryError("unsupported boundary schema version")
    if (
        not isinstance(event_id, str)
        or not event_id
        or event_id != event_id.strip()
        or len(event_id) > 255
    ):
        raise DshBoundaryError("boundary event_id is invalid")
    if provider_name != _BOUNDARY_PROVIDER_NAME:
        raise DshBoundaryError("boundary targets a different provider")
    if not isinstance(parent_session_id, str) or not parent_session_id.strip():
        raise DshBoundaryError("boundary parent_session_id must be non-empty")
    if not isinstance(result_session_id, str) or not result_session_id.strip():
        raise DshBoundaryError("boundary result_session_id must be non-empty")
    if mode not in _BOUNDARY_MODES:
        raise DshBoundaryError("boundary mode is unsupported")
    if mode == "in_place" and parent_session_id != result_session_id:
        raise DshBoundaryError("in-place boundary must retain its parent session target")
    if mode == "rotation" and parent_session_id == result_session_id:
        raise DshBoundaryError("rotation boundary must target a distinct result session")
    if (
        not isinstance(payload_hash, str)
        or len(payload_hash) != 64
        or any(char not in "0123456789abcdef" for char in payload_hash)
    ):
        raise DshBoundaryError("boundary payload_hash is invalid")
    if not isinstance(source_messages, list):
        raise DshBoundaryError("boundary source_messages must be a list")
    if not source_messages:
        raise DshBoundaryError("boundary source_messages must not be empty")
    normalized_messages: list[Mapping[str, object]] = []
    source_refs: set[str] = set()
    for message_index, message in enumerate(source_messages):
        if not isinstance(message, Mapping):
            raise DshBoundaryError("boundary source_messages must contain only mappings")
        if set(message) - _BOUNDARY_MESSAGE_KEYS:
            raise DshBoundaryError("boundary source message contains unsupported fields")
        role = message.get("role")
        content = message.get("content")
        source_ref = message.get("source_ref")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            raise DshBoundaryError("boundary source message must contain a clean role and text")
        if (
            not isinstance(source_ref, str)
            or source_ref != f"source:{message_index}"
            or source_ref in source_refs
        ):
            raise DshBoundaryError("boundary source_ref must be a contiguous source ordinal")
        source_refs.add(source_ref)
        for flag in ("observed", "synthetic"):
            if flag in message and not isinstance(message[flag], bool):
                raise DshBoundaryError("boundary source flags must be boolean")
            if message.get(flag) is True:
                raise DshBoundaryError("boundary cannot deliver observed or synthetic source rows")
        timestamp = message.get("timestamp")
        if timestamp is not None and (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(float(timestamp))
        ):
            raise DshBoundaryError("boundary timestamp must be a finite number")
        if timestamp is not None and _occurred_at(timestamp) is None:
            raise DshBoundaryError("boundary timestamp is outside the supported UTC range")
        for scalar_key in ("platform_message_id", "message_id", "display_kind"):
            scalar = message.get(scalar_key)
            if scalar is None:
                continue
            if not isinstance(scalar, (str, int, float, bool)) or (
                isinstance(scalar, float) and not math.isfinite(scalar)
            ):
                raise DshBoundaryError("boundary source identity fields must be finite scalars")
        normalized_messages.append(dict(message))

    try:
        calculated_payload_hash = _boundary_payload_hash(
            {
                "schema_version": schema_version,
                "provider_name": provider_name,
                "parent_session_id": parent_session_id,
                "result_session_id": result_session_id,
                "mode": mode,
                "source_messages": normalized_messages,
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DshBoundaryError("boundary payload cannot be canonicalized") from exc
    if calculated_payload_hash != payload_hash:
        raise DshBoundaryError("boundary payload hash does not match")
    event_parts = event_id.split(":")
    if (
        len(event_parts) != 3
        or event_parts[0] != _BOUNDARY_EVENT_PREFIX
        or len(event_parts[1]) != 32
        or any(char not in "0123456789abcdef" for char in event_parts[1])
        or event_parts[2] != payload_hash
    ):
        raise DshBoundaryError("boundary event_id does not bind its occurrence and payload hash")
    return str(event_id), normalized_messages


def default_one_shot_route() -> Callable[..., Mapping[str, object]] | None:
    """Host-owned strict one-shot route (AUTHORITY budget: exactly 0 or 1 per boundary).

    Test support: ``MEMOWEFT_TEST_MODEL_RESPONSE`` pins a canned interpretation so
    WeftMate contract tests can drive a real durable pipeline with zero network
    and zero real model calls.  Guarded behind ``MEMOWEFT_TESTING=1`` so a stray
    test variable can never hijack a production process.  Production: DeepSeek
    API via env-injected credentials (``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL``,
    never logged).  Temperature is pinned to 0 — the evaluated optimum
    (deepseek-v4-flash + temperature 0, see the eval harness); the model name
    defaults to the public ``deepseek-chat`` and can be overridden with
    ``MEMOWEFT_WORLD_MODEL`` to match the host's routed model.
    """

    testing = os.environ.get("MEMOWEFT_TESTING") == "1"
    mock = os.environ.get("MEMOWEFT_TEST_MODEL_RESPONSE")
    if mock is not None and not testing:
        logger.warning(
            "MEMOWEFT_TEST_MODEL_RESPONSE ignored: MEMOWEFT_TESTING=1 is required "
            "(test-route guard; production keeps the real API route)"
        )
        mock = None
    if mock == "__smart__":
        # 测试专用自适应解释（WeftMate 契约测试）：解析宿主 payload 里第一条
        # user Evidence（id + 原文），按 stated 归一（我→用户）生成 V8 信封——
        # 与真实模型同一条编译/锚定/落库链路，零网络、零真实模型调用。
        def smart_mock(messages: object, session_id: str = "") -> Mapping[str, object]:
            del session_id
            try:
                import json as _json

                payload = _json.loads(str(messages[-1]["content"]))  # type: ignore[index]
                evidence = payload.get("evidence") or []
                first = evidence[0] if evidence else None
                if first is None:
                    return {"content": _json.dumps({"schema_version": 8, "result": "cognitions", "cognitions": []}, ensure_ascii=False), "model": "mock", "usage": {"total_tokens": 0}}
                first_text = str(first["text"])
                proposition = first_text.replace("我", "用户", 1) if first_text.startswith("我") else first_text
                envelope = {
                    "schema_version": 8,
                    "result": "cognitions",
                    "cognitions": [
                        {
                            "action": "form",
                            "target": "owner_self",
                            "statement_kind": "preference",
                            "formed_by": "stated",
                            "proposition": proposition,
                            "supports": [{"evidence_id": str(first["id"]), "start": 0, "end": len(first_text)}],
                        }
                    ],
                }
                return {"content": _json.dumps(envelope, ensure_ascii=False), "model": "mock", "usage": {"total_tokens": 0}}
            except Exception:  # noqa: BLE001 - 测试 mock 任何失败退化为空解释
                return {"content": '{"schema_version": 8, "result": "cognitions", "cognitions": []}', "model": "mock", "usage": {"total_tokens": 0}}
        return smart_mock
    if mock is not None:
        def mock_route(messages: object, session_id: str = "") -> Mapping[str, object]:
            del messages, session_id
            return {"content": mock, "model": "mock", "usage": {"total_tokens": 0}}
        return mock_route

    api_key = os.environ.get("DEEPSEEK_API_KEY") or ""
    if not api_key:
        return None  # model-free no_change processor stays the production default

    import httpx

    base = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("MEMOWEFT_WORLD_MODEL") or "deepseek-chat"

    def route(messages: object, session_id: str = "") -> Mapping[str, object]:
        del session_id
        response = httpx.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "stream": False, "temperature": 0},
            timeout=120.0,
        )
        response.raise_for_status()
        payload = response.json()
        return {
            "content": payload["choices"][0]["message"]["content"],
            "model": model,
            "usage": payload.get("usage") or {},
        }

    return route


class _DshRuntimeStore:
    def __init__(self, db_path: Path, *, subject_id: str, host_id: str) -> None:
        self._db_path = db_path
        self._subject_id = subject_id
        self._host_id = host_id
        self._lock = threading.Lock()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def subject_id(self) -> str:
        return self._subject_id

    @property
    def host_id(self) -> str:
        return self._host_id

    def initialize(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        _assert_existing_database_is_current(self._db_path)
        db = open_db(str(self._db_path))
        db.close()

    def accept_durable_boundary(self, boundary: ValidatedHermesBoundary) -> dict[str, object]:
        with self._lock:
            db = open_db(str(self._db_path))
            try:
                return HermesBoundaryStore(db).accept(boundary).as_dict()
            finally:
                db.close()


class DshMemoWeftRuntime:
    """WeftMate/DSH-facing runtime behind the stdio bridge."""

    def __init__(self) -> None:
        self._ingestor: _DshRuntimeStore | None = None
        self._world_worker: WorldJobWorker | None = None
        self._enabled = False
        self._last_recall_count = 0

    @property
    def db_path(self) -> Path | None:
        return self._ingestor.db_path if self._ingestor is not None else None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def subject_id(self) -> str | None:
        return self._ingestor.subject_id if self._ingestor is not None else None

    @property
    def host_id(self) -> str | None:
        return self._ingestor.host_id if self._ingestor is not None else None

    @property
    def last_recall_count(self) -> int:
        return self._last_recall_count

    def initialize(self, session_id: str, **kwargs: Any) -> dict[str, str]:
        self.shutdown()
        self._ingestor = None
        dsh_home = Path(str(kwargs.get("dsh_home") or "."))
        platform = str(kwargs.get("platform") or "dsh")
        identity_source = str(kwargs.get("user_id") or kwargs.get("agent_identity") or "local-user")
        model_tier = kwargs.get("model_tier", "cloud")
        if model_tier not in ("cloud", "local"):
            raise DshBoundaryError("model_tier must be 'cloud' or 'local'")
        if "subject_id" in kwargs:
            explicit_subject_id = kwargs["subject_id"]
            if (
                not isinstance(explicit_subject_id, str)
                or not explicit_subject_id
                or explicit_subject_id != explicit_subject_id.strip()
                or len(explicit_subject_id) > 512
            ):
                raise DshBoundaryError("subject_id must be a non-empty stable identifier")
            subject_id = explicit_subject_id
        else:
            subject_id = "weftmate-user-" + sha256(
                f"{platform}:{identity_source}".encode()
            ).hexdigest()[:24]
        db_path = dsh_home / "memoweft" / "memoweft.sqlite3"
        self._ingestor = _DshRuntimeStore(
            db_path,
            subject_id=subject_id,
            host_id=f"weftmate:{platform}",
        )
        self._ingestor.initialize()
        route = kwargs.get("one_shot_llm")
        if route is None and kwargs.get("auto_route", True):
            route = default_one_shot_route()
        processor = None
        if callable(route):
            lang = kwargs.get("lang")
            if lang not in (None, "zh", "en"):
                raise DshBoundaryError("lang must be None, 'zh' or 'en'")
            processor = HermesBatchAdapterProcessor(
                str(db_path), route, lang=lang, model_tier=model_tier
            )
        self._world_worker = WorldJobWorker(db_path, processor=processor)
        self._world_worker.start()
        self._enabled = True
        return {
            "db_path": str(db_path),
            "subject_id": subject_id,
            "host_id": self._ingestor.host_id,
            "session_id": str(session_id),
        }

    def ingest_durable_boundary(self, boundary: Mapping[str, object]) -> dict[str, object]:
        if not self._enabled or self._ingestor is None:
            raise DshBoundaryError("runtime is not initialized for durable writes")
        event_id, normalized_messages = _validate_boundary_envelope(boundary)
        candidates = _candidates(
            normalized_messages,
            session_id=str(boundary["parent_session_id"]),
            subject_id=self._ingestor.subject_id,
            host_id=self._ingestor.host_id,
            boundary_id=event_id,
        )
        accepted_boundary = ValidatedHermesBoundary(
            event_id=event_id,
            payload_hash=str(boundary["payload_hash"]),
            formal_target=HermesBoundaryFormalTarget(
                boundary_schema_version=cast(int, boundary["schema_version"]),
                provider_name=str(boundary["provider_name"]),
                parent_session_id=str(boundary["parent_session_id"]),
                result_session_id=str(boundary["result_session_id"]),
                mode=str(boundary["mode"]),
                subject_id=self._ingestor.subject_id,
                host_id=self._ingestor.host_id,
            ),
            evidence=candidates,
        )
        receipt = self._ingestor.accept_durable_boundary(accepted_boundary)
        worker = self._world_worker
        if worker is not None:
            try:
                if not worker.kick():
                    logger.warning("MemoWeft World worker was unavailable after receipt")
            except Exception as exc:
                logger.warning("MemoWeft World worker wake failed: error_type=%s", type(exc).__name__)
        return receipt

    def prefetch(self, query: str, *, session_id: str = "") -> dict[str, object]:
        """Deterministic read-only Recall (zero model calls, zero writes).

        Shares the Hermes graph-aware, permission-gated implementation: entity
        names/aliases participate in matching; rows whose Evidence disallows
        local reads are never injected.
        """

        del session_id
        self._last_recall_count = 0
        if not self._enabled or self._ingestor is None:
            return {"text": "", "count": 0}
        db_path = self._ingestor.db_path
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return {"text": "", "count": 0}
        try:
            text, count = recall_world_text(db, self._ingestor.subject_id, query)
            self._last_recall_count = count
            return {"text": text, "count": count}
        except sqlite3.Error:
            return {"text": "", "count": 0}
        finally:
            db.close()

    def list_world(self) -> dict[str, object]:
        """Read-only browse view for the management page: current, non-invalid World rows."""

        if not self._enabled or self._ingestor is None:
            return {"cognitions": [], "entities": [], "relationships": [], "events": []}
        db_path = self._ingestor.db_path
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return {"cognitions": [], "entities": [], "relationships": [], "events": []}
        try:
            subject = self._ingestor.subject_id
            cognitions = [
                {"id": str(r[0]), "content": str(r[1]), "content_type": str(r[2]), "confidence": int(r[3])}
                for r in db.execute(
                    "SELECT id, content, content_type, confidence FROM cognition "
                    "WHERE subject_id = ? AND invalid_at IS NULL AND archived_at IS NULL AND muted_at IS NULL "
                    "ORDER BY created_at, id", (subject,),
                ).fetchall()
                if world_item_visible(db, subject, "cognition", str(r[0]), surface="export")
            ]
            entities = [
                {"id": str(r[0]), "canonical_name": str(r[1]), "kind": str(r[2])}
                for r in db.execute(
                    "SELECT id, canonical_name, kind FROM entity WHERE world_id = ? AND invalid_at IS NULL "
                    "ORDER BY created_at, id", (subject,),
                ).fetchall()
                if world_item_visible(db, subject, "entity", str(r[0]), surface="export")
            ]
            relationships = [
                {"id": str(r[0]), "content": str(r[1]), "confidence": int(r[2])}
                for r in db.execute(
                    "SELECT id, content, confidence FROM relationship WHERE world_id = ? AND invalid_at IS NULL "
                    "ORDER BY created_at, id", (subject,),
                ).fetchall()
                if world_item_visible(db, subject, "relationship", str(r[0]), surface="export")
            ]
            events = [
                {"id": str(r[0]), "content": str(r[1]), "confidence": int(r[2])}
                for r in db.execute(
                    "SELECT id, content, confidence FROM world_event WHERE world_id = ? AND invalid_at IS NULL "
                    "ORDER BY created_at, id", (subject,),
                ).fetchall()
                if world_item_visible(db, subject, "event", str(r[0]), surface="export")
            ]
            return {
                "cognitions": cognitions,
                "entities": entities,
                "relationships": relationships,
                "events": events,
            }
        except sqlite3.Error:
            return {"cognitions": [], "entities": [], "relationships": [], "events": []}
        finally:
            db.close()

    def export_world(self) -> dict[str, object]:
        """Read-only export: World rows plus supporting Evidence with provenance
        (raw text kept).  Permission-gated: Evidence rows that disallow local
        reads and World rows whose Evidence is not visible are excluded."""

        if not self._enabled or self._ingestor is None:
            return {"cognitions": [], "evidence": []}
        db_path = self._ingestor.db_path
        try:
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        except sqlite3.Error:
            return {"cognitions": [], "evidence": []}
        try:
            subject = self._ingestor.subject_id
            cognitions = [
                {
                    "id": str(r[0]), "content": str(r[1]), "content_type": str(r[2]),
                    "confidence": int(r[3]),
                    "evidence_ids": list(linked_evidence(db, "cognition", str(r[0]))),
                }
                for r in db.execute(
                    "SELECT c.id, c.content, c.content_type, c.confidence "
                    "FROM cognition c WHERE c.subject_id = ? "
                    "AND c.invalid_at IS NULL AND c.archived_at IS NULL AND c.muted_at IS NULL "
                    "ORDER BY c.created_at, c.id", (subject,),
                ).fetchall()
                if world_item_visible(db, subject, "cognition", str(r[0]), surface="export")
            ]
            evidence = [
                {
                    "id": str(r[0]), "raw_content": str(r[1]), "origin_id": str(r[2]),
                    "source_kind": str(r[3]), "host_id": str(r[4]), "occurred_at": str(r[5]),
                }
                for r in db.execute(
                    "SELECT id, raw_content, origin_id, source_kind, host_id, occurred_at, "
                    "deleted_at, allow_local_read, allow_cloud_read, allow_inference FROM evidence "
                    "WHERE subject_id = ? ORDER BY recorded_at, id",
                    (subject,),
                ).fetchall()
                if evidence_state(
                    {
                        "deleted_at": r[6],
                        "allow_local_read": r[7],
                        "allow_cloud_read": r[8],
                        "allow_inference": r[9],
                    },
                    surface="export",
                )
                is None
            ]
            return {"cognitions": cognitions, "evidence": evidence}
        except sqlite3.Error:
            return {"cognitions": [], "evidence": []}
        finally:
            db.close()

    def health(self) -> dict[str, object]:
        return {
            "enabled": self._enabled,
            "db_path": str(self.db_path) if self.db_path is not None else None,
            "subject_id": self.subject_id,
            "host_id": self.host_id,
            "last_recall_count": self._last_recall_count,
            "worker_running": self._world_worker is not None,
        }

    def kick_world_worker(self) -> bool:
        """Wake the existing worker after an RPC-created follow-up Job."""

        worker = self._world_worker
        if not self._enabled or worker is None:
            return False
        try:
            return worker.kick()
        except Exception as exc:  # noqa: BLE001 - host wake failure stays diagnostic
            logger.warning(
                "MemoWeft World worker wake failed: error_type=%s",
                type(exc).__name__,
            )
            return False

    def shutdown(self) -> None:
        worker = self._world_worker
        self._world_worker = None
        self._enabled = False
        if worker is not None:
            worker.shutdown()


__all__ = ["DshBoundaryError", "DshMemoWeftRuntime", "default_one_shot_route"]
