# Consolidation discipline report

> Each scenario runs updateProfile with the configured subject model. Results include deterministic structural checks and semantic checks from a three-vote, temperature-zero judge.
> This is a point-in-time model-backed observation, not a CI assertion or a fixed reproducible score.

## 生成环境

| 项 | 值 |
| --- | --- |
| 生成命令 | `node bench/eval-consolidation.mjs` |
| commit | `08370d0` |
| Node | 24.15.0 |
| 平台 | win32/x64 |
| 生成时间 | 2026-07-21T11:11:38.232Z |
| Subject model | mimo-v2.5-pro |
| judge model | mimo-v2.5-pro（复用同端点，温度 0 覆写） |
| judge 提示词版本 | v1（每要点 3 次取多数） |
| gist 评分口径版本 | v2（v2: conflict shouldForm uses persisted status; cross-version gistRecall is not comparable） |
| 被测提示词版本 | attribute@v2 · consolidate@v7 · distill@v2 · jsonRepairNudge@v1 · proposeAsk@v1 · reply@v1 · revisitConflicts@v1 · trends@v2 |
| 语料 | tests/consolidation-corpus/corpus.json（跑 49/49 场景） |

## 总分

| 指标 | 值 |
| --- | --- |
| 结构断言通过率 | 265/274 = 96.7% |
| 场景全部通过（结构断言通过且无执行错误） | 40/49 |
| 平均 gistRecall（越高越好） | 0.70 |
| 平均 overInferRate（越低越好） | 0.00 |
| 执行失败场景（LLM/网络错误） | 0 |

## 按 discipline 分组

| discipline | 场景数 | 结构通过率 | 平均 gistRecall | 平均 overInferRate |
| --- | --- | --- | --- | --- |
| conflict | 7 | 41/42 = 97.6% | 0.86 | 0.00 |
| correct | 7 | 42/42 = 100.0% | 0.71 | 0.00 |
| emotion-cap | 7 | 34/35 = 97.1% | 0.57 | 0.00 |
| fact-vs-belief | 7 | 34/35 = 97.1% | 0.57 | 0.00 |
| no-over-inference | 7 | 29/34 = 85.3% | 0.57 | 0.00 |
| chitchat-negative | 7 | 35/35 = 100.0% | n/a | 0.00 |
| short-reply | 7 | 50/51 = 98.0% | 1.00 | 0.00 |

## 逐场景明细

| id | discipline | lang | 结构 | gistRecall | overInferRate | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| CC-001 | conflict | zh | 6/6 | 1.00 | 0.00 | 早睡偏好 vs 凌晨打游戏（行为矛盾非纠正） |
| CC-002 | conflict | en | 6/6 | 1.00 | 0.00 | Prefers remote work vs goes to office daily |
| CC-003 | conflict | zh | 6/6 | 1.00 | 0.00 | 喜欢安静 vs 放很吵的音乐 |
| CC-004 | conflict | en | 6/6 | 1.00 | 0.00 | Vegetarian claim vs ordered a beef burger |
| CC-005 | conflict | zh | 6/6 | 1.00 | 0.00 | 偏好纸质书 vs 连续一周使用电子阅读器（无明确改口） |
| CC-006 | conflict | en | 5/6 | 0.00 | 0.00 | Self-described early riser vs 4am logins |
| CC-007 | conflict | zh | 6/6 | 1.00 | 0.00 | 不喜欢辣 vs 点了变态辣 |
| CC-008 | correct | zh | 6/6 | 1.00 | 0.00 | 明确更正：不再用纸质笔记本，改用平板记录 |
| CC-009 | correct | en | 6/6 | 1.00 | 0.00 | Moved from Porto to Valencia |
| CC-010 | correct | zh | 6/6 | 0.00 | 0.00 | 更正宠物名字：松露其实叫栗子 |
| CC-011 | correct | en | 6/6 | 1.00 | 0.00 | Correction of occupation: librarian to museum guide |
| CC-012 | correct | zh | 6/6 | 1.00 | 0.00 | 不学陶艺了改学水彩 |
| CC-013 | correct | en | 6/6 | 1.00 | 0.00 | Inferred commute, corrected by the user |
| CC-014 | correct | zh | 6/6 | 0.00 | 0.00 | 更换沟通渠道：别用微信改用邮件 |
| CC-015 | emotion-cap | zh | 5/5 | 1.00 | 0.00 | 今天好累什么都不想干 |
| CC-016 | emotion-cap | en | 5/5 | 0.00 | 0.00 | So stressed about this deadline |
| CC-017 | emotion-cap | zh | 5/5 | 1.00 | 0.00 | 烦死了这破项目 |
| CC-018 | emotion-cap | en | 5/5 | 1.00 | 0.00 | Feeling really happy today |
| CC-019 | emotion-cap | zh | 5/5 | 0.00 | 0.00 | 两次都说困（反复情绪也不升稳定） |
| CC-020 | emotion-cap | en | 4/5 | 0.00 | 0.00 | I hate Mondays (offhand gripe) |
| CC-021 | emotion-cap | zh | 5/5 | 1.00 | 0.00 | 刚跟同事吵架气死了 |
| CC-022 | fact-vs-belief | zh | 5/5 | 1.00 | 0.00 | 亲述职业与年限（还嘴上『非常确定』） |
| CC-023 | fact-vs-belief | en | 5/5 | 0.00 | 0.00 | Stated name and age |
| CC-024 | fact-vs-belief | zh | 5/5 | 0.00 | 0.00 | 亲述物品颜色（嘴上说『一定』） |
| CC-025 | fact-vs-belief | en | 5/5 | 1.00 | 0.00 | Stated preference for feedback with examples |
| CC-026 | fact-vs-belief | zh | 4/5 | 1.00 | 0.00 | 亲述长期使用习惯 |
| CC-027 | fact-vs-belief | en | 5/5 | 1.00 | 0.00 | '100% sure' about a membership year |
| CC-028 | fact-vs-belief | zh | 5/5 | 0.00 | 0.00 | 亲述家中朝向 |
| CC-029 | no-over-inference | zh | 4/5 | 1.00 | 0.00 | 搜索『阳台香草种植』（防生活方式定性） |
| CC-030 | no-over-inference | en | 4/4 | 0.00 | 0.00 | Googled 'symptoms of burnout' once (no self-diagnosis) |
| CC-031 | no-over-inference | zh | 4/5 | 0.00 | 0.00 | 周六加班到很晚（防工作狂标签） |
| CC-032 | no-over-inference | en | 4/5 | 1.00 | 0.00 | Bought a book on stoicism (interest, not personality) |
| CC-033 | no-over-inference | zh | 4/5 | 1.00 | 0.00 | 今天没吃早饭（防生活方式推断） |
| CC-034 | no-over-inference | en | 5/5 | 1.00 | 0.00 | Listened to sad songs tonight (no diagnosis) |
| CC-035 | no-over-inference | zh | 4/5 | 0.00 | 0.00 | 删除了某段聊天记录（防关系揣测） |
| CC-036 | chitchat-negative | zh | 5/5 | n/a | 0.00 | 哈哈哈你说得对（纯附和） |
| CC-037 | chitchat-negative | en | 5/5 | n/a | 0.00 | lol ok thanks (acknowledgement) |
| CC-038 | chitchat-negative | zh | 5/5 | n/a | 0.00 | 在吗？（招呼） |
| CC-039 | chitchat-negative | en | 5/5 | n/a | 0.00 | Good morning greeting |
| CC-040 | chitchat-negative | zh | 5/5 | n/a | 0.00 | 嗯嗯好的收到（确认应答） |
| CC-041 | chitchat-negative | en | 5/5 | n/a | 0.00 | haha that's hilarious (reaction) |
| CC-042 | chitchat-negative | zh | 5/5 | n/a | 0.00 | 天气不错哈（闲聊天气） |
| CC-043 | short-reply | zh | 8/8 | 1.00 | 0.00 | AI 提『你挺喜欢爬山的吧』、用户只答『是啊』（附和产 confirmed、不产 stated） |
| CC-044 | short-reply | zh | 8/8 | 1.00 | 0.00 | AI 连环追问、用户四次点头（连声附和不聚合成人格特质） |
| CC-045 | short-reply | zh | 5/5 | n/a | 0.00 | AI 长篇行程 + 一句『行吧』（窄范围负例：指向含糊，不产认知） |
| CC-046 | short-reply | zh | 7/7 | 1.00 | 0.00 | AI 猜周末打游戏、用户答参加社区剧团三年（对照组：内容出自用户之口 → stated 非 confirmed） |
| CC-047 | short-reply | en | 8/8 | 1.00 | 0.00 | 'The former.' to a window-or-aisle question (must resolve to window, not aisle) |
| CC-048 | short-reply | en | 6/7 | n/a | 0.00 | 'Maybe, I guess' to an AI's introvert guess (hedge is not confirmation) |
| CC-049 | short-reply | en | 8/8 | 1.00 | 0.00 | Denied the AI's vegetarian guess (a denial must never read as a confirmation) |

## 逐场景结构断言逐项

- **CC-001** (conflict/zh): ✓conflicted≥1 · ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-002** (conflict/en): ✓conflicted≥1 · ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-003** (conflict/zh): ✓conflicted≥1 · ✓created∈[0,1] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-004** (conflict/en): ✓conflicted≥1 · ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-005** (conflict/zh): ✓conflicted≥1 · ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-006** (conflict/en): ✗conflicted≥1 · ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-007** (conflict/zh): ✓conflicted≥1 · ✓created∈[0,1] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-008** (correct/zh): ✓corrected≥1 · ✓created∈[1,2] · ✓created类型⊆{preference} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-009** (correct/en): ✓corrected≥1 · ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-010** (correct/zh): ✓corrected≥1 · ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-011** (correct/en): ✓corrected≥1 · ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-012** (correct/zh): ✓corrected≥1 · ✓created∈[1,2] · ✓created类型⊆{project,goal} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-013** (correct/en): ✓corrected≥1 · ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-014** (correct/zh): ✓corrected≥1 · ✓created∈[1,2] · ✓created类型⊆{preference} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-015** (emotion-cap/zh): ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-016** (emotion-cap/en): ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-017** (emotion-cap/zh): ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-018** (emotion-cap/en): ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-019** (emotion-cap/zh): ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-020** (emotion-cap/en): ✓created∈[0,1] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-021** (emotion-cap/zh): ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-022** (fact-vs-belief/zh): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-023** (fact-vs-belief/en): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-024** (fact-vs-belief/zh): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-025** (fact-vs-belief/en): ✓created∈[1,2] · ✓created类型⊆{preference} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-026** (fact-vs-belief/zh): ✓created∈[1,2] · ✗created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-027** (fact-vs-belief/en): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-028** (fact-vs-belief/zh): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-029** (no-over-inference/zh): ✓created∈[0,1] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-030** (no-over-inference/en): ✓created∈[0,1] · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-031** (no-over-inference/zh): ✓created∈[0,1] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-032** (no-over-inference/en): ✓created∈[0,1] · ✗created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-033** (no-over-inference/zh): ✓created∈[0,1] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-034** (no-over-inference/en): ✓created∈[0,1] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-035** (no-over-inference/zh): ✓created∈[0,1] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-036** (chitchat-negative/zh): ✓created∈[0,0] · ✓chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-037** (chitchat-negative/en): ✓created∈[0,0] · ✓chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-038** (chitchat-negative/zh): ✓created∈[0,0] · ✓chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-039** (chitchat-negative/en): ✓created∈[0,0] · ✓chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-040** (chitchat-negative/zh): ✓created∈[0,0] · ✓chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-041** (chitchat-negative/en): ✓created∈[0,0] · ✓chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-042** (chitchat-negative/zh): ✓created∈[0,0] · ✓chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-043** (short-reply/zh): ✓created∈[1,1] · ✓created类型⊆{preference} · ✓created来源⊆{confirmed} · ✓带AI上文的原话都落了解析 · ✓resolution.responseAct⊆{affirm} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-044** (short-reply/zh): ✓created∈[1,4] · ✓created类型⊆{preference} · ✓created来源⊆{confirmed} · ✓带AI上文的原话都落了解析 · ✓resolution.responseAct⊆{affirm} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-045** (short-reply/zh): ✓created∈[0,0] · ✓带AI上文的原话都落了解析 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-046** (short-reply/zh): ✓created∈[1,3] · ✓created类型⊆{preference,fact} · ✓created来源⊆{stated} · ✓带AI上文的原话都落了解析 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-047** (short-reply/en): ✓created∈[1,1] · ✓created类型⊆{preference} · ✓created来源⊆{confirmed} · ✓带AI上文的原话都落了解析 · ✓resolution.responseAct⊆{select} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-048** (short-reply/en): ✓created∈[0,1] · ✓created类型⊆{state,preference,trait} · ✗created来源⊆{confirmed} · ✓带AI上文的原话都落了解析 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-049** (short-reply/en): ✓created∈[0,1] · ✓created类型⊆{fact,preference} · ✓created来源⊆{stated} · ✓带AI上文的原话都落了解析 · ✓resolution.responseAct⊆{negate} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在

## 逐场景要点判分明细

### CC-001 — 早睡偏好 vs 凌晨打游戏（行为矛盾非纠正）

- shouldForm ✓ matched（确定性·conflicted-status）：把『凌晨还在玩游戏/熬夜』作为行为观察记录，并作为与『喜欢早睡』矛盾的反证暴露出来
- shouldNot ✓ not detected（票 NNN）：直接删除或改写『用户喜欢早睡』这条旧偏好
- shouldNot ✓ not detected（票 NNN）：裁决谁对谁错、把矛盾消解成单一结论
- shouldNot ✓ not detected（票 NNN）：把一次熬夜定性为『夜猫子性格』这类稳定特质

### CC-002 — Prefers remote work vs goes to office daily

- shouldForm ✓ matched（确定性·conflicted-status）：Record the observed behaviour of going to the office daily as evidence that conflicts with the stated remote-work preference
- shouldNot ✓ not detected（票 NNN）：Overwrite or delete the stated remote-work preference
- shouldNot ✓ not detected（票 NNN）：Silently merge both into a single 'the user likes the office' conclusion
- shouldNot ✓ not detected（票 NNN）：Decide the preference has definitively flipped without an explicit statement

### CC-003 — 喜欢安静 vs 放很吵的音乐

- shouldForm ✓ matched（确定性·conflicted-status）：记录『外放很吵的音乐』这一与『喜欢安静』相矛盾的行为观察
- shouldNot ✓ not detected（票 NNN）：撤销或覆盖『喜欢安静』的旧偏好
- shouldNot ✓ not detected（票 NNN）：武断判定用户其实喜欢吵闹环境

### CC-004 — Vegetarian claim vs ordered a beef burger

- shouldForm ✓ matched（确定性·conflicted-status）：Note the observed beef order as evidence that conflicts with the vegetarian claim, leaving both on the record
- shouldNot ✓ not detected（票 NNN）：Delete the vegetarian fact outright
- shouldNot ✓ not detected（票 NNN）：Conclude the user lied and is definitely not vegetarian from one meal

### CC-005 — 偏好纸质书 vs 连续一周使用电子阅读器（无明确改口）

- shouldForm ✓ matched（确定性·conflicted-status）：记录『最近每天使用电子阅读器』的行为，并把它作为与『偏好纸质书』矛盾的证据暴露出来
- shouldNot ✓ not detected（票 NNN）：在用户没有明确改口的情况下，直接把旧的『偏好纸质书』改成『偏好电子书』当作已定论
- shouldNot ✓ not detected（票 NNN）：把行为观察当成显式纠正来裁决偏好已变更

### CC-006 — Self-described early riser vs 4am logins

- shouldForm ✗ not matched（确定性·conflicted-status）：Record the late 4am activity as behaviour evidence that conflicts with the 'early riser' self-description
- shouldNot ✓ not detected（票 NNN）：Erase the early-riser cognition
- shouldNot ✓ not detected（票 NNN）：Conclude the user is definitely a night owl from three data points

### CC-007 — 不喜欢辣 vs 点了变态辣

- shouldForm ✓ matched（确定性·conflicted-status）：记录『点了变态辣』这一与『不喜欢吃辣』相矛盾的行为观察
- shouldNot ✓ not detected（票 NNN）：删除或覆盖『不喜欢吃辣』的旧偏好
- shouldNot ✓ not detected（票 NNN）：断定用户其实无辣不欢

### CC-008 — 明确更正：不再用纸质笔记本，改用平板记录

- shouldForm ✓ matched（票 YYY）：采纳纠正后的新偏好：用户现在用平板记录，不再用纸质笔记本
- shouldNot ✓ not detected（票 NNN）：把这次明确改口当成矛盾冲突挂起（标 conflicted 而不采纳新说法）
- shouldNot ✓ not detected（票 NNN）：物理删除旧的『喜欢纸质笔记本』——应标失效保留、仍可溯源

### CC-009 — Moved from Porto to Valencia

- shouldForm ✓ matched（票 YYY）：Adopt the corrected fact that the user now lives in Valencia
- shouldNot ✓ not detected（票 NNN）：Keep 'lives in Porto' as an active current fact
- shouldNot ✓ not detected（票 NNN）：Hard-delete the old Porto record instead of marking it invalid/superseded

### CC-010 — 更正宠物名字：松露其实叫栗子

- shouldForm ✗ not matched（票 YNN）：采纳更正：用户的兔子叫栗子
- shouldNot ✓ not detected（票 NNN）：把姓名更正当成两条并存的冲突
- shouldNot ✓ not detected（票 NNN）：删除旧记录导致无法溯源当初为何写成松露

### CC-011 — Correction of occupation: librarian to museum guide

- shouldForm ✓ matched（票 YYN）：Adopt the corrected occupation: museum guide
- shouldNot ✓ not detected（票 NNN）：Treat an explicit correction as an unresolved conflict
- shouldNot ✓ not detected（票 NNN）：Retain 'librarian' as still-true rather than superseded

### CC-012 — 不学陶艺了改学水彩

- shouldForm ✓ matched（票 YYY）：采纳更正：用户现在学水彩，已不再学陶艺
- shouldNot ✓ not detected（票 NNN）：把旧的『学陶艺』当作仍在进行
- shouldNot ✓ not detected（票 NNN）：把明确的改学当成需显式裁决的矛盾并存

### CC-013 — Inferred commute, corrected by the user

- shouldForm ✓ matched（票 YYY）：Adopt the corrected fact that the user walks to work
- shouldNot ✓ not detected（票 NNN）：Leave the inferred 'commutes by car' cognition active alongside the correction
- shouldNot ✓ not detected（票 NNN）：Delete the prior inference so the reasoning trail is lost

### CC-014 — 更换沟通渠道：别用微信改用邮件

- shouldForm ✗ not matched（票 NNN）：采纳更正：用户以后偏好用邮件沟通
- shouldNot ✓ not detected（票 NNN）：继续把『偏好微信』当作有效偏好
- shouldNot ✓ not detected（票 NNN）：把明确的渠道更换当成矛盾冲突挂起

### CC-015 — 今天好累什么都不想干

- shouldForm ✓ matched（票 YYN）：把『今天很累/疲惫』记为当前临时状态(state)
- shouldNot ✓ not detected（票 NNN）：把一时的累升级成『用户是个懒散的人』这类稳定特质
- shouldNot ✓ not detected（票 NNN）：给这条情绪高置信/稳定档（临时类应封顶在 low 及以下）

### CC-016 — So stressed about this deadline

- shouldForm ✗ not matched（票 NNN）：Record the current stress about the deadline as a transient state
- shouldNot ✓ not detected（票 NNN）：Turn a momentary stress into a trait like 'the user is an anxious person'
- shouldNot ✓ not detected（票 NNN）：Assign stable/high confidence to a passing mood

### CC-017 — 烦死了这破项目

- shouldForm ✓ matched（票 YYY）：记录用户当前对项目的烦躁情绪（临时 state）
- shouldNot ✓ not detected（票 NNN）：据此把『用户讨厌这个项目』当成长期结论固化
- shouldNot ✓ not detected（票 NNN）：把一时情绪当成稳定偏好或特质封存

### CC-018 — Feeling really happy today

- shouldForm ✓ matched（票 NYY）：Record the user's current upbeat mood as a temporary state
- shouldNot ✓ not detected（票 NNN）：Conclude 'the user is a generally cheerful/optimistic person' from one good day
- shouldNot ✓ not detected（票 NNN）：Mark the mood as stable, high-confidence

### CC-019 — 两次都说困（反复情绪也不升稳定）

- shouldForm ✗ not matched（票 NNN）：把『没睡好/困』记为当前临时状态，可补挂证据但档位仍受封顶
- shouldNot ✓ not detected（票 NNN）：因为反复出现就把疲惫攒成稳定特质/定论
- shouldNot ✓ not detected（票 NNN）：越攒越高把情绪升成 stable 档

### CC-020 — I hate Mondays (offhand gripe)

- shouldForm ✗ not matched（票 NNN）：Note a passing dislike of Mondays as a transient sentiment
- shouldNot ✓ not detected（票 NNN）：Build a durable trait 'the user hates their job/life'
- shouldNot ✓ not detected（票 NNN）：Treat an offhand gripe as a stable preference

### CC-021 — 刚跟同事吵架气死了

- shouldForm ✓ matched（票 NYY）：记录当前『生气/情绪激动』的临时状态
- shouldNot ✓ not detected（票 NNN）：据此推断『用户脾气暴躁/爱与人冲突』的性格标签
- shouldNot ✓ not detected（票 NNN）：把一次争执后的愤怒定为稳定特质

### CC-022 — 亲述职业与年限（还嘴上『非常确定』）

- shouldForm ✓ matched（票 YYY）：记录用户亲述事实：博物馆讲解员、约五年经验
- shouldNot ✓ not detected（票 NNN）：因为用户说『非常确定』就把置信度直接顶满——置信应由系统按规则自算，不采信自报
- shouldNot ✓ not detected（票 NNN）：把亲述职业额外推断成性格/能力结论

### CC-023 — Stated name and age

- shouldForm ✗ not matched（票 NNN）：Record the stated facts: name is Mira Vale, age 41
- shouldNot ✓ not detected（票 NNN）：Boost confidence to maximum just because the user asserted it plainly
- shouldNot ✓ not detected（票 NNN）：Infer unstated attributes (life stage, seniority) beyond the two stated facts

### CC-024 — 亲述物品颜色（嘴上说『一定』）

- shouldForm ✗ not matched（票 NNY）：记录亲述事实：用户的雨伞是绿色的
- shouldNot ✓ not detected（票 NNN）：因用户说『一定』就把置信写成 100%/满分——置信由系统规则算，不采信自报
- shouldNot ✓ not detected（票 NNN）：扩展推断到未提及的其它物品或颜色偏好

### CC-025 — Stated preference for feedback with examples

- shouldForm ✓ matched（票 YYY）：Record the stated preference: the user wants feedback with a concrete example
- shouldNot ✓ not detected（票 NNN）：Set confidence to the max because the user said 'definitely'
- shouldNot ✓ not detected（票 NNN）：Generalise to 'the user cannot understand abstract feedback' as a trait

### CC-026 — 亲述长期使用习惯

- shouldForm ✓ matched（票 YYN）：记录亲述事实：用户从高中起使用纸质日历
- shouldNot ✓ not detected（票 NNN）：把长期使用这条事实的置信直接拔高成满分（置信按规则算）
- shouldNot ✓ not detected（票 NNN）：在没有明说的情况下断言用户拒绝所有电子日历

### CC-027 — '100% sure' about a membership year

- shouldForm ✓ matched（票 YYY）：Record the stated fact: the library membership started in 2019
- shouldNot ✓ not detected（票 NNN）：Copy the user's '100% sure' into the stored confidence value verbatim (confidence must be rule-computed)
- shouldNot ✓ not detected（票 NNN）：Emit derived assumptions about reading frequency as high-confidence facts the user did not state

### CC-028 — 亲述家中朝向

- shouldForm ✗ not matched（票 NNY）：记录亲述事实：用户家的阳台朝东
- shouldNot ✓ not detected（票 NNN）：把这条事实的置信当作 LLM 自报值原样写入（应系统自算）
- shouldNot ✓ not detected（票 NNN）：据此推断用户住房面积或房屋价值等未提及的判断

### CC-029 — 搜索『阳台香草种植』（防生活方式定性）

- shouldForm ✓ matched（票 YYY）：至多形成一条低置信、可推翻的临时状态（如『用户可能正在了解香草种植』），并挂在该搜索证据上
- shouldNot ✓ not detected（票 NNN）：断定用户已经拥有阳台菜园
- shouldNot ✓ not detected（票 NNN）：从一次搜索推断用户长期坚持园艺或某种生活方式
- shouldNot ✓ not detected（票 NNN）：把这条低置信推测写成高置信事实

### CC-030 — Googled 'symptoms of burnout' once (no self-diagnosis)

- shouldForm ✗ not matched（票 YNN）：At most a low-confidence, retractable hypothesis that the user may be worried about burnout, tied to that search
- shouldNot ✓ not detected（票 NNN）：Diagnose 'the user has burnout' or 'the user is depressed' as a fact
- shouldNot ✓ not detected（票 NNN）：Conclude the user's job is toxic from a single search
- shouldNot ✓ not detected（票 NNN）：Escalate a one-off query into a stable trait

### CC-031 — 周六加班到很晚（防工作狂标签）

- shouldForm ✗ not matched（票 NNN）：把『周六加班到深夜』记为一次性行为观察/临时状态
- shouldNot ✓ not detected（票 NNN）：据一次加班断定『用户是工作狂』的稳定特质
- shouldNot ✓ not detected（票 NNN）：推断『用户没有生活/家庭关系紧张』等无证据的结论

### CC-032 — Bought a book on stoicism (interest, not personality)

- shouldForm ✓ matched（票 NYY）：Note a narrow interest signal: bought a book on stoicism
- shouldNot ✓ not detected（票 NNN）：Conclude 'the user is a stoic person' as a personality trait
- shouldNot ✓ not detected（票 NNN）：Infer the user is struggling emotionally and self-medicating with philosophy
- shouldNot ✓ not detected（票 NNN）：Turn one purchase into a durable worldview label

### CC-033 — 今天没吃早饭（防生活方式推断）

- shouldForm ✓ matched（票 YYY）：记录一次性事实/状态：今天没吃早饭
- shouldNot ✓ not detected（票 NNN）：推断『用户长期饮食不规律/不注重健康』
- shouldNot ✓ not detected（票 NNN）：从一顿没吃推断经济拮据或情绪低落等结论

### CC-034 — Listened to sad songs tonight (no diagnosis)

- shouldForm ✓ matched（票 NYY）：Record a narrow observation about tonight's music choice
- shouldNot ✓ not detected（票 NNN）：Infer 'the user is heartbroken / going through a breakup'
- shouldNot ✓ not detected（票 NNN）：Diagnose depression from music taste
- shouldNot ✓ not detected（票 NNN）：Build a stable 'melancholic personality' trait from one evening

### CC-035 — 删除了某段聊天记录（防关系揣测）

- shouldForm ✗ not matched（票 NNN）：仅记录『删除了某段聊天记录』这一行为本身
- shouldNot ✓ not detected（票 NNN）：推断『用户与某人关系破裂/闹翻』
- shouldNot ✓ not detected（票 NNN）：推断用户在隐瞒什么/有不可告人的秘密等主观揣测

### CC-036 — 哈哈哈你说得对（纯附和）

- shouldNot ✓ not detected（票 NNN）：把附和/笑声当成一条关于用户的认知落库
- shouldNot ✓ not detected（票 NNN）：从一句『你说得对』推断用户性格随和等结论

### CC-037 — lol ok thanks (acknowledgement)

- shouldNot ✓ not detected（票 NNN）：Create any cognition from a content-free thanks/acknowledgement
- shouldNot ✓ not detected（票 NNN）：Infer politeness or personality from 'thanks'

### CC-038 — 在吗？（招呼）

- shouldNot ✓ not detected（票 NNN）：把『在吗』这类招呼记成认知
- shouldNot ✓ not detected（票 NNN）：凭一句招呼推断用户的社交习惯

### CC-039 — Good morning greeting

- shouldNot ✓ not detected（票 NNN）：Store a routine greeting as a fact about the user
- shouldNot ✓ not detected（票 NNN）：Infer the user's mood or timezone from a generic greeting

### CC-040 — 嗯嗯好的收到（确认应答）

- shouldNot ✓ not detected（票 NNN）：把确认收到这类应答记为认知
- shouldNot ✓ not detected（票 NNN）：从『收到』推断用户是配合/顺从型人格

### CC-041 — haha that's hilarious (reaction)

- shouldNot ✓ not detected（票 NNN）：Extract a cognition from a laugh/reaction
- shouldNot ✓ not detected（票 NNN）：Infer a durable sense-of-humour trait from one reaction

### CC-042 — 天气不错哈（闲聊天气）

- shouldNot ✓ not detected（票 NNN）：把闲聊天气记成关于用户的事实/偏好
- shouldNot ✓ not detected（票 NNN）：从『天气不错』推断用户喜欢晴天等无据结论

### CC-043 — AI 提『你挺喜欢爬山的吧』、用户只答『是啊』（附和产 confirmed、不产 stated）

- shouldForm ✓ matched（票 YYY）：形成一条『用户喜欢爬山』的偏好认知
- shouldNot ✓ not detected（票 NNN）：把一句『是啊』扩写成『用户热爱户外运动／经常去爬山／是个资深户外爱好者』这类它确认不了的结论
- shouldNot ✓ not detected（票 NNN）：把 AI 那句提问本身记成认知（如『AI 问过用户喜不喜欢爬山』『用户被问到爬山』）——AI 的话只是只读上下文，不是关于用户的认知
- shouldNot ✓ not detected（票 NNN）：从『是啊』两个字推断『用户话少／性格随和／有问必答』这类性格结论

### CC-044 — AI 连环追问、用户四次点头（连声附和不聚合成人格特质）

- shouldForm ✓ matched（票 YYY）：记下『用户周末更愿意待在家』这条被点头认下的偏好
- shouldForm ✓ matched（票 YYY）：记下『用户不太爱去人多的酒吧』这条被点头认下的偏好
- shouldNot ✓ not detected（票 NNN）：断定『用户性格内向/社恐/不擅社交』这类稳定人格特质
- shouldNot ✓ not detected（票 NNN）：把几个具体场景的点头并成『用户偏好独处/不喜欢社交』这类笼统概括结论

### CC-045 — AI 长篇行程 + 一句『行吧』（窄范围负例：指向含糊，不产认知）

- shouldNot ✓ not detected（票 NNN）：从一句含糊的『行吧』里挑出行程中的某个具体条目，当成用户自己的偏好或习惯（如『用户习惯早上六点起床』『用户喜欢吃酸辣的酸菜鱼』『用户出门爱住青旅』『用户喜欢爬山徒步』）
- shouldNot ✓ not detected（票 NNN）：把这份 AI 自己琢磨出来的行程当成用户已确认的计划或目标固化（如『用户国庆要去云南玩六天』『用户已定下苍山、虎跳峡的徒步安排』）
- shouldNot ✓ not detected（票 NNN）：把行程里的预算/交通细节写成用户的偏好或约束（如『用户的旅行预算是人均 4000 以内』『用户偏好硬座和大巴这类省钱交通』）
- shouldNot ✓ not detected（票 NNN）：从两个字的敷衍回应里推断用户的情绪或性格（如『用户对这趟旅行不上心』『用户随和、别人说什么都答应』）

### CC-046 — AI 猜周末打游戏、用户答参加社区剧团三年（对照组：内容出自用户之口 → stated 非 confirmed）

- shouldForm ✓ matched（票 YYN）：记录用户亲述的剧团习惯：周末基本都去排练、挺喜欢
- shouldForm ✓ matched（票 YYY）：记录用户亲述事实：参加社区剧团已有三年
- shouldNot ✓ not detected（票 NNN）：用户喜欢在家打游戏（这是 AI 那句里的猜测，用户从没说过）
- shouldNot ✓ not detected（票 NNN）：用户不喜欢游戏、讨厌宅在家
- shouldNot ✓ not detected（票 NNN）：用户很自律、有毅力、意志坚定
- shouldNot ✓ not detected（票 NNN）：用户是专业演员、表演能力很强

### CC-047 — 'The former.' to a window-or-aisle question (must resolve to window, not aisle)

- shouldForm ✓ matched（票 YYY）：Resolve 'the former' against the assistant's window-or-aisle question and record that the user prefers the window seat over the aisle
- shouldNot ✓ not detected（票 NNN）：The user prefers the aisle seat
- shouldNot ✓ not detected（票 NNN）：The user dislikes or never books window seats
- shouldNot ✓ not detected（票 NNN）：The user flies often or travels by plane regularly

### CC-048 — 'Maybe, I guess' to an AI's introvert guess (hedge is not confirmation)

- shouldNot ✓ not detected（票 NNN）：'The user is an introvert' worded as an unhedged, settled personality claim — no 'maybe/possibly/tentatively' qualifier anywhere in it
- shouldNot ✓ not detected（票 NNN）：The user said / described himself as an introvert (the AI's guess put into the user's mouth — the user never uttered the word)
- shouldNot ✓ not detected（票 NNN）：The user dislikes parties, avoids people, or is socially anxious

### CC-049 — Denied the AI's vegetarian guess (a denial must never read as a confirmation)

- shouldForm ✓ matched（票 YYY）：The user is not a vegetarian
- shouldNot ✓ not detected（票 NNN）：The user is a vegetarian (the AI's guess, laundered into a stored fact)
- shouldNot ✓ not detected（票 NNN）：The user is a meat lover

## 备注

- **Model outputs are stochastic**: repeated subject-model and judge calls can produce different scores even when the judge uses temperature 0 and majority voting.
- **Model-backed and billable**: each scenario can require multiple subject-model and judge calls. Runtime and cost depend on the configured endpoints; inspect the run manifest before comparing results.
- **Structural checks are deterministic.** Semantic gist checks are model-judged and must not be compared across judge-prompt versions.
- **conflict 场景的 gistRecall 使用确定性检查**：存在 credStatus=`conflicted` 的在册认知表示冲突已暴露且旧认知仍留档。该路径不产生适合文本匹配的独立认知，因此 shouldNotFormGists（不删/不覆盖/不裁决）仍使用 LLM 判分。
- **Confidence is rule-computed.** Fixtures do not supply expected confidence values; structural checks validate bounds, transient-state caps, and evidence references.
- **Compare like with like**: changing the corpus, prompts, model, judge, or scoring version starts a new result series.
