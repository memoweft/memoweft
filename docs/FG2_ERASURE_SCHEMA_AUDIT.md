# FG-2 遗忘文本列盘点

范围为 Python Core（记忆核心）的 schema（数据库结构）v21、运行时追加的交互承诺／关系取代表，以及可选 FTS（全文索引）。依据 `store/schema.py`、`store/interaction_commitment.py`、实际新库 `sqlite_master` / `PRAGMA table_info` 与写入路径逐表核对。下表列出所有可能保存用户原话、助手原话或其派生文本的列；未列出的列是标识、时间、固定枚举、数字或哈希，不是叙述文本。

| 表 | 原话／派生文本列 | 遗忘处理与写入依据 |
|---|---|---|
| evidence | raw_content, summary, preceding_ai_context | 来源原话和摘要清空；前置上下文按被删原话／标识／传递依赖清除；边界接收、语义消解写入 |
| event | summary | event_evidence 关联来源后整项删除；旧事件聚合路径 |
| cognition | content, scope | cognition_evidence 关联来源后整项删除；形成／纠正路径 |
| entity | canonical_name, aliases_json, kind | 来源台账指向的实体及失去全部关系／认知来源的第三方实体删除；形成／别名合并；保留所有者自身实体 |
| relationship | content, relation_type | relationship_evidence 关联来源后整项删除；形成／纠正路径 |
| world_event | content, time_expression, participants_json, objects_json | world_event_evidence 关联来源后整项删除；事件形成路径 |
| interaction_context | context_json | 遗忘清除来源及相关助手回复和传递依赖；删除会话清空该会话上下文；DSH（助手运行时）交互写入 |
| interaction_commitment | content, raw_quote | **本包新增**：来源所在片段／任务或传递依赖的承诺整行删除；删除会话按 subject_id + conversation_id 删除，含上下文已不存在的旧行；所有 kind / status 均覆盖 |
| semantic_resolution | resolved_content, required_context, response_act, prompt_act, proposition_origin, assertion_strength | 来源对应整行删除；语义消解写入，后四列通常为解释标签，但按潜在文本列计入 |
| evidence_ledger | content, payload_json | 来源及被删对象／依赖关联行删除；形成与审查台账 |
| proposals | payload_json, review_payload_json | 引用被删对象的提议及其回执删除；审查路径 |
| management_log | reason, detail | 被删 target_id 对应行删除；管理／纠正路径 |
| cognition_transitions | reason | 被删前项或替代项对应行删除；正式认知取代链 |
| relationship_transitions | reason | 被删前项或替代项对应行删除；正式关系取代链 |
| retraction | reason | 被删认知／关系／事件对应行删除；撤回路径 |
| memory_state | snapshot_json | 修订推进时从存活正式项重建，不保留旧文本快照；revision.py |
| identity_state | state_json | 来源删除后清除派生身份缓存；identity_store.py |
| memory_world_job | formal_target_json, model_result_json, world_result_json, delivery_receipt_json, terminal_detail, last_error_type, model_task, model_provider, model_name, model_usage_json | 来源批次任务整行删除，在写锁内阻止旧工作者重新落库；后五列主要为运行元数据，也计入可能文本列 |
| terminal_outcome | terminal_detail, world_result_json, last_error | 来源任务对应行删除；终态投递路径 |
| clarification | question, target_hint | 被删 answer_evidence_id / source_job_id / follow_up_job_id 对应行删除；澄清生命周期 |
| trust_command | payload_json | 指向被删项或负载引用被删标识的命令负载改为 `{}`；保留无内容回执身份 |
| portable_import_receipt | result_json | 写入内容为导入回执（计数、标识映射、修订、哈希、结果），不含来源／对象正文；portable_service.py 的 apply 回执构造已核对 |
| cognition_fts（可选） | 索引正文 | 按被删 cognition_id 删除；非版本化索引也清理 |

其余表逐表核对，只有标识／固定枚举／时间／哈希：`boundary_evidence_content`、`cognition_evidence`、`cognition_target`、`event_evidence`、`evidence_origin_history`、`evidence_retraction`、`hard_deleted_origin`、`observed_source`、`proposal_decision_receipts`、`relationship_evidence`、`trust_command_receipt`、`trust_command_rejection`、`trust_delete_storage_status`、`world_delete_marker`、`world_event_evidence`、`world_item_lifecycle`。其中来源 origin_id 原值随真正删除清除，仅留内容无关的防重放哈希与标识；Observed 生命周期行保留版本与哈希，不保存原文。

交互承诺由 `dsh_bridge/__init__.py::_Ingestor.record_interaction` 同时写入交互片段，`commitments.py::record_commitments_for_episode` 从助手消息确定性提取建议、承诺、约定，`interaction_commitment.py::record` 保存短正文及完整匹配原话。它没有多来源拆分字段。因此来源片段混合多条依据时删除整个相关派生行；不同片段、不同会话、不同账户的无关承诺保留。任务仍存在时 `boundary_event_id` 可恢复承诺片段关联；旧任务已清掉时保留的交互片段继续提供关联；会话删除不依赖这两者。

预览在只读一致快照的内存副本上调用同一删除算法，包含 `object_kind=interaction_commitment`、kind 对应的 item_type、名称与总数；不改变原库、回执或修订。会话仅剩旧承诺时删除仍推进修订并返回 applied（已应用），重复删除返回 no_change（未变化）。沿用 FG-1 的 secure_delete（安全删除）、VACUUM（数据库整理）与 WAL（预写日志）截断，不降低失败／待清理状态。

验证对每张实际表每一列全文检查，并检查 JSON（结构化文本）解码后的文本及库／WAL 实际字节；不以无法召回代替不可恢复。WeftMate 第 7 步新备份扫描也逐表逐列，无固定表名清单。旧备份的保留策略属于既有 FG-1 范围，本包检查遗忘后新生成的备份。
