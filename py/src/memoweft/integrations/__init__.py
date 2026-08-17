"""Host integrations for the Python MemoWeft runtime — the production mainline.

Integrations adapt host lifecycle events into MemoWeft's stable Evidence and
recall boundaries.  Host-specific transport state must not leak into the core
domain model.

Mainline contract (see ``py/MAINLINE.md``, Owner decision 先C后A, 2026-08-17):
Hermes and DSH/WeftMate are the only production chains; they never import
``memoweft.world`` (parity insurance layer).
"""

from __future__ import annotations

__all__: list[str] = []
