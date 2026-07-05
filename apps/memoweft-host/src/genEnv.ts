/**
 * 配置向导·把请求体拼成 .env 文本（纯字符串函数，Host 自实现，不碰 Core/记忆）。
 * 独立成模块便于单测（server.ts 顶层会 listen，不宜在测试里 import）。
 *
 * ⚠ 隐私核心铁律：apiKey 只在【本函数栈内】流过——读进局部 const、拼进返回串、随 [code, body] 交回调用方。
 *   本函数不 writeFile、不 console.log、不往任何模块级变量/缓存写。函数返回后，局部量（含 apiKey）随栈回收，
 *   进程内不留 key。调用方 handler 也只把返回值一次性 sendJson 出去，同样不留存。
 *
 * 键名遵 Core 的 env 口径（src/llm/client.ts loadLLMConfig / src/retrieval/embedder.ts loadEmbedConfig）：
 *   对话模型 MEMOWEFT_LLM_* / 写路径小模型 MEMOWEFT_WRITE_LLM_*（含 _TIER·档2）/ 嵌入器 MEMOWEFT_EMBED_*。
 *   可选组整组为空则省略、只留一行注释说明回退/降级行为（不写半截配置）。
 *
 * @returns [httpCode, body]。缺对话模型必填三项 → [400, {error}]；否则 → [200, {env}]。
 */
export function buildEnvResponse(body: Record<string, unknown>): [number, { env: string } | { error: string }] {
  const s = (v: unknown): string => String(v ?? '').trim(); // 统一去空白；不落库、不缓存
  // dotenv 值转义：含 '#'/空格/引号的值加双引号并转义内部引号——否则 process.loadEnvFile() 会把 '#'
  //   及其后当行内注释截断（apiKey/base_url 含 '#' 会被悄悄截短 → 加载回来鉴权失败、用户对着"看着完整"的 .env 难自查）。
  const q = (v: string): string => (/[#\s"]/.test(v) ? '"' + v.replace(/"/g, '\\"') + '"' : v);

  // 对话大模型（必配三项）
  const llmBase = s(body.llmBaseUrl), llmKey = s(body.llmApiKey), llmModel = s(body.llmModel);
  // 写路径小快模型（可选三项，整组空则整组省略）
  const wBase = s(body.writeBaseUrl), wKey = s(body.writeApiKey), wModel = s(body.writeModel);
  // 写模型 tier（档2）：'local' = 本地私密消化"行为观察"(observed)；缺省 / 非 'local' → 'cloud'（最保守）。
  const wTier = s(body.writeTier).toLowerCase() === 'local' ? 'local' : 'cloud';
  // 向量嵌入（可选三项，整组空则整组省略）
  const eBase = s(body.embedBaseUrl), eKey = s(body.embedApiKey), eModel = s(body.embedModel);
  // 部署选项：是否带体验界面（对齐 testbench gen-env 收的唯一布尔字段）
  const withUI = body.withExperienceUI === true;

  // 服务端兜底校验：对话三项缺任一 → 400（前端已拦，这里是唯一可信边界，绝不生成半截配置）。
  const missing: string[] = [];
  if (!llmBase) missing.push('MEMOWEFT_LLM_BASE_URL');
  if (!llmKey) missing.push('MEMOWEFT_LLM_API_KEY');
  if (!llmModel) missing.push('MEMOWEFT_LLM_MODEL');
  if (missing.length) {
    return [400, { error: `对话模型必填项缺失：${missing.join('、')}` }];
  }

  const lines: string[] = [];
  lines.push('# ── 对话大模型（chat · 必配）：回话质量优先 ──────────────');
  lines.push(`MEMOWEFT_LLM_BASE_URL=${q(llmBase)}`);
  lines.push(`MEMOWEFT_LLM_API_KEY=${q(llmKey)}`);
  lines.push(`MEMOWEFT_LLM_MODEL=${q(llmModel)}`);
  lines.push('');

  // 写路径小模型：整组任一非空才写 + 声明 tier；整组空 → 省略 + 回退注释 + 隐私提醒。
  if (wBase || wKey || wModel) {
    lines.push('# ── 写路径小快模型（write · 可选）：整理记忆走它，不拖慢整理，也省钱 ──');
    lines.push(`MEMOWEFT_WRITE_LLM_BASE_URL=${q(wBase)}`);
    lines.push(`MEMOWEFT_WRITE_LLM_API_KEY=${q(wKey)}`);
    lines.push(`MEMOWEFT_WRITE_LLM_MODEL=${q(wModel)}`);
    // tier（档2）：声明这个写模型是云端还是本地。local = 能私密消化"行为观察"(observed，默认不上云)。
    lines.push(`MEMOWEFT_WRITE_LLM_TIER=${wTier}`);
    if (wTier === 'local') {
      lines.push('#   本地写模型：行为观察（observed）会被本地私密消化，不上云。');
    } else {
      lines.push('#   ⚠ tier=cloud：行为观察（observed，默认不上云）不会被这个云端写模型消化——');
      lines.push('#     要私密消化它们，把写模型指向本地端点（如 ollama / llama.cpp）并改 TIER=local；');
      lines.push('#     或在记忆管理里对具体证据「授权上云」（那些证据会离开本机）。');
    }
  } else {
    lines.push('# ── 写路径小快模型（write · 可选）：未配 → 自动复用上面的对话大模型（行为同旧，不崩）──');
    lines.push('#   ⚠ 隐私提醒：没有本地写模型时，行为观察（observed，默认不上云）不会被消化、会挂着等——');
    lines.push('#     要消化：配一个指向本地端点的写模型并设 MEMOWEFT_WRITE_LLM_TIER=local，');
    lines.push('#     或在记忆管理里对具体证据「授权上云」（会离开本机）。');
  }
  lines.push('');

  // 向量嵌入：整组任一非空才写；整组空 → 省略 + 注释说明降级（语义联想降级为空，整理记忆照常）。
  if (eBase || eKey || eModel) {
    lines.push('# ── 嵌入模型（embed · 可选）：让它在对话里更容易想起相关的旧事 ──');
    lines.push(`MEMOWEFT_EMBED_BASE_URL=${q(eBase)}`);
    lines.push(`MEMOWEFT_EMBED_API_KEY=${q(eKey)}`);
    lines.push(`MEMOWEFT_EMBED_MODEL=${q(eModel)}`);
  } else {
    lines.push('# ── 嵌入模型（embed · 可选）：未配 → 暂不启用语义联想（聊天/整理记忆都不受影响）──');
  }
  lines.push('');

  // 部署选项：是否带体验界面 → MEMOWEFT_EXPERIENCE_UI=on/off（on=带网页 / off=纯库模式，见下方 EXPERIENCE_UI 开关）。
  lines.push('# ── 部署选项：是否带体验界面（on=带网页 / off=纯库，不起网页）──');
  lines.push(`MEMOWEFT_EXPERIENCE_UI=${withUI ? 'on' : 'off'}`);
  lines.push('');

  return [200, { env: lines.join('\n') }];
}
