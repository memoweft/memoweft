# Glossary

**English** | [简体中文](./glossary.zh-CN.md)

This page is the single formal definition of MemoWeft's core terms. Column one is the code name; source, API, and type names use only this word. Column two defines it for engineers. Column three is the plain wording a user-facing surface may show instead — the same concept, softer words.

## Terms

| Term (code) | Definition | User-facing wording |
| --- | --- | --- |
| `evidence` | A stored record of what actually arrived — a user's words, an observation, or a tool result. Every judgment must cite it; nothing else counts as a source. | 记忆线索 / 原话 / 记录 |
| `event` | One or more pieces of evidence grouped into a single episode ("something that happened"), with a summary and an `occurredAt`. Built by `distill`. | 经历片段 / 一次经历 |
| `cognition` | One judgment MemoWeft holds about a user (a `fact`, `preference`, `goal`, `project`, `state`, `trait`, `hypothesis`, `trend`; see `ContentType`), carrying a rule-computed `confidence` / `credStatus` and links to the evidence it rests on. | 对你的理解 / 理解条目 |
| `profile` | The current set of a subject's active cognitions — the whole picture MemoWeft has assembled about one user. Rebuilt by `updateProfile`. | 对你的了解 / 个人上下文（用户界面避免「用户画像」） |
| `confidence` | An integer 0–1000 score MemoWeft computes from the supporting evidence, never taken from the model's self-report. Algorithm-tuning input, not a 0–1 probability. See [Confidence tiers](#confidence-tiers). | 把握度（用定性档，不显示数字） |
| `credStatus` | The qualitative band a cognition's confidence falls in: `candidate \| low \| limited \| stable \| conflicted`. Adding values is not a breaking change; hosts keep a `default` branch. | 候选 / 低把握 / 有一定把握 / 比较确定 / 有冲突，需确认 |
| `sourceKind` | How a piece of evidence arrived: `spoken \| inferred \| observed \| tool`. Routes the default authorization bits — `observed` and `tool` default to no cloud read. | （对内；对外通过溯源体现） |
| `recall` | A synchronous, lightweight read that returns the cognitions relevant to a query (`RecalledCognition[]`), gated by validity / archive / scope / decay. Never blocks the chat. | 想起相关内容 / 相关记忆 |
| `attribution` | The write-path step that starts from a phenomenon (a `state` cognition), pulls evidence in a time window, and asks "why" — producing an explainable hypothesis: low confidence, attached to evidence, refutable. | 猜测原因 / 可能是因为 |
| `conflict` | Two cognitions that contradict each other. MemoWeft exposes them side by side and marks the status `conflicted`; it never auto-picks a winner. | 需要确认的矛盾 / 说法不一致 |
| `hypothesis` | A `ContentType` value: an explainable guess produced by `attribution` — `formedBy: inferred`, low capped confidence, always refutable by the user. | 暂时的猜测 / 待确认的想法 |
| `decay` / `expire` | Cognitions fade at typed speeds — a passing `state` fades fast, an explicit preference is never auto-forgotten. `effectiveConfidence` = stored `confidence` × decay factor, computed at read time (not persisted). | 变淡 / 不再当成现在的你 |
| `updateProfile` | The one-shot batch write: `distill` → `consolidate` → `attribute` → rebuild the recall index. The heavy digest that reads never wait on. | 整理记忆 / 重新认识你 |
| `distill` | The `updateProfile` step that turns raw evidence into events. | 整理成经历片段 |
| `consolidate` | The `updateProfile` step that digests events into cognitions incrementally, computing `confidence` / `credStatus`. | 更新理解 |
| `retriever` | The injectable backend that finds relevant cognitions for `recall` (vector or keyword). An implementation seam, not a user concept. | （不给用户看；对内叫「找相关记忆」） |
| source trace | The provenance chain: which evidence a cognition cites, each link tagged `support` or `contradict` (`EvidenceLink`). | 根据哪句话知道的 / 从哪里看出来的 |
| `scope` | Where a cognition applies; `null` = general. | （属于「对你的了解」的一部分） |

## Confidence tiers

MemoWeft stores `confidence` as an integer 0–1000, computed by rule from the evidence — never self-reported by the model. The number tunes the algorithm. Do not show it to end users, and do not read it as a 0–1 probability.

A user-facing surface shows a qualitative tier instead, derived from `credStatus`:

| credStatus (code) | Confidence band | User tier (English) | 用户档（中文） |
| --- | --- | --- | --- |
| `candidate` | lowest — just proposed | just a candidate | 候选 |
| `low` | low | low confidence | 低把握 |
| `limited` | middling | some confidence | 有一定把握 |
| `stable` | high, settled | fairly settled | 比较确定 / 已较稳定 |
| `conflicted` | contradicted | conflicting, needs confirming | 有冲突，需确认 |

`conflicted` is not a higher or lower score — it means two cognitions disagree and a human should confirm (see `conflict`). Adding a new `credStatus` value is not a breaking change, so a user surface keeps a fallback for unknown tiers.

---

Positioning and wording discipline — how to talk about MemoWeft, and which layer is allowed to say "she" — live in [internal/naming-positioning.md](./internal/naming-positioning.md), not here.
