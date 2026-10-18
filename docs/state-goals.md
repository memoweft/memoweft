# State changes and long-term goals

G9 item 5. The stopped synthetic HaluMem pilot is diagnostic evidence, not a
leaderboard result. Source: `memoweft/memoweft-halumem-eval` at `b32e942`,
`reports/reproduction-fixtures.json`, `reports/diagnosis-evidence.json`,
`reports/valid-records.json` and `reports/qa-audit.json`. No new exam is run.

## Fixed failures and attribution

The exact English user turns and original interpreter responses are preserved in
[`state-goals-exam.json`](../py/tests/fixtures/state-goals-exam.json). Chinese
counterparts are new synthetic equivalents; they are not original failed exam
questions. Original evidence is never edited to obtain a passing result.

| Original exam item                                                                                                                                                                                                    | Expected                                                                                                         | Recorded actual behavior                                                                                                                                                                                                                                                                                                       | Root cause                                                                                   |
| --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Session 1, extraction points 13 and 15: “Establish a global health initiative to improve access to healthcare for underserved communities.” and “Provide healthcare access to 1 million people in underserved areas.” | Remember an intention, its motivation and the million-person target; do not claim the initiative already exists. | First interpretation and the existing single reconsideration both returned `no_change`. Goal and target scored 0 in the stopped extraction report. There is no corresponding failed QA in the 32-question sample.                                                                                                              | Formation scope excluded wishes/expectations without distinguishing explicit enduring goals. |
| Session 11, travel change: “I used to love them for their focus on health and relaxation, but it seems like many have shifted towards luxury experiences.”                                                            | Remember the preference change with its health-focused reason and retain the old preference as past.             | Model selected only the preceding introductory sentence, formed a new preference and left the old preference current. The original complete turn is in the fixture.                                                                                                                                                            | Formation selected incomplete support; English prompt lacked temporal supersession guidance. |
| Session 12 QA: “Did Martin's movie preference shift from fictional narratives to documentaries after a relaxing week on Jan 19, 2027?”                                                                                | No: documentaries → fictional narratives, after a stressful week.                                                | Recorded answer: “No, memories show continued preference for fictional narratives.” Judge: `Omission`. Formation initially selected the introductory segment; the stored claim later included only the beginning of the next sentence, omitting the stressful-week reason and direction. No replacement chain was established. | Incomplete formation and absent temporal link, rather than a missing rendering label alone.  |
| Session 11 QA: “What alternatives is Martin exploring to wellness retreats as of October 16, 2026?”                                                                                                                   | Nature hikes and cultural tours.                                                                                 | Correct alternative was already stored but omitted by ranking; answer reported no specific alternatives. Judge: `Omission`.                                                                                                                                                                                                    | Scoring, owned by #124. This PR does not claim to fix that question.                         |

The travel fixture's introductory sentence is
“I've been thinking a lot about my travel preferences lately, especially wellness retreats.”
The film fixture begins
“I've been thinking a lot about my movie preferences lately.”
Neither introduction alone expresses the change. Tests select the whole original
turn, preserving direction, reason and qualifications. The full goal turn also
retains the family-background motivation. Question answers and judge results
remain frozen; passing a compiler test does not rescore the historical exam.

## Current baseline defects

On `b6fc783`, the initial deterministic suite had **15 failures and 6 passes**.
It runs the actual World worker, durable checkpoint, interpretation compiler,
SQLite Apply and `recall_world_snapshot`; only model interpretation is scripted.
Failures cover ten English/Chinese state/goal examples and five invalid
supersession cases. The six complete-source formation cases already passed;
they show the storage path can represent the intended claims, not that the old
model selected them correctly.

Additional current-code findings:

- Chinese instructions both required corrections for state changes and required
  temporal supersession. English instructions omitted the latter distinction.
- The formation `current_*` lists included already superseded states, allowing a
  later update to pick an obsolete predecessor.
- Cognition supersession wrote `reason=corrects`; history called a natural change
  a user correction. English historical questions were not recognized.
- A model-selected missing, foreign, revoked, already replaced or self target
  could still form a successor or modify the old state. Target validity was not
  checked together with the atomic temporal write.

## Resulting contract

An explicit enduring plan uses the existing `preference` representation for an
intention. This adds no type, label, table, column or schema version. Keep exact
English source wording and existing Chinese subject normalization. Numbers,
dates, reasons and limits stay in the selected source; intentions do not become
completed events. Vague wishes, questions, hypothetical plans and one-task
instructions remain outside this scope.

A reported natural change selects a current predecessor through
`supersedes_cognition_id` or `supersedes_relationship_id`. The fenced transaction
checks target ownership, lifecycle, source permission, viewpoint/entity,
identity collision and predecessor uniqueness. It preserves source evidence and
old rows, records a deterministic `supersedes` transition and offers only the
successor as a later formation target. Old source wording remains separate,
permission-filtered predecessor context. Explicit errors keep `corrects`.
Relationships retain their existing `superseded` reason spelling; their
transition IDs are now deterministic and their revision is the committed World
revision. Both temporal spellings render as a change, rather than a correction.

Completion, abandonment and rescheduling require an explicit user report.
Ordinary recall returns the latest status, with its original tense and modality.
Historical questions can return previous states marked as past. The change
explanation distinguishes evolution from correction. A deadline passing or an
unrelated later message cannot complete or abandon a goal.

## Validation boundaries

The fixed tests cover Chinese/English job, residence, preference, relationship,
goal completion and abandonment; complete exam claims; a three-state chain with
an omitted topic; overdue goals remaining intentions; and permission revocation
during interpretation. All source and World assertions inspect actual behavior.
An additional red case reproduced a mixed batch that both superseded and
corrected the same predecessor: one order incorrectly applied two successors.
Both orders now reject the batch without World writes.

The prompt anti-contamination test retains every assertion and uses short
`zh`/`en` parameter IDs. Using entire expanded prompts as Windows test IDs
overflowed the 32,767-character environment-variable limit during test setup.

The Chinese frozen gate is run with the repository CI setting
`MEMOWEFT_SEMANTIC_ENABLED=0`. From `py`, run
`uv run python ../bench/recall-quality/evaluate.py --check --exact-baseline --reference-ref b6fc783 --exact-preview-reference --repeats 3`.
All 638 cases must retain their selected IDs, hits and false recalls, and their
complete previews, including rendered bytes, must equal the reference. The new
optional evaluator assertion does not change fixtures, scores or the baseline.
Existing Chinese wire-format fixtures are retained byte for byte. No scoring or label code is
changed. Only historical queries over new `supersedes` chains intentionally gain
the natural-change explanation; legacy `corrects` rendering is unchanged.

Deterministic compiler tests do not measure a live provider's extraction quality.
This package does not rerun the exam, use a new answer/judge score or claim a live
model accuracy improvement. The focused prompt changes still need separate live
acceptance; they do not justify guessing missing facts or altering old results.

Initial delivery validation (`50f65bec`): the initial 15 failures became green, and the
additional mixed-operation red case became green in both orders. The final
state/goal suite has 35 passing tests, repeated three times. Full Python has
2,326 passes; Node has 636 passes. Strict mypy checks all 242 source/test files;
format, lint, TypeScript typecheck, build, API snapshot and documentation links
pass. All 638 frozen complete previews equal `b6fc783`, with unchanged hit and
false-recall results. These are source-level results; CI and live-provider
acceptance remain separate.

## Independent review follow-up

The review reproduced a real `A → B → A` failure: a past value's deterministic
identity collided with its retained row. Removing the collision check alone
would produce a cycle and hide every state. The repair keeps the existing
identity for first occurrences, and derives a new occurrence identity only when
the value is already in this current predecessor's history. The derivation uses
the prior identity and references to the actual new user Evidence. Existing
sources are never cloned, rewritten or reassigned. State projections can repeat
the same value because the user actually stated it again; their evidence and
transition order remain distinct. `A → B → A → B` and `A → B → C → A` therefore
remain linear, traceable histories with one current occurrence. A later plain
restatement supports that current occurrence. Whole-chain forgetting follows
the existing transition graph and clears every occurrence and source.

The compiler also applies a small bilingual source guard independently of the
model's classification. It validates the enclosing user-source sentence, not a
model proposition. This validation context does not expand the stored support.
Questions, explicit hypothetical states, negated intentions, quoted third-party
claims, fictional context, unaccepted suggestions and explicitly one-off plans
cannot become new owner goals/states. Selecting only a fragment cannot strip
its question, conditional frame or speaker. Ordinary dislikes, reported goal
completion/abandonment, and genuine writing/translation goals remain valid.
Legacy single-item envelopes use the same check. Existing confirmation and
explicit correction/retraction contracts remain distinct.

[`state-goals-negatives.json`](../py/tests/fixtures/state-goals-negatives.json)
preserves all 30 review utterances exactly: 15 Chinese and 15 English. On
`50f65bec`, forcing `form + preference` accepted 24/30; the guarded compiler
accepts 0/30. This is a deterministic adversarial-output measure, not a live
model rejection-rate claim. All original 35 state/goal tests remain required.
Explicit counterfactual grammar (`if I were`, `如果…会`) is checked even without
goal, profession or location keywords; it cannot be promoted to either an
attribute or a preference. ASCII single-quoted claims are checked too, while
contractions such as `I'm` remain normal source text.

The 24 recurrence reproductions (four topics × two languages × three sequences)
were all red before the repair. They include the review's Northport/Southport
and Aster/Clover cases, plus job and preference returns. They assert current
recall, each transition, each original support, unchanged Evidence count/text,
and retained past states. Additional tests cover source-fragment bypasses,
both attribute/preference output kinds, genuine writing/translation goals,
current-occurrence restatement and whole-chain erasure. Explicit topic and
predecessor-kind mismatches are also rejected; an explicit new topic cannot be
mistaken for an omitted topic.

Follow-up local validation: 24/24 recurrence cases pass after their red
reproductions, and original review-negative acceptance is 24/30 → 0/30. All
original 35 tests remain green; the combined focused suite has 123 passes.
Full Python has 2,414 passes, Node has 636 passes, and strict mypy checks 244
source/test files. Format, lint, typecheck, build, API snapshot and documentation
links pass. The 638 complete Chinese previews remain byte-identical to
`b6fc783`, with unchanged hits and false recalls. No live-provider score is
claimed.

Temporal explanations are emitted on the current occurrence, keeping the
existing output budget available for past facts rather than repeating the same
explanation on every past entry. The Chinese 638-case complete-preview gate is
unchanged; no scoring function, label, schema, deadline, retry or wait changes.

## Automated-review thread verification

Both reported guards are already present in `38381703`; this follow-up adds
behavioral evidence without changing product code. The new
[`test_state_goals_review_threads.py`](../py/tests/test_state_goals_review_threads.py)
runs the real worker/compiler, SQLite transaction and read-only recall. Only
interpretations are fixed responses; legacy topic sidecars are seeded explicitly
after real formation, without changing original Evidence or topic provenance.

| Finding                                        | Historical reproduction                                                                                           | Current evidence                                                                                                                                                                                            |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Parsed topic was mistaken for an omitted topic | Four mismatched-topic cases were incorrectly applied by the `50f65bec` formation adapter and successor validator. | `38381703` rejects all four before World writes, for Chinese/English and both current topic cues and legacy topic targets; all eight same-topic/omitted-topic controls apply and recall only the successor. |
| Predecessor cognition kind was not compared    | Four mismatches were incorrectly applied by the same historical adapter/validator.                                | `38381703` rejects attribute→preference and preference→attribute in both languages; all four matching-kind controls apply and recall only the successor.                                                    |

The unchanged 20 tests produce **8 failures / 12 passes** with the frozen
`50f65bec` formation adapter and successor validator, and **20/20 passes** with
current product code. Rejected cases assert the exact reason, unchanged World
rows, support/target/entity ledgers and revision, and identical current recall.
Accepted controls assert the real replacement transition and current recall;
every case also checks both verbatim input sources. The existing 123 focused
tests stay required alongside these 20 tests, including the 30 rejected review
negatives and the original 35 cases. Historical replay does not claim a complete
historical-system or live-provider evaluation.

Thread-follow-up verification: full Python **2,434/2,434**, focused
**143/143** (the existing 123 plus 20 thread cases), mypy **245** files,
format and lint pass. The 30 review negatives still accept zero, the original
35 cases pass, and all 638 complete Chinese previews equal `b6fc783` byte for
byte with unchanged hits and false recalls. Product code is unchanged from
`38381703`.
