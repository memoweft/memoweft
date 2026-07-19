# Security Policy

## Supported versions

Security fixes target the latest published MemoWeft release and the current `main` branch. Older pre-1.0 releases are not maintained separately.

| Version            | Supported |
| ------------------ | --------- |
| Latest npm release | Yes       |
| Current `main`     | Yes       |
| Older releases     | No        |

## Report a vulnerability

Please do not open a public issue or discussion for a suspected vulnerability.

Use GitHub's [private vulnerability reporting](https://github.com/memoweft/memoweft/security/advisories/new) and include, where possible:

- the affected version or commit;
- impact and likely attack path;
- minimal reproduction steps or a proof of concept;
- relevant configuration and platform details;
- any suggested mitigation.

Do not include credentials, private memory databases, or real user data. Use a minimal synthetic reproduction.

## What to expect

MemoWeft is maintained by a single author working alongside AI assistants, on a best-effort basis. **There is no SLA and no guaranteed response time.** What can be committed to: security reports are triaged ahead of feature and other work, receipt is acknowledged once the report can be reproduced, and disclosure is coordinated after a fix or mitigation is available.

Please allow a reasonable remediation window before public disclosure. Credit will be offered when requested unless the report must remain anonymous.

## Security scope

High-priority reports include:

- authorization or cloud-read boundary bypasses;
- unintended disclosure of evidence or cognition content;
- unsafe import, export, or database handling;
- command execution, path traversal, or injection issues;
- dependency or workflow compromise affecting published packages.

General hardening suggestions and non-sensitive bugs can use the normal issue tracker.
