# Release guide

This document is for MemoWeft maintainers. Users should start with the [installation guide](./INSTALL.md).

MemoWeft releases are immutable. Publish only from a clean, reviewed commit after every required check passes.

## What the version tag publishes

A `vX.Y.Z` tag triggers the publish job in [CI](../.github/workflows/ci.yml). The current workflow publishes only the root `memoweft` package with npm provenance.

Adapters and the MCP server have independent package versions. Do not publish all workspaces from a root release tag.

## Publish an integration workspace

The tag workflow publishes only the root package. Publish an adapter or the MCP server as its own reviewed release:

1. Update only that workspace's version, its package-lock entry, peer range, README, and changelog entry; do not reuse a published version.
2. From a clean checkout, run its checks and inspect its tarball:

   ```bash
   npm install --package-lock-only --ignore-scripts
   npm run typecheck --workspace=@memoweft/adapter-ai-sdk
   npm test --workspace=@memoweft/adapter-ai-sdk
   npm run build --workspace=@memoweft/adapter-ai-sdk
   npm pack --workspace=@memoweft/adapter-ai-sdk --dry-run --json
   ```

   Substitute the target workspace name (for example, `@memoweft/mcp-server`).

3. Publish from the reviewed commit with an npm account authorized for that package:

   ```bash
   npm publish --workspace=@memoweft/adapter-ai-sdk --access public --provenance
   ```

4. For `@memoweft/mcp-server`, update the official MCP registry only after its npm version is live. Keep `mcpName` and `server.json.name` identical, set `server.json` and its npm package reference to the published version, validate with the registry publisher, then submit the registry update.
5. Verify the published package in a fresh temporary directory without `--legacy-peer-deps`. For `0.1.0`, the compatible pair is `memoweft@0.5.1` plus the integration at `0.1.0`; for the planned `0.2.0`, verify the declared peer range after publication.

   ```bash
   npm init -y
   npm install memoweft@0.5.1 @memoweft/adapter-ai-sdk@0.1.0
   ```

Use a workspace-specific annotated tag if release tracking needs one (for example, `adapter-ai-sdk-v0.2.0`); it does not trigger the root package workflow.

## Prepare the release

1. Confirm the target commit is on `main` and the worktree is clean.
2. Choose the next version according to the compatibility impact. MemoWeft is pre-1.0, so minor releases may still contain documented breaking changes.
3. Keep these values identical:
   - `package.json`
   - the root package entry in `package-lock.json`
   - `MEMOWEFT_VERSION` in `src/version.ts`
4. Move completed entries from `Unreleased` into a dated section in [CHANGELOG.md](../CHANGELOG.md).
5. Open and merge a release-preparation pull request. Do not tag an unreviewed local commit.

## Verify the release candidate

From a clean checkout on Node 24:

```bash
npm ci
npm run lint
npm run typecheck
npm test
npm run build
npm run api:check
npm run prompts:update
npm run shared:check
npm run docs:links
npm run docs:snippets
npm pack --dry-run --json
```

`prompts:update` must leave the worktree unchanged. Review the `npm pack` manifest and confirm that it contains the compiled JavaScript and declarations, package metadata, license, changelog, and public READMEs—without source databases, secrets, logs, testbench files, or local artifacts.

The workflow also verifies Node 20/22 compatibility, Python parity, SDK dependency ranges, and distribution smoke tests.

## Tag and publish

After the release-preparation pull request is merged and the `main` checks are green:

```bash
git fetch origin
git switch main
git pull --ff-only
git tag -a vX.Y.Z -m "MemoWeft vX.Y.Z"
git push origin vX.Y.Z
```

The tag must match the package version exactly. The publish job requires repository npm credentials with permission to publish `memoweft`; it runs `npm publish --provenance --access public` only after the aggregate CI gate succeeds.

Do not publish from a different commit while the tag workflow is running.

## Verify after publication

Check the workflow, npm metadata, package contents, and a clean install:

```bash
npm view memoweft version --registry=https://registry.npmjs.org
npm view memoweft dist.integrity --registry=https://registry.npmjs.org
```

Then, in a temporary directory:

```bash
npm init -y
npm install memoweft@X.Y.Z
node --input-type=module -e "import { MEMOWEFT_VERSION } from 'memoweft'; console.log(MEMOWEFT_VERSION)"
```

The printed runtime version must equal `X.Y.Z`.

## Failed releases

- Never move or overwrite a published tag.
- Never reuse an npm version.
- If publication fails before npm accepts the package, fix the workflow and rerun the same tag job only when the tag still points to the reviewed commit.
- If a defective version is already public, document the issue and publish a new patch version. Deprecate the defective version on npm when appropriate; do not silently replace it.
