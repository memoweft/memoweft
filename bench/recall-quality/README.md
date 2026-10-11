# Synthetic recall quality regression corpus

This change adds evaluation and baseline evidence only. The attempted MEM-4 handwritten vocabulary implementation was rejected and reverted: product sources match `origin/main` at `233cc2f`. Recall behavior, dependencies, model usage and storage formats are unchanged (Python schema 21, TypeScript/shared 6, Portable 4).

All facts, accounts and hosts are fictional. The runner creates temporary SQLite stores and exercises the complete `QueryService.preview_recall` path: permissions, currentness, correction notices, recent evidence and rendering. It checks deterministic repeated output and absence of World writes. It performs no model requests and reads no personal store.

## Run and acceptance contract

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
