/**
 * MemoWeft example — portable memory bundle (export / validate / import).
 *
 * A user's memory is a portable, migratable asset: export it from one store, validate it, and
 * import it into another — the source trace is preserved. This round-trip uses evidence only,
 * so it runs WITHOUT any model configured.
 *
 * This demonstrates:
 *   1) Ingest evidence into a source store (no model needed to store evidence).
 *   2) Export a portable bundle (portable.exportBundle) and validate it (portable.validateBundle).
 *   3) Import it into a fresh store (portable.importBundle, mode 'merge') and verify.
 *
 * Prerequisites:
 *   - Build first: `npm run build` (examples import by package name).
 *   - No .env / model needed. Uses ./example-src.db and ./example-dst.db.
 *
 * Run (from the repo root, after building):
 *   node examples/portable-bundle.ts
 */
import { createMemoWeftCore } from 'memoweft';

const SRC = './example-src.db';
const DST = './example-dst.db';
const SUBJECT = 'demo-user';

async function main() {
  // 1) Source store: ingest a couple of evidence pieces (storing evidence needs no model).
  const src = createMemoWeftCore({ dbPath: SRC });
  await src.ingestUserMessage({ subjectId: SUBJECT, content: 'I love pour-over coffee in the morning.' });
  await src.ingestUserMessage({ subjectId: SUBJECT, content: 'I usually run before work.' });

  // 2) Export a portable bundle (synchronous — deps already bound by the core facade).
  const bundle = src.portable.exportBundle({ subjectId: SUBJECT });
  console.log(`Exported bundle: ${bundle.data.evidence.length} evidence, ${bundle.data.cognitions.length} cognitions.`);

  // 3) Validate before importing — never import a malformed / dangling bundle.
  const check = src.portable.validateBundle(bundle);
  console.log(`Bundle valid: ${check.valid} (errors: ${check.errors.length}, warnings: ${check.warnings.length})`);

  // 4) Destination store: import the bundle. mode 'merge' actually writes (idempotent by id).
  const dst = createMemoWeftCore({ dbPath: DST });
  const plan = dst.portable.importBundle(bundle, { mode: 'merge' });
  console.log(`Imported: ${plan.counts.evidence} evidence written, ${plan.duplicates.evidence} duplicate(s) skipped.`);

  // 5) Verify the destination now holds the evidence — a portable memory asset moved across stores.
  const restored = dst.memory.listEvidence({ subjectId: SUBJECT });
  console.log(`Destination now has ${restored.length} evidence.`);

  src.close();
  dst.close();
  console.log('\nDone. Example data is in ./example-src.db / ./example-dst.db — safe to delete.');
}

main().catch((e) => {
  console.error('Example error:', e instanceof Error ? e.message : e);
  process.exit(1);
});
