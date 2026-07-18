/**
 * MemoWeft minimal runnable example — the full write→read loop via the unified entry createMemoWeftCore.
 *
 * createMemoWeftCore assembles the three-layer stores + retriever + model pool in one line
 * (all read from .env; missing config degrades gracefully instead of crashing). It is the
 * recommended way to integrate MemoWeft — hosts need not wire the low-level stores by hand.
 *
 * This demonstrates four things:
 *   1) Build a core in one line.
 *   2) Ingest one "user-spoken" piece of evidence (ingestUserMessage).
 *   3) updateProfile distills it into the profile (distill → consolidate → attribute → index).
 *   4) handleConversationTurn handles the next message: recall the relevant profile → inject into the reply.
 *
 * Prerequisites:
 *   - Build the package first (examples import by package name): `npm run build`.
 *   - A .env at the repo root with at least a chat model (MEMOWEFT_LLM_*, or legacy DLA_LLM_*).
 *     A chat model alone is enough: without MEMOWEFT_EMBED_*, recall falls back to local
 *     FTS5 keyword search. Semantic/vector recall requires an embedder.
 *   - Node >= 20 to import the built package. (Node >= 24 also runs .ts directly with the
 *     built-in node:sqlite; on Node 20/22 install the optional driver better-sqlite3 — see docs/INSTALL.md.)
 *
 * Run (from the repo root, after building):
 *   node examples/minimal.ts
 *
 * Note: this example uses its own ./example.db and never touches your real memory file.
 */
import { createMemoWeftCore, MEMOWEFT_VERSION } from 'memoweft';

const DB = './example.db'; // standalone example db
const SUBJECT = 'demo-user'; // whose profile this is

async function main() {
  console.log(`MemoWeft ${MEMOWEFT_VERSION} · minimal example (createMemoWeftCore)\n`);

  // 1) One-line assembly: three stores + retriever + model pool, all from .env, degrade without crashing.
  const core = createMemoWeftCore({ dbPath: DB });

  const { llmReady, embedReady } = core.health();
  if (!llmReady)
    console.log(
      '!  No chat model configured (MEMOWEFT_LLM_* / legacy DLA_LLM_*): a real call will error; the write-to-db part still runs.',
    );
  if (!embedReady)
    console.log('!  No embedder configured (MEMOWEFT_EMBED_*): using local FTS5 keyword recall.');
  console.log();

  // 2) Ingest one "user-spoken" piece of evidence (authorization flags & time defaulted by Core).
  await core.ingestUserMessage({
    subjectId: SUBJECT,
    hostId: 'example',
    content: 'I focus best coding at night; daytime meetings make it hard to settle down.',
  });
  console.log(
    'Wrote 1 piece of evidence. Running updateProfile (distill event -> profile -> attribute -> index)...',
  );

  // 3) updateProfile = the one-shot write path. Real model calls; latency depends on your model
  //    (the returned timings show which step is slow).
  const result = await core.updateProfile({ subjectId: SUBJECT });
  console.log(
    `Profile updated: ${result.consolidated.created.length} new cognition(s) ` +
      `(reinforced ${result.consolidated.reinforced} / corrected ${result.consolidated.corrected} / conflicted ${result.consolidated.conflicted}), ` +
      `indexed ${result.indexed}, took ${result.timings.totalMs}ms` +
      (result.indexError ? ` (index degraded: ${result.indexError})` : ''),
  );

  // Inspect the profile via the controlled read-only API (memory.listCognitions) — no direct store access.
  const profile = core.memory.listCognitions({ subjectId: SUBJECT });
  console.log(`\nCurrent profile (${profile.length}):`);
  for (const c of profile) {
    console.log(
      `  - [${c.contentType}/${c.credStatus}] ${c.content}  (confidence ${c.confidence})`,
    );
  }

  // 4) Read path: handle the next message and see whether it recalls & injects the profile above.
  const message = "Help me plan tomorrow's schedule.";
  const turn = await core.handleConversationTurn({ subjectId: SUBJECT, message });
  console.log(`\nUser: ${message}`);
  console.log(`Assistant: ${turn.reply}`);
  if (turn.recall.length) {
    console.log(`(recalled & injected: ${turn.recall.map((r) => r.content).join(' / ')})`);
  }
  if (turn.error)
    console.log(`(reply error: ${turn.error} — but your message was stored as evidence)`);

  // 5) Clean up (close db + retriever).
  core.close();
  console.log('\nDone. Example data is in ./example.db — safe to delete.');
}

main().catch((e) => {
  console.error('Example error:', e instanceof Error ? e.message : e);
  process.exit(1);
});
