# T4 · hostId 默认名改名

> 兑现 `T5-repo-cleanup.md:20` 延到 0.4 的决策。**工作量小、破坏面极小、独立**——可任意波并入。

## 背景

身份默认名定在 `config.ts:89` 一行：`identity: { subjectId: 'owner', hostId: 'testbench' }`。两层含义不同：

- `subjectId='owner'`（记忆归谁）是**真正的分区键**：每张表都有 `subject_id` 列，几乎所有读查询 `WHERE subject_id = ?` 硬过滤。**改它 = 换库主人、老数据全部失联**——本卡**不动**（B7）。
- `hostId='testbench'`（跑在哪个宿主/环境）**只是证据上的一个来源属性列 `host_id`**。**已亲验**：全仓 `host_id` 仅现于 `evidence/store.ts` 的 schema 列（:21）、类型（:41）、`toRow`（:58）、`fromRow`（:76）、INSERT 列表（:167/171/239/243）——**无任何 `WHERE host_id=`、无索引、无 GROUP BY**。它只被写进去、原样读出来。**改默认名只影响今后新落库证据的 `host_id` 取值，不参与任何查询/键。**

`'testbench'` 是历史遗留（本是测试台的名，连正式 Host `apps/memoweft-host` 落库都还带着它）。这正是要改掉它的动机。

## 作者已拍板（本卡相关）

- **A3 = hostId 新默认名 `'local'`**（中性占位，守 `naming.md`：非人格名）。
- **A4 = 不迁移**：老库带旧 `'testbench'` 照读，仅新证据用 `'local'`；同库 host_id 新旧混存可接受。
- **B7 = `subjectId` 默认 `'owner'` 不动**（分区键）。

## 改哪里

1. `src/config.ts:89`：`hostId: 'testbench'` → `hostId: 'local'`。**只改这一处字面量；`subjectId` 同行不动。**
2. `tests/configInjection.test.ts:59` 测试标题文案（提到「缺省=单例的 owner/testbench」）改为 owner/local——**不改断言**（:67 断言的是 `subjectId==='owner'`，不受影响）。
3. `testbench/config-meta.js:27` 那句「v1 恒为 testbench」只读提示改为 `'local'`（顺路，也是英文化会过的中文文案）。
4. 评估 `exportBundle.ts:71` 的独立默认 `source: { hostId: opts.hostId ?? 'memoweft' }`：作者未选「与 export 统一」，故 **`exportBundle` 的 `'memoweft'` 保持不动**（两处默认语义不同：config 默认 = 落库来源，export 默认 = 导出包来源）。若日后要统一再单独处理。
5. 勾掉 `docs/internal/tasks/0.3.0/T5-repo-cleanup.md:20` 的延期 backlog 条目（标注已在 0.4 T4 兑现）。

## 不许动

- `subjectId` 默认 `'owner'`（分区键，B7）。
- **不写数据迁移脚本**（A4 = 不迁移）；老库 `host_id='testbench'` 保留。
- `perceive.ts:19` / `ingest.ts:61` 的注入逻辑（`opts.hostId ?? cfg.identity.hostId`）不动——它们自动取到新默认。
- `DLA_*` 回退与 `./dla.db`（**反向已验**：无 `DLA_HOST_ID`/`DLA_SUBJECT_ID` env，改名碰不到 DLA_* 红线；库路径与 identity 独立）。

## 验收（可执行核对）

- [ ] 三绿：`npm run typecheck && npm test && npm run build`。
- [ ] `grep -n "testbench" src/config.ts` 无命中（默认已改 `'local'`）；`grep -rn "hostId: 'local'" src/config.ts` 命中。
- [ ] `configInjection.test.ts` 全绿（`subjectId==='owner'` 断言不受影响）。
- [ ] 新建库落一条证据，其 `host_id` = `'local'`（不显式传 hostId 时）。
- [ ] 老库（若有）仍可正常读写，其历史 `host_id='testbench'` 行照常召回。
- [ ] `T5-repo-cleanup.md:20` backlog 已勾掉。

## 发现待办

- C 探测开放项：正式 Host `apps/memoweft-host` 落库 `host_id` 仍是 `'testbench'`（已确认 host.db 11 条），说明 Host 启动没给 core 传自己的 hostId。是靠改默认名「兜底修正」，还是应让 Host 显式传真实宿主名（如 `'memoweft-host'`）——**属产品口径，不在本卡**，记此供后续（Host 侧改动应单独立项）。
- `docs/internal/STATE.md` 等内部文档可能还有几处把 `'testbench'` 当身份默认名的描述，英文化（T6 或后续）时一并核对更新。
