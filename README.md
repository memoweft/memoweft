# MemoWeft

MemoWeft is a local-first memory engine for AI applications. It keeps Evidence, derived World objects, provenance, revisions, corrections, retractions, permission state, and portable data as distinct records so a host can explain what it remembers and why.

[简体中文](./README.zh-CN.md)

## Package surfaces

The root npm package is the TypeScript library. The `py/` project is the Python MemoWeft 2.0 implementation and contains the production Hermes provider, Trust services, Portable v4, and DSH RPC v2. The source tree also contains a Memory Experience app; that app is not embedded in the root npm tarball.

The Python distribution exposes the `memoweft` provider in the `hermes_agent.memory_providers` entry-point group. Package metadata, source manifests, and installed behavior are separate evidence layers and should be verified independently.

## Install the TypeScript package

Use `npm install memoweft` for a published npm release. Node 24 has a built-in SQLite path; Node 20 and Node 22 use the optional `better-sqlite3` driver.

The repository candidate may be newer than the latest published package. A successful local build or tarball hash does not prove registry publication.

## Memory contracts

MemoWeft keeps these boundaries explicit:

- Raw user content remains exact Evidence with provenance and permissions.
- Stated formation propositions selected by segment or quote are derived from
  verbatim Evidence, preserving names, forms of address, numbers and dates.
  Formation payloads offer intact `sentences` alongside existing fine `segments`.
  A support can explicitly select `sentence_id` for a complete same-topic claim,
  or `segment_id` for independent clauses. These are alternative source ranges;
  Core never expands an unselected range and rejects overlapping selections.
  Named people with an explicit relationship to the user are classified as
  Relationship records, rather than naming-only or third-party attributes.
  Invalid interpretation JSON or compiler fields receive one model rewrite
  with the concrete compiler error before the job settles without a write.
  The durable model checkpoint retains the first response, error and rewrite
  outcome; recovery never repeats a reserved or completed rewrite. The default
  DSH HTTP route requests `response_format: {"type": "json_object"}`; Core
  validation still checks required fields and evidence grounding.
  A model `no_change` result after a declarative source also receives the same
  single, checkpointed reconsideration, with its first result retained.
  Event calendar dates are normalized from an unambiguous explicit numeric
  date in the selected source; relative or ambiguous dates retain their original
  time expression without a guessed `occurred_at` value. Reasoning-only model
  output is never used as an interpretation or included in rewrite feedback.
- Entity, Relationship, Event, Cognition, and Evaluation records are formal World objects.
- Confirmations can reference the preceding four permission-eligible conversation
  turns. The compiler validates the exact assistant proposal against its persisted
  message ID and keeps the user's confirmation as Evidence. Proposal references
  join the existing support/deletion graph; source queries expose both originals.
  An explicit user restatement stays verbatim and may retain a unique same-person
  preceding proposal as context. Assistant prose never becomes user Evidence.
- Recall is deterministic for the same revision and performs no World writes.
  Short natural corrections can be retrieved through the topic of their
  permission-eligible predecessors. Only the current successor is rendered
  and selected; past preference values appear for historical questions or
  explicit retained-person identity lookups, labeled as past memory.
- DSH recall previews additionally expose `recent_evidence`: permission-filtered
  exact user quotes from unfinished formation, kept separate from formal World
  items. This read-only bridge uses the existing Evidence and conversation
  lifecycle, takes at most four quotes / 800 characters from the last 24 hours
  and 32 accepted turns, and disappears after successful formation. It never
  copies assistant claims or recreates deleted conversation context. Hosts must
  label these quotes provisional and retain their distinction from formal
  memory. A short explicit correction may carry its preceding user quote across
  conversations within four accepted turns and five minutes. Repeated quantities
  or an explicit numeric/time reference must agree in dimension; competing
  candidates are returned together with `correction_status: "ambiguous"`, never
  assigned to the topic selected by the query. A unique pair uses
  `preceding_text` / `preceding_evidence_id`; ambiguous groups use
  `preceding_candidates`. Both fit atomically in the existing quote budget.
  Completed turns wake formation immediately; host queues own capacity
  and foreground priority.
- A single explicit correction can replace several named Cognition targets with
  one deterministic successor. Only identical, compiler-grounded correction items
  with the same support spans may share that successor; ordinary duplicate forms,
  repeated targets and unrelated topic labels remain rejected. Every predecessor
  retains its own correction ledger and transition to the shared successor.
  A correction topic may reuse the permission-eligible predecessor's wording.
- `query_jobs` also returns unresolved explicit `formation_requests`; rejected
  corrections do not become healthy merely because their worker is `no_change`.
  `retry_formation` reuses the existing source reprocessing operation with a
  request ID, preserves the rejected terminal, and wakes the configured worker.
  Recall previews expose permission-filtered `pending_corrections` independently
  of the recent-quote cutoff, so hosts can label old facts pending correction.
  History provenance includes `successor_provenance` to explain the correcting
  source from either predecessor; model provenance is unchanged.
- Correction, retract, forget, archive, mute, and permission changes produce durable receipts.
- Shared assistant interactions retain their original history and explicit memory dependencies. Model projection excludes unresolved or no-longer-current dependencies, including transitive reuse; history projection remains subject to source permissions.
- Portable bundles are versioned, validated, planned before apply, and conflict checked.
- Hermes host events and user presentation remain distinct from Core business terminals.

## Observed source lifecycle

DSH RPC v2 accepts typed observed evidence revisions, source permission changes and true withdrawal, independently of chat ingest. Recall can filter each source and its derivatives for a local or cloud destination. The TypeScript Core exposes equivalent observed DTOs and lifecycle methods. See the [observed RPC contract](py/src/memoweft/integrations/dsh_bridge/README.md) for version, consent, validity and deletion semantics.

## Trust boundary

Local-first does not mean that data can never leave the machine. Hosts choose model routes, storage paths, consent flows, authentication, authorization, encryption, backup, and logging policies. MemoWeft permission fields constrain its formal paths; they do not turn arbitrary host code into a security boundary.

MemoWeft is licensed under the [MIT License](./LICENSE).
