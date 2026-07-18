# @memoweft/adapter-mastra

> [!IMPORTANT]
> **Unreleased source preview.** This adapter is not published on npm. Its package name resolves inside this repository's npm workspace only.

Give a [Mastra](https://mastra.ai) agent long-term memory with [MemoWeft](https://github.com/memoweft/memoweft) — the portable memory library that keeps **facts and guesses apart** (confidence is rule-derived, conflicts are surfaced not adjudicated).

One `Processor` wires both directions:

- **Read** (`processInput`, before the model runs): recall relevant memory for the user's turn and inject it into the **system channel** — the user message is never touched, so nothing you inject can leak back in as "what the user said".
- **Write** (`processOutputResult`, after the model answers):
  - the user's turn → a `spoken` evidence (captured pre-injection);
  - each tool **result** → a `tool` evidence (the evidence boundary accepts only `payload.result`, never call arguments);
  - the assistant reply → `recordAssistantReply` (MemoWeft 0.6 conversation context — kept only as context for the _next_ turn, never stored as evidence).

## Try from a source checkout

```bash
git clone https://github.com/memoweft/memoweft.git
cd memoweft
npm ci
npm run build
npm run build --workspace @memoweft/adapter-mastra
```

`memoweft` `^0.6.0` and `@mastra/core` are peer dependencies. The adapter uses the 0.6 `recordAssistantReply` surface for assistant-reply and preceding-context handling.

## Usage

```ts
import { Agent } from '@mastra/core/agent';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftProcessor } from '@memoweft/adapter-mastra';

const core = createMemoWeftCore({ dbPath: './memory.db', llm, embedder });

// One instance serves both directions — register it in BOTH arrays.
const memory = createMemoWeftProcessor(core, { lang: 'en' });

const agent = new Agent({
  name: 'assistant',
  instructions: 'You are a helpful assistant.',
  model,
  inputProcessors: [memory], // processInput  → recall + inject
  outputProcessors: [memory], // processOutputResult → persist
});
```

To thread the 0.6 conversation context (so a bare "yes" is understood against the assistant's previous question), give your Mastra messages a stable `threadId` — the adapter uses it as the MemoWeft `conversationId`.

## Options

`createMemoWeftProcessor(core, options)`:

| option            | default             | meaning                                                                                                                                                                                        |
| ----------------- | ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `processorId`     | `'memoweft-memory'` | Mastra processor id.                                                                                                                                                                           |
| `subjectId`       | Core default        | Whose memory to recall / write.                                                                                                                                                                |
| `lang`            | `'en'`              | Language of the injected knowledge block (`'en'` \| `'zh'`). Wording only — does not change Core behavior.                                                                                     |
| `contentTypes`    | all                 | Recall filter by cognition type (allow-list); passed through to `core.recall`.                                                                                                                 |
| `explain`         | `false`             | Ask Core for each recalled cognition's provenance; delivered **only** via `onRecall` (never injected).                                                                                         |
| `onRecall`        | —                   | Called after each successful recall with the recalled items (id / contentType / score, and provenance when `explain`). Use it to observe or to self-filter before forwarding to a cloud model. |
| `recallTimeoutMs` | `200`               | Recall timeout. On timeout/error the turn degrades to **no injection**; the read path does not retry.                                                                                          |
| `logger`          | —                   | Structured degradation events `{ event, op, reason }`. Never receives user content, utterances, or secrets.                                                                                    |

## Guarantees

- **No injection into the user message.** Recall goes to the system channel; the captured user utterance is always the pristine input.
- **Source-role boundary.** Only tool _results_ become evidence; tool-call arguments never do, and assistant replies remain context-only.
- **Privacy.** `provenance` (raw evidence text + cloud/inference authorization bits), `contentType`, `id` and `score` are never placed in the injected prompt — they travel only through `onRecall`.
- **Never blocks the conversation.** Recall is bounded by a timeout and degrades to no-injection on failure; writes retry once then give up silently. Memory failures never abort a generation.

## Coexisting with Mastra's built-in memory

Mastra ships its own working / semantic / observational memory. MemoWeft **replaces the semantic-recall layer** with fact-vs-guess memory. If you also enable Mastra's built-in semantic recall you will get two memory systems injecting in parallel — disable Mastra's semantic recall (keep message history / working memory as you like) so recall stays coherent.

## License

MIT
