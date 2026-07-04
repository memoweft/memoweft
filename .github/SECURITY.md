# Security Policy · 安全策略

## How this project is maintained · 维护方式

MemoWeft is maintained by a **single author working alongside AI assistants**, on a
**best-effort** basis. There is **no SLA** and no guaranteed response time. What we can
commit to: **security issues are triaged first**, ahead of feature and other work.

MemoWeft 由**单人 + AI 协作维护**，以 **best-effort（尽力而为）** 的节奏推进。**没有 SLA**、
不承诺固定响应时间。能承诺的是：**安全问题优先分诊**，排在功能与其他工作之前。

## Reporting a vulnerability · 报告安全问题

Please **do not** open a public issue for a security vulnerability. Instead, report it
privately:

- Use GitHub's **[Private vulnerability reporting](https://github.com/memoweft/memoweft/security/advisories/new)**
  (Security tab → "Report a vulnerability").

请**不要**用公开 issue 报告安全漏洞，改走私密渠道：

- 用 GitHub 的**私密漏洞报告**（仓库 Security 页 →「Report a vulnerability」）：
  <https://github.com/memoweft/memoweft/security/advisories/new>

When you report, it helps to include: what you observed, steps to reproduce, and the
affected version or commit.

报告时如能附上：你观察到的现象、复现步骤、受影响的版本或 commit，会更好处理。

## What to expect · 响应预期

- **Security issues are prioritized** over other work — that is the one thing we prioritize.
- Everything is **best-effort**: acknowledgement, assessment, and any fix land as time allows,
  with **no committed timeline**.
- We will let you know whether a report is accepted, and coordinate disclosure once a fix is out.

—

- **安全问题优先**处理——这是我们唯一明确前置的一类。
- 其余一律 **best-effort**：确认、评估、修复都在时间允许时推进，**不承诺时间表**。
- 会告知你报告是否被采纳，并在修复发布后协调披露。

## Supported versions · 支持版本

This is early-alpha software. Security fixes target the **latest published release on npm**
and the current `main`. Older versions are not separately patched.

本项目处于早期 alpha。安全修复只针对 **npm 上的最新发布版**与当前 `main` 分支；旧版本不单独回补。
