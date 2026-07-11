# Contributing to MemoWeft

**English** | [简体中文](./CONTRIBUTING.zh-CN.md)

This document covers the **hard rules you must follow every time you change code**. They apply to both humans and AI.

When you first pick this up, read the repo root [`AGENTS.md`](AGENTS.md) (minimal getting-started notes) and [`CURRENT.md`](CURRENT.md) (what's being worked on right now). `docs/internal/` only keeps optional long-term boundary notes; it is not required reading.

---

## The rule in one sentence

> For any code change, before you deliver it, **`npm run typecheck && npm test && npm run build` must all three be green**. If they don't pass, it's not done, and you may not submit it.

This is not a suggestion, it's a gate. CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the same three steps on the PR, and anything red cannot be merged.

---

## Environment requirements

- **Node ≥ 24 works out of the box; Node 20/22 need `npm i better-sqlite3`.** The storage layer uses SQLite, with two drivers: Node ≥24 defaults to the built-in `node:sqlite` (that module only became official in 24), zero extra dependencies; on Node 20/22 the built-in module is unavailable, so installing the optional `better-sqlite3` lets it run (see [`docs/INSTALL.md`](docs/INSTALL.md)). For developing the library itself (running `.ts` tests, native type stripping), Node ≥24 is still recommended: Node 22 needs 22.18+ to support native `.ts` type stripping by default, and Node 20 lacks this capability entirely (the full `.ts` test suite can't run on 20; CI instead verifies via a dist smoke script).
- **Zero runtime dependencies.** The runtime `dependencies` are always empty—this is the precise meaning of "zero runtime dependencies." `better-sqlite3` is an **optional peer dependency** (`peerDependenciesMeta.optional`); whether to install it is up to the user, and an optional peer does not count as breaking the rule. Dependencies installed during development are devDependencies: `typescript`, `@types/node`, plus `better-sqlite3` used solely for the multi-version test matrix (Node 24 defaults to the built-in driver; when testing the zero-dependency path it is `rm`'d first).
- To run the test bench / the real write path, you need to configure the model and embedder in `.env` (see "Configuration" below). **But unit tests need no `.env` at all**—the tests use a fake LLM, purely offline; go by the actual output of `npm test` in each workspace, and `fail` must be 0.

```bash
npm ci            # or npm install
npm run typecheck # types
npm test          # Core unit tests (offline, go by actual output, fail must be 0)
npm run build     # produces dist/
```

Host and collector plugins each have their own independent tests: `npm test -w @memoweft/host`, `npm test -w @memoweft/collector-active-window`—likewise, go by each workspace's actual output, and `fail` must be 0.

> **Mirror-registry development (CI has a lockfile guardrail):** maintainers can keep using a domestic mirror registry for day-to-day local development; but **any `npm install` that will rewrite `package-lock.json`** must carry the official-registry prefix—`npm_config_registry=https://registry.npmjs.org npm install`—otherwise the mirror registry URL gets written into the lockfile, and CI's `lockfile registry guard` (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)) will therefore go red. This repo does **not** commit a project-level `.npmrc`, so your local mirror configuration is unaffected.

---

## The three-green guardrail (must run before submitting)

| Command | What it does | Green standard |
| --- | --- | --- |
| `npm run typecheck` | `tsc` full type check (`src` + `tests`) | no errors |
| `npm test` | `node --test tests/**/*.test.ts`, purely offline | go by actual output, `fail` must be 0 (the pass number grows/shrinks with the tests) |
| `npm run build` | `tsc` produces `dist/` (including `.d.ts`) | no errors, `dist/` updated |

All three must **pass in order** to count as done. Don't submit after running only typecheck.

---

## Branch and commit conventions

- **Don't change things directly on the default branch (`main`).** Open a branch:
  - `feat/<short description>` new feature
  - `fix/<short description>` bug fix
  - `docs/<short description>` docs-only changes
  - `chore/<short description>` miscellaneous (build, config, renames)
- **Small commits, one thing per commit.** Write commit messages that clearly state "what changed + why"; don't write uninformative ones like "update" or "fix".
- **`git commit --no-verify` is forbidden, and skipping tests is forbidden.** The guardrail backstops the whole project; bypassing it just hands the risk to whoever picks this up next.
- The PR body must at minimum make clear **what changed / why / how it was verified (paste the three-green results)**.

---

## Dependency minimization (new dependencies are rejected by default)

This is a principle carved into `package.json` (see map cell 11):

- Use `node:sqlite` for storage, `node:http` for HTTP, `node:fs` for logging/files—**if a Node built-in can do it, never add a package**.
- To add any new dependency (including dev dependencies), first explain in an issue **why the built-in can't handle it**, and let the author make the call. The default answer is "don't add it."
- The runtime `dependencies` goal is always to be empty. When a host runs `npm install memoweft`, it should not be dragged into a pile of transitive dependencies.

---

## The cognition layer is core, don't touch it on a whim

Brand names, docs, comments, build config—change these freely; if you get them wrong, the three-green catches it. But the following belong to **core mechanisms**, and before touching them you must first lay out the trade-offs in an issue and get confirmation from the author (PM); no "optimizing on a whim" allowed:

- The three-layer data model (evidence → event → cognition)
- Cognitive discipline: records are not beliefs (LLM inferences are first treated as low-confidence candidates), no self-corroboration (the assistant's own output / user silence is not evidence), conflicts are exposed first, never auto-resolved, confidence is computed by MemoWeft, never taken from the LLM's self-report, typed staleness (emotions forgotten quickly / explicit preferences never forgotten)
- The confidence algorithm, decay half-life, read/write decoupling logic
- Public API signatures (the things exported from `src/index.ts`)—hosts may already be using them, so breaking renames must keep a deprecated alias (for example, `DLA_VERSION` / `DlaConfig` were kept this way)

The criterion is simple: **if you're unsure whether something counts as "core," treat it as such and ask first.**

---

## Doc synchronization (mandatory after changes)

Code and docs move together; don't just get the code green:

- Changed the **current mainline's progress / boundaries** → update [`CURRENT.md`](CURRENT.md).
- Have **history / decisions** worth recording → write them into the commit message; for public milestones add them to [`CHANGELOG.md`](CHANGELOG.md).
- Changed **public capabilities / usage** → sync the public docs (`README`, architecture / integration under `docs/`, etc.).

"Code is green but docs didn't keep up" = not done.

---

## Environment variables / configuration

- After a rename, **recognize both prefixes**: when reading each env key, the code reads the primary `MEMOWEFT_*` name first, and falls back to the old `DLA_*` name if not found. Docs always write `MEMOWEFT_*`, and note that `DLA_*` remains backward-compatible.
- **Don't touch `.env`** (which contains the user's real keys), and don't delete / change the old `DLA_*` keys—only add `MEMOWEFT_*`, letting the code recognize both sets.
- Keys involved: `MEMOWEFT_LLM_*`, `MEMOWEFT_WRITE_LLM_*`, `MEMOWEFT_EMBED_*` (each backward-compatible with the corresponding `DLA_*`).
- The default SQLite filename `./dla.db` doesn't change (changing it would break away from the existing data file in the root); the physical directory name `memoweft` doesn't change.

---

## Pre-submission self-check list

- [ ] `npm run typecheck` green
- [ ] `npm test` green (`fail 0`)
- [ ] `npm run build` green, `dist/` is fresh
- [ ] No new dependencies added (or already approved in an issue)
- [ ] Did not touch core mechanisms / break the public API without authorization
- [ ] Relevant docs synced (CURRENT.md / public docs / milestones into CHANGELOG)
- [ ] Did not touch `.env`, did not delete old `DLA_*` keys, did not change the physical directory name or `./dla.db`
- [ ] The PR body clearly states "what changed / why / how it was verified (paste the three-green)"

License: MIT (see repo root [`LICENSE`](LICENSE)). Submitting a contribution means you agree to license your changes under MIT.
