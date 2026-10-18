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

Explicit enduring goals remain sourced `preference` intentions. No new stored
kind or SQLite schema is introduced. The optional boolean interpretation field
`is_long_term_goal` requests whitelist validation; it cannot disable source
syntax checks when omitted or false. The existing typed conditional-reminder
and two-source confirmation paths remain ordinary decisions, not long-term
goals. Original Evidence and support offsets are never rewritten or duplicated.

The deterministic guard now uses a clause whitelist. These are the manager's
rules, including the exact additional exclusion lists:

1. 先把用户原话按句号、分号、逗号后接主语等切成分句（中英文都要）。
2. 一个分句**只有同时满足**以下条件，才能支持「本人长期目标/计划」：
   - 主语是说话人本人：分句以「我 / 我的目标 / 我的计划 / 我打算 / 我想 / 我要 / 我准备」或 `I / My goal / My plan / I plan / I intend / I want / I'm going to / I aim` 开头（允许前面有时间状语，例如「三年内我要…」`next year I intend …`）；
   - 该分句内没有否定词修饰目标动词（不/没/从未/不再/不打算/don't/never/not/no longer…）；
   - 该分句内没有他人所有格或他人主语（「我女儿的 / 我父母的 / 我朋友想」`my daughter's / my parents' / my friend wants`；任何「我的 + 称谓/人名 + 的」或 `my + noun + 's/s'`）；
   - 不是问句。
3. 模型建议形成目标、但没有任何分句满足白名单的：**不形成长期目标**，原话照常作为普通记忆来源保存（不丢信息）。白名单之外的说法识别不出来，接受为召回限制，写进 PR「已知限制」。
4. 「如果/if」条件不影响判断（条件目标仍是目标）；目标从句里的动词（say/tell 等）不能被当作转述他人的标记——只有分句**主语**是别人、或整句是引号引述时才算他人发言。

5. 同一分句内，只要否定词（不/没/没有/从未/并不/不再/别、`not/n't/never/no longer`）出现在目标动词**之前**，不论中间隔了多少修饰语（真的/really/actually/特别…），都算否定。但否定若只修饰另一个分句（「我不打算中断训练，我的长期目标是…」）不影响后一个分句。
6. 白名单要求**目标动词的直接主语**是「我/I」（或「我的目标/计划是…」这种本人所有格作主语）。如果在「我」之后、目标动词之前出现了另一个主语（他人所有格、称谓、人名、他/她/他们、`my parents / my friend / he / she / they / 人名`），例如「我知道我朋友想…」`I know my parents plan …`，目标动词的主语是那个人，不算本人目标。

7. 中文分句切分补上转折/并列连接：「但、但是、可是、不过、然而、而、只是、而且、并且」（前面有逗号或句首时），与英文 `but/however/though/and` 对称。否定、他人主语、问句、排除标记的检查只针对支持该目标的那个分句，不扫整条消息。来源范围必须与该目标分句相交；无效的已选分句不能借邻句的有效目标通过。

- 近期时间词：今晚、今天、明天、待会、等会、马上、这周末；`tonight`、`today`、`tomorrow`、`this afternoon`、`this evening`、`later today`、`right now`。
- 明确玩笑/收回：开玩笑的、开个玩笑、说着玩的、逗你的；`just kidding`、`jk`、`kidding`。

两组排除标记分句内出现就不算长期目标，包括加引号的标记。没有反讽、
反事实或活动类别黑名单，也不再用目标内容中的 say/tell 猜测转述。
被选来源必须实际覆盖合格目标分句：只选否定前半句不能凭后半句形成目标。
完整来源含独立肯定目标分句时仍能形成；独立引用后用户另说的目标也能形成。

## Review corpus: three explicit expectations

[`state-goals-policy-cases.json`](../py/tests/fixtures/state-goals-policy-cases.json)
retains all **171** records: the previous **138** exact records, all **24**
fifth-review records (12 negatives and 12 positives), and **9** Chinese
positive variants. The variants cover three examples each of「我不知道别的，
但…」「我没想别的，可是…」「我不确定，不过三年内我要…」. Original
utterances and overlapping review records remain unchanged. `old-en-task` and
`old-zh-task` remain 规则必拦.

| Class    | Cases | Forced-form result                                                                                                                                           |
| -------- | ----: | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 规则必拦 |    81 | **0/81** owner goals formed, including the previous **69/69** rejections; all Evidence survives.                                                             |
| 规则必放 |    65 | **65/65** form and recall, including the previous **44/44**, 12 new reviewer positives and nine Chinese variants. Before the clause repair **55/65** formed. |
| 交给模型 |    25 | **17** semantic negatives and **8** positive known limitations. Actual forced `form` forms **10/25**; this is not a model correctness score.                 |

Every case runs the real worker, compiler, SQLite and recall with forced
`form + preference`. There is no scripted `no_change` substitute for kernel
rejection. Additional deterministic tests cover every exact exclusion marker,
quoted markers, arbitrary singular/plural possessives, direct negation forms,
time adjuncts, conditional goals, say/tell in goal content, optional annotations
and source-span clipping. The original 35 state/goal cases remain required.

Negation is a token-order check before the target predicate, with no modifier
count or distance cap. The modifier 特别 is not split into the negation token
别. Subject validation checks the nearest explicit owner subject and the
auxiliary/modifier grammar before that predicate. A new finite verb or noun
phrase, such as `know my parents`, cannot pass as an owner adjunct. This uses
no person-name list or reporting-verb blacklist. A nested explicit owner (`I
think I intend ...`) still passes. Goal content after the predicate, including
say/tell and unrelated negative descriptions, is not scanned as the owner's
subject or a pre-predicate negation. Recognition of affirmative adjuncts is
finite; unrecognized forms remain a whitelist limitation. Bare terminal 计划
and English determiner + plan references are nouns, not goal predicates.
Bilingual lifecycle tests verify that a completed-plan reference still
supersedes the ongoing plan, without adding completion/activity keyword rules.

## Clause-local validation and support ranges

The comma/connector boundary and clause-start prefix now share the Chinese
and English connector lists. Longest Chinese connectors are matched first,
so 但是/而且 are not partially consumed as 但/而. Non-clausal commas such as
`, just kidding` stay with the goal. Quotes remain attribution context;
clipping a foreign quotation cannot remove its speaker.

Negation, direct subject, questions and literal exclusions use only an
intersecting goal clause. A negative/question/third-party/near-term neighbour
cannot veto a valid selected goal; a valid neighbouring goal cannot rescue
an invalid selected clause. The compiler does not expand or rewrite stored
support. Worker/SQLite/recall tests assert the exact selected content, original
Evidence and provenance offsets for whole-source and goal-only selection.

All nine Chinese connectors and but/however/though/and are tested at the
start and after a comma, with both source selections. The review failure and
nine variants also run with the goal annotation omitted. A further matrix
checks all four guards with the invalid clause before/after the valid goal
and whole/goal/invalid-only support. The measured reproduction is **84 red
cases → 84 green cases**, all sharing the clause-boundary root cause.

## 已知限制

内核不判、由模型判断/已知限制。白名单之外的说法识别不出来，接受为召回限制。
模型负责反讽、隐含否定、反事实、角色扮演和尚未决定等语义；上述 **17** 条
语义负例不宣称零形成，后续真实模型考试衡量。

**8** 条正例审查记录按当前白名单不形成目标，原话仍留在 Evidence：

- `new-positive-en-4`、`new-positive-zh-4`：首个目标分句有明确否定，后半句省略本人主语。
- `new-positive-en-6`：`My ambition` 不在白名单主语句型中。
- `new-positive-en-8`：未加引号书名中的问号不能由规则区别于问句；中文书名号内问号不拆句。
- `new-positive-zh-1`：肯定分句「今年一定要」没有本人主语。
- `new-positive-zh-3`、`false-rejection-3`、`review3-positive-zh-1`：分句含指定排除词「今天」，后半句没有新的本人主语；不再把「今天开始」当成无条件放行理由。

语法覆盖是有限的：未识别的时间状语、主语省略和其他句式可能被拒绝；
目标仍与普通偏好共用现有类型，省略目标注记且没有目标句法线索的模型输出
仍由解释模型负责。这个边界不扩展为内核的全面语义分类器。

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

The current round preserves all 638 complete Chinese previews byte for byte
against `b6fc783`, including selected identities, hits and false recalls, over
three repeats. The merged #124 candidate and frame invariants pass **318/318**.
The original recurrence, P2 source attachment, forgetting, kind and topic guards
remain required. Full-suite results are recorded on the PR for the final head;
CI and real-model acceptance remain separate.
