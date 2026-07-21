# Contributing to MemoWeft

Thank you for helping improve MemoWeft. Contributions are welcome across the core library, integrations, examples, documentation, compatibility testing, and evaluation cases.

**English** | [简体中文](./CONTRIBUTING.zh-CN.md)

## Before you start

- Use [GitHub Discussions](https://github.com/memoweft/memoweft/discussions) for usage questions and early design conversations.
- Search existing [issues](https://github.com/memoweft/memoweft/issues) before opening a new one.
- Report security vulnerabilities privately as described in [SECURITY.md](./.github/SECURITY.md).
- For a large change, open an issue first so the scope and compatibility impact can be agreed before implementation.

## Development setup

Node 24 is recommended for repository development. Node 20 and 22 are supported for the built package through the optional `better-sqlite3` driver, but the source test suite relies on native TypeScript execution available in newer Node releases.

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run typecheck
npm test
npm run build
```

Unit tests run offline and do not require `.env` or model credentials. Live-model tests and the interactive test bench use the variables documented in [docs/INSTALL.md](./docs/INSTALL.md).

## Contribution workflow

1. Create a focused branch from `main`.
2. Keep the change scoped to one problem.
3. Add or update tests for behavior changes.
4. Update public documentation when behavior or usage changes.
5. Run the relevant checks locally.
6. Open a pull request that explains what changed, why, and how it was verified.

Recommended branch prefixes are `feat/`, `fix/`, `docs/`, and `chore/`. Do not force-push shared branches or bypass required checks.

## Required checks

Every code change should pass:

```bash
npm run format
npm run lint
npm run typecheck
npm test
npm run build
npm run api:check
```

Run `npm run format:write` to apply Prettier formatting automatically. CI runs `npm run format` (check-only) as its first step, so an unformatted change fails CI before anything else.

Documentation changes should also pass:

```bash
npm run docs:links
npm run docs:snippets
```

Individual workspaces expose their own `typecheck`, `test`, and `build` scripts. CI runs the supported Node matrix, workspace checks, API-surface verification, and documentation checks.

## Core invariants

MemoWeft deliberately separates records from beliefs. Changes to the memory model must preserve these invariants:

- assistant replies are context, never evidence by themselves;
- confidence is computed by deterministic rules, never accepted from a model self-report;
- unresolved conflicts remain visible instead of being silently adjudicated;
- derived cognitions may reference only permitted evidence identifiers;
- corrections, invalidation, authorization, and deletion retain an auditable data path.

Changes to public APIs, the SQLite schema, portable-bundle formats, authorization behavior, confidence rules, or decay policy need an issue describing compatibility and migration impact before implementation.

### The evaluation corpus is a ratchet

Existing cases under `tests/eval/` encode behavior that was verified against a live model and then frozen. **A failing eval case means the implementation regressed, not that the case is outdated.** Do not edit or delete an existing case to make a change pass — add a new file for new behavior, and if a case genuinely no longer describes intended behavior, say so explicitly in the pull request and change it in a commit that does nothing else.

The same applies to `tests/api/api-surface.snapshot` and `tests/prompts/prompt-hashes.snapshot`: refreshing them is a deliberate act that belongs in the same commit as the change it reflects, with the compatibility impact stated in the pull request. Prompt changes additionally invalidate the published evaluation numbers until a full run is repeated.

## Dependencies

The core package intentionally has no runtime dependencies. Prefer Node built-ins where they provide the required behavior. A new dependency should be justified in the pull request, including its runtime, security, maintenance, and package-size impact.

`better-sqlite3` is an optional peer dependency for Node 20 and 22 and a development dependency for compatibility testing. It is not required on the Node 24 zero-dependency path.

## Documentation and release notes

- User-visible behavior belongs in the relevant README or `docs/` page.
- Public API changes must keep the Memory Surface Contract and API snapshot synchronized.
- Notable user-facing changes belong under `[Unreleased]` in `CHANGELOG.md`.
- Keep roadmap entries outcome-oriented; implementation notes belong in issues and pull requests.

## Pull request checklist

- [ ] The change is focused and its motivation is clear.
- [ ] Relevant tests were added or updated.
- [ ] Lint, typecheck, tests, build, and API checks pass.
- [ ] Documentation and examples match the implemented behavior.
- [ ] No credentials, local databases, generated run artifacts, or user data are included.
- [ ] Compatibility and migration impact are documented where applicable.

By contributing, you agree that your contribution is licensed under the repository's [MIT License](./LICENSE). Please follow the [Code of Conduct](./CODE_OF_CONDUCT.md) in all project spaces.
