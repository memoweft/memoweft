/**
 * LLM 池：按用途选择模型。
 *
 * 背景：对话和写路径共用一个大模型 → 写路径（distill/consolidate/attribute）拖慢"更新画像"。
 * 按【用途】分流：对话(chat)使用主模型，写路径(write)可使用独立配置的低延迟模型
 * （环境变量 `MEMOWEFT_WRITE_LLM_*`，兼容旧名 `DLA_WRITE_LLM_*`）。
 *
 * 路由抽象按维度选择 client，而不是把调用方绑定到两个固定实例；当前维度是用途（chat/write）。
 *   模型路由（tier）已落 config 层：每个 client 从自己的 env 前缀读 `*_TIER`（见 client.ts LLMConfig.tier），
 *   故写模型 client 天生带自己声明的 tier；写模型缺配回退成对话 client 时，自然继承对话 client 的 tier
 *   （杜绝"标 local 实跑云端"）。写路径 prompt 选择依据 client.tier 决定按哪个授权位筛（见 evidence/privacy.ts）。
 *   该接口可在保持调用方稳定的前提下扩展更多路由维度。
 */
import { resolveLang } from '../config.ts';
import { OpenAICompatClient, loadLLMConfig, type LLMClient } from './client.ts';

/** 模型用途；每个 client 还独立声明 tier（local/cloud）。 */
export type LLMPurpose = 'chat' | 'write';

export interface LLMPool {
  /** 按用途取一个 client。 */
  for(purpose: LLMPurpose): LLMClient;
}

/**
 * 从 .env 装配模型池（双前缀兼容由 loadLLMConfig 统一处理，此处只传中性语义 key）：
 *   - chat：`MEMOWEFT_LLM_*`（对话大模型，兼容旧名 `DLA_LLM_*`）。
 *   - write：`MEMOWEFT_WRITE_LLM_*`（写路径小快模型，兼容旧名 `DLA_WRITE_LLM_*`）；【缺配则回退 chat】——不强制、不崩、行为同旧。
 * 配置缺失不阻止服务初始化：缺 chat 配时返回延迟报错客户端；缺 write 配时回退到 chat 客户端。
 */
export function loadLLMPool(): LLMPool {
  const make = (prefix: string): LLMClient | null => {
    try {
      return new OpenAICompatClient(loadLLMConfig(prefix));
    } catch {
      return null;
    }
  };

  const chat =
    make('LLM') ??
    failStub(
      resolveLang() === 'zh'
        ? '对话模型未配（.env MEMOWEFT_LLM_*，或兼容 DLA_LLM_*）'
        : 'Chat model not configured (.env MEMOWEFT_LLM_*, or legacy DLA_LLM_*)',
    );
  const write = make('WRITE_LLM') ?? chat; // 没配写路径小模型 → 回退对话模型（行为同旧、不强制）
  const clients: Record<LLMPurpose, LLMClient> = { chat, write };

  return { for: (purpose) => clients[purpose] ?? chat };
}

/** 延迟报错客户端：允许无需模型的 Core 能力初始化，并在模型请求时报告配置缺失。 */
function failStub(msg: string): LLMClient {
  return {
    callCount: 0,
    async chat() {
      throw new Error(msg);
    },
  };
}
