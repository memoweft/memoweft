/**
 * 最小集成示例：把 MemoWeft 长期记忆接入 Vercel AI SDK 的一整轮对话。
 *
 * 读：wrapLanguageModel(model, createMemoWeftMiddleware(core)) —— 召回记忆注入进 prompt。
 * 写：generateText({ onEnd: createPersistOnEnd(...) }) —— 对话结束后沉淀【用户原话】。
 *
 * 从源码检出试用未发布的 0.2.0 workspace：
 *   git clone https://github.com/memoweft/memoweft.git && cd memoweft
 *   npm ci && npm run build && npm run build --workspace @memoweft/adapter-ai-sdk
 *   # 将下方 baseModel 声明替换为已配置的 provider model（例如 @ai-sdk/openai）
 *   node --experimental-strip-types packages/adapter-ai-sdk/examples/basic.ts
 * npm 已发布版本的兼容安装组合见本包 README；不要把 registry 0.1.0 与 Core 0.6 强行组合。
 */
import { generateText, wrapLanguageModel } from 'ai';
import { createMemoWeftCore } from 'memoweft';
import { createMemoWeftMiddleware, createPersistOnEnd } from '@memoweft/adapter-ai-sdk';

// 1) 起一个 Core（这里用一次性内存库；真实宿主传自己的 dbPath）。
const core = createMemoWeftCore({ dbPath: ':memory:' });

// 2) 你的 ai provider 模型。换成任意 @ai-sdk/* provider，例如：
//    import { openai } from '@ai-sdk/openai'; const baseModel = openai('gpt-4o-mini');
declare const baseModel: Parameters<typeof wrapLanguageModel>[0]['model'];

// 3) 读适配器：把 Core 包进模型——每次调用前，召回相关记忆注入 prompt。
const model = wrapLanguageModel({
  model: baseModel,
  middleware: createMemoWeftMiddleware(core, { lang: 'en' }),
});

// 4) 一整轮对话：发起 generateText，用 onEnd 在结束后落用户原话。
async function chatTurn(userMessage: string, turnId: string) {
  const { text } = await generateText({
    model,
    prompt: userMessage,
    // 写适配器：只存【用户原话】、不存助手回话；originId 给稳定 turnId 保证幂等。
    onEnd: createPersistOnEnd(core, { userMessage, originId: turnId }),
  });
  return text;
}

// 用两轮演示"这一轮存进去、下一轮召回出来"（第二轮的 prompt 会被注入第一轮沉淀的记忆）。
const reply1 = await chatTurn('I strongly prefer short, direct answers.', 'turn-1');
console.log('assistant:', reply1);

const reply2 = await chatTurn('Now explain recursion.', 'turn-2');
console.log('assistant:', reply2);

core.close();
