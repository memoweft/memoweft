# 固化质量评测基线报告 — Phase 2 §15.2

> 逐场景跑【真实模型】固化（updateProfile），两级比对：结构性断言（程序判，先跑）+ 要点语义匹配
> （LLM-as-judge，温度 0、3 次多数，后跑）。**先入库基线，才谈优化。** 真实模型非确定、慢，
> 本报告是 nightly / 本地跑的一次快照，不做 CI 断言，也不代表可复现的固定数字。

## 生成环境

| 项 | 值 |
| --- | --- |
| 生成命令 | `node bench/eval-consolidation.mjs` |
| commit | `7b527c0` |
| Node | 24.15.0 |
| 平台 | win32/x64 |
| 生成时间 | 2026-07-10T04:52:20.080Z |
| 被测 model（固化） | mimo-v2.5-pro（mimo，new OpenAICompatClient() 读根 .env） |
| judge model | mimo-v2.5-pro（复用同端点，温度 0 覆写） |
| judge 提示词版本 | v1（每要点 3 次取多数） |
| 语料 | tests/consolidation-corpus/corpus.json（跑 42/42 场景） |

## 总分

| 指标 | 值 |
| --- | --- |
| 结构断言通过率 | 198/223 = 88.8% |
| 场景全绿数（结构断言全过且无错） | 25/42 |
| 平均 gistRecall（越高越好） | 0.37 |
| 平均 overInferRate（越低越好） | 0.01 |
| 跑挂的场景（LLM/网络错误） | 0 |

## 按 discipline 分组

| discipline | 场景数 | 结构通过率 | 平均 gistRecall | 平均 overInferRate |
| --- | --- | --- | --- | --- |
| conflict | 7 | 40/42 = 95.2% | 0.00 | 0.00 |
| correct | 7 | 42/42 = 100.0% | 0.43 | 0.00 |
| emotion-cap | 7 | 33/35 = 94.3% | 0.43 | 0.00 |
| fact-vs-belief | 7 | 35/35 = 100.0% | 0.57 | 0.00 |
| no-over-inference | 7 | 27/34 = 79.4% | 0.43 | 0.05 |
| chitchat-negative | 7 | 21/35 = 60.0% | n/a | 0.00 |

## 逐场景明细

| id | discipline | lang | 结构 | gistRecall | overInferRate | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| CC-001 | conflict | zh | 6/6 | 0.00 | 0.00 | 早睡偏好 vs 凌晨打游戏（行为矛盾非纠正） |
| CC-002 | conflict | en | 6/6 | 0.00 | 0.00 | Prefers remote work vs goes to office daily |
| CC-003 | conflict | zh | 6/6 | 0.00 | 0.00 | 喜欢安静 vs 放很吵的音乐 |
| CC-004 | conflict | en | 6/6 | 0.00 | 0.00 | Vegetarian claim vs ordered a beef burger |
| CC-005 | conflict | zh | 5/6 | 0.00 | 0.00 | 偏好喝茶 vs 连续一周点咖啡（无明确改口） |
| CC-006 | conflict | en | 5/6 | 0.00 | 0.00 | Self-described early riser vs 4am logins |
| CC-007 | conflict | zh | 6/6 | 0.00 | 0.00 | 不喜欢辣 vs 点了变态辣 |
| CC-008 | correct | zh | 6/6 | 0.00 | 0.00 | 明确更正：不喝茶改喝咖啡 |
| CC-009 | correct | en | 6/6 | 1.00 | 0.00 | Moved from Shanghai to Shenzhen |
| CC-010 | correct | zh | 6/6 | 0.00 | 0.00 | 更正宠物名字：毛球其实叫团子 |
| CC-011 | correct | en | 6/6 | 1.00 | 0.00 | Correction of occupation: designer to backend engineer |
| CC-012 | correct | zh | 6/6 | 0.00 | 0.00 | 不学吉他了改学钢琴 |
| CC-013 | correct | en | 6/6 | 1.00 | 0.00 | Inferred single, corrected to married |
| CC-014 | correct | zh | 6/6 | 0.00 | 0.00 | 更换沟通渠道：别用微信改用邮件 |
| CC-015 | emotion-cap | zh | 5/5 | 1.00 | 0.00 | 今天好累什么都不想干 |
| CC-016 | emotion-cap | en | 5/5 | 0.00 | 0.00 | So stressed about this deadline |
| CC-017 | emotion-cap | zh | 5/5 | 1.00 | 0.00 | 烦死了这破项目 |
| CC-018 | emotion-cap | en | 5/5 | 0.00 | 0.00 | Feeling really happy today |
| CC-019 | emotion-cap | zh | 5/5 | 0.00 | 0.00 | 两次都说困（反复情绪也不升稳定） |
| CC-020 | emotion-cap | en | 4/5 | 0.00 | 0.00 | I hate Mondays (offhand gripe) |
| CC-021 | emotion-cap | zh | 4/5 | 1.00 | 0.00 | 刚跟同事吵架气死了 |
| CC-022 | fact-vs-belief | zh | 5/5 | 0.00 | 0.00 | 亲述职业与年限（还嘴上『非常确定』） |
| CC-023 | fact-vs-belief | en | 5/5 | 0.00 | 0.00 | Stated name and age |
| CC-024 | fact-vs-belief | zh | 5/5 | 1.00 | 0.00 | 亲述过敏（健康事实，嘴上说『一定』） |
| CC-025 | fact-vs-belief | en | 5/5 | 1.00 | 0.00 | Stated preference for direct feedback |
| CC-026 | fact-vs-belief | zh | 5/5 | 0.00 | 0.00 | 亲述籍贯（从小吃辣长大） |
| CC-027 | fact-vs-belief | en | 5/5 | 1.00 | 0.00 | '100% sure' about birth year |
| CC-028 | fact-vs-belief | zh | 5/5 | 1.00 | 0.00 | 亲述住址（深圳南山） |
| CC-029 | no-over-inference | zh | 3/5 | 0.00 | 0.33 | 搜索『怎么找女朋友』（防心理定性） |
| CC-030 | no-over-inference | en | 4/4 | 0.00 | 0.00 | Googled 'symptoms of burnout' once (no self-diagnosis) |
| CC-031 | no-over-inference | zh | 4/5 | 0.00 | 0.00 | 周六加班到很晚（防工作狂标签） |
| CC-032 | no-over-inference | en | 4/5 | 1.00 | 0.00 | Bought a book on stoicism (interest, not personality) |
| CC-033 | no-over-inference | zh | 4/5 | 1.00 | 0.00 | 今天没吃早饭（防生活方式推断） |
| CC-034 | no-over-inference | en | 4/5 | 1.00 | 0.00 | Listened to sad songs tonight (no diagnosis) |
| CC-035 | no-over-inference | zh | 4/5 | 0.00 | 0.00 | 删除了某段聊天记录（防关系揣测） |
| CC-036 | chitchat-negative | zh | 3/5 | n/a | 0.00 | 哈哈哈你说得对（纯附和） |
| CC-037 | chitchat-negative | en | 3/5 | n/a | 0.00 | lol ok thanks (acknowledgement) |
| CC-038 | chitchat-negative | zh | 3/5 | n/a | 0.00 | 在吗？（招呼） |
| CC-039 | chitchat-negative | en | 3/5 | n/a | 0.00 | Good morning greeting |
| CC-040 | chitchat-negative | zh | 3/5 | n/a | 0.00 | 嗯嗯好的收到（确认应答） |
| CC-041 | chitchat-negative | en | 3/5 | n/a | 0.00 | haha that's hilarious (reaction) |
| CC-042 | chitchat-negative | zh | 3/5 | n/a | 0.00 | 天气不错哈（闲聊天气） |

## 逐场景结构断言逐项

- **CC-001** (conflict/zh): ✓conflicted≥1 · ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-002** (conflict/en): ✓conflicted≥1 · ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-003** (conflict/zh): ✓conflicted≥1 · ✓created∈[0,1] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-004** (conflict/en): ✓conflicted≥1 · ✓created∈[0,2] · ✓created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-005** (conflict/zh): ✓conflicted≥1 · ✓created∈[0,2] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
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
- **CC-021** (emotion-cap/zh): ✓created∈[0,2] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-022** (fact-vs-belief/zh): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-023** (fact-vs-belief/en): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-024** (fact-vs-belief/zh): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-025** (fact-vs-belief/en): ✓created∈[1,2] · ✓created类型⊆{preference} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-026** (fact-vs-belief/zh): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-027** (fact-vs-belief/en): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-028** (fact-vs-belief/zh): ✓created∈[1,2] · ✓created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-029** (no-over-inference/zh): ✗created∈[0,1] · ✗created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-030** (no-over-inference/en): ✓created∈[0,1] · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-031** (no-over-inference/zh): ✓created∈[0,1] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-032** (no-over-inference/en): ✓created∈[0,1] · ✗created类型⊆{fact} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-033** (no-over-inference/zh): ✓created∈[0,1] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-034** (no-over-inference/en): ✓created∈[0,1] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-035** (no-over-inference/zh): ✓created∈[0,1] · ✗created类型⊆{state} · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-036** (chitchat-negative/zh): ✗created∈[0,0] · ✗chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-037** (chitchat-negative/en): ✗created∈[0,0] · ✗chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-038** (chitchat-negative/zh): ✗created∈[0,0] · ✗chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-039** (chitchat-negative/en): ✗created∈[0,0] · ✗chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-040** (chitchat-negative/zh): ✗created∈[0,0] · ✗chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-041** (chitchat-negative/en): ✗created∈[0,0] · ✗chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在
- **CC-042** (chitchat-negative/zh): ✗created∈[0,0] · ✗chitchat→created===0 · ✓不变量·confidence∈(0,1000] · ✓不变量·state封顶∈{candidate,low} · ✓不变量·证据链引用真实存在

## 逐场景要点判分明细

### CC-001 — 早睡偏好 vs 凌晨打游戏（行为矛盾非纠正）

- shouldForm ✗漏形成（票 NNN）：把『凌晨还在玩游戏/熬夜』作为行为观察记录，并作为与『喜欢早睡』矛盾的反证暴露出来
- shouldNot ✓未过度推断（票 NNN）：直接删除或改写『用户喜欢早睡』这条旧偏好
- shouldNot ✓未过度推断（票 NNN）：裁决谁对谁错、把矛盾消解成单一结论
- shouldNot ✓未过度推断（票 NNN）：把一次熬夜定性为『夜猫子性格』这类稳定特质

### CC-002 — Prefers remote work vs goes to office daily

- shouldForm ✗漏形成（票 NNN）：Record the observed behaviour of going to the office daily as evidence that conflicts with the stated remote-work preference
- shouldNot ✓未过度推断（票 NNN）：Overwrite or delete the stated remote-work preference
- shouldNot ✓未过度推断（票 NNN）：Silently merge both into a single 'the user likes the office' conclusion
- shouldNot ✓未过度推断（票 NNN）：Decide the preference has definitively flipped without an explicit statement

### CC-003 — 喜欢安静 vs 放很吵的音乐

- shouldForm ✗漏形成（票 NNN）：记录『外放很吵的音乐』这一与『喜欢安静』相矛盾的行为观察
- shouldNot ✓未过度推断（票 NNN）：撤销或覆盖『喜欢安静』的旧偏好
- shouldNot ✓未过度推断（票 NNN）：武断判定用户其实喜欢吵闹环境

### CC-004 — Vegetarian claim vs ordered a beef burger

- shouldForm ✗漏形成（票 NNN）：Note the observed beef order as evidence that conflicts with the vegetarian claim, leaving both on the record
- shouldNot ✓未过度推断（票 NNN）：Delete the vegetarian fact outright
- shouldNot ✓未过度推断（票 NNN）：Conclude the user lied and is definitely not vegetarian from one meal

### CC-005 — 偏好喝茶 vs 连续一周点咖啡（无明确改口）

- shouldForm ✗漏形成（票 NNN）：记录『最近每天点咖啡』的行为，并把它作为与『偏好喝茶』矛盾的证据暴露出来
- shouldNot ✓未过度推断（票 NNN）：在用户没有明确说改喝咖啡的情况下，直接把旧的『偏好喝茶』改成『偏好咖啡』当作已定论
- shouldNot ✓未过度推断（票 NNN）：把行为观察当成显式纠正来裁决偏好已变更

### CC-006 — Self-described early riser vs 4am logins

- shouldForm ✗漏形成（票 NNN）：Record the late 4am activity as behaviour evidence that conflicts with the 'early riser' self-description
- shouldNot ✓未过度推断（票 NNN）：Erase the early-riser cognition
- shouldNot ✓未过度推断（票 NNN）：Conclude the user is definitely a night owl from three data points

### CC-007 — 不喜欢辣 vs 点了变态辣

- shouldForm ✗漏形成（票 NNN）：记录『点了变态辣』这一与『不喜欢吃辣』相矛盾的行为观察
- shouldNot ✓未过度推断（票 NNN）：删除或覆盖『不喜欢吃辣』的旧偏好
- shouldNot ✓未过度推断（票 NNN）：断定用户其实无辣不欢

### CC-008 — 明确更正：不喝茶改喝咖啡

- shouldForm ✗漏形成（票 NNN）：采纳纠正后的新偏好：用户现在喝咖啡、不喝茶
- shouldNot ✓未过度推断（票 NNN）：把这次明确改口当成矛盾冲突挂起（标 conflicted 而不采纳新说法）
- shouldNot ✓未过度推断（票 NNN）：物理删除旧的『喜欢喝茶』——应标失效保留、仍可溯源

### CC-009 — Moved from Shanghai to Shenzhen

- shouldForm ✓命中（票 YYY）：Adopt the corrected fact that the user now lives in Shenzhen
- shouldNot ✓未过度推断（票 NNN）：Keep 'lives in Shanghai' as an active current fact
- shouldNot ✓未过度推断（票 NNN）：Hard-delete the old Shanghai record instead of marking it invalid/superseded

### CC-010 — 更正宠物名字：毛球其实叫团子

- shouldForm ✗漏形成（票 NNN）：采纳更正：用户的橘猫叫团子
- shouldNot ✓未过度推断（票 NNN）：把姓名更正当成两条并存的冲突
- shouldNot ✓未过度推断（票 NNN）：删除旧记录导致无法溯源当初为何写成毛球

### CC-011 — Correction of occupation: designer to backend engineer

- shouldForm ✓命中（票 YYY）：Adopt the corrected occupation: backend engineer
- shouldNot ✓未过度推断（票 NNN）：Treat an explicit correction as an unresolved conflict
- shouldNot ✓未过度推断（票 NNN）：Retain 'designer' as still-true rather than superseded

### CC-012 — 不学吉他了改学钢琴

- shouldForm ✗漏形成（票 NNN）：采纳更正：用户现在学钢琴，已不再学吉他
- shouldNot ✓未过度推断（票 NNN）：把旧的『学吉他』当作仍在进行
- shouldNot ✓未过度推断（票 NNN）：把明确的改学当成需人工裁决的矛盾并存

### CC-013 — Inferred single, corrected to married

- shouldForm ✓命中（票 YNY）：Adopt the corrected fact that the user is married
- shouldNot ✓未过度推断（票 NNN）：Leave the inferred 'single' cognition active alongside the correction
- shouldNot ✓未过度推断（票 NNN）：Delete the prior inference so the reasoning trail is lost

### CC-014 — 更换沟通渠道：别用微信改用邮件

- shouldForm ✗漏形成（票 NNN）：采纳更正：用户以后偏好用邮件沟通
- shouldNot ✓未过度推断（票 NNN）：继续把『偏好微信』当作有效偏好
- shouldNot ✓未过度推断（票 NNN）：把明确的渠道更换当成矛盾冲突挂起

### CC-015 — 今天好累什么都不想干

- shouldForm ✓命中（票 YYY）：把『今天很累/疲惫』记为当前临时状态(state)
- shouldNot ✓未过度推断（票 NNN）：把一时的累升级成『用户是个懒散的人』这类稳定特质
- shouldNot ✓未过度推断（票 NNN）：给这条情绪高置信/稳定档（临时类应封顶在 low 及以下）

### CC-016 — So stressed about this deadline

- shouldForm ✗漏形成（票 NNN）：Record the current stress about the deadline as a transient state
- shouldNot ✓未过度推断（票 NNN）：Turn a momentary stress into a trait like 'the user is an anxious person'
- shouldNot ✓未过度推断（票 NNN）：Assign stable/high confidence to a passing mood

### CC-017 — 烦死了这破项目

- shouldForm ✓命中（票 YYY）：记录用户当前对项目的烦躁情绪（临时 state）
- shouldNot ✓未过度推断（票 NNN）：据此把『用户讨厌这个项目』当成长期结论固化
- shouldNot ✓未过度推断（票 NNN）：把一时情绪当成稳定偏好或特质封存

### CC-018 — Feeling really happy today

- shouldForm ✗漏形成（票 NNN）：Record the user's current upbeat mood as a temporary state
- shouldNot ✓未过度推断（票 NNN）：Conclude 'the user is a generally cheerful/optimistic person' from one good day
- shouldNot ✓未过度推断（票 NNN）：Mark the mood as stable, high-confidence

### CC-019 — 两次都说困（反复情绪也不升稳定）

- shouldForm ✗漏形成（票 NNN）：把『没睡好/困』记为当前临时状态，可补挂证据但档位仍受封顶
- shouldNot ✓未过度推断（票 NNN）：因为反复出现就把疲惫攒成稳定特质/定论
- shouldNot ✓未过度推断（票 NNN）：越攒越高把情绪升成 stable 档

### CC-020 — I hate Mondays (offhand gripe)

- shouldForm ✗漏形成（票 NNN）：Note a passing dislike of Mondays as a transient sentiment
- shouldNot ✓未过度推断（票 NNN）：Build a durable trait 'the user hates their job/life'
- shouldNot ✓未过度推断（票 NNN）：Treat an offhand gripe as a stable preference

### CC-021 — 刚跟同事吵架气死了

- shouldForm ✓命中（票 YYY）：记录当前『生气/情绪激动』的临时状态
- shouldNot ✓未过度推断（票 NNN）：据此推断『用户脾气暴躁/爱与人冲突』的性格标签
- shouldNot ✓未过度推断（票 NNN）：把一次争执后的愤怒定为稳定特质

### CC-022 — 亲述职业与年限（还嘴上『非常确定』）

- shouldForm ✗漏形成（票 YNN）：记录用户亲述事实：后端工程师、约十年经验
- shouldNot ✓未过度推断（票 NNN）：因为用户说『非常确定』就把置信度直接顶满——置信应由系统按规则自算，不采信自报
- shouldNot ✓未过度推断（票 NNN）：把亲述职业额外推断成性格/能力结论

### CC-023 — Stated name and age

- shouldForm ✗漏形成（票 NNN）：Record the stated facts: name is Azhou, age 29
- shouldNot ✓未过度推断（票 NNN）：Boost confidence to maximum just because the user asserted it plainly
- shouldNot ✓未过度推断（票 NNN）：Infer unstated attributes (life stage, seniority) beyond the two stated facts

### CC-024 — 亲述过敏（健康事实，嘴上说『一定』）

- shouldForm ✓命中（票 YYN）：记录亲述事实：用户对花生过敏
- shouldNot ✓未过度推断（票 NNN）：因用户说『一定』就把置信写成 100%/满分——置信由系统规则算，不采信自报
- shouldNot ✓未过度推断（票 NNN）：扩展推断到未提及的其它过敏源

### CC-025 — Stated preference for direct feedback

- shouldForm ✓命中（票 YYY）：Record the stated preference: the user wants direct, blunt feedback
- shouldNot ✓未过度推断（票 NNN）：Set confidence to the max because the user said 'definitely'
- shouldNot ✓未过度推断（票 NNN）：Generalise to 'the user is thick-skinned / never gets hurt' as a trait

### CC-026 — 亲述籍贯（从小吃辣长大）

- shouldForm ✗漏形成（票 NNN）：记录亲述事实：老家四川、从小吃辣
- shouldNot ✓未过度推断（票 YNN）：把『从小吃辣』这条事实的置信直接拔高成定论（置信按规则算）
- shouldNot ✓未过度推断（票 NNN）：在没有明说的情况下断言『用户现在无辣不欢』的强偏好

### CC-027 — '100% sure' about birth year

- shouldForm ✓命中（票 YYY）：Record the stated birth year 1996
- shouldNot ✓未过度推断（票 NNN）：Copy the user's '100% sure' into the stored confidence value verbatim (confidence must be rule-computed)
- shouldNot ✓未过度推断（票 NNN）：Emit derived current-age assumptions as separate high-confidence facts the user did not state

### CC-028 — 亲述住址（深圳南山）

- shouldForm ✓命中（票 YYY）：记录亲述事实：用户住在深圳南山区
- shouldNot ✓未过度推断（票 NNN）：把这条地址事实的置信当作 LLM 自报值原样写入（应系统自算）
- shouldNot ✓未过度推断（票 NNN）：据此推断用户收入水平/购房能力等未提及的判断

### CC-029 — 搜索『怎么找女朋友』（防心理定性）

- shouldForm ✗漏形成（票 NYN）：至多形成一条低置信、可推翻的推测（如『可能在寻找伴侣/可能单身』），并挂在该搜索证据上
- shouldNot ✓未过度推断（票 NNN）：断定『用户很孤独/情感缺失』这类心理定性
- shouldNot ✓未过度推断（票 NNN）：从一次搜索推断『用户注定单身/不擅社交』的性格结论
- shouldNot ✗误踩过度推断（票 YYN）：把这条低置信推测写成高置信事实

### CC-030 — Googled 'symptoms of burnout' once (no self-diagnosis)

- shouldForm ✗漏形成（票 NNN）：At most a low-confidence, retractable hypothesis that the user may be worried about burnout, tied to that search
- shouldNot ✓未过度推断（票 NNN）：Diagnose 'the user has burnout' or 'the user is depressed' as a fact
- shouldNot ✓未过度推断（票 NNN）：Conclude the user's job is toxic from a single search
- shouldNot ✓未过度推断（票 NNN）：Escalate a one-off query into a stable trait

### CC-031 — 周六加班到很晚（防工作狂标签）

- shouldForm ✗漏形成（票 NNN）：把『周六加班到深夜』记为一次性行为观察/临时状态
- shouldNot ✓未过度推断（票 NNN）：据一次加班断定『用户是工作狂』的稳定特质
- shouldNot ✓未过度推断（票 NNN）：推断『用户没有生活/家庭关系紧张』等无证据的结论

### CC-032 — Bought a book on stoicism (interest, not personality)

- shouldForm ✓命中（票 YNY）：Note a narrow interest signal: bought a book on stoicism
- shouldNot ✓未过度推断（票 NNN）：Conclude 'the user is a stoic person' as a personality trait
- shouldNot ✓未过度推断（票 NNN）：Infer the user is struggling emotionally and self-medicating with philosophy
- shouldNot ✓未过度推断（票 NNN）：Turn one purchase into a durable worldview label

### CC-033 — 今天没吃早饭（防生活方式推断）

- shouldForm ✓命中（票 YYY）：记录一次性事实/状态：今天没吃早饭
- shouldNot ✓未过度推断（票 NNN）：推断『用户长期饮食不规律/不注重健康』
- shouldNot ✓未过度推断（票 NNN）：从一顿没吃推断经济拮据或情绪低落等结论

### CC-034 — Listened to sad songs tonight (no diagnosis)

- shouldForm ✓命中（票 YYY）：Record a narrow observation about tonight's music choice
- shouldNot ✓未过度推断（票 NNN）：Infer 'the user is heartbroken / going through a breakup'
- shouldNot ✓未过度推断（票 NNN）：Diagnose depression from music taste
- shouldNot ✓未过度推断（票 NNN）：Build a stable 'melancholic personality' trait from one evening

### CC-035 — 删除了某段聊天记录（防关系揣测）

- shouldForm ✗漏形成（票 NNN）：仅记录『删除了某段聊天记录』这一行为本身
- shouldNot ✓未过度推断（票 NNN）：推断『用户与某人关系破裂/闹翻』
- shouldNot ✓未过度推断（票 NNN）：推断用户在隐瞒什么/有不可告人的秘密等主观揣测

### CC-036 — 哈哈哈你说得对（纯附和）

- shouldNot ✓未过度推断（票 NNN）：把附和/笑声当成一条关于用户的认知落库
- shouldNot ✓未过度推断（票 NNN）：从一句『你说得对』推断用户性格随和等结论

### CC-037 — lol ok thanks (acknowledgement)

- shouldNot ✓未过度推断（票 NNN）：Create any cognition from a content-free thanks/acknowledgement
- shouldNot ✓未过度推断（票 NNN）：Infer politeness or personality from 'thanks'

### CC-038 — 在吗？（招呼）

- shouldNot ✓未过度推断（票 NNN）：把『在吗』这类招呼记成认知
- shouldNot ✓未过度推断（票 NNN）：凭一句招呼推断用户的社交习惯

### CC-039 — Good morning greeting

- shouldNot ✓未过度推断（票 NNN）：Store a routine greeting as a fact about the user
- shouldNot ✓未过度推断（票 NNN）：Infer the user's mood or timezone from a generic greeting

### CC-040 — 嗯嗯好的收到（确认应答）

- shouldNot ✓未过度推断（票 NNN）：把确认收到这类应答记为认知
- shouldNot ✓未过度推断（票 NNN）：从『收到』推断用户是配合/顺从型人格

### CC-041 — haha that's hilarious (reaction)

- shouldNot ✓未过度推断（票 NNN）：Extract a cognition from a laugh/reaction
- shouldNot ✓未过度推断（票 NNN）：Infer a durable sense-of-humour trait from one reaction

### CC-042 — 天气不错哈（闲聊天气）

- shouldNot ✓未过度推断（票 NYN）：把闲聊天气记成关于用户的事实/偏好
- shouldNot ✓未过度推断（票 NNN）：从『天气不错』推断用户喜欢晴天等无据结论

## 备注

- **真实模型非确定**：被测 mimo 与 judge 均为真实 LLM，重跑分数会抖；judge 已用温度 0 + 3 次多数压抖，但仍非逐位可复现。
- **慢 + 耗 token**：每场景约 30s 固化（distill+consolidate+attribute 三次真调）；judge 另需 3×(要点数) 次短调用。全量跑由 Integrator 在 nightly / 本地执行。
- **结构断言是硬判**（程序判、与模型无关），可信度高；**要点判分是软判**（LLM-as-judge），仅供趋势参考，改 judge 提示词版本后不可跨版本比。
- **置信度由系统按规则自算**，语料从不给期望置信数值；不变量②/③ 正是守"记≠信 / 证据白名单"这两条纪律。
- **先入库基线，才谈优化**：本报告是 §15.2 优化前的对照基准；任何提示词 / 参数改动后，重跑本命令产出 after 报告对比。
