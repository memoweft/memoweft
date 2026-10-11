# MEM-4 attempt: what was tried and why it was rejected

The handwritten topic vocabulary at `6535ea4a` was rejected by independent review and removed in MEM-4c. These are archived observations, not product promises or current acceptance floors. The current product matches the main baseline. All three original collections are exposed regression data; review probes are exposed too. New approaches need new blind data before tuning.

## Free comparison arms

| Approach                                     | Development Hit@5 | All-query false recall | Decision                                   |
| -------------------------------------------- | ----------------: | ---------------------: | ------------------------------------------ |
| Main baseline                                |               17% |                     5% | Lexical candidate bottleneck               |
| Normalization only                           |               18% |                     5% | Limited gain                               |
| Multi-grain lexical tokens                   |               10% |                  2.22% | Lost relevant targets                      |
| Topic expansion without domain/detail guards |               83% |                 12.22% | Increased false recall                     |
| Final guarded vocabulary                     |            99.67% |                     0% | Overfit and independently unsafe; reverted |

The frozen heuristic had 89 groups / 341 written terms, ten directed hierarchy rules, ten constraints, eight domain rules and eleven detail rules. Independent review found 268/341 terms (78.59%) and 74/89 groups (83.15%) occurring in development text under its normalized substring method. It is not a comprehensive multilingual model. Adding special cases to known probes would not resolve its ambiguity model.

## Retained collection outcomes

| Observation                                   | Hit@5 baseline → attempted | All-query false recall baseline → attempted | Pure-negative false recall baseline → attempted | Forbidden items baseline → attempted |
| --------------------------------------------- | -------------------------- | ------------------------------------------- | ----------------------------------------------- | ------------------------------------ |
| Final development                             | 17% → 99.67%               | 5% → 0%                                     | 21.67% → 0%                                     | 37 → 0                               |
| First retained set, original failed run       | 17% → 62%                  | 5.83% → 4.17%                               | 25% → 10%                                       | 14 → 16                              |
| Same exposed set after unique-candidate guard | 17% → 64%                  | 5.83% → 2.50%                               | 25% → 10%                                       | 14 → 6                               |
| Final retained set                            | 12% → 66%                  | 4.17% → 1.67%                               | 20% → 10%                                       | 8 → 2                                |

The first 62% observation (`validation-rejected.json`) lost two baseline positives. The final attempt improved averages but newly returned the cat claim for `cat eye gemstone怎么收藏？`; the baseline had returned an unrelated stamp, so the overall error rate hid a new forbidden item. Development versus final retained performance differs by 33.67 percentage points. No general semantic recall guarantee follows from these numbers.

The original cloud red checks terminated at `AssertionError: frozen fixture changed`, before the per-item cat error check. The old report's attribution of cloud failure solely to cat was incorrect. MEM-4c fixes newline portability while retaining both failure facts.

## Independent review, preserved separately

| Group                                | Baseline                               | Rejected attempt                                          | Permanent requirement                                               |
| ------------------------------------ | -------------------------------------- | --------------------------------------------------------- | ------------------------------------------------------------------- |
| Four existing-behavior probes        | 4/4 hit                                | 1/4 hit; three targets lost, one replaced by wrong memory | Keep all four hits and no new forbidden items                       |
| Ten technical/word-meaning negatives | 0/10 false recall                      | 10/10 false recall                                        | Empty result for every query                                        |
| Name 樂, local and cloud             | Hit in both tiers                      | Empty in both tiers                                       | Hit in both tiers                                                   |
| Control name 云, local and cloud     | Hit in both tiers                      | Hit in both tiers                                         | Hit in both tiers                                                   |
| Shared20 independent novel facts     | 0/20 targets hit, no forbidden returns | 10/20 targets hit, one non-target return                  | Preserve baseline; future gains allowed without new forbidden items |

`review.json` reproduces original memories, distractors, queries and labels. `review-rejected.json` replays the rejected Git module offline, interleaved against restored main, with three repetitions. Its `reference` arm is the rejected attempt (the file states this explicitly). The isolated 20-fact probes also gave 0/20 → 10/20 with all successes first; the shared corpus additionally returned the afternoon-exercise fact for a ball-sport question. The twenty samples and ten directed technical negatives are targeted evidence, not unbiased estimates of real-user recall/error rates.

The regressions identify three route-level flaws:

- Domain/detail filtering removed correct literal candidates: seaside photography and two mixed apple/phone/fruit questions. The cycling-insurance control still passed.
- A single polysemous token plus the sole library candidate was treated as relevance: cat commands, rose tense, tea tree, 8 pm, running process, 种花家, geometry meeting point, 美式英语, exercise book, painting software all returned unrelated personal facts.
- Query normalization changed the original name 樂 to 乐 while authoritative stored-name matching retained 樂, defeating a previously valid name anchor. 云 controlled for fixture structure.

Thirty original baseline positives and thirty ordinary unknown-word probes had passed, but they did not protect these counterexamples. Permission, forget, correction, expiry and account tests showed no leakage in the reviewed attempt; that does not waive the independent failures.

## Timing and scale limits

Original seven-repeat p95 baseline → attempt (ms): development 2.025 → 2.235; challenge 2.948 → 3.259; retained 2.350 → 2.650. Review repeats instead saw up to +24.80%/+25.84% for small-store p95. MEM-4c reports timing without a fragile relative gate.

Original 1,000-padding, twelve-query observation was 173.160 → 138.147 ms p95; failed earlier measurements are in both rejected scale JSON files. Independent mixed-template 10,000-record observation was 8,701.356 → 9,411.264 ms query-median p95 (+8.16%). At 50,000 records only two pairs completed, with maximum 460,545.831 → 472,767.294 ms (+2.65%); this is **not** an adequate p95 estimate. The inherited predecessor SQL did a per-record account scan in both versions; it was not introduced here and is not changed in this evaluation-only PR.

For 1,000 in-memory rows, repeated-known-word queries of 3,999 characters took about 10.16 → 105.59 ms, and 4,000-character all-vocabulary queries 66.75 → 195.32 ms. Calls were bounded; no unbounded query cache was observed. These observations motivate separate boundary-query performance assessment for any future approach.

## Model/dependency options: historical assessment only

`cloud.json` retains an earlier MiMo rewrite experiment: 24 development queries (20 positive / 4 negative), baseline subset Hit@5 5%, rewrite 35%, attempted guarded subset 100%; rewrite all-query false recall 12.5%, model latency p50 3.506 s / p95 9.828 s. It made 27 requests including three synthetic answer smoke calls, 2,014 input / 729 output / 128 cached input tokens. The then-recorded uncached cost estimate was CNY 0.003472 at CNY 1/million input and CNY 2/million output, not a bill or a current-price claim. Two of three answer smokes visibly used supplied memory; the Thai-food allergy case did not. This was seeded-memory answering, not full ingestion/formation verification.

Rewriting sends query text to a provider and adds a request per recall. Embedding/vector retrieval was not measured: no endpoint/model was configured, so quality, latency and cost remain unknown. Cloud embeddings would send permission-eligible memory content. Formation-time durable aliases already exist, but this task measured no added token cost and changes no formation prompt. The owner must separately decide the next approach, provider, privacy and budget. MEM-4c requests zero models, adds no dependencies or schema changes and performs no backfill.

Original observations are preserved byte-for-byte: `baseline.json`, `accepted.json`, comparison-arm JSON, cloud JSON, first rejected validation, final evaluation, negative audit, development/scale evidence and verification records. The active gate uses `regression-baseline.json` instead of the rejected scores.
