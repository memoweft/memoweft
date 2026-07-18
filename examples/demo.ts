/**
 * MemoWeft 四幕 demo——90 秒看懂「认知纪律」为什么值钱。
 *
 * clone → 一条命令 `npm run demo` → 无 API key、无网络、确定性复现。
 * 确定性三件套：注入【固定可前进的 clock】+ 离线 stub LLM（本文件内，输出写死）+ 简易词匹配召回器。
 * demo 只经【公共 API】(`import 'memoweft'`) 调用核心——它同时是 API 的活体验收。
 *
 * 四幕（每幕：用户输入 → 记忆层动作 → 认知状态表）：
 *   1. 记住：「我有一辆红色自行车」→ 固化为高置信度事实 → 可被召回：说过的话被记住，且带置信度。
 *   2. 纠正：「其实不是我的，是我妹妹的」→ 旧认知标失效但不删、新认知取代。纠正有痕，历史可溯。
 *   3. 矛盾：说爱美式，却连点奶茶 → 暴露 conflict、不裁决任何一方：矛盾不是被谁悄悄赢了，而是被看见。
 *   4. 时间：--fast-forward 快进 → 上周的坏心情衰减消失，事实留存：情绪会过去，事实会留下。
 *
 * 跑：npm run demo            （顺序演完四幕）
 *     npm run demo -- --act 3 （只演第 3 幕）
 *     npm run demo -- --fast-forward 30d （第 4 幕快进的时长，缺省 7d）
 */
import { createMemoWeftCore, type ChatMessage, type Retriever, type Clock } from 'memoweft';

const SUBJECT = 'demo-user';
const BASE = '2026-01-10T09:00:00.000Z'; // 固定基准时刻——确定性的锚。

// ── 离线 stub LLM：updateProfile 每轮调 distill（要纯文本事件摘要）+ consolidate（要 JSON）。 ──
// 按【系统提示是否要 JSON】分流；consolidate 解析 prompt 拿真实 evidence id / 现有认知 id
// （MemoWeft 只保留引用真实 evidence id 的认知；conflict/correct 必须指向已存在的认知 id）。
function offlineStubLLM() {
  const parseProfile = (body: string) =>
    body.split('\n').flatMap((line) => {
      const m = line.match(/^- \[([^\]]+)\] \(([^)]+)\) (.+)$/);
      return m ? [{ id: m[1]!, type: m[2]!, content: m[3]! }] : [];
    });
  const parseUtterances = (body: string) =>
    body.split('\n').flatMap((line) => {
      const m = line.match(/^\s+- \[([^\]]+)\] (.+)$/);
      return m ? [{ id: m[1]!, text: m[2]! }] : [];
    });

  return {
    callCount: 0,
    async chat(messages: ChatMessage[]): Promise<string> {
      this.callCount++;
      const system = messages[0]?.content ?? '';
      const body = messages[1]?.content ?? '';

      // distill：非 JSON → 一行事件摘要（按最新一条 utterance 内容分流）。
      if (!/JSON/.test(system)) {
        const u = parseUtterances(body);
        const last = u[u.length - 1]?.text ?? '';
        if (/sister|妹妹|not.*mine|不是我的/i.test(last))
          return 'The user clarifies the red bicycle belongs to their sister, not them.';
        if (/red bicycle|红色自行车|own/i.test(last))
          return 'The user says they own a red bicycle.';
        if (/americano|美式/i.test(last)) return 'The user says they love americano.';
        if (/milk tea|bubble|奶茶/i.test(last)) return 'The user ordered milk tea again.';
        if (/stress|mood|心情|tired|累/i.test(last))
          return 'The user says they have been stressed and in a low mood this week.';
        return 'The user said something.';
      }

      // consolidate：按新 utterance 内容产 new/correct/conflict，每条引用真实 id。
      const existing = parseProfile(body);
      const utter = parseUtterances(body);
      const out: { new: unknown[]; reinforce: unknown[]; correct: unknown[]; conflict: unknown[] } =
        { new: [], reinforce: [], correct: [], conflict: [] };
      const find = (re: RegExp) => utter.find((u) => re.test(u.text));

      const sister = find(/sister|妹妹|not.*mine|不是我的/i);
      const bicycle = !sister && find(/red bicycle|红色自行车|own/i);
      const americano = find(/americano|美式/i);
      const milkTea = find(/milk tea|bubble|奶茶/i);
      const mood = find(/stress|mood|心情|tired|累/i);

      if (bicycle) {
        out.new.push({
          content: 'The user owns a red bicycle',
          content_type: 'fact',
          formed_by: 'stated',
          support_evidence_ids: [bicycle.id],
        });
      }
      if (sister) {
        // 明确纠正：correct 一条 = 指向旧认知 + 新认知内容（consolidate 据此把旧标失效保留、采纳新的）。
        const old = existing.find(
          (c) => /owns a red bicycle/i.test(c.content) && !/sister/i.test(c.content),
        );
        const corrected = {
          content: "The user's sister owns the red bicycle",
          content_type: 'fact',
          formed_by: 'stated',
          support_evidence_ids: [sister.id],
        };
        if (old) out.correct.push({ cognition_id: old.id, ...corrected });
        else out.new.push(corrected);
      }
      if (americano) {
        out.new.push({
          content: 'The user likes americano',
          content_type: 'preference',
          formed_by: 'stated',
          support_evidence_ids: [americano.id],
        });
      }
      if (milkTea) {
        // 行为与旧偏好冲突 → 标 conflict、两条都留，不覆盖。
        const coffee = existing.find((c) => /americano/i.test(c.content));
        if (coffee)
          out.conflict.push({ cognition_id: coffee.id, support_evidence_ids: [milkTea.id] });
        out.new.push({
          content: 'The user ordered milk tea',
          content_type: 'state',
          formed_by: 'observed',
          support_evidence_ids: [milkTea.id],
        });
      }
      if (mood) {
        out.new.push({
          content: 'The user is stressed and in a low mood',
          content_type: 'state',
          formed_by: 'stated',
          support_evidence_ids: [mood.id],
        });
      }
      return JSON.stringify(out);
    },
  };
}

// ── 简易词匹配召回器（同 no-key-demo；真实宿主插向量召回器）。 ──
function wordRetriever(): Retriever {
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

// ── 渲染：三段式纯文本，无运行时依赖。 ──
const line = (s = '') => console.log(s);
function showProfile(core: ReturnType<typeof createMemoWeftCore>) {
  const cogs = core.memory
    .listCognitions({ subjectId: SUBJECT })
    .slice()
    .sort((a, b) => a.content.localeCompare(b.content));
  line('   认知状态表 (core.memory.listCognitions):');
  line('   ' + 'cognition'.padEnd(42) + 'type'.padEnd(12) + 'conf'.padEnd(6) + 'status');
  for (const c of cogs) {
    const mark =
      c.credStatus === 'conflicted' ? ' !! CONFLICT' : c.invalidAt ? ' (invalidated, kept)' : '';
    line(
      '   ' +
        c.content.padEnd(42) +
        c.contentType.padEnd(12) +
        String(c.confidence).padStart(4).padEnd(6) +
        c.credStatus +
        mark,
    );
  }
}

async function act1(core: ReturnType<typeof createMemoWeftCore>) {
  line('\n━━ 幕 1 · 记住 ━━  说过的话被记住，且带 MemoWeft 自算的置信度');
  line('User: "I own a red bicycle."');
  await core.ingestUserMessage({
    content: 'I own a red bicycle',
    subjectId: SUBJECT,
    occurredAt: BASE,
  });
  await core.updateProfile({ subjectId: SUBJECT });
  line('Memory: ingest → updateProfile(distill→consolidate) → 固化为 fact');
  showProfile(core);
  const hits = await core.recall({ query: 'red bicycle ownership', subjectId: SUBJECT });
  line(
    '   recall("red bicycle"): ' +
      hits.map((h) => `${h.content} [${h.credStatus} ${h.confidence}]`).join(' / '),
  );
}

async function act2(core: ReturnType<typeof createMemoWeftCore>) {
  line('\n━━ 幕 2 · 纠正 ━━  纠正有痕，历史可溯（旧认知失效但不删）');
  line('User: "Actually it is not mine — my sister owns the red bicycle."');
  await core.ingestUserMessage({
    content: 'Actually it is not mine, my sister owns the red bicycle',
    subjectId: SUBJECT,
    occurredAt: '2026-01-11T09:00:00.000Z',
  });
  await core.updateProfile({ subjectId: SUBJECT });
  line(
    'Memory: consolidate.correct → 旧「user bicycle ownership」标 invalidAt（保留可溯源）+ 新「sister bicycle ownership」',
  );
  showProfile(core);
}

async function act3(core: ReturnType<typeof createMemoWeftCore>) {
  line('\n━━ 幕 3 · 矛盾 ━━  矛盾不是被谁悄悄赢了，而是被看见');
  line('User: "I love americano."');
  await core.ingestUserMessage({
    content: 'I love americano',
    subjectId: SUBJECT,
    occurredAt: '2026-01-12T09:00:00.000Z',
  });
  await core.updateProfile({ subjectId: SUBJECT });
  line('User(behaviour): ordered milk tea again');
  await core.ingestUserMessage({
    content: 'ordered milk tea again',
    subjectId: SUBJECT,
    occurredAt: '2026-01-13T09:00:00.000Z',
  });
  await core.updateProfile({ subjectId: SUBJECT });
  line('Memory: consolidate.conflict → 「likes americano」标 conflicted，两条都留，不覆盖');
  showProfile(core);
}

async function act4(
  core: ReturnType<typeof createMemoWeftCore>,
  advance: () => void,
  fastForwardLabel: string,
) {
  line('\n━━ 幕 4 · 时间 ━━  情绪会过去，事实会留下');
  line('User: "I have been really stressed and in a low mood this week."');
  await core.ingestUserMessage({
    content: 'I have been really stressed and in a low mood this week',
    subjectId: SUBJECT,
    occurredAt: '2026-01-14T09:00:00.000Z',
  });
  await core.updateProfile({ subjectId: SUBJECT });
  const q = 'stressed mood low red bicycle sister americano';
  const before = await core.recall({ query: q, subjectId: SUBJECT });
  line('   recall(now): ' + before.map((h) => h.content).join(' / '));
  line(`Memory: --fast-forward ${fastForwardLabel} → 读路径 now 前进，情绪 state 有效置信衰减出局`);
  advance();
  const after = await core.recall({ query: q, subjectId: SUBJECT });
  line(`   recall(+${fastForwardLabel}): ` + after.map((h) => h.content).join(' / '));
  line('   → 上周的「stressed/low mood」淡出；妹妹的红色自行车与偏好类事实留存。');
}

function parseDurationDays(s: string | undefined): number {
  if (!s) return 7;
  const m = s.match(/^(\d+)\s*d?$/i);
  return m ? parseInt(m[1]!, 10) : 7;
}

async function main() {
  const argv = process.argv.slice(2);
  const actArg = argv.includes('--act') ? parseInt(argv[argv.indexOf('--act') + 1] ?? '0', 10) : 0;
  const ffDays = parseDurationDays(
    argv.includes('--fast-forward') ? argv[argv.indexOf('--fast-forward') + 1] : undefined,
  );

  line('=== MemoWeft · 四幕 demo（无 key · 无网络 · 确定性）===');

  // 可变 clock：base 固定；第 4 幕 advance() 前移。确定性靠 base 固定 + 注入。
  let nowMs = Date.parse(BASE);
  const clock: Clock = () => new Date(nowMs);
  const advance = () => {
    nowMs += ffDays * 24 * 3600 * 1000;
  };

  const core = createMemoWeftCore({
    dbPath: ':memory:',
    llm: offlineStubLLM(),
    retriever: wordRetriever(),
    clock,
  });
  try {
    if (actArg === 0 || actArg === 1) await act1(core);
    if (actArg === 0 || actArg === 2) {
      if (actArg === 2) await act1(core);
      await act2(core);
    }
    if (actArg === 0 || actArg === 3) {
      if (actArg === 3) {
        await act1(core);
      }
      await act3(core);
    }
    if (actArg === 0 || actArg === 4) {
      if (actArg === 4) {
        await act1(core);
      }
      await act4(core, advance, `${ffDays}d`);
    }
    line('\nDone. (in-memory database — nothing written to disk)');
  } finally {
    core.close();
  }
}

main().catch((e) => {
  console.error('Demo error:', e instanceof Error ? e.message : e);
  process.exit(1);
});
