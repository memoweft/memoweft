/**
 * LLM 池 / 按用途取模型（"可切换模型"架构第一块 · 2026-07-01 治慢）。
 *
 * 背景：对话和写路径共用一个大模型 → 写路径（distill/consolidate/attribute）拖慢"更新画像"。
 * 本版：按【用途】分流——对话(chat)用大模型、写路径(write)用独立配的小快模型（.env `MEMOWEFT_WRITE_LLM_*`，兼容旧名 `DLA_WRITE_LLM_*`）。
 *
 * 🧭 留口（重要，别做成一次性 hack）：不写死"俩固定 client"，而是"按维度选 client"。
 *   本版维度 = 用途(chat/write)。档2「按证据 allowCloudRead 路由本地/云端」在此之上加 tier 维度即可
 *   （如给 LLMPool 加 forEvidence(ev) → 按 ev.allowCloudRead 选 local/cloud client），不用重构本文件。
 */
import { OpenAICompatClient, loadLLMConfig, type LLMClient } from './client.ts';

/** 模型用途。档2 会在此之上再叠 tier(local/cloud) 路由维度。 */
export type LLMPurpose = 'chat' | 'write';

export interface LLMPool {
  /** 按用途取一个 client。 */
  for(purpose: LLMPurpose): LLMClient;
}

/**
 * 从 .env 装配模型池（双前缀兼容由 loadLLMConfig 统一处理，此处只传中性语义 key）：
 *   - chat：`MEMOWEFT_LLM_*`（对话大模型，兼容旧名 `DLA_LLM_*`）。
 *   - write：`MEMOWEFT_WRITE_LLM_*`（写路径小快模型，兼容旧名 `DLA_WRITE_LLM_*`）；【缺配则回退 chat】——不强制、不崩、行为同旧。
 * 起服务不因缺配崩：缺 chat 配 → 抛错 stub（真调用才报）；缺 write 配 → 回退 chat。
 */
export function loadLLMPool(): LLMPool {
  const make = (prefix: string): LLMClient | null => {
    try {
      return new OpenAICompatClient(loadLLMConfig(prefix));
    } catch {
      return null;
    }
  };

  const chat = make('LLM') ?? failStub('对话模型未配（.env MEMOWEFT_LLM_*，或兼容 DLA_LLM_*）');
  const write = make('WRITE_LLM') ?? chat; // 没配写路径小模型 → 回退对话模型（行为同旧、不强制）
  const clients: Record<LLMPurpose, LLMClient> = { chat, write };

  return { for: (purpose) => clients[purpose] ?? chat };
}

/** 缺配时的兜底 client：调用即抛错（起服务不崩，真用到才报）。 */
function failStub(msg: string): LLMClient {
  return { callCount: 0, async chat() { throw new Error(msg); } };
}
