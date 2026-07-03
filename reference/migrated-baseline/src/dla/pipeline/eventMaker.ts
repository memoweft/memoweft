/**
 * ③ Event 事件化 —— 调大模型把人话解析成 Event 语义字段，落库。
 * 对应决策：D-001（大模型只解析，不判权重！）/ D-009 / D-015（大模型第 1 次出场）。
 * 阶段：TASK-02 实现。
 *
 * 红线（D-001）：此处调模型仅做语义解析，prompt 中不得含任何"判断权重/重要性/该不该记"的指令。
 */

import type { EventInput, Event } from '../event/model.ts';
import type { EventStore } from '../event/store.ts';
import type { LLMClient, ChatMessage } from '../llm/client.ts';
import type { RawInput } from './perception.ts';

/** 大模型解析出的语义字段（仅"翻译"，不含任何价值判断——D-001）。 */
interface ParsedSemantics {
  event_form: Event['event_form'];
  is_directional_change: boolean;
  topic: string;
  tags: string[];
  summary: string;
  sentiment: Event['sentiment'];
  temporal_orientation: Event['temporal_orientation'];
}

/**
 * 解析 prompt（D-001 红线：只解析、不判价值）。
 * 刻意不含任何"重要性/权重/要不要记"的字样——那是 DLA 的事，不是模型的事。
 */
function buildParsePrompt(rawContent: string): ChatMessage[] {
  return [
    {
      role: 'system',
      content:
        '你是一个语义解析器。把用户这句话解析成结构化字段，只做"翻译"，不做任何价值判断。' +
        '严禁判断这句话是否重要、是否值得记住、权重多大——那不是你的职责。' +
        '只输出 JSON，不要任何多余文字、不要代码块标记。',
    },
    {
      role: 'user',
      content:
        `用户原话：「${rawContent}」\n\n` +
        '输出如下 JSON：\n' +
        '{\n' +
        '  "event_form": "explicit | correction",  // 普通陈述=explicit；在纠正之前说过的话=correction\n' +
        '  "is_directional_change": true | false,    // 是否表达了方向/立场的改变（如"我改主意了"）\n' +
        '  "topic": "一个词的主题",\n' +
        '  "tags": ["相关标签", ...],\n' +
        '  "summary": "一句话客观转述这句话的内容",\n' +
        '  "sentiment": "positive | negative | neutral",\n' +
        '  "temporal_orientation": "long_term | present"  // 谈长期倾向=long_term；谈当下一时=present\n' +
        '}',
    },
  ];
}

/** 从模型返回文本里抽出 JSON（容忍被代码块或多余文字包裹）。 */
function extractJson(text: string): ParsedSemantics {
  const start = text.indexOf('{');
  const end = text.lastIndexOf('}');
  if (start === -1 || end === -1 || end < start) {
    throw new Error(`解析结果不含 JSON：${text.slice(0, 300)}`);
  }
  return JSON.parse(text.slice(start, end + 1)) as ParsedSemantics;
}

/**
 * ③ 调大模型解析语义 → 组装 Event → 落库（LLM 第 1 次出场，D-015）。
 * @returns 落库后的 Event 与其 id。
 */
export async function eventMaker(
  rawInput: RawInput,
  store: EventStore,
  llm: LLMClient,
): Promise<{ event: Event; eventId: string }> {
  const raw = await llm.chat(buildParsePrompt(rawInput.raw_content));
  const parsed = extractJson(raw);

  const input: EventInput = {
    raw_content: rawInput.raw_content,
    event_form: parsed.event_form,
    is_directional_change: parsed.is_directional_change,
    topic: parsed.topic,
    tags: Array.isArray(parsed.tags) ? parsed.tags : [],
    summary: parsed.summary,
    sentiment: parsed.sentiment,
    // source_type 由感知层给定，不让模型判（D-001：来源是事实，不是语义）
    source_type: rawInput.source_type,
    temporal_orientation: parsed.temporal_orientation,
    // 关联是下阶段的事（占位空）；本阶段不连纠正链
    related_event_ids: [],
    correction_target_id: null,
  };

  const eventId = store.write(input);
  const event = store.read(eventId);
  if (!event) throw new Error('写入后立即读取失败，存储层异常');
  return { event, eventId };
}
