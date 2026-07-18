/**
 * MemoWeft plugin contract v2 — a runnable, end-to-end demo of the hook lifecycle (step 7).
 *
 * Shows a `tool` plugin observing the pipeline through hooks and asking Core for things through
 * the restricted PluginContext — WITHOUT being able to mutate the pipeline or bypass memory rules:
 *   - onLoad(ctx)          fires once when the core is created.
 *   - onUserMessage(msg,ctx) fires after each conversation turn (observe-only: the returned value is ignored).
 *   - onObservation(obs,ctx) fires after each observation is ingested.
 *   - ctx.submitObservation(...)  records an observation (forced to the conservative `observed`
 *                                 defaults — a plugin cannot mark it cloud-readable).
 *   - ctx.requestMemory(query)    reads back the relevant recalled cognitions.
 *
 * Self-contained: it ships a tiny stub model, so no .env / real LLM is required.
 *   Build first (examples import by package name): `npm run build`
 *   Run:                                           `node examples/plugin-hook.ts`
 * Uses an in-memory db (':memory:') — it never touches your real memory file.
 */
import { createMemoWeftCore, type MemoWeftPlugin, type ChatMessage } from 'memoweft';

// A minimal "observer" tool plugin: it logs what it sees and exercises both context capabilities.
const observer: MemoWeftPlugin = {
  id: 'demo-observer',
  name: 'Demo Observer',
  type: 'tool',
  // Declarative permissions: without these, ctx.submitObservation / ctx.requestMemory throw.
  permissions: { submitObservation: true, requestMemory: true },
  onLoad() {
    console.log('[plugin] onLoad: plugin registered');
  },
  async onUserMessage(msg, ctx) {
    console.log(`[plugin] onUserMessage: user said "${msg.content}" -> reply was "${msg.reply}"`);
    const related = await ctx.requestMemory(msg.content);
    console.log(`[plugin]   requestMemory -> ${related.length} relevant cognition(s)`);
    // React by recording an observation. Note: no auth bits allowed here — it lands as `observed`
    // with the conservative defaults (local-readable, NOT cloud-readable).
    await ctx.submitObservation({
      kind: 'demo_turn',
      occurredAt: new Date().toISOString(),
      content: `user chatted about: ${msg.content}`,
    });
  },
  async onObservation(obs, _ctx) {
    console.log(`[plugin] onObservation: kind=${obs.kind} "${obs.content}"`);
  },
};

// Tiny stub model so the demo runs with no .env. In real use, drop `llm` and configure MEMOWEFT_LLM_* instead.
const stubLLM = {
  callCount: 0,
  async chat(_messages: ChatMessage[]): Promise<string> {
    return '(stub reply) got it, noted.';
  },
};

async function main(): Promise<void> {
  const core = createMemoWeftCore({ dbPath: ':memory:', llm: stubLLM, plugins: [observer] });
  console.log('=== plugin hook demo ===');

  // A conversation turn -> onUserMessage fires (which itself submits an observation -> onObservation fires).
  await core.handleConversationTurn({ message: 'I have been learning guitar lately' });

  console.log('--- ingest an observation directly ---');
  await core.ingestObservation({
    observations: [
      {
        kind: 'active_window',
        occurredAt: new Date().toISOString(),
        content: 'spent 20 min in GuitarTab',
      },
    ],
  });

  await new Promise((r) => setTimeout(r, 30)); // let the fire-and-forget onLoad settle for tidy output
  core.close();
  console.log('=== done (every [plugin] line above is a hook that actually fired) ===');
}

void main();
