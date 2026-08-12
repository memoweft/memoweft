# MemoWeft local model runtime

This directory manages the local llama.cpp service used by MemoWeft Next. All
large or machine-specific artifacts live under the gitignored `.local/` tree.

## Fixed service profile

- CUDA llama.cpp runtime copied from an explicitly supplied local source
- Qwen3 14B Q5_K_M model hard-linked from an explicitly supplied GGUF source
- bind address `127.0.0.1:8012`
- OpenAI model alias `qwen3-14b-local`
- one server slot, 32,768-token context, 99 GPU layers
- Q8_0 K/V cache, Flash Attention on, Jinja chat templates on
- no inherited `LLAMA_API_KEY`

## Commands

Run these from the repository root:

```powershell
# One-time/idempotent local installation
# The managed service and every 8012 listener must be stopped first.
pwsh -NoProfile -File .\scripts\local-model\Install-Local-Model.ps1 `
  -RuntimeSource 'C:\path\to\llama-cpp' `
  -ModelSource 'C:\path\to\Qwen3-14B-Q5_K_M.gguf'

# Hidden background process; waits until /health returns HTTP 200
.\Start-Local-Model.cmd

# PID/path/command-line-safe stop
.\Stop-Local-Model.cmd

# Managed PID identity plus /health
.\Status-Local-Model.cmd

# Blocking foreground session (logs still go to .local/logs)
pwsh -NoProfile -File .\scripts\local-model\Serve-Local-Model.ps1

# Real /health, /v1/models, short Chinese chat, CUDA-log and VRAM evidence
pwsh -NoProfile -File .\scripts\local-model\Verify-Local-Model.ps1

# Focused stopped/unmanaged/collision lifecycle smoke; requires free port 8012
pwsh -NoProfile -File .\scripts\local-model\Test-Local-ModelLifecycle.ps1

# Pure state/argv/mutex contract; safe while the managed model is running
pwsh -NoProfile -File .\scripts\local-model\Test-Local-ModelStateContract.ps1
```

For unattended installation, the same two inputs may be supplied through
`MEMOWEFT_LLAMA_CPP_RUNTIME_SOURCE` and `MEMOWEFT_LOCAL_MODEL_SOURCE`. The
source paths are recorded under `.local/state/installation.json`; they are
never committed.

The stop command never searches by process name. It only stops the PID stored in
`.local/state/server.json` after the process creation time, executable path,
and exact argv all match; when a listener exists, it must have the same PID.
State schema v2 is strict;
the original v1 state is accepted only through a bounded creation-time
compatibility check, and the next managed start writes v2. If trusted state is
absent but `127.0.0.1:8012` still has a
listener, Status reports the unmanaged owner PID, identity result and health;
Stop reports the same diagnostics and refuses to adopt or terminate it. Multiple
listener rows, invalid owners and vanished owners are reported as collisions.

Install, Start, foreground Serve, Status, and Stop share one abandoned-safe
Windows lifecycle mutex. Concurrent launchers therefore cannot both pass the
empty-state/empty-port check or overwrite each other's state. Installation also
refuses to replace the runtime while any managed state or 8012 listener exists.

Status exit codes are: `0` managed and healthy; `1` truly stopped or stale;
`2` managed loading/unhealthy or an identity-matching unmanaged listener; and
`3` identity mismatch or listener collision. Stop returns `0` only when a
managed PID was stopped, stale state was removed, or no state/listener exists;
an unmanaged listener is always a nonzero fail-closed result.

Logs are timestamped under `.local/logs/`. Installation and verification records
are under `.local/state/`; both locations are intentionally untracked.
