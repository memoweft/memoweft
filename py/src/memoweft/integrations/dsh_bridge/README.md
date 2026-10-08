# DSH RPC v2 observed Evidence (MW-2)

The default local formation route requests streamed OpenAI-compatible completions
with usage and `chat_template_kwargs: {"enable_thinking": false}`. The existing
300-second HTTP read timeout measures transport inactivity: queue informational
responses and generation chunks can keep slow work alive without increasing that
timeout. Core assembles only content into the checkpointed interpretation, keeps
usage and the finish reason, and rejects interrupted streams before compilation.
Cloud formation keeps its existing non-streamed route and 120-second timeout.
Formation prompts and rewrite feedback display Unicode source text directly,
without expanding names into ASCII escape sequences; persisted canonical hashes
remain unchanged. When an interpretation selects adjacent source segments for
one statement, the compiler coalesces their exact range before deriving the stated
proposition. It never includes an unselected gap between source segments.

All calls use the existing `memoweft.dsh_rpc` / protocol version 2 / schema version 1 envelope. Initialize binds the subject and host; observed calls cannot select another subject, impersonate a chat role, or change `source_kind`. Capabilities advertise `observed_evidence: 1`, `recall_model_tier: true` and the three methods below. Python databases migrate atomically from schema 20 to 21; TypeScript retains its separate database ownership.

`upsert_observed` takes exactly `{evidence: ObservedEvidenceV1}`:

```json
{
  "evidence": {
    "source_key": "daily:test-device:2026-10-01",
    "version": "2026-10-02T00:00:00.000Z",
    "content": "2026-10-01 睡眠 5 小时 40 分。",
    "occurred_at": "2026-10-01T00:00:00.000Z",
    "valid_at": "2026-10-01T00:00:00.000Z",
    "valid_until": null,
    "permissions": {
      "allow_local_read": true,
      "allow_cloud_read": false,
      "allow_inference": true
    }
  }
}
```

`source_key` is a stable host key (up to 512 characters), scoped to the initialized subject and host. Core stores its hash. `version`, `occurred_at`, `valid_at`, optional `valid_until`, and the version fields below are UTC ISO timestamps with optional 1–3 fractional digits; Core normalizes milliseconds. Validity is `[valid_at, valid_until)`, with null meaning no expiry. Content is exact observation text, at most 64 KiB; an empty observation is valid and removes the preceding version's facts. No user/assistant boundary, model or embedding call is involved. Nonempty content is projected verbatim into a source-linked `state` Cognition with `formed_by=observed`; `allow_inference` governs subsequent inference, separately from this exact projection.

Same version and same content/time payload is idempotent. Older versions fail with `stale_observed_version`; different content for the same version fails with `observed_version_conflict`. A new content/time payload truly deletes the old evidence and all of its derived objects before atomically installing the replacement. A version-only change retains the evidence identity. Omitted facts disappear completely.

`update_observed_permissions` takes exactly `{source_key, permission_version, permissions}`. It changes all three evidence permissions, and every derived World item reads those permissions through its provenance (including mixed-source items and transitive assistant interaction dependencies). Older permission decisions fail with `stale_observed_permissions`. At equal time, permission tightening wins; widening fails with `observed_permission_conflict`. Replaying an older upsert cannot relax a newer permission decision.

`retract_observed` takes exactly `{source_key, withdrawn_through}`. The watermark must cover the current version. Core reuses Trust true deletion: evidence content, source-derived World rows, formation/review ledgers, jobs, indexes, cached identity/snapshots and observed-dependent assistant prose are removed. Exact user discussion remains. Receipts and tombstones contain hashes, IDs and time watermarks; they contain no health content. Old source events and Portable backups cannot revive the deleted source. A new version strictly later than the watermark may be uploaded; a delayed withdrawal cannot delete it (`stale_observed_withdrawal`). Unknown-source and repeated withdrawal are idempotent.

All methods return a content-free receipt with `schema_version`, `subject_id`, `source_hash`, `source_kind=observed`, current `evidence_id` (null after withdrawal), `result_state=applied|no_change`, `before_revision`, `after_revision`, `world_revision`, `model_call_count=0` and `storage_cleanup`. Secure deletion is enabled before destructive writes. A nonblocking WAL checkpoint reports `complete` or `pending`; replay the same source operation to retry pending cleanup. This covers current Core storage, not filesystem snapshots or external backups.

RPC `preview_recall` and `prefetch` accept `model_tier: local|cloud` (RPC default: cloud). `query_interactions` and `query_interaction` accept the same tier for the model projection. Each selected item's sources must permit that destination. Cloud permission does not imply local permission, and local permission does not imply cloud permission. Validity applies to observed evidence and all linked derivations. The rest of memory remains eligible. Queries make no model calls or World writes; snapshots for different destinations have distinct identities. Direct legacy Python recall helpers retain their local default.

TypeScript exposes the same wire-shaped DTOs through `core.observed.upsert`, `core.observed.updatePermissions`, `core.observed.retract`, and `core.recall({query, modelTier})` (legacy TS default: local). Exact observed facts use local matching, without embedding the content. Source-linked derivations pass the destination gate before selection. Standard FTS/vector indexes support content-free deletion, including separate vector database paths; custom retrievers may implement `remove(ids)` or accept a rebuild of independently cloud-readable non-observed survivors. Pending cleanup IDs persist for retry.

The shared `shared/parity/observed.json` suite covers permission replay, equal-time tightening, replacements, deletion fences and reopens in both languages. Dedicated tests additionally verify mixed-source derivations, typed RPC validation, assistant dependency filtering, valid time, index cleanup and old Portable restore suppression. WeftMate's integration suite exercises the real Python Core through RPC, rather than substituting a transport fixture.

FG-1 adds the optional RPC v2 method `erase_conversation_context` with
`{conversation_id}`. A host calls it after explicitly deleting a conversation
and its source memories; it blanks that subject's original interaction contexts,
invalidates dependent assistant text, preserves IDs/hashes against stale Portable
replay, advances World revision when changed, and reports `storage_cleanup`.
It is idempotent; `pending` requires retry before claiming physical erasure.
True delete commands also accept optional `delete_conversation_snippets: true`
(default false). Their source cascade uses secure_delete, VACUUM, and WAL truncation.

FG-1 rework adds optional read-only `preview_forget`: either
`{target_kind, target_id}` (Evidence or one World item) or `{conversation_id}`.
It returns `world_revision`, `items` (`object_kind`, `item_id`, `name`,
`item_type`), `item_count`, `evidence_ids` and `evidence_count`. It opens the
live database read-only and runs the actual erasure cascade on an in-memory
snapshot. No source writes, receipt, revision advance, model call or cleanup
occurs. Hosts display the affected names/count before confirmation and use the
preview revision for deletion. Conversation preview recovers exact origins from
interaction metadata when earlier erasure has already removed the batch job.
Preceding AI context is cleared only when it contains the source wording or
removed IDs (including transitive interaction dependencies); unrelated context
is preserved verbatim.
