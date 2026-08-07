# MemoWeft Next — Personal Memory World

Status: **experimental / Python-first**

TypeScript 1.x remains the first-generation stable cognition-memory architecture.  The Python line is the experimental proving ground for the next semantic model and is no longer required to mirror TypeScript at the world-model layer.

## Core thesis

> The user is the coordinate origin of the memory world, not the grammatical subject of every memory.

A memory belongs to a person's world because it is relevant to that person.  Once inside the world, however, it may semantically belong to the user, another person, a relationship, a project, an event, an AI participant, a place, or another persistent entity.

## Architectural invariants

### 1. World ownership is not semantic target

`world_id` answers **whose memory world is this?**

`MemoryTarget` answers **what is this cognition about?**

Do not encode every third-party memory as prose beginning with "the user says/thinks..." merely to preserve source information.

### 2. Target, perspective and provenance are orthogonal

For a statement such as "my mother is kind":

- target: `Mother`
- content: `kind`
- perspective: `User`
- provenance: the user's spoken Evidence

"User thinks mother is kind" is a presentation sentence, not the canonical semantic representation.

### 3. Relationships are first-class memory objects

`User -> friend_of -> Friend_X` as a graph edge is insufficient.

The relationship itself may own:

- shared events,
- current state,
- recurring interaction patterns,
- conflicts and repairs,
- cognitions and hypotheses,
- perspective-dependent interpretations.

It therefore has its own stable id and may be the target of a cognition or event.

### 4. Events are future recall anchors

A lived event must preserve enough structure to be reopened later.

For the Nanjing conflict, remembering only "the user argued with a friend about a trip" is insufficient.  A future query such as "why did we argue about Nanjing?" must be able to recover:

- the event,
- both participants,
- the trip/destination,
- the relationship,
- the disagreement cause,
- each participant's position,
- provenance for the remembered claims.

Events keep a narrative summary **plus** queryable semantic facets.  Do not force every human event into a rigid subject-predicate-object triple.

### 5. AI may participate in experience without becoming evidence authority

Preserve the TypeScript 1.x No-Self-Evidence discipline.

An AI/agent may be an Entity and an Event participant.  A user and agent may share a remembered conversation or jointly reach an interpretation.  However, an assistant-generated claim about the external world does not become supporting Evidence merely because the assistant said it.

Participation != proof.

### 6. User Profile is a derived view

The profile is a projection over the personal world, not the source of truth and not the destination of every memory.

A third-party fact that matters to the user may change Friend_X's region of the world while changing nothing in the user profile.

### 7. Keep two logical graphs

**World graph**

- Entity
- Relationship
- WorldEvent
- target links

**Provenance graph**

- Evidence
- support / contradict
- Cognition
- derivation / correction history

The UI may combine them, but storage semantics must not confuse "how the world is represented" with "why the system believes that representation".

## First executable golden case

`py/tests/test_world_model_golden_nanjing.py`

Conversation semantics:

- User and Friend_X are friends.
- They planned a trip to Nanjing.
- User prefers spontaneous, low-plan travel and values uncertainty.
- Friend_X prefers itineraries and guides.
- They argued because of that planning-style difference.

Required memory outcome:

- User has a scoped travel preference.
- Friend_X has an independent cognition region from the user's perspective.
- `User <-> Friend_X` is a first-class relationship with a possible planning-friction hypothesis.
- The conflict is an event anchor with participant-specific positions and a cause facet.
- Expanding that event yields the local world needed to answer "why did we argue?" without flattening the history into a user-profile paragraph.

## Next golden cases

1. **Mother gives candy repeatedly** — third-party entity memory + relationship pattern; repeated giving must not prove that the mother likes candy unless separately supported.
2. **AI proposes an interpretation, user confirms it** — shared experience is remembered while assistant text remains non-evidence context.
3. **Relationship evolution** — one argument must not rewrite a long-term relationship; apology/repair changes relationship state while preserving conflict history.
4. **Off-stage entity** — a person never directly interacting with MemoWeft can still exist richly in the owner's world.
5. **Long-dormant friend** — activity/recall salience may decay without deleting identity or historically important shared events.

## Deliberate non-goals for the first slice

Do not build yet:

- ontology-heavy RDF schemas,
- universe/galaxy UI rendering,
- automatic domain clustering,
- multi-device synchronization,
- graph database infrastructure,
- a replacement for the TypeScript 1.x API surface.

First prove that the semantic model stores and reconstructs lived-world memory better than the 1.x user-profile model.  Storage, extraction and retrieval should follow the golden cases rather than lead them.
