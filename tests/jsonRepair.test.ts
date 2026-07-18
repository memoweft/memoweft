/**
 * JSON 解析加固（写路径结构化输出）：去代码块围栏、只认对象、失败落日志 + 最多重试一次。
 * 用假 LLM，不依赖网络。
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';
import type { ChatMessage, LLMClient } from '../src/llm/client.ts';
import {
  extractJsonObject,
  parseJsonObject,
  parseJsonObjectWithRepair,
} from '../src/llm/jsonRepair.ts';

/** 按队列依次吐回复的假模型；记录调用次数。 */
function stubLLM(replies: string[]): LLMClient & { calls: ChatMessage[][] } {
  let i = 0;
  const calls: ChatMessage[][] = [];
  return {
    calls,
    get callCount() {
      return i;
    },
    async chat(messages: ChatMessage[]): Promise<string> {
      calls.push(messages);
      return replies[i++] ?? '';
    },
  };
}

test('extractJsonObject：去掉 ```json 代码块围栏，抠出对象文本', () => {
  const raw = '好的，结果如下：\n```json\n{"a":1}\n```\n（以上）';
  assert.equal(extractJsonObject(raw), '{"a":1}');
});

test('extractJsonObject：无对象 → null', () => {
  assert.equal(extractJsonObject('这里没有 JSON'), null);
  assert.equal(extractJsonObject('[1,2,3]'), null); // 数组不是对象
});

test('extractJsonObject：超长且未闭合的代码围栏不会走回溯型正则', () => {
  const raw = `解释\n\`\`\`json\n${'x'.repeat(200_000)}`;
  assert.equal(extractJsonObject(raw), null);
});

test('extractJsonObject：首个非 JSON 围栏仍按旧语义优先，不跳到后续围栏', () => {
  const raw = '```text\n{"first":true}\n```\n```json\n{"second":true}\n```';
  assert.equal(extractJsonObject(raw), '{"first":true}');
});

test('extractJsonObject：json 标签紧贴对象时仍会消费标签', () => {
  assert.equal(extractJsonObject('```json{"ok":true}```'), '{"ok":true}');
});

test('extractJsonObject：jsonish 保持旧正则的 json 前缀消费语义', () => {
  assert.equal(extractJsonObject('```jsonish{"ok":true}```'), '{"ok":true}');
});

test('extractJsonObject：纯空白 JSON 围栏不会越过闭围栏读取后续文本', () => {
  assert.equal(extractJsonObject('```json   ``` 后续解释 {"mustNotParse":true}'), null);
});

test('parseJsonObject：数组 / 标量 / 非法都算不合法 → null', () => {
  assert.equal(parseJsonObject('[1,2]'), null);
  assert.equal(parseJsonObject('42'), null);
  assert.equal(parseJsonObject('{半截'), null);
  assert.deepEqual(parseJsonObject('前言 {"x":[1]} 后语'), { x: [1] });
});

test('parseJsonObjectWithRepair：首次就合法 → 只调一次、不落日志', async () => {
  const llm = stubLLM(['{"new":[]}']);
  const logs: string[] = [];
  const out = await parseJsonObjectWithRepair<{ new: unknown[] }>({
    llm,
    messages: [{ role: 'user', content: 'x' }],
    log: (m) => logs.push(m),
  });
  assert.deepEqual(out, { new: [] });
  assert.equal(llm.callCount, 1, '未触发重试');
  assert.equal(logs.length, 0, '成功不落日志');
});

test('parseJsonObjectWithRepair：首次坏、重试合法 → 调两次、落一条日志、追加"只输出 JSON"提示', async () => {
  const llm = stubLLM(['抱歉我先解释一下……没有 JSON', '```json\n{"ok":true}\n```']);
  const logs: string[] = [];
  const out = await parseJsonObjectWithRepair<{ ok: boolean }>({
    llm,
    messages: [{ role: 'user', content: 'x' }],
    log: (m) => logs.push(m),
  });
  assert.deepEqual(out, { ok: true });
  assert.equal(llm.callCount, 2, '重试了一次');
  assert.equal(logs.length, 1, '落了一条失败日志');
  // 重试的那次消息里，末尾追加了"只输出 JSON"提示（双语层后 nudge 按语言取，缺省 en；查两语都含的 JSON 字样，不绑定语言）
  const retryMsgs = llm.calls[1]!;
  assert.ok(retryMsgs[retryMsgs.length - 1]!.content.includes('JSON'), '重试提示已追加');
});

test('parseJsonObjectWithRepair：不注入 log 时，默认 sink 不打模型原文（隐私优先）', async () => {
  // 假模型回显一个隐私词；默认 sink 只该记结构特征，不该把原文（含隐私词）打进 console.warn。
  const SECRET = 'SENSITIVE_TEST_PAYLOAD_7F3A';
  const llm = stubLLM([`抱歉先解释一下：${SECRET}，没有 JSON`, `还是没有：${SECRET}`]);
  const warned: string[] = [];
  const origWarn = console.warn;
  console.warn = (...args: unknown[]) => {
    warned.push(args.map(String).join(' '));
  };
  try {
    // 关键：不传 log，走默认 sink（console.warn）
    const out = await parseJsonObjectWithRepair({
      llm,
      messages: [{ role: 'user', content: 'x' }],
    });
    assert.equal(out, null);
  } finally {
    console.warn = origWarn;
  }
  assert.equal(warned.length, 2, '默认 sink 落了两条失败日志');
  const joined = warned.join('\n');
  assert.ok(!joined.includes(SECRET), '默认日志不含模型原文里的隐私词');
  assert.ok(!joined.includes('SENSITIVE_TEST_PAYLOAD'), '默认日志不含模型原文片段');
  // 仍应带结构特征，方便定位
  assert.ok(joined.includes('length='), '默认日志带结构特征（length；默认使用英文内部日志）');
});

test('parseJsonObjectWithRepair：两次都坏 → 最多重试一次、返回 null、落两条日志', async () => {
  const llm = stubLLM(['没有 JSON', '还是没有']);
  const logs: string[] = [];
  const out = await parseJsonObjectWithRepair({
    llm,
    messages: [{ role: 'user', content: 'x' }],
    log: (m) => logs.push(m),
  });
  assert.equal(out, null);
  assert.equal(llm.callCount, 2, '只重试一次，不无限重试');
  assert.equal(logs.length, 2, '首次失败 + 重试仍失败，各落一条');
});
