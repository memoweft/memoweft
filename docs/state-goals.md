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

## Current formation boundary

Explicit enduring goals remain sourced `preference` intentions, never invented
completed events. Original Evidence, numbers, conditions and wording remain
unchanged; the existing Chinese subject normalization is retained. Model
interpretation owns semantic eligibility. The deterministic source guard is
limited to the following high-confidence rules:

- 长期目标/状态只能来自用户本人的发言（不是助手、不是引用别人原话）。
- 明确否定直接作用于目标动词（「我不打算/从未打算/不想 + 成为/做…」、
  `I don't/never intend/plan/want to …`），且该句里没有另一个肯定目标从句时拦；
  「我不打算中断训练，我的长期目标是成为护士」这种后半句有肯定目标的必须通过。
- 明确的他人所有格作为目标主语（「我女儿的目标」「我朋友想」
  `My daughter's goal`、`my friend wants`）不记成本人的目标；
  本包对错误的 owner 解释零写入，保留 Evidence，不猜那个人的实体身份。
- 删除「如果/要是/if」一律当假设的规则，带条件的目标仍是目标；
  删除「今天开始…」判成一次性计划的规则；删除其他靠关键词猜语义的规则。

Possessives must be goal subjects, not objects in a user's own goal such as
“My goal is to support my daughter's goal”. Negative and affirmative clauses
are checked separately, including semicolon-separated clauses. Source-range
validation retains explicit speaker context when a model selects inside a
foreign quotation; it never expands or rewrites the stored support. A book
title ending in `?` does not prevent an explicit goal declaration from reaching
interpretation. Existing typed Evidence, permission, confirmation, task-only
eligibility and explicit correction/retraction paths remain separate.

## Review corpus: three explicit expectations

[`state-goals-policy-cases.json`](../py/tests/fixtures/state-goals-policy-cases.json)
contains all 76 exact synthetic review examples: 30 old negatives, 20 new
negatives, 20 new positives, and six previously false-rejected sources. The six
overlapping positive examples remain separate records rather than being
silently deduplicated. Every record names its source batch, language,
expectation and rationale.

| Class    | Cases | Deterministic expectation                                                                                                                                                                                                        |
| -------- | ----: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 规则必拦 |    14 | Explicit foreign/assistant speech, direct goal-verb negation without another affirmative goal clause, or an explicit third-party possessive goal subject: reject a forced owner `form + preference` with zero World writes.      |
| 规则必放 |    26 | All 20 new positives and all six false-rejected cases: form with complete verbatim support, including conditional goals, start dates, and affirmative goals following negation.                                                  |
| 交给模型 |    36 | 内核不判、由模型判断，后续真实模型考试衡量。The new source guard returns no semantic refusal; a scripted model `no_change` makes no World writes. Forced `form` results are reported separately, not scored as correct memories. |

The retained original negative tests now use this approved classification
instead of the superseded “all negatives must be rejected by keyword rules”
assertion. No original utterance is removed or rewritten. Supplemental cases
likewise retain all original inputs: hypothetical/question semantics belong to
the model, while explicit source attribution and directly negated intentions
stay deterministic. The original 35 state/goal cases remain required.

Measured forced-output results on these 76 sources: rules reject **14/14**,
rules allow **26/26**, and the new source guard abstains on **36/36** delegated
examples. A scripted model `no_change` makes zero World writes for all 36.
Forcing an erroneous `form` instead forms 28/36; eight retain the pre-existing
task-only or pure-inquiry eligibility outcomes. This is deliberately not a
36-case model accuracy or rejection-rate score. Actual semantic classification
still needs the later real-model examination.

## Current state, recurrence and P2 source repair

Natural changes retain the existing `supersedes_cognition_id` /
`supersedes_relationship_id` transitions. First occurrences keep their identity;
returning values use identities derived from the actual new support references
and predecessor, preserving a linear `A → B → A` history without cloning old
Evidence. `A → B → A → B` and `A → B → C → A` retain each original source and one
current occurrence. Explicit topic and cognition-kind mismatches remain rejected.

The P2 reproduction is Aster → Clover → “Aster and I are partners again.” →
“Aster is my partner.”, with the exact Chinese counterpart 小禾 → 小岚 →
「我和小禾又成为伴侣了。」→「小禾是我的伴侣。」. On `45c66a0c`, both restatements
returned the historical node and linked the new source there. Both are red
before the repair.

The current relationship lookup uses the world, source entity, target entity,
relationship type, currentness and permission eligibility. It does not use
narrative equality to select a current relationship. A unique current occurrence
receives the new support and exact provenance range; a historical occurrence is
never the fallback. Ambiguous or unavailable current candidates fail closed.
The existing current narrative is not overwritten. Source/provenance writes
advance World revision, and each new utterance remains exact Evidence.

The two repaired tests assert that source four supports only occurrence three,
that all three relationship occurrences and both transitions remain, that the
current narrative stays unchanged, and that current recall selects occurrence
three. Whole-chain forgetting and the original recurrence tests remain covered.

## Verification scope

All review-case tests run the actual worker, compiler, SQLite Apply and recall
where applicable; only interpretation output is scripted. No paid calls or
new full exam are run. The three classes are a deterministic boundary test,
not a live-model extraction or hallucination score.

Run Python checks from `py`, using the repository CI setting
`MEMOWEFT_SEMANTIC_ENABLED=0`:

```text
uv run pytest -q
uv run mypy --strict src tests
uv run python ../bench/recall-quality/evaluate.py --check --exact-baseline --reference-ref b6fc783 --exact-preview-reference --repeats 3
```

The frozen gate compares all 638 complete Chinese previews, including rendered
bytes, selected identities, hits and false recalls. No frozen recall fixture, scoring function,
label, schema, deadline, retry or waiting limit is changed. Source-level tests,
CI and live-provider acceptance remain separate.

Before integrating #124: **2,525** full Python tests,
**234** focused tests, **246** mypy source/test files, and **636** Node tests
pass. Format, lint, typecheck, build, API snapshot and documentation links pass.
All 76 review utterances remain exact, all 26 positive/false-rejection cases
form, both P2 reproductions are green, and the 638 complete Chinese previews
remain byte-identical with unchanged hit and false-recall results.

After an ordinary merge of `origin/main` at `6b569fb` (#124), final integration
validation passes **2,907/2,907** full Python tests, **234/234** focused tests,
**254** mypy source/test files and **636/636** Node tests. Format, lint,
typecheck and all 638 complete Chinese previews pass on the merged source;
preview bytes, hits and false recalls still equal `b6fc783`. The 76-case rule
classes and both P2 results remain unchanged. The PR retains its existing
history recognition, predecessor traversal and explanations; #124's scoring
implementation is not changed by this task.
