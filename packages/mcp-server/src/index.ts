/**
 * @memoweft/mcp-server 公开面。
 *
 * 外部集成包：把 MemoWeft Core 门面包成 MCP tool 给外部 AI 客户端自主调用。
 * 只暴露 5 读 + 3 轻写（存用户原话 / 存工具结果 / 静音一条认知；见 tools.ts 白名单 / README 的 SECURITY 段）；
 * 破坏性 / 改上云授权 / 整套消化改画像的 Core 方法一律不注册成 tool。
 */
export { createMcpServer, createCoreFromEnv, MCP_SERVER_VERSION } from './server.ts';
export {
  registerTools,
  READ_TOOL_NAMES,
  WRITE_TOOL_NAMES,
  ALL_TOOL_NAMES,
  type ToolName,
  type RegisterToolsOptions,
} from './tools.ts';

// 降级语义公开类型：供宿主为注入的 logger 标注类型。
export {
  DEFAULT_RECALL_TIMEOUT_MS,
  type McpServerLogger,
  type McpDegradedEvent,
} from './degrade.ts';
