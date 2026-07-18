# MemoWeft's four-scene offline demo

**English** | [简体中文](./demo-script.zh-CN.md)

This is the quickest proof of MemoWeft's core rules: **what was said is remembered, corrections retain history, conflicts remain visible, and transient states fade while durable facts remain**. It is deterministic and offline: no API key, no network, and no persistent database.

## Run it in about 30 seconds

Requires Node.js 24+ and a repository checkout with dependencies already installed.

```bash
npm run build
node examples/no-key-demo.ts
```

The script uses an in-memory SQLite database and a stub LLM defined in the source. It writes no database file. Its output includes a stated fact, a visible conflict, and a low-confidence inference; that is the shortest full write-path demonstration.

For the longer four-scene walkthrough below, run:

```bash
npm run demo
```

`npm run demo -- --act 4` runs only scene 4 (with scene 1 prepared first). `npm run demo -- --fast-forward 30d` changes the scene-4 clock jump (default: `7d`).

## Why it is deterministic

The four-scene script injects a fixed, advanceable clock (`CreateCoreOptions.clock`), an offline stub LLM with fixed outputs, and a simple keyword retriever. A production host supplies its own model and retriever. When no embedder is configured in the normal Core setup, MemoWeft uses local FTS5 keyword retrieval; semantic/vector recall is optional.

## The four scenes

### 1. Remember — a statement becomes a confidence-scored fact

- Input: `I own a red bicycle.`
- Action: `ingest → updateProfile (distill → consolidate)` forms a `fact`.
- Result: `recall("red bicycle")` returns it. MemoWeft computes confidence itself; it does not trust a model-supplied score.

### 2. Correct — history is retained

- Input: `Actually it isn't mine — my sister owns the red bicycle.`
- Action: `consolidate.correct` marks the old user-ownership cognition with `invalidAt` and accepts the new sister-ownership cognition.
- Result: the old cognition remains inspectable as invalidated rather than being silently overwritten.

### 3. Conflict — neither side quietly wins

- Input: `I love americano.` followed by `ordered milk tea again`.
- Action: `consolidate.conflict` marks the americano preference as `conflicted`.
- Result: both accounts remain visible; MemoWeft does not make an ungrounded choice for the user.

### 4. Time — a state fades; facts and preferences persist

- Input: `I have been really stressed and in a low mood this week.` then `--fast-forward 7d`.
- Action: the injected clock advances. The effective confidence of the transient `state` decays below the recall threshold.
- Result: the mood no longer appears in recall after the jump, while the sister's bicycle fact and preference remain.

## Verify deterministic output

```bash
npm run build
node examples/demo.ts > /tmp/run1.txt
node examples/demo.ts > /tmp/run2.txt
diff /tmp/run1.txt /tmp/run2.txt
```

An empty `diff` output is the expected result.

## Recording note

To record the terminal walkthrough, run `npm run demo` with a terminal recorder such as asciinema or VHS. Store a resulting asset under `docs/assets/` before linking it from the README.
