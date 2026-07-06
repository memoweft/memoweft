#!/usr/bin/env node
/**
 * MemoWeft MCP server 可执行入口（package.json bin → dist/bin.js 的源）。
 *
 * 从环境变量建 core（缺 MEMOWEFT_DB_PATH 报错退出），起 stdio transport 连接 server。
 * MCP 客户端（Claude Desktop / Cursor / 其它）以子进程方式拉起这个 bin，经 stdin/stdout 通信。
 */
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { createCoreFromEnv, createMcpServer } from './server.ts';

async function main(): Promise<void> {
  const core = createCoreFromEnv();
  const server = createMcpServer(core);
  const transport = new StdioServerTransport();
  await server.connect(transport);
  // 连接建立后进程常驻，靠 stdio 收发；stderr 打一行给运维看（stdout 归 JSON-RPC，绝不能污染）。
  process.stderr.write('[memoweft-mcp-server] connected on stdio\n');
}

main().catch((err) => {
  process.stderr.write(`[memoweft-mcp-server] fatal: ${err instanceof Error ? err.message : String(err)}\n`);
  process.exit(1);
});
