# T7 · 公开承诺：永远全开源（随车小任务，纯文档）

**对应五关**：信任关。**作者已拍板**（2026-07-04 决策 5）。改的是对外定位文案——**最终措辞需作者在 PR 里过目**，这是本批唯一需要作者看文案的任务。

## 背景

竞品调查结论：Zep 2025 年砍掉社区版只留引擎开源，这类事让企业用户对 open-core 项目留了心眼。单人项目没有投资人逼着圈地，"核心永远全开源、无隐藏企业版"是小项目对大公司为数不多的结构性优势，作者拍板把它写成公开承诺。

## 改哪里

1. `README.md` 与 `README.zh-CN.md`：在项目状态/定位段落附近加一小段承诺（两语同义）。草稿（作者可改）：
   > **Open source, permanently.** The core library is and will remain fully open source under MIT — no hidden enterprise edition, no open-core split. If a hosted service ever exists, it will only sell convenience, never withheld features.
   >
   > **永远全开源。** 核心库现在是、将来也是 MIT 全开源——没有隐藏的企业版，不搞"开源阉割版"。将来若有托管服务，卖的只会是省事，不会是被扣下的功能。
2. `ROADMAP.md`：Non-goals 段补一行呼应（如 "Open-core split — the library stays fully open source."）。

## 措辞纪律（naming.md 口径）

- 只承诺自己控制得了的事（license 与功能不拆分），不吹"最开放""唯一"这类比较级。
- 不贬损点名任何竞品（Zep 的事是内部依据，不写进对外文案）。

## 验收

- [ ] 中英两段语义一致；作者在 PR 里确认过措辞。
- [ ] 不碰代码；三绿顺手跑一遍即可。
