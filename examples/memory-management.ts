/**
 * MemoWeft example — controlled memory management (core.memory.*).
 *
 * A user's memory isn't edited freely. MemoWeft exposes a controlled, audited API: you
 * invalidate / merge / safely-remove cognitions, each with a reason recorded to an audit log.
 * This keeps the "facts vs. guesses" discipline intact while still letting the host curate.
 *
 * This demonstrates:
 *   1) Form cognitions via the write path (ingest -> updateProfile).
 *   2) List the active profile (memory.listCognitions).
 *   3) Invalidate one with a reason (memory.invalidateCognition) — it drops out of the active profile
 *      but is retained (soft, traceable), not hard-deleted.
 *
 * Prerequisites:
 *   - Build first: `npm run build` (examples import by package name).
 *   - A .env with a chat model — updateProfile calls the model to form cognitions (see examples/minimal.ts).
 *   - Uses its own ./example-memory.db.
 *
 * Run (from the repo root, after building):
 *   node examples/memory-management.ts
 */
import { createMemoWeftCore } from 'memoweft';

const DB = './example-memory.db';
const SUBJECT = 'demo-user';

async function main() {
  const core = createMemoWeftCore({ dbPath: DB });
  if (!core.health().llmReady) {
    console.log(
      '!  No chat model configured — updateProfile needs one to form cognitions. See examples/minimal.ts.\n',
    );
  }

  // 1) Seed some evidence and form cognitions.
  await core.ingestUserMessage({
    subjectId: SUBJECT,
    content: 'I love pour-over coffee in the morning.',
  });
  await core.ingestUserMessage({
    subjectId: SUBJECT,
    content: 'I switched from tea to coffee this year.',
  });
  await core.updateProfile({ subjectId: SUBJECT });

  // 2) List the active profile.
  let profile = core.memory.listCognitions({ subjectId: SUBJECT });
  console.log(`Active cognitions (${profile.length}):`);
  for (const c of profile) console.log(`  - [${c.id}] ${c.content}`);

  // 3) Invalidate the first one with a reason (recorded to the audit log).
  if (profile.length > 0) {
    const target = profile[0]!;
    core.memory.invalidateCognition({
      cognitionId: target.id,
      reason: 'example: user retracted this',
    });
    console.log(
      `\nInvalidated [${target.id}] with a reason (soft-invalidate: retained & traceable, not deleted).`,
    );

    profile = core.memory.listCognitions({ subjectId: SUBJECT });
    console.log(
      `Active cognitions now (${profile.length}) — invalidated ones drop out of the active profile.`,
    );
  }

  core.close();
  console.log('\nDone. Example data is in ./example-memory.db — safe to delete.');
}

main().catch((e) => {
  console.error('Example error:', e instanceof Error ? e.message : e);
  process.exit(1);
});
