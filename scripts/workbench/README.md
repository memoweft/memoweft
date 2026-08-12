# MemoWeft 1.0-based workbench

This launcher runs the existing MemoWeft 1.0 testbench at `http://127.0.0.1:7888` as the single browser host, with the local MemoWeft Next backend on port `7891` and the managed local model on port `8012`.

The original chat, sessions, cloud/local model configuration, 1.x profile organizer, memory manager, backup/import, and debug tools stay in the testbench. MemoWeft Next is connected only through the allowlisted loopback bridge for candidate review, accept/reject, the accepted world, recall, and correction.

- `Start-MemoWeft-Workbench.cmd` takes a named startup mutex, rejects an existing `7888` collision before touching dependencies, then starts the managed dependencies and workbench. Pass `-SkipLocalModel` when the original 1.x chat/write path is deliberately configured for a cloud model and the local 2.0 model should remain stopped; the 2.0 extractor/answer path will then report model unavailability rather than silently switching Evidence to the cloud.
- `Status-MemoWeft-Workbench.cmd` verifies the versioned state schema, Node executable, creation ticks, exact argv, listener owner, workbench HTTP instance token, and the forwarded Next server identity/token.
- `Stop-MemoWeft-Workbench.cmd` stops only a fully verified workbench UI by default. It never stops an unmanaged UI or any dependency when no trusted UI state exists. Pass `-StopDependencies` directly to the PowerShell script only when the managed Next/model dependencies should also be stopped. `-KeepDependencies` remains accepted for compatibility and has the default behavior.

The state file is intentionally a strict, schema-versioned ownership record. A workbench process carries a fresh random token both as `--workbench-instance-token` and `MEMOWEFT_WORKBENCH_INSTANCE_TOKEN`; readiness verifies the loopback-only `/api/workbench-identity` response against that token. This prevents state, PID, listener, and HTTP identities from being mixed across launches.

If a repository `.env` exists, the original testbench continues to use it, including cloud model configuration. Without `.env` or inherited LLM variables, the launcher supplies the managed local Qwen endpoint as the default.
