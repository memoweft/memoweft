# Legacy shortcut (redirects to the unified workbench)

The writable `testbench/` is now the original MemoWeft 1.0 console upgraded in place with the MemoWeft 2.0 memory bridge. It is therefore no longer truthful to launch it as a pristine “TypeScript 1.x control.”

From the repository root, run:

```powershell
.\Start-1x-Testbench-Control.cmd
```

This legacy command now redirects to `Start-MemoWeft-Workbench.cmd`, which starts the local model, the MemoWeft Next memory backend, and the upgraded original console at `http://127.0.0.1:7888/`.

If a separate immutable v1.0.0 reference checkout is maintained, this shortcut
does not read or modify it. The unified workbench is diagnostic integration,
not product acceptance evidence.
