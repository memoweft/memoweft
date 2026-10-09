"""Versioned DSH JSON RPC v2 protocol over the production Core services.

The protocol layer owns envelopes, closed parameter shapes, request correlation,
and in-process replay detection. Business idempotency remains in the durable
boundary, Trust Command, Clarification, and Portable services.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping, cast

from . import DshBoundaryError, DshMemoWeftRuntime
from ..hermes.boundary_store import BoundaryHardDeletedSourceError
from .interactions import InteractionQueryError
from .observed import ObservedError, ObservedService
from ..trust import (
    CLARIFICATION_SCHEMA_VERSION,
    PORTABLE_CAPABILITIES_VERSION,
    PORTABLE_SERVICE_SCHEMA_VERSION,
    TRUST_CAPABILITIES_VERSION,
    TRUST_SCHEMA_VERSION,
    ClarificationError,
    ClarificationService,
    CommandService,
    PortableError,
    PortableService,
    QueryService,
    TrustCommandError,
    TrustQueryError,
)


DSH_RPC_PROTOCOL = "memoweft.dsh_rpc"
DSH_RPC_PROTOCOL_VERSION = 2
DSH_RPC_SCHEMA_VERSION = 1
DSH_RPC_CAPABILITIES_VERSION = "dsh-rpc-v2"

DSH_RPC_METHODS: tuple[str, ...] = (
    "initialize",
    "capabilities",
    "ingest_boundary",
    "upsert_observed",
    "update_observed_permissions",
    "retract_observed",
    "prefetch",
    "query_world",
    "query_evidence",
    "query_provenance",
    "query_jobs",
    "preview_recall",
    "preview_recall_batch",
    "preview_forget",
    "query_interactions",
    "query_interaction",
    "link_interaction_dependencies",
    "submit_command",
    "query_command_receipt",
    "retry_delete_storage_cleanup",
    "erase_conversation_context",
    "list_clarifications",
    "answer_clarification",
    "portable_plan",
    "portable_export",
    "portable_import",
    "health",
    "shutdown",
)

_METHOD_SET = frozenset(DSH_RPC_METHODS)
_PRE_INITIALIZE_METHODS = frozenset(
    {"initialize", "capabilities", "health", "shutdown"}
)
_REQUEST_KEYS = frozenset(
    {
        "protocol",
        "protocol_version",
        "schema_version",
        "request_id",
        "method",
        "params",
    }
)
_INITIALIZE_KEYS = frozenset(
    {
        "session_id",
        "dsh_home",
        "platform",
        "user_id",
        "agent_identity",
        "subject_id",
        "clarification_host_id",
        "auto_route",
        "model_tier",
        "lang",
        "model_api_key",
    }
)


class DshRpcProtocolError(ValueError):
    """Stable fail-closed protocol validation error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_rpc_json(value: object) -> str:
    """Canonical JSON used only for request replay identity."""

    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise DshRpcProtocolError("request_not_canonical") from exc


def _request_fingerprint(request: Mapping[str, object]) -> str:
    return sha256(canonical_rpc_json(dict(request)).encode("utf-8")).hexdigest()


def _identifier(value: object, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 512
    ):
        raise DshRpcProtocolError(code)
    return value


def _require_params(
    params: Mapping[str, object],
    *,
    allowed: frozenset[str],
    required: frozenset[str] = frozenset(),
) -> dict[str, object]:
    keys = frozenset(params)
    if keys - allowed:
        raise DshRpcProtocolError("unexpected_method_parameter")
    if required - keys:
        raise DshRpcProtocolError("missing_method_parameter")
    return dict(params)


class DshRpcV2Server:
    """One long-lived RPC server bound to one DSH runtime identity."""

    def __init__(self, runtime: DshMemoWeftRuntime | None = None) -> None:
        self._runtime = runtime or DshMemoWeftRuntime()
        self._query: QueryService | None = None
        self._command: CommandService | None = None
        self._clarification: ClarificationService | None = None
        self._portable: PortableService | None = None
        self._clarification_host_id: str | None = None
        self._replay: dict[str, tuple[str, dict[str, object]]] = {}
        self._shutdown_requested = False

    @property
    def runtime(self) -> DshMemoWeftRuntime:
        return self._runtime

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    def handle(self, raw_request: object) -> dict[str, object]:
        """Validate, dispatch, and envelope one JSON-native request."""

        request_id = self._possible_request_id(raw_request)
        fingerprint: str | None = None
        try:
            request = self._request(raw_request)
            request_id = cast(str, request["request_id"])
            fingerprint = _request_fingerprint(request)
            prior = self._replay.get(request_id)
            if prior is not None:
                if prior[0] != fingerprint:
                    return self.error_response(
                        "request_id_conflict", request_id=request_id
                    )
                return deepcopy(prior[1])
            method = cast(str, request["method"])
            if not self._runtime.enabled and method not in _PRE_INITIALIZE_METHODS:
                raise DshRpcProtocolError("not_initialized")
            result, result_code = self._dispatch(
                method, cast(dict[str, object], request["params"])
            )
            response = self._success_response(
                request_id=request_id,
                result_code=result_code,
                result=result,
            )
            self._replay[request_id] = (fingerprint, deepcopy(response))
            return response
        except BaseException as exc:  # noqa: BLE001 - protocol must return one line
            response = self.error_response(
                self._exception_code(exc),
                request_id=request_id,
                error_type=self._exception_type(exc),
            )
            if (
                request_id is not None
                and fingerprint is not None
                and request_id not in self._replay
            ):
                self._replay[request_id] = (fingerprint, deepcopy(response))
            return response

    def error_response(
        self,
        code: str,
        *,
        request_id: str | None = None,
        error_type: str = "protocol",
    ) -> dict[str, object]:
        """Build a stable error envelope without traceback or user content."""

        return {
            "protocol": DSH_RPC_PROTOCOL,
            "protocol_version": DSH_RPC_PROTOCOL_VERSION,
            "schema_version": DSH_RPC_SCHEMA_VERSION,
            "request_id": request_id,
            "ok": False,
            "result_code": code,
            "world_revision": self._current_world_revision(),
            "error": {"type": error_type, "code": code},
        }

    @staticmethod
    def _possible_request_id(raw_request: object) -> str | None:
        if not isinstance(raw_request, Mapping):
            return None
        value = raw_request.get("request_id")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _request(raw_request: object) -> dict[str, object]:
        if not isinstance(raw_request, Mapping):
            raise DshRpcProtocolError("invalid_request")
        request = dict(raw_request)
        if frozenset(request) != _REQUEST_KEYS:
            raise DshRpcProtocolError("invalid_request_envelope")
        if request["protocol"] != DSH_RPC_PROTOCOL:
            raise DshRpcProtocolError("unsupported_protocol")
        if (
            type(request["protocol_version"]) is not int
            or request["protocol_version"] != DSH_RPC_PROTOCOL_VERSION
        ):
            raise DshRpcProtocolError("unsupported_protocol_version")
        if (
            type(request["schema_version"]) is not int
            or request["schema_version"] != DSH_RPC_SCHEMA_VERSION
        ):
            raise DshRpcProtocolError("unsupported_request_schema")
        request["request_id"] = _identifier(
            request["request_id"], "invalid_request_id"
        )
        method = _identifier(request["method"], "invalid_method")
        if method not in _METHOD_SET:
            raise DshRpcProtocolError("unknown_method")
        request["method"] = method
        params = request["params"]
        if not isinstance(params, Mapping):
            raise DshRpcProtocolError("invalid_method_params")
        request["params"] = dict(params)
        canonical_rpc_json(request)
        return request

    def _bind_services(self) -> None:
        db_path = self._runtime.db_path
        subject_id = self._runtime.subject_id
        host_id = self._runtime.host_id
        if db_path is None or subject_id is None or host_id is None:
            raise DshRpcProtocolError("runtime_identity_unavailable")
        self._query = QueryService(db_path, subject_id=subject_id)
        self._command = CommandService(
            db_path, subject_id=subject_id, host_id=host_id
        )
        self._clarification = ClarificationService(
            db_path,
            subject_id=subject_id,
            host_id=self._clarification_host_id or host_id,
        )
        self._portable = PortableService(
            db_path, subject_id=subject_id, host_id=host_id
        )

    def _services(
        self,
    ) -> tuple[QueryService, CommandService, ClarificationService, PortableService]:
        if self._runtime.enabled and (
            self._query is None
            or self._command is None
            or self._clarification is None
            or self._portable is None
        ):
            self._bind_services()
        if (
            self._query is None
            or self._command is None
            or self._clarification is None
            or self._portable is None
        ):
            raise DshRpcProtocolError("not_initialized")
        return self._query, self._command, self._clarification, self._portable

    def _dispatch(
        self, method: str, params: Mapping[str, object]
    ) -> tuple[object, str]:
        if method == "initialize":
            raw = _require_params(params, allowed=_INITIALIZE_KEYS)
            session_id = str(raw.pop("session_id", "") or "")
            clarification_host_id = raw.pop("clarification_host_id", None)
            if clarification_host_id is not None:
                clarification_host_id = _identifier(
                    clarification_host_id, "invalid_clarification_host_id"
                )
                if not clarification_host_id.startswith("hermes:"):
                    raise DshRpcProtocolError("untrusted_clarification_host_id")
            if "subject_id" in raw:
                raw["subject_id"] = _identifier(
                    raw["subject_id"], "invalid_subject_id"
                )
            initialized = self._runtime.initialize(session_id, **raw)
            self._clarification_host_id = clarification_host_id or initialized["host_id"]
            initialized["clarification_host_id"] = self._clarification_host_id
            self._bind_services()
            return {
                "runtime": initialized,
                "capabilities": self._capabilities(),
            }, "initialized"
        if method == "capabilities":
            _require_params(params, allowed=frozenset())
            return self._capabilities(), "capabilities"
        if method == "health":
            _require_params(params, allowed=frozenset())
            runtime_health = self._runtime.health()
            runtime_health["clarification_host_id"] = self._clarification_host_id
            return {
                "protocol": DSH_RPC_PROTOCOL,
                "protocol_version": DSH_RPC_PROTOCOL_VERSION,
                "runtime": runtime_health,
            }, "healthy"
        if method == "shutdown":
            _require_params(params, allowed=frozenset())
            self._runtime.shutdown()
            self._shutdown_requested = True
            return {"shutdown": True}, "shutdown"

        query, command, clarification, portable = self._services()
        if method in {"upsert_observed", "update_observed_permissions", "retract_observed"}:
            assert self._runtime.db_path is not None
            assert self._runtime.subject_id is not None and self._runtime.host_id is not None
            result = ObservedService(self._runtime.db_path, subject_id=self._runtime.subject_id,
                host_id=self._runtime.host_id).execute(method, params)
            # Content-bearing response replays must not survive source mutation.
            self._replay.clear()
            return result, "observed_" + str(result["result_state"])
        if method == "ingest_boundary":
            raw = _require_params(
                params,
                allowed=frozenset({"boundary"}),
                required=frozenset({"boundary"}),
            )
            boundary = raw["boundary"]
            if not isinstance(boundary, Mapping):
                raise DshRpcProtocolError("invalid_boundary_parameter")
            return self._runtime.ingest_durable_boundary(boundary), "boundary_accepted"
        if method == "prefetch":
            raw = _require_params(
                params,
                allowed=frozenset({"query", "session_id", "model_tier"}),
                required=frozenset({"query"}),
            )
            text = raw["query"]
            prefetch_session_id = raw.get("session_id", "")
            if not isinstance(text, str) or not isinstance(prefetch_session_id, str):
                raise DshRpcProtocolError("invalid_recall_parameter")
            return self._runtime.prefetch(
                text, session_id=prefetch_session_id, model_tier=cast(Any, raw.get("model_tier", "cloud"))
            ), "recall_ready"
        if method == "query_world":
            return query.execute_provider_tool(
                "memoweft_query_world", params
            ), "query_ok"
        if method == "query_evidence":
            return query.execute_provider_tool(
                "memoweft_query_evidence", params
            ), "query_ok"
        if method == "query_provenance":
            raw = _require_params(
                params,
                allowed=frozenset({"object_kind", "item_id", "projection"}),
                required=frozenset({"object_kind", "item_id"}),
            )
            projection = raw.get("projection", "history")
            if not isinstance(projection, str):
                raise DshRpcProtocolError("invalid_provenance_projection")
            result = query.get_world_item_provenance(
                cast(Any, raw["object_kind"]),
                cast(str, raw["item_id"]),
                projection=projection,
            )
            return result, "query_ok"
        if method == "query_jobs":
            return query.execute_provider_tool(
                "memoweft_query_jobs", params
            ), "query_ok"
        if method == "preview_recall":
            params = {"model_tier": "cloud", **params}
            return query.execute_provider_tool(
                "memoweft_preview_recall", params
            ), "recall_preview"
        if method == "preview_recall_batch":
            raw = _require_params(params, allowed=frozenset({"queries", "model_tier"}), required=frozenset({"queries"}))
            return query.preview_recall_batch(cast(Any, raw["queries"]), model_tier=cast(Any, raw.get("model_tier", "cloud"))), "recall_preview"
        if method == "query_interactions":
            raw = _require_params(
                params,
                allowed=frozenset(
                    {
                        "query",
                        "session_id",
                        "projection",
                        "conversation_id",
                        "user_message_id",
                        "search_mode",
                        "model_tier",
                    }
                ),
            )
            query_text = raw.get("query")
            interaction_session_id = raw.get("session_id", "")
            projection = raw.get("projection", "history")
            conversation_id = raw.get("conversation_id")
            user_message_id = raw.get("user_message_id")
            search_mode = raw.get("search_mode")
            if (
                (query_text is not None and not isinstance(query_text, str))
                or not isinstance(interaction_session_id, str)
                or not isinstance(projection, str)
                or (
                    conversation_id is not None
                    and not isinstance(conversation_id, str)
                )
                or (
                    user_message_id is not None
                    and not isinstance(user_message_id, str)
                )
                or (search_mode is not None and not isinstance(search_mode, str))
            ):
                raise InteractionQueryError("invalid_interaction_query")
            return self._runtime.query_interactions(
                query_text,
                session_id=interaction_session_id,
                projection=projection,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                search_mode=search_mode,
                model_tier=cast(Any, raw.get("model_tier", "cloud")),
            ), "interactions_found"
        if method == "query_interaction":
            raw = _require_params(
                params,
                allowed=frozenset({"id", "projection", "model_tier"}),
                required=frozenset({"id"}),
            )
            interaction_id = raw["id"]
            projection = raw.get("projection", "history")
            if not isinstance(interaction_id, str) or not isinstance(projection, str):
                raise InteractionQueryError("invalid_interaction_id")
            return self._runtime.query_interaction(
                interaction_id, projection=projection, model_tier=cast(Any, raw.get("model_tier", "cloud"))
            ), "interaction_found"
        if method == "link_interaction_dependencies":
            raw = _require_params(
                params,
                allowed=frozenset(
                    {
                        "conversation_id",
                        "user_message_id",
                        "assistant_message_id",
                        "expected_context_hash",
                        "model_context_dependencies",
                    }
                ),
                required=frozenset(
                    {
                        "conversation_id",
                        "user_message_id",
                        "assistant_message_id",
                        "expected_context_hash",
                        "model_context_dependencies",
                    }
                ),
            )
            conversation_id = _identifier(
                raw["conversation_id"], "invalid_conversation_id"
            )
            user_message_id = _identifier(
                raw["user_message_id"], "invalid_user_message_id"
            )
            assistant_message_id = _identifier(
                raw["assistant_message_id"], "invalid_assistant_message_id"
            )
            expected_context_hash = _identifier(
                raw["expected_context_hash"], "invalid_context_hash"
            )
            if len(expected_context_hash) != 64 or any(
                char not in "0123456789abcdef" for char in expected_context_hash
            ):
                raise DshRpcProtocolError("invalid_context_hash")
            dependencies = raw["model_context_dependencies"]
            if not isinstance(dependencies, Mapping):
                raise DshRpcProtocolError("invalid_model_context_dependencies")
            result = self._runtime.link_interaction_dependencies(
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                expected_context_hash=expected_context_hash,
                model_context_dependencies=dependencies,
            )
            return result, f"interaction_dependencies_{result['result_state']}"
        if method == "preview_forget":
            raw = _require_params(params, allowed=frozenset({"target_kind", "target_id", "conversation_id"}), required=frozenset())
            if set(raw) not in ({"target_kind", "target_id"}, {"conversation_id"}):
                raise DshRpcProtocolError("invalid_forget_preview")
            kind = raw.get("target_kind")
            if kind is not None and kind not in {"evidence", "entity", "relationship", "event", "cognition"}:
                raise DshRpcProtocolError("invalid_world_target")
            assert self._runtime.db_path is not None and self._runtime.subject_id is not None
            from ..trust.forget_preview import preview_forget
            result = preview_forget(str(self._runtime.db_path), self._runtime.subject_id,
                target_kind=str(kind) if kind is not None else None,
                target_id=_identifier(raw["target_id"], "invalid_target_id") if "target_id" in raw else None,
                conversation_id=_identifier(raw["conversation_id"], "invalid_conversation_id") if "conversation_id" in raw else None)
            return result, "query_ok"
        if method == "erase_conversation_context":
            raw = _require_params(params, allowed=frozenset({"conversation_id"}), required=frozenset({"conversation_id"}))
            assert self._runtime.db_path is not None and self._runtime.subject_id is not None
            from ..trust.true_delete import erase_conversation_context
            result = erase_conversation_context(str(self._runtime.db_path), self._runtime.subject_id,
                                                _identifier(raw["conversation_id"], "invalid_conversation_id"))
            return result, "conversation_context_erased"
        if method == "submit_command":
            raw = _require_params(
                params,
                allowed=frozenset({"command"}),
                required=frozenset({"command"}),
            )
            value = raw["command"]
            if not isinstance(value, Mapping):
                raise DshRpcProtocolError("invalid_command_parameter")
            command_receipt = command.submit_command(value)
            return {
                "schema_version": TRUST_SCHEMA_VERSION,
                "subject_id": self._runtime.subject_id,
                "world_revision": command_receipt["after_revision"],
                "receipt": command_receipt,
            }, f"command_{command_receipt['result_state']}"
        if method == "query_command_receipt":
            raw = _require_params(
                params,
                allowed=frozenset({"command_id"}),
                required=frozenset({"command_id"}),
            )
            command_receipt = command.get_command_receipt(
                _identifier(raw["command_id"], "invalid_command_id")
            )
            return {
                "schema_version": TRUST_SCHEMA_VERSION,
                "subject_id": self._runtime.subject_id,
                "world_revision": command_receipt["after_revision"],
                "receipt": command_receipt,
            }, "command_receipt"
        if method == "retry_delete_storage_cleanup":
            raw = _require_params(
                params,
                allowed=frozenset({"command_id"}),
                required=frozenset({"command_id"}),
            )
            command_receipt = command.retry_delete_storage_cleanup(
                _identifier(raw["command_id"], "invalid_command_id")
            )
            return {
                "schema_version": TRUST_SCHEMA_VERSION,
                "subject_id": self._runtime.subject_id,
                "world_revision": command_receipt["after_revision"],
                "receipt": command_receipt,
            }, "delete_storage_cleanup_checked"
        if method == "list_clarifications":
            raw = _require_params(
                params,
                allowed=frozenset({"result_session_id", "state"}),
            )
            result_session_id = raw.get("result_session_id")
            state = raw.get("state")
            if result_session_id is not None and not isinstance(result_session_id, str):
                raise DshRpcProtocolError("invalid_result_session_id")
            if state is not None and not isinstance(state, str):
                raise DshRpcProtocolError("invalid_clarification_state")
            records = clarification.list_clarifications(
                result_session_id=result_session_id,
                state=cast(Any, state),
            )
            return {
                "schema_version": CLARIFICATION_SCHEMA_VERSION,
                "subject_id": self._runtime.subject_id,
                "clarification_host_id": self._clarification_host_id,
                "world_revision": self._current_world_revision(),
                "clarifications": records,
            }, "clarifications_listed"
        if method == "answer_clarification":
            raw = _require_params(
                params,
                allowed=frozenset(
                    {"clarification_id", "result_session_id", "answer"}
                ),
                required=frozenset(
                    {"clarification_id", "result_session_id", "answer"}
                ),
            )
            clarification_id = _identifier(
                raw["clarification_id"], "invalid_clarification_id"
            )
            result_session_id = _identifier(
                raw["result_session_id"], "invalid_result_session_id"
            )
            answer = raw["answer"]
            if not isinstance(answer, str):
                raise DshRpcProtocolError("invalid_clarification_answer")
            clarification_receipt = clarification.answer(
                clarification_id=clarification_id,
                result_session_id=result_session_id,
                answer=answer,
            )
            worker_woken = self._runtime.kick_world_worker()
            return {
                "schema_version": CLARIFICATION_SCHEMA_VERSION,
                "subject_id": self._runtime.subject_id,
                "world_revision": self._current_world_revision(),
                "receipt": clarification_receipt,
                "worker_woken": worker_woken,
            }, "clarification_answered"
        if method == "portable_plan":
            raw = _require_params(
                params,
                allowed=frozenset({"bundle"}),
                required=frozenset({"bundle"}),
            )
            bundle = raw["bundle"]
            if not isinstance(bundle, Mapping):
                raise DshRpcProtocolError("invalid_portable_bundle")
            plan = portable.plan_import(bundle)
            return plan, "portable_plan_valid" if plan["valid"] else "portable_plan_invalid"
        if method == "portable_export":
            raw = _require_params(
                params, allowed=frozenset({"exported_at"})
            )
            exported_at = raw.get("exported_at")
            if exported_at is not None and not isinstance(exported_at, str):
                raise DshRpcProtocolError("invalid_exported_at")
            return portable.export_bundle(exported_at=exported_at), "portable_exported"
        if method == "portable_import":
            raw = _require_params(
                params,
                allowed=frozenset(
                    {"operation", "bundle", "plan_hash", "receipt_id"}
                ),
                required=frozenset({"operation"}),
            )
            operation = raw["operation"]
            if operation == "get_receipt":
                _require_params(
                    raw,
                    allowed=frozenset({"operation", "receipt_id"}),
                    required=frozenset({"operation", "receipt_id"}),
                )
                portable_receipt = portable.get_receipt(
                    _identifier(raw["receipt_id"], "invalid_portable_receipt_id")
                )
                return portable_receipt, "portable_receipt"
            if operation == "apply":
                _require_params(
                    raw,
                    allowed=frozenset({"operation", "bundle", "plan_hash"}),
                    required=frozenset({"operation", "bundle", "plan_hash"}),
                )
                bundle = raw["bundle"]
                plan_hash = raw["plan_hash"]
                if not isinstance(bundle, Mapping):
                    raise DshRpcProtocolError("invalid_portable_bundle")
                if not isinstance(plan_hash, str):
                    raise DshRpcProtocolError("invalid_portable_plan_hash")
                portable_receipt = portable.apply_import(bundle, plan_hash=plan_hash)
                return portable_receipt, f"portable_{portable_receipt['result_state']}"
            raise DshRpcProtocolError("unsupported_portable_import_operation")
        raise DshRpcProtocolError("unknown_method")

    def _capabilities(self) -> dict[str, object]:
        initialized = self._runtime.enabled
        service_capabilities: dict[str, object] | None = None
        if initialized:
            query, command, _clarification, portable = self._services()
            service_capabilities = {
                "query": query.get_capabilities(),
                "command": command.get_capabilities(),
                "clarification": {
                    "schema_version": CLARIFICATION_SCHEMA_VERSION,
                    "operations": ["list", "answer"],
                    "durable_lifecycle": True,
                },
                "portable": portable.get_capabilities(),
            }
        return {
            "protocol": DSH_RPC_PROTOCOL,
            "protocol_version": DSH_RPC_PROTOCOL_VERSION,
            "schema_version": DSH_RPC_SCHEMA_VERSION,
            "capabilities_version": DSH_RPC_CAPABILITIES_VERSION,
            "methods": list(DSH_RPC_METHODS),
            "request_id_replay": "same_process_exact_response",
            "restart_replay": "core_durable_identity_or_same_revision_query",
            "request_id_conflict": "fail_closed",
            "legacy_unversioned_bridge": True,
            "interaction_dependency_projection": 1,
            "observed_evidence": 1,
            "recall_model_tier": True,
            "initialized": initialized,
            "subject_id": self._runtime.subject_id,
            "host_id": self._runtime.host_id,
            "world_revision": self._current_world_revision(),
            "service_versions": {
                "trust_schema": TRUST_SCHEMA_VERSION,
                "trust_capabilities": TRUST_CAPABILITIES_VERSION,
                "clarification_schema": CLARIFICATION_SCHEMA_VERSION,
                "portable_service_schema": PORTABLE_SERVICE_SCHEMA_VERSION,
                "portable_capabilities": PORTABLE_CAPABILITIES_VERSION,
            },
            "services": service_capabilities,
        }

    def _success_response(
        self, *, request_id: str, result_code: str, result: object
    ) -> dict[str, object]:
        return {
            "protocol": DSH_RPC_PROTOCOL,
            "protocol_version": DSH_RPC_PROTOCOL_VERSION,
            "schema_version": DSH_RPC_SCHEMA_VERSION,
            "request_id": request_id,
            "ok": True,
            "result_code": result_code,
            "world_revision": self._revision_from_result(result),
            "result": result,
        }

    def _revision_from_result(self, result: object) -> int | None:
        if isinstance(result, Mapping):
            for key in ("world_revision", "after_world_revision", "after_revision"):
                value = result.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    return value
            receipt = result.get("receipt")
            if isinstance(receipt, Mapping):
                for key in ("after_world_revision", "after_revision"):
                    value = receipt.get(key)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        return value
        return self._current_world_revision()

    def _current_world_revision(self) -> int | None:
        if self._query is None:
            return None
        try:
            result = self._query.get_world_revision()
            value = result.get("revision")
            return value if isinstance(value, int) and not isinstance(value, bool) else None
        except (OSError, RuntimeError, ValueError):
            return None

    @staticmethod
    def _exception_code(error: BaseException) -> str:
        if isinstance(error, BoundaryHardDeletedSourceError):
            return "hard_deleted_source"
        if isinstance(error, (DshRpcProtocolError, ObservedError)):
            return error.code
        if isinstance(
            error,
            (TrustQueryError, TrustCommandError, ClarificationError, InteractionQueryError),
        ):
            return error.code
        if isinstance(error, PortableError):
            value = str(error)
            return value if value else "portable_error"
        if isinstance(error, DshBoundaryError):
            return "dsh_boundary_error"
        return "internal_error"

    @staticmethod
    def _exception_type(error: BaseException) -> str:
        if isinstance(error, BoundaryHardDeletedSourceError):
            return "boundary"
        if isinstance(error, (DshRpcProtocolError, ObservedError)):
            return "protocol"
        if isinstance(error, TrustQueryError):
            return "trust_query"
        if isinstance(error, TrustCommandError):
            return "trust_command"
        if isinstance(error, ClarificationError):
            return "clarification"
        if isinstance(error, InteractionQueryError):
            return "interaction_query"
        if isinstance(error, PortableError):
            return "portable"
        if isinstance(error, DshBoundaryError):
            return "boundary"
        return "internal"


__all__ = [
    "DSH_RPC_CAPABILITIES_VERSION",
    "DSH_RPC_METHODS",
    "DSH_RPC_PROTOCOL",
    "DSH_RPC_PROTOCOL_VERSION",
    "DSH_RPC_SCHEMA_VERSION",
    "DshRpcProtocolError",
    "DshRpcV2Server",
    "canonical_rpc_json",
]
