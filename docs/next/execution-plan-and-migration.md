# MemoWeft Next — Execution Path & 1.0 Migration Assessment

Status: **experimental / Python-first**  
Execution baseline: **2026-08-08 onward**

## 1. Project line split

- **WeftMate**: frozen. No new product features during MemoWeft Next semantic validation.
- **MemoWeft TypeScript 1.x**: stable first-generation architecture. Maintenance-only; do not rewrite its public worldview in-place.
- **MemoWeft Python Next**: experimental proving ground for the Personal Memory World architecture.
- **Universe UI / device ecosystem / multi-device sync**: explicitly out of scope until the semantic and retrieval gates are proven.

## 2. Core thesis

> The user is the coordinate origin of the memory world, not the grammatical subject of every memory.

A memory belongs to the user's world because it is relevant to that user. Once admitted, its semantic target may be the user, another person, a relationship, a project, a place, an AI participant, an event, or another persistent world object.

The canonical dimensions remain separate:

- **Target** — what the cognition is about.
- **Perspective** — whose view/interpretation it represents.
- **Provenance** — what evidence supports or contradicts it.

`User thinks mother is kind` is a presentation sentence. Canonically:

- target = Mother
- content = kind
- perspective = User
- provenance = user's spoken evidence

## 3. Governance: AI executes, user judges semantic boundaries

AI is responsible for:

- implementation details,
- module/file layout,
- SQLite schema mechanics,
- migrations,
- tests and benchmark harnesses,
- refactors,
- CI integration,
- debugging and routine engineering trade-offs.

The user is the architectural judge when a change affects one of these semantic boundaries:

1. what counts as memory;
2. what inference is allowed to become a cognition;
3. how far the personal world may expand beyond the user;
4. what should decay, persist, or be forgotten;
5. what role AI/agents have as participants versus evidence authorities;
6. privacy, ownership, permissions, or multi-agent data boundaries;
7. whether the product scope is still a Personal Memory World or has become a generic world model.

## 4. Mandatory execution loop

For every work unit:

1. Read the current project map and stage/gate.
2. Implement the smallest complete loop needed for the current stage.
3. Run tests/benchmarks.
4. Fix engineering failures without asking for routine approval.
5. Update the project map and stage evidence.
6. If the change crosses a semantic boundary, stop at the gate and present:
   - what has been proven;
   - what remains unproven;
   - the boundary decision needed;
   - consequences of each viable choice.
7. Continue only within the approved semantic boundary.

Do not skip stages merely because later work appears straightforward.

---

# 5. Complete staged execution path

## Stage 0 — Semantic Constitution

**Question:** What is the memory world allowed to mean?

### AI executes

- `PersonalWorld`
- `Entity`
- `Relationship`
- `WorldEvent`
- `Perspective`
- `MemoryTarget`
- `WorldCognition`
- local graph expansion primitives
- golden-case tests

### Required golden cases

1. **Nanjing trip conflict** — independent friend memory, first-class relationship, event cause and participant positions.
2. **Mother gives candy repeatedly** — third-party memory + relationship pattern; repeated giving must not prove that mother likes candy.
3. **AI proposes an interpretation, user confirms** — shared experience can be remembered; assistant text still cannot become self-evidence.
4. **Conflict → apology → repair** — relationship history persists while current state changes.
5. **Long-dormant friend** — identity/history persists while active salience decays.

### User judges

Whether the resulting structures feel like lived memory rather than a user-profile database.

### Gate 0 passes when

- third-party entities own independent cognitions;
- relationships can be cognition/event targets;
- events reopen enough local structure for future recall;
- target, perspective and provenance are separable;
- no golden case collapses back into prose whose subject is always `User`.

### Forbidden scope

No production SQLite redesign, universe UI, Hermes adapter, ontology-heavy RDF, or device ecosystem work.

---

## Stage 1 — Evidence → WorldDelta

**Question:** Can raw memory evidence automatically form the expected world?

### AI executes

A `WorldExtractor` producing a reviewable `WorldDelta` before storage mutation.

`WorldDelta` should be able to express:

- entities to create/update;
- aliases/references to resolve;
- relationships to create/update;
- world events and event facets;
- targeted cognitions;
- perspective and evidence links;
- unresolved references;
- semantic uncertainties.

### Critical rule

The extractor proposes a delta; it does not receive unrestricted authority to silently rewrite the world.

### Gate 1 passes when

The original Nanjing conversation automatically produces a structure materially equivalent to Golden Case #1, without hand-constructed world objects.

---

## Stage 2 — Entity & Reference Continuity

**Question:** Are `妈妈`, `我妈`, `她`, `那个南京旅游的朋友`, and a known person resolved safely over time?

### AI executes

- alias handling;
- pronoun/reference resolution;
- candidate entity retrieval;
- merge and split mechanics;
- uncertainty states;
- conservative duplicate handling;
- evidence-backed entity identity.

### Principle

Prefer a temporary unresolved/off-stage entity over a wrong merge.

### Gate 2 passes when

Multi-session tests maintain stable identities without systematic duplicate creation or aggressive false merges.

---

## Stage 3 — Persistent Personal World

**Question:** Can the world survive restart without distorting its semantics?

### AI executes

SQLite persistence only after Stages 0–2 constrain the model.

Expected logical areas include:

- worlds;
- entities and aliases;
- relationships and relationship history/state;
- world events;
- event participants/entities/relationships/facets;
- targeted cognitions and perspectives;
- evidence and provenance links.

### Architectural rule

Maintain two logical graphs:

**World graph:** entities, relationships, events, semantic targets.  
**Provenance graph:** evidence, support/contradict, cognition derivation/correction history.

### Gate 3 passes when

A saved world round-trips through restart/export/import while preserving identity, relationship/event structure, perspective and provenance.

---

## Stage 4 — World Evolution

**Question:** Does new experience evolve the world rather than overwrite history?

### AI executes

- relationship state/history;
- event chains (conflict, repair, resolution, continuation);
- correction versus contradiction;
- scope narrowing;
- confidence recomputation;
- decay/expiration/salience;
- perspective-specific disagreement.

### Required behavior

`argument → apology → repair` must keep the argument as history while changing relationship current state.

A later statement such as `she is not always a planner; she was nervous because it was her first trip to Nanjing` must narrow/correct the earlier trait rather than erase the original event.

### Gate 4 passes when

Long sequential scenario suites remain coherent and auditable after multiple reversals, corrections and repairs.

---

## Stage 5 — Memory Reconstruction & Recall

**Question:** Can the system actually remember an experience rather than merely retrieve similar text?

### AI executes

- memory-query parsing;
- anchor resolution;
- entity/event/relationship anchor scoring;
- local graph expansion;
- query-dependent traversal direction;
- context composition;
- evidence-aware answer context.

Example:

`Do you remember why she and I argued about the Nanjing trip?`

should resolve toward:

- the Nanjing conflict event;
- Friend_X;
- the shared relationship;
- the trip;
- the cause facet;
- each participant's position;
- relevant cognitions and provenance.

### Gate 5 passes when

The system can reconstruct the reason for the Nanjing argument without requiring the original conversation transcript as its primary recall artifact.

---

## Stage 6 — Trust & Cognitive Discipline Generalization

**Question:** Can the richer world model keep MemoWeft 1.0's epistemic discipline?

### AI executes / ports

- stated / confirmed / observed / inferred / ruled;
- support / contradict provenance;
- confidence rules;
- contested/conflicted states;
- correction and invalidation;
- typed decay and expiration;
- asking/revisit mechanisms;
- No-Self-Evidence.

### Critical invariant

AI may be an Entity and Event participant, but assistant-generated claims are not automatically external-world evidence.

Participation != proof.

### Gate 6 passes when

Shared user-agent experiences can be remembered without allowing agents to manufacture facts about third parties or the external world.

---

## Stage 7 — Personal Memory World Benchmark

**Question:** Is Next measurably different from existing memory systems?

### AI executes

Create a PMW benchmark alongside standard long-term-memory evaluations.

Dimensions:

- entity continuity;
- off-stage entity memory;
- relationship memory/history;
- event reconstruction;
- perspective separation;
- provenance accuracy;
- correction/scope narrowing;
- shared user-agent experience;
- temporal evolution;
- selective local recall.

Run available comparisons against systems such as Graphiti, Hindsight and Honcho where interfaces permit a fair test.

### Gate 7 passes when

Differentiation is demonstrated by repeatable scenarios/metrics rather than architecture descriptions alone.

---

## Stage 8 — Hermes Integration

**Question:** Can an actual agent live with this memory world over time?

### AI executes

- native Python adapter where practical;
- MCP compatibility for generic hosts;
- controlled ingest of user messages and real tool results;
- recall/context injection;
- agent participation event reporting;
- permission boundaries.

### Write boundary

Hermes may submit observations/events/tool results through controlled interfaces. It must not directly create stable world truth, arbitrarily raise confidence, or bypass provenance.

### Gate 8 passes when

Extended Hermes usage grows a coherent world and improves later recall without contaminating evidence boundaries.

---

## Stage 9 — Universe Projection

**Question:** Can humans understand the accumulated personal world?

Only now build the universe visualization.

The universe is a **projection**, never the ontology itself.

Possible visual mappings:

- distance = relationship/world distance or affinity;
- brightness = confidence/active salience;
- mass/size = importance, history, centrality;
- galaxies = emergent clusters/domains;
- orbits = relationship structure;
- events = transient/shared objects;
- camera origin = user.

The data model must never encode `galaxy/star/planet` as mandatory semantic types.

---

## Stage 10 — MemoWeft 2.0 Decision

Do not call the project 2.0 merely because Entity/Relationship exists.

A major-version decision requires evidence that:

- worlds form automatically;
- entity identity remains continuous;
- relationships evolve coherently;
- events can be reopened/reconstructed;
- epistemic provenance remains intact;
- long-term recall works;
- Hermes integration works in real use;
- benchmark results demonstrate a useful difference.

Until then the line remains **MemoWeft Next**.

---

# 6. Current project map (2026-08-07)

Branch: `next/personal-memory-world`

Stage 0 — Semantic Constitution

Completed:

- branch isolated from stable TS 1.x;
- `PersonalWorld`;
- `Entity`;
- `Relationship` as first-class object;
- `WorldEvent`;
- `Perspective`;
- targeted cognition;
- local graph expansion;
- Golden Case #1: Nanjing trip conflict.

Next execution order:

1. Mother/candy golden case.
2. AI shared-experience/no-self-evidence golden case.
3. Relationship conflict/repair golden case.
4. Long-dormant friend golden case.
5. Gate 0 semantic review.
6. Stage 1 `WorldDelta` schema and deterministic fixtures.
7. LLM-backed WorldExtractor only after the delta contract is stable enough to test.

---

# 7. What MemoWeft 1.0 is NOT losing

TypeScript 1.x is frozen, not deleted. The question below is about what Next inherits.

The most valuable 1.0 work remains relevant because it answers **why a memory should be trusted**, not **what kind of world the memory describes**.

Strongly retained concepts:

- Evidence as immutable/source-oriented raw material;
- source kinds and host/origin identity;
- occurred-at versus recorded-at time distinction;
- privacy/read/inference authorization gates;
- InteractionContext / SemanticResolution;
- No-Self-Evidence;
- deterministic `formedBy` and confidence discipline;
- support versus contradict provenance;
- correction versus conflict;
- contested/conflicted representation;
- typed decay and natural expiration;
- asking/revisit principles;
- controlled memory-management/audit ideas;
- portable-memory principle;
- SQLite-first local ownership;
- model/retriever replaceability.

---

# 8. What 1.0 must be abandoned or demoted in Next

These are worldview assumptions that should not constrain Next:

1. **Every cognition is ultimately about the user.**
2. **`subjectId` doubles as semantic subject.** In Next it becomes closer to world ownership/namespace while semantic target is separate.
3. **Event is primarily a batch contextual summary used on the way to user cognition.** Narrative remains useful, but events become persistent recall anchors with participants/facets/links.
4. **User Profile is the destination of memory formation.** In Next the profile is a derived view.
5. **The existing MemoryGraph can stand in for the personal world.** It remains useful as a provenance/audit view, but it is not the World Graph.
6. **Python's world-model layer must remain TypeScript parity.** Rule-kernel parity may remain where useful; semantic-world parity is intentionally broken.

These discarded assumptions are estimated to represent roughly **15–20% of the conceptual architecture**, but they sit in high-leverage locations, so replacing them touches a much larger share of the semantic pipeline.

---

# 9. Migration/refactor estimate

These values are engineering estimates, not measured LOC. Recalculate after Stage 1 using actual diffs and test coverage.

| 1.0 / Python area | Reuse as-is | Generalize / refactor | Replace / new work | Notes |
|---|---:|---:|---:|---|
| Evidence/source/time model | 80–90% | 10–20% | low | Add world ownership semantics, keep provenance discipline |
| Privacy/inference gates | 85–95% | 5–15% | low | Mostly worldview-independent |
| InteractionContext / SemanticResolution | 75–85% | 15–25% | low | Important for pronouns, confirmations and AI context |
| formedBy / confidence rules | 75–90% | 10–25% | low | Must accept non-user targets and perspectives |
| correction/conflict discipline | 65–80% | 20–35% | low–medium | Extend to entity/relationship/world cognitions |
| decay / expiration | 60–75% | 25–40% | low | Separate identity persistence from active salience |
| SQLite driver/transactions/migration mechanics | 60–75% | 25–40% | medium | New world tables and migrations required |
| memory-management/audit ideas | 55–70% | 30–45% | medium | Targets become heterogeneous world objects |
| Event model + distill semantics | 20–35% | 65–80% | high | One of the largest changes |
| Cognition model | 40–55% | 45–60% | medium–high | Add target + perspective; keep epistemic fields |
| consolidate/write semantics | 15–30% | 70–85% | high | Current prompt/logic is explicitly user-profile-centric |
| attribution/trends/asking | 35–55% | 45–65% | medium–high | Must operate on targeted objects/relationships |
| retrieval | 35–50% | 50–65% | high | Move from cognition top-K toward anchor + local graph reconstruction |
| graph layer | 10–25% | 75–90% | high | Provenance graph retained; separate World Graph added |
| portable bundle mechanics | 35–50% | 50–65% | medium–high | New schema, but versioning/import principles remain |
| adapters/MCP | 20–40% later | 60–80% later | deferred | Do not bind Core design to host interfaces yet |
| WeftMate/UI | 0% for Next execution | — | frozen | No Next product work until semantic gates pass |

## Overall estimate

For the **Python Core itself**:

- **35–45%**: likely reusable with little semantic change;
- **30–35%**: reusable only after generalization/refactor;
- **25–35%**: genuinely new implementation.

For the **high-leverage semantic path** (`Event → Cognition → Graph → Retrieval`):

- expect **65–75%** of semantics/implementation to be rewritten or meaningfully reworked.

For the **whole monorepo**:

- far less changes, because stable TypeScript, existing adapters and frozen product surfaces remain untouched during early Next work.

The project is therefore **not a ground-up rewrite**, but it is a **major semantic refactor**.

A useful mental model:

> MemoWeft 1.0 built the epistemic trust infrastructure. MemoWeft Next changes what that trustworthy memory is allowed to form into.

---

# 10. Tomorrow's development entry point

When development resumes, do not restart architecture discussion from zero.

Start at Stage 0 in this exact order:

1. implement Golden Case #2 (mother/candy);
2. implement Golden Case #3 (AI participates but cannot self-prove);
3. implement Golden Case #4 (relationship conflict/repair);
4. implement Golden Case #5 (dormant friend/salience);
5. run the Stage 0 test suite;
6. present only semantic discrepancies that require user judgment;
7. after Gate 0 approval, begin Stage 1 `WorldDelta`.

No SQLite world-schema migration should start before Gate 1 establishes that automatic extraction produces the intended semantic structure.
