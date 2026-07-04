<!--
Keep it short and honest. Reviewers (human or AI) look first at whether the three checks
pass, then at whether this PR quietly touches the core or breaks a public API.

写短、写实。审查者（人或 AI）先看三绿过没过，再盯有没有偷偷动核心或破坏公共 API。
-->

## What changed · 改了什么

<!-- The change in a sentence or a short list. 一句话或几行说清改动。 -->

## Why · 为什么

<!-- The reason / the issue it closes. Closes #NNN. 动机 / 关联 issue。Closes #NNN。 -->

## How it was verified · 怎么验的

<!--
Paste the three-checks output (e.g. `pass 71 fail 0`). CI re-runs them as the merge gate.
贴三绿输出（比如 `pass 71 fail 0`）。CI 会作为合并门复跑。
-->

```
npm run typecheck && npm test && npm run build
# → paste result here, e.g. tests: pass 71 fail 0
```

## Core / API check · 核心与 API 自查

- [ ] Does **not** change core runtime logic or the cognitive-discipline algorithm
      （未改核心运行时逻辑 / 认知纪律判定算法）
- [ ] Runtime `dependencies` still `{}` (tooling stays in `devDependencies`)
      （runtime `dependencies` 仍为 `{}`，工具留在 `devDependencies`）
- [ ] Any breaking public-API change keeps a `@deprecated` alias
      （破坏性公共 API 改动保留了 `@deprecated` 别名）
- [ ] Docs synced if behavior/usage changed（行为或用法变了则已同步文档）
