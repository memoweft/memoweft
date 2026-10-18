# Synthetic recall quality regression corpus

MEM-6 owner decision: deploy production B for trial with at most three semantic additions and a conservative development-set threshold, retain every baseline hit and the 18 mandatory review probes, and report unrelated additions honestly. This explicitly supersedes the research-only new-blind requirement below for this implementation. Frozen labels/results and the strict literal regression gate remain unchanged. See [production configuration](../../docs/semantic-recall.md) and [production evidence](semantic-results/quality.json).

This change adds evaluation and baseline evidence only. The attempted MEM-4 handwritten vocabulary implementation was rejected and reverted: product sources match `origin/main` at `233cc2f`. Recall behavior, dependencies, model usage and storage formats are unchanged (Python schema 21, TypeScript/shared 6, Portable 4).

All facts, accounts and hosts are fictional. The runner creates temporary SQLite stores and exercises the complete `QueryService.preview_recall` path: permissions, currentness, correction notices, recent evidence and rendering. It checks deterministic repeated output and absence of World writes. It performs no model requests and reads no personal store.

## Run and acceptance contract

### English main-candidate invariant

English selection now runs the original main matcher, eligibility, optional
semantic nomination and rendering path first. A main candidate may be removed
only if every shared occurrence is grammatical in its original case and
apostrophe context on both query and memory sides. The explicit vocabulary is
`recall_english_policy.FRAME_WORDS`; actual contractions generate auxiliaries
only within that construction. Any other shared word must survive regardless
of supplementary scores or thresholds. This includes ordinary nouns such as
`version`, `date` and `color`, and substantive verbs such as `use` and `think`.

The previous English nomination arm (`ebe07284`) is supplementary only. New
admissions must be in that arm, and may use only slots left after retained main
items. The three stopped-exam retrieval regressions remain covered. Nomination
failure cannot invalidate main, and its new scores can be arbitrarily low
without losing a substantive main hit. Original main ordering is a feasible
fallback if reranking or supplementary text would consume a retained item's
character budget. Optional semantic nomination is called once and reused in
both arms, inside the same read transaction. Chinese/mixed matching stays on
the original main path.

The closed set shrank from **110 to 79** words/forms. Removed everyday
homographs are `can will may must might mine won don us being tell tells`;
bare `s t d m ll re ve` and obsolete negative-contraction stems were also
removed. Remaining words serve interrogative, auxiliary, article, conjunction,
preposition, pronoun or explicit request-verb roles. No attribute nouns occur.
The exact set is in the source and PR description.

Classification preserves original occurrences before case folding. A word is
substantive if it is all uppercase (including single letters and standalone
`I`), or initially capitalized after another word in the same sentence. Either
side's protected occurrence preserves the shared word. Each authorized search
projection has its own sentence start. A bare `d`, `m` or `re` is substantive;
only an actual apostrophe construction generates grammatical suffixes.
Case is checked on the original root before either straight or curly
apostrophes: `IT's`, `IT’s`, `WHO's` and `US's` retain their uppercase roots.
Ordinary personal-pronoun `I'm`/`I've`/`I'll`/`I'd` retain their existing grammar
treatment: English capitalizes this pronoun by convention. Independent letters
and acronym roots are protected; explicit controls verify both behaviors.
Contractions expand completely (`What's` → `what is`, `won't` → `will not`),
including curly apostrophes. Generated auxiliary `will` in `won't`/`I'll` is
grammatical; bare `will` remains substantive. No truncated `won` is generated.

#### Known limitations / 已知限制

Lowercase `he be as in at do and for not or` and standalone lowercase `a` can
still be classified as grammatical when intended as chemical symbols, musical
notes, program operators or blood groups. These rare senses need additional
context beyond this rule. All-uppercase and non-sentence-initial capitalized
variants are protected. Eleven explicit known-limit tests assert the policy's
permitted deletion, without skipping or relabelling them as negatives. In the
complete matcher the previous nomination arm already retains the `not` sample;
the other ten samples demonstrate the remaining misses. This limitation is
accepted for this revision, not a claim of universal English recall.
The lowercase noun `does` (plural of female deer, `doe`) is another accepted
rare-sense limitation. An explicit positive control records main recalling
`The does sleep in the meadow.` for `Where are the does?`, while current recall
remains empty; this sample is not skipped or relabelled as a negative.

### Frozen collections and measured floors

All collections are exposed synthetic regression data, not blind or general
quality estimates. No labels or existing baseline outcomes were changed.

- `english.json`: original 120 English queries, twenty factual pools with three
  correlated phrasings each plus 60 negatives. Its baseline was recorded on
  main `fc79404d`; the main recall implementation is unchanged at `b6fc783d`.
- `english-review-attributes.json`: the independent review's exact 21 positive
  and 10 negative tuples. `english-fact-inverse.json`: 30 fact-first inversions
  authored separately before that fix. Their baselines were frozen on main
  `b6fc783d`.
- `english-review-long.json`: the third independent review's exact 30 new facts
  and 30 corresponding no-answer controls, including long memories, tense,
  quantities, negations and `What's`. Baseline main recalls all 30 positives
  and returns 11 false items. Data were extracted as literals; no reviewer code
  runs in the gate.
- `recall-mixed-review.json`: the independent 15 Chinese/mixed queries and
  shared 14-fact pool. This collection has no answer-quality labels; compare
  complete previews byte-for-byte, including the mixed `version` lookup.
- `english-frame-context.json`: independently authored facts covering all 29
  fourth-review token ideas. Baseline recorded from unmodified main `b6fc783d`
  recalls 29/29; pre-fix `ab59e6c3` recalls 10/29, current recalls 29/29. The
  new context tests first reported 46 failures, then pass. Capitalized chemical
  symbols, notes and operators are protected; lowercase known-limit controls
  are separate explicit tests, not hidden in the four quality collections.

| English collection  | Main / current correct-answer recall | Main / current false items | Main / current item precision |
| ------------------- | -----------------------------------: | -------------------------: | ----------------------------: |
| Original 120        |                        60/60 / 60/60 |                    42 / 25 |               58.82% / 70.59% |
| Independent 21 + 10 |                        21/21 / 21/21 |                    25 / 11 |               45.65% / 65.63% |
| Fact-first 30       |                        30/30 / 30/30 |                     30 / 0 |              50.00% / 100.00% |
| Independent 30 + 30 |                        30/30 / 30/30 |                     11 / 5 |               73.17% / 85.71% |
| Context audit 29    |                        29/29 / 29/29 |                      0 / 0 |             100.00% / 100.00% |

Each collection independently requires recall and precision at least main,
false items at most main, no lost main positive and no newly forbidden item.
Required positives still rank first. Under the new invariant, main false hits
sharing substantive words such as `running`, `middle` or `specific` must remain;
old zero-return expectations conflicted with this explicit new contract. Those
examples and false-answer labels are retained, and assertions now enforce main
preservation and no additional false returns. Remaining errors are reported,
not relabeled or hidden by another collection's aggregate gains.
The original-case requirement also preserves standalone `I` and capitalized
contraction bases inside sentences. Tests that previously expected those main
false hits to disappear now explicitly require retention; their false-answer
labels and all historical baselines are unchanged. A fully uppercased query
may deliberately retain additional main candidates.

All 638 original Chinese full snapshots and all 15 mixed full previews remain
byte-identical to main. Repeated previews are deterministic; database dumps
before and after recall remain identical. No models or network are used.

### Property tests

`english-invariant-random.json` records actual main and prior-head candidates
for 1,152 seeded random scenarios: 1,024 question/memory pairs and 128 pools of
2–8 memories. The stdlib random generator also runs at test time and must
reproduce those inputs exactly. `english-invariant-labelled.json` records
248 scenarios from every English collection, the three exam cases and the
post-copular attribute/version cases. Both matchers must reproduce main's
frozen candidates, preserve every substantive main candidate and admit only
main or prior-head candidates: 2,800 scenario/entrypoint comparisons. Their
independent occurrence-level specification now also checks original case and
contraction provenance; the frozen inputs and reference candidates are unchanged.
Another 640 seeded scenarios vary case, sentence position, bare letters,
homographs and straight/curly apostrophes through both matchers: 1,280 more
candidate-preservation/admission comparisons. These use the unchanged main and
prior nomination arms as references. The 29 context probes exercise both
matchers and full snapshots, with an actual frozen-main baseline.

`english-invariant-snapshots.json` adds 192 full snapshot cases (96 seeded
pools in both rendering languages). They check the same membership boundaries,
retained rendered bodies, count/character budgets and no writes. Additional
checks cover contraction expansion, the closed vocabulary, arbitrary low new
scores and improvement-arm exceptions. A manifest freezes all new inputs and
reference outcomes. Normal pytest requires no Git history.

```sh
cd py
uv run python tests/support/english_recall_gate.py --repeats 3
uv run python tests/support/english_recall_gate.py --suite english-review-attributes --repeats 3
uv run python tests/support/english_recall_gate.py --suite english-fact-inverse --repeats 3
uv run python tests/support/english_recall_gate.py --suite english-review-long --repeats 3
uv run python tests/support/english_recall_gate.py --suite recall-mixed-review --repeats 3
uv run python tests/support/english_recall_gate.py --suite english-frame-context --repeats 3
uv run pytest -q tests/test_english_candidate_invariant.py
uv run pytest -q tests/test_english_frame_context.py
```

### Original Chinese corpus

```sh
npm run bench:recall-quality
# Optional same-process paired latency report:
cd py
uv run python ../bench/recall-quality/evaluate.py --check --reference-ref 233cc2f --repeats 3
# Verify that restored main reproduces every recorded selection/hit/error:
uv run python ../bench/recall-quality/evaluate.py --check --exact-baseline --repeats 3
```

The default command runs all 638 queries with three warm repetitions. `--split dev|challenge|holdout|review` permits a focused run; `--out` records raw results. Historical-module comparisons need Git history, but the default gate uses the frozen recorded baseline and requires no historical checkout. `--padding` enables optional synthetic scale experiments and is not part of CI. The rejected comparison arms are archived JSON; the evaluator no longer patches private APIs from the removed implementation.

`regression-baseline.json` is the active gate contract. It reuses the original development/challenge per-case baseline unchanged, takes the final holdout reference arm from `final-evaluation.json`, and records restored-main review outcomes. `baseline.json`, `accepted.json` and all earlier observations remain unchanged as historical evidence; `accepted.json` contains **rejected implementation floors, not active acceptance thresholds**.

The gate rejects any new forbidden item per query, any lost baseline Hit@5 positive, any lifecycle leak, missing/duplicate cases, and any failure of a required review item. Four existing-behavior probes and both names in both local/cloud tiers must hit; all ten review technical negatives must return nothing. These requirements are separate from aggregate scores. The shared20 positives currently miss: future gains are allowed, while new forbidden returns fail. `--exact-baseline` additionally requires identical ordered selections, ranks and forbidden items, and has passed on restored main across all 638 queries. Ordinary CI uses the regression contract so future improvements can pass.

Fixture hashes are checked before execution. Never delete samples, relabel them, replace the frozen baseline, or weaken review requirements to accommodate an implementation. A future proposal must pass these regression protections **and create a new independently authored blind evaluation before tuning**. Passing exposed regressions cannot establish general recall quality. Choosing durable themes/aliases, embeddings, providers, privacy scope and cost requires a separate owner decision.

## Collections and exposure

| File             | Queries | Positive / pure negative | Provenance and current status                                                                    |
| ---------------- | ------: | -----------------------: | ------------------------------------------------------------------------------------------------ |
| `dev.json`       |     360 |                 300 / 60 | Original tuning set; 12 categories × 30 queries                                                  |
| `challenge.json` |     120 |                 100 / 20 | Original holdout, renamed after exposure; historical `validation` key                            |
| `holdout.json`   |     120 |                 100 / 20 | Final retained set; historical final observation, no longer blind                                |
| `review.json`    |      38 |                  28 / 10 | Independent review: 4 existing-behavior + 10 technical negatives + 2 names in 2 tiers + shared20 |

**The development, challenge and final retained sets have all been exposed. They are regression data only. A new blind set is mandatory before evaluating a new approach.** The review set is exposed too and is a permanent regression contract. It contains 36 distinct samples, expanded to 38 queries by testing each name in local and cloud tiers. Its sources are REV-MEM-4 `probes.py`, `names_probe.py` and `shared20.py`; the twenty new facts share one database exactly as in `shared20.py`.

`make_fixture.py`, `make_holdout.py` and `make_review.py` reproduce the fixtures using LF bytes. Generators are historical reproduction tools, not permission to reset frozen labels. Development uses ten factual seeds per category with three queries each; challenge/holdout use five seeds and two queries each. Queries from a seed are correlated. The categories cover synonym, hierarchy, bilingual, colloquial, abbreviation, typo/pinyin, indirect constraints, time, correction, nearby competitors, polysemy and lifecycle. Non-target records are forbidden, including active nearby distractors.

The old report claimed a fresh final holdout and one final invocation. The review found that the same generator and provisional data already existed in an earlier commit, so Git history cannot certify complete historical blindness or prove no earlier execution. We retain the observations without asserting unbiased blind evaluation.

## Baseline and attribution

Hit@1/3/5 and MRR@5 are identical on these fixtures because every successful target ranks first. False-query rate includes positive queries with forbidden distractors; pure-negative rate uses only queries with no relevant memory.

| Collection     |     Hit@1/3/5 |  MRR@5 | All-query false recall | Pure-negative false recall | Forbidden returned items |
| -------------- | ------------: | -----: | ---------------------: | -------------------------: | -----------------------: |
| Development    |  17% (51/300) |   0.17 |            5% (18/360) |             21.67% (13/60) |                       37 |
| Challenge      |  17% (17/100) |   0.17 |          5.83% (7/120) |                 25% (5/20) |                       14 |
| Final retained |  12% (12/100) |   0.12 |          4.17% (5/120) |                 20% (4/20) |                        8 |
| Review         | 28.57% (8/28) | 0.2857 |                     0% |                  0% (0/10) |                        0 |

Of 249 development positive misses, 247 (99.20% of misses; 82.33% of all positives) are candidate matching failures, 2 (0.80%) are rank/cap truncation, and 0 are eligibility filtering failures. This is a lexical candidate bottleneck; weakening permission or lifecycle gates would not address it. Restored main reproduces the recorded hit and false-recall outcomes case by case, including existing errors. Category metrics, return counts and latency are retained in the baseline and verification JSON.

## Portable frozen hashes and timings

Active SHA-256 is computed over UTF-8 content after CRLF/CR normalization to LF, not platform checkout bytes. Original baseline and rejected reports preserve their old Windows CRLF hashes. The conversion is only newline normalization, without changing sample text or labels:

| Collection  | Historical CRLF SHA-256                                            | Canonical LF SHA-256                                               |
| ----------- | ------------------------------------------------------------------ | ------------------------------------------------------------------ |
| `dev`       | `67a9b11e82e4f0a7ee892140fdcf1fb3af2ddaa7e17d2f3909120c7c59304fe0` | `0bd30223c20bb731ec08c0e21e28622567f9768bc6e5cbd0657f96560b345d8e` |
| `challenge` | `750be9682f5fbacbbc310950fa7bce32b57d7cfd5012fa079fae90a36dd3bb62` | `978a06342f47bb92ece14108acde463c6cf1b247cd7b5bebe538ff8d1d155e71` |
| `holdout`   | `c7fd7cdbff039321861c17c1187959e210cbff2b2ffc51b00561bd0e9f2a9cd4` | `c3bdeb566afb8924a67805e8925c1fc020abba1715090dfebec39c5c115d32f5` |

`verify_git_bytes.py` implements the review `hash_probe.py` approach: obtain fixtures and the baseline through `git show`, place exact committed bytes in a temporary directory, and run the same gate. It runs all four collections by default. Unit coverage also verifies CRLF/LF equivalence and rejection of substantive text changes. The review hash is already canonical LF in `regression-baseline.json`.

Latency is descriptive, without a pass/fail threshold. Seed stores contain only 3–10 records, and review observed paired p95 swings of +24.80%/+25.84% on repeated identical code in a shared machine. A 20% relative threshold on millisecond-scale samples is not a reliable performance decision. Reports retain per-call values, query medians, p50/p95 and return counts; optional paired timing interleaves old/current arms. Before deploying a future implementation, separately measure representative library sizes and ordinary/boundary-length queries with enough repetitions; do not infer production capacity from these fixtures.

Restored-main three-repetition run: 12.15 seconds for 638 queries, with ordered selections, hit ranks and forbidden items exactly matching baseline. The report records the actual product recall source hash as well as the pre-commit working tree's HEAD. CI retains the offline gate as a step in the Python parity job.

## Historical rejected approach

See [the rejected attempt record](rejected-attempt.md) for all comparison scores, initial 62% and final 66% outcomes, independent review failures, resource observations and cloud experiment limits. All original JSON files remain archived; this package makes zero new model calls. The old `cloud_experiment.py` remains an explicit optional historical experiment tool, never imported by Core or CI; its guarded comparison now runs the current product implementation if invoked separately.
