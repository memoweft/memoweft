/**
 * MemoWeft no-key demo — see the differentiators in ~30s, with no API key, no network, no config.
 *
 * It runs the full write path (ingest -> updateProfile: distill -> consolidate) using a small
 * OFFLINE stub LLM defined right here in this file. It shows the two things that set MemoWeft apart:
 *   1. A conflict (the user moves Tokyo -> Osaka) is EXPOSED and the old belief is KEPT — MemoWeft
 *      does not silently overwrite what it knew.
 *   2. An inferred item stays a low-confidence GUESS rather than being presented as a stated memory. Confidence is
 *      computed by MemoWeft from the evidence — it is never taken from the LLM's own say-so.
 *
 * No API key, no network: the stub LLM returns fixed, deterministic output. A trivial in-memory
 * retriever is injected so recall has something to search (a real host plugs a vector retriever).
 *
 * Prerequisites:
 *   npm run build            # this example imports the package by name ('memoweft')
 * Run (no environment variables needed):
 *   node examples/no-key-demo.ts
 *
 * Note: this example uses an in-memory database (':memory:') — nothing is written to disk.
 */
import { createMemoWeftCore } from 'memoweft';
import type { ChatMessage, Retriever } from 'memoweft';

const SUBJECT = 'demo-user';

// ── Offline stub LLM ──────────────────────────────────────────────────────────────────────────
// updateProfile() calls the LLM per round: distill (wants a plain-text event summary) and
// consolidate (wants a JSON object). We branch on whether the system prompt asks for JSON.
// For consolidate we PARSE the prompt to recover (a) the real evidence ids of the new utterances
// and (b) the ids of existing cognitions — because MemoWeft only keeps a cognition that cites a
// real evidence id, and a conflict must point at an already-existing cognition id.
function createOfflineStubLLM() {
  let calls = 0;

  // Existing-profile lines in the prompt look like:  - [cog-id] (fact) The user lives in Tokyo
  const parseProfile = (body: string) =>
    body.split('\n').flatMap((line) => {
      const m = line.match(/^- \[([^\]]+)\] \(([^)]+)\) (.+)$/);
      return m ? [{ id: m[1], type: m[2], content: m[3] }] : [];
    });
  // New-material utterance lines look like:      - [evidence-id] I live in Tokyo ...
  const parseUtterances = (body: string) =>
    body.split('\n').flatMap((line) => {
      const m = line.match(/^\s+- \[([^\]]+)\] (.+)$/);
      return m ? [{ id: m[1], text: m[2] }] : [];
    });

  return {
    get callCount() {
      return calls;
    },
    async chat(messages: ChatMessage[]): Promise<string> {
      calls++;
      const system = messages[0]?.content ?? '';
      const body = messages[1]?.content ?? '';

      // distill: the system prompt does NOT ask for JSON -> return a one-line event summary.
      if (!/JSON/.test(system)) {
        return body.includes('Osaka')
          ? 'The user says they moved to Osaka last month.'
          : 'The user says they live in Tokyo and commute by subway.';
      }

      // consolidate: emit new / conflict, each citing a real evidence id parsed from the prompt.
      const existing = parseProfile(body);
      const utterances = parseUtterances(body);
      const out: { new: unknown[]; reinforce: unknown[]; correct: unknown[]; conflict: unknown[] } =
        {
          new: [],
          reinforce: [],
          correct: [],
          conflict: [],
        };

      const tokyo = utterances.find((u) => u.text.includes('Tokyo'));
      const osaka = utterances.find((u) => u.text.includes('Osaka'));

      if (tokyo) {
        // A stated memory, plus a separate INFERRED claim that stays low-confidence.
        out.new.push({
          content: 'The user lives in Tokyo',
          content_type: 'fact',
          formed_by: 'stated',
          support_evidence_ids: [tokyo.id],
        });
        out.new.push({
          content: 'The user probably works somewhere central',
          content_type: 'fact',
          formed_by: 'inferred',
          support_evidence_ids: [tokyo.id],
        });
      }
      if (osaka) {
        out.new.push({
          content: 'The user lives in Osaka',
          content_type: 'fact',
          formed_by: 'stated',
          support_evidence_ids: [osaka.id],
        });
        // Contradicts the earlier "lives in Tokyo" belief -> flag a conflict, keep BOTH.
        const tokyoCog = existing.find((c) => c.content.includes('Tokyo'));
        if (tokyoCog) {
          out.conflict.push({ cognition_id: tokyoCog.id, support_evidence_ids: [osaka.id] });
        }
      }
      return JSON.stringify(out);
    },
  };
}

// ── Trivial in-memory retriever ─────────────────────────────────────────────────────────────────
// A real host plugs a vector retriever; here we score by shared words so recall has something to
// return. It hands back profile items still carrying their credStatus, so guesses and conflicts
// are never presented to a caller as bare facts.
function createDemoRetriever(): Retriever {
  let items: Array<{ id: string; text: string }> = [];
  const words = (s: string) => new Set(s.toLowerCase().split(/\W+/).filter(Boolean));
  return {
    async indexAll(next) {
      items = [...next];
    },
    async search(query, topK) {
      const q = words(query);
      return items
        .map((it) => ({ id: it.id, score: [...words(it.text)].filter((w) => q.has(w)).length }))
        .filter((h) => h.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, topK);
    },
  };
}

// How to read a credibility status for a human.
const label = (credStatus: string, formedBy?: string) => {
  if (credStatus === 'conflicted') return 'conflict kept, not overwritten';
  if (credStatus === 'candidate' || credStatus === 'low' || formedBy === 'inferred')
    return 'guess (low confidence)';
  return 'stated memory';
};

async function main() {
  console.log(
    '=== MemoWeft · no-key demo (offline stub LLM — no API key, no network, no config) ===\n',
  );

  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: createOfflineStubLLM(),
    retriever: createDemoRetriever(),
  });

  try {
    // Fixed timestamps keep the run deterministic.
    console.log('1) ingest: "I live in Tokyo and take the subway to work every day."');
    await core.ingestUserMessage({
      content: 'I live in Tokyo and take the subway to work every day.',
      subjectId: SUBJECT,
      occurredAt: '2026-01-10T09:00:00.000Z',
    });
    console.log(
      '2) updateProfile() -> distill -> consolidate  (forms the Tokyo belief + an inferred guess)',
    );
    await core.updateProfile({ subjectId: SUBJECT });

    console.log('3) ingest: "Actually, I moved to Osaka last month."');
    await core.ingestUserMessage({
      content: 'Actually, I moved to Osaka last month.',
      subjectId: SUBJECT,
      occurredAt: '2026-02-15T20:00:00.000Z',
    });
    console.log('4) updateProfile()  -> the new info CONFLICTS with "lives in Tokyo"\n');
    await core.updateProfile({ subjectId: SUBJECT });

    // ── Profile: every cognition carries a MemoWeft-computed confidence + credibility status ──
    const cogs = core.memory
      .listCognitions({ subjectId: SUBJECT })
      .slice()
      .sort((a, b) => a.content.localeCompare(b.content));

    console.log('--- Profile (core.memory.listCognitions) ---');
    for (const c of cogs) {
      console.log(
        `  [${c.credStatus.padEnd(10)}] conf ${String(c.confidence).padStart(4)}/1000  ` +
          `${c.content}  — ${label(c.credStatus, c.formedBy)}`,
      );
    }
    console.log(
      '\n  -> The old belief (Tokyo) is KEPT and flagged as conflicted — not silently replaced by Osaka.',
    );
    console.log(
      '  -> The inferred item stays a low-confidence guess. Confidence is computed by MemoWeft,',
    );
    console.log("     never taken from the LLM's own claim.\n");

    // ── Recall: results still carry their credStatus, so guesses/conflicts stay visible ──
    const hits = (await core.recall({ query: 'Where does the user live?', subjectId: SUBJECT }))
      .slice()
      .sort((a, b) => a.content.localeCompare(b.content));
    console.log('--- Recall: "Where does the user live?" (core.recall) ---');
    for (const h of hits) {
      console.log(`  · ${h.content}  [${h.credStatus}] — ${label(h.credStatus)}`);
    }

    // ── Self-check: the example proves its own point, so the acceptance run is meaningful. ──
    const conflicts = cogs.filter((c) => c.credStatus === 'conflicted');
    console.log(
      `\nSummary: ${cogs.length} cognitions, ${conflicts.length} in conflict-exposed state; ` +
        'inference remains labeled and rule-scored separately from stated memory.',
    );
    if (cogs.length === 0 || conflicts.length === 0) {
      console.error('Self-check FAILED: expected >=1 cognition and >=1 conflict.');
      process.exit(1);
    }
    console.log('Done. (in-memory database — nothing written to disk)');
  } finally {
    core.close();
  }
}

main().catch((e) => {
  console.error('Example error:', e instanceof Error ? e.message : e);
  process.exit(1);
});
