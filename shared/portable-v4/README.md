# Portable v4 shared contract

This directory is the cross-language authority for MemoWeft Portable v4.

- `portable-v4.schema.json` fixes the JSON envelope and v4-only history/currentness sections.
- `fixtures/full-v4.json` exercises every v4 section and is consumed by Python and TypeScript tests.
- `fixtures/compat-v2.json` and `fixtures/compat-v3.json` prove reader compatibility without inventing v4 history.

Identity rules:

1. Canonical JSON uses recursively sorted object keys, preserves array order, emits UTF-8 without ASCII escaping, and contains no insignificant whitespace.
2. `bundleId` is `portable:v4:` plus SHA-256 of the canonical bundle after removing only the top-level `bundleId`.
3. An import `planHash` covers the bundle id, source/target subject mapping, target revision/snapshot identity, conflicts, warnings, counts, duplicates and the exact write set.
4. `commandId` and `receiptId` are deterministic derivatives of `planHash`; apply must present the planned target revision and hash.

Evidence carries the three user permissions, correction identity and deletion tombstone. `precedingAiContext` is deliberately excluded: assistant text is typed interaction context, not portable user Evidence, and export must not turn it into a cross-host memory payload.

The fixtures contain memory text and ids only. They contain no credentials, runtime configuration, vector indexes, logs or host UI state.
