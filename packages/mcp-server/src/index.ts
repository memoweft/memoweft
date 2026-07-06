/**
 * @memoweft/mcp-server 公开面。
 *
 * 外部集成包：把 MemoWeft Core 门面包成 MCP tool 给外部 AI 客户端自主调用。
 * 只暴露 5 读 + 1 轻写（见 tools.ts 白名单 / README 的 SECURITY 段）；
 * 破坏性 / 改上云授权 / 整套消化改画像的 Core 方法一律不注册成 tool。
 */
export { createMcpServer, createCoreFromEnv, MCP_SERVER_VERSION } from './server.ts';
export {
  registerTools,
  READ_TOOL_NAMES,
  WRITE_TOOL_NAMES,
  ALL_TOOL_NAMES,
  type ToolName,
} from './tools.ts';
