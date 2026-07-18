/**
 * MemoWeft MCP server 装配。
 *
 * 两件事分开：
 *   1. createCoreFromEnv() —— 从环境变量（MEMOWEFT_DB_PATH）建进程内 MemoWeftCore。
 *      缺 MEMOWEFT_DB_PATH 时明确报错要求指定，【不】建危险缺省库（不误写用户其它库/临时库）。
 *      模型 / 嵌入配置沿用 memoweft 的 .env 装配：配置按需解析，缺失项仅在相应能力被调用时报告。
 *   2. createMcpServer(core) —— 建 McpServer 并注册白名单 tool，返回配好的 server。
 *      测试可注入基于 :memory: 的替代 core，从而隔离持久化存储与外部网络。
 */
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { createMemoWeftCore, type MemoWeftCore } from 'memoweft';
import { registerTools, type RegisterToolsOptions } from './tools.ts';

/** 本包版本（与 package.json 同步；serverInfo 用）。 */
export const MCP_SERVER_VERSION = '0.2.0';

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
  // 模型与嵌入配置采用按需解析；未配置的能力会在首次使用时返回针对性的配置错误。
  return createMemoWeftCore({ dbPath });
}

/**
 * 建 MCP server 并注册白名单 tool。
 * @param core 进程内 MemoWeftCore 门面（读写都经它）。
 * @param opts 降级语义选项（logger / recallTimeoutMs；默认静默记录，超时 200ms）。
 * @returns 配好白名单 tool 的 McpServer（尚未 connect transport）。
 */
export function createMcpServer(core: MemoWeftCore, opts: RegisterToolsOptions = {}): McpServer {
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
  registerTools(server, core, opts);
  return server;
}
