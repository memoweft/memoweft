/**
 * MemoWeft MCP server 装配。
 *
 * 两件事分开：
 *   1. createCoreFromEnv() —— 从环境变量（MEMOWEFT_DB_PATH）建进程内 MemoWeftCore。
 *      缺 MEMOWEFT_DB_PATH 时明确报错要求指定，【不】建危险缺省库（不误写用户其它库/临时库）。
 *      模型 / 嵌入配置沿用 memoweft 的 .env 装配：缺配不崩，真调用才报（见 createMemoWeftCore 文件头）。
 *   2. createMcpServer(core) —— 建 McpServer 并注册白名单 tool，返回配好的 server。
 *      测试可注入一个 :memory: 的假 core，不碰真库真网络。
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { createMemoWeftCore, type MemoWeftCore } from 'memoweft';
import { registerTools } from './tools.ts';

/** 本包版本（与 package.json 同步；serverInfo 用）。 */
export const MCP_SERVER_VERSION = '0.1.0';

/**
 * 从环境变量建 core。
 * @throws 若缺 MEMOWEFT_DB_PATH（不建危险缺省库——库路径必须由宿主明确指定）。
 */
export function createCoreFromEnv(): MemoWeftCore {
  const dbPath = process.env.MEMOWEFT_DB_PATH;
  if (!dbPath || dbPath.trim() === '') {
    throw new Error(
      'MEMOWEFT_DB_PATH is required. Set it to the path of your MemoWeft database file ' +
        "(or ':memory:' for an ephemeral in-memory database). Refusing to guess a default path.",
    );
  }
  // 模型 / 嵌入缺配不崩：createMemoWeftCore 走 loadLLMPool/loadEmbedConfig 降级（真调用才报错）。
  return createMemoWeftCore({ dbPath });
}

/**
 * 建 MCP server 并注册白名单 tool。
 * @param core 进程内 MemoWeftCore 门面（读写都经它）。
 * @returns 配好白名单 tool 的 McpServer（尚未 connect transport）。
 */
export function createMcpServer(core: MemoWeftCore): McpServer {
  const server = new McpServer(
    { name: 'memoweft', version: MCP_SERVER_VERSION },
    {
      capabilities: { tools: {} },
      instructions:
        'MemoWeft exposes a portable AI memory. Use the read tools (recall, list_*, graph) to ' +
        'retrieve stored knowledge, and memoweft_ingest_user_message to record a verbatim user ' +
        'message. Low-credibility cognitions are guesses, not established facts. Destructive and ' +
        'authorization-changing operations are intentionally not available as tools.',
    },
  );
  // 注册在此单独一层（tools.ts），便于测试枚举核对"没多暴露破坏性面"。
  registerTools(server, core);
  return server;
}
