# T5 · 扫尾：残留 / 文档漂移 / 静默失败

**对应五关**：信任关（外人翻仓库时的观感与文档可信度）。全是独立小项，一个提交打包。

## 清单（每项做完打勾）

1. **删 `src/dla/` 空目录**（审计：0 个文件，6 月 23 日旧品牌残留）。git 不追踪空目录，本项可能只是本地 rmdir，无提交内容也算完成。
2. **CONTRIBUTING.md 去掉写死的测试通过数**——共 **4 处**：第 21 行（"Core 144 个全过"）、26 行、30 行、39 行。一律改为"以 `npm test` 各 workspace 实际输出为准、fail 必须为 0"，留命令不留数字。改完对 `144`、`25 过`、`8 过` 三个写死数字各查一遍都应零命中（`grep -nE "144|25 过|8 过" CONTRIBUTING.md`；只查 144 会漏掉第 30 行的 25/8）。
3. **testbench gen-env 补转义**：`testbench/server.mjs` 生成 .env 的裸值共 **9 行**——554-556（对话组）、562-564（写模型组）、573-575（嵌入组），key 含 `#` 会被 `loadEnvFile` 静默截断。对照 Host 已修好的 `q()` 转义（`apps/memoweft-host/src/server.ts:191`，整段 187-246）同样修；必填缺失从 200 带 error 改回 400（现状在 548 行）。**照抄陷阱**：响应字段名保持 testbench 自己的——成功是 `envText`、失败是 `{error}`（**别**照抄 Host 的 `env` 字段名），前端 `testbench/index.html:1337-1338` 依赖它们；前端不看状态码，改 400 不影响。
4. **importBundle 事务风险写醒目**（查证已做完，执行者不用再查）：`src/core/createCore.ts:257` 的 `core.portable.importBundle` **已传** openStores 的 transaction，core 正门无残留风险。本项只做一件事：把 `src/portable/importBundle.ts` 顶部 doc 注释里"散装调用不传 transaction、中途失败会残留半截数据"的风险写醒目（现状只藏在 146 行运行时 warning 里）。不改任何逻辑。
5. **resetSubject 清向量索引失败要可感知**——做法写死（校对核实过两条"带 warning"的路都走不通，别自行发挥）：把 `src/memory/managementApi.ts:438` 的 `void retriever.indexAll([])` 改成
   ```ts
   retriever.indexAll([]).catch((e) => console.error('resetSubject 清向量索引失败：', e));
   ```
   仅此而已。**不许**改成 async（动公共签名）；**不许**往审计日志补记录——resetSubject 刚在 429 行整表清空审计，428-429 行注释明写"出厂=无历史"是作者已拍板的决策，往里塞 warning 等于推翻它。"更好的可感知方案"记入文末发现待办。
6. **Host 侧栏静默失败提示**（随车快赢，演示质量，可选——时间紧就跳过并记待办）：`apps/memoweft-host/src/web/index.html` 里 loadSessions（1545）/openSession（1594,1598）/newSession（1609）/archiveSession（1618,1625）失败全静默。**复用既有 `memToast()`**（函数在 945 行、样式 `#memToast` 在 348-356 行，记忆管理页在大量用），如 `memToast('对话列表没拉到，稍后重试', 'danger')`。注意：聊天失败走的是"错误文案 settle 进思考气泡"（647、651 行），**不是** toast，别照那个找。

## 明确不做（防顺手）

- **不改** `identity.hostId` 默认值 `'testbench'`（`src/config.ts:89`）：改默认影响新落库证据的 host_id 语义，需作者定名并评估兼容，已列 0.4 决策。（✅ 已于 **0.4.0 T4** 兑现：默认改 `'local'`、host_id 非查询键故不迁移老数据。）
- **不改** `'./dla.db'` 默认路径、`DLA_*` 前缀回退——CONTRIBUTING.md §环境变量/配置 明文保留：DLA_* 双认前缀在 96-98 行、"./dla.db 不改"在 99 行、提交前自查清单 111 行再次点名。
- **不动** logs/、testbench 其他端点、dist/。

## 验收

- [ ] 逐项完成或记明跳过原因；三绿。
- [ ] testbench 向导用含 `#` 的假 key 生成 .env，粘贴后 `node --env-file` 读回完整值（第 3 项的实测法）。
- [ ] 本任务不记 CHANGELOG（无库行为变化；第 5 项属内部日志改善，第 3 项属 testbench 工具修复，提交说明里带一句即可）。
