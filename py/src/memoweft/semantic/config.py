"""CPU semantic recall is optional derived data, never an authority source."""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path


@dataclass(frozen=True)
class SemanticConfig:
    enabled: bool = True
    threshold: float = 0.87
    max_additions: int = 3
    batch_size: int = 16
    cache_dir: Path | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.threshold) or not 0 <= self.threshold <= 1:
            raise ValueError("semantic threshold must be between 0 and 1")
        if not 0 <= self.max_additions <= 3:
            raise ValueError("semantic recall allows at most 3 additions")
        if not 1 <= self.batch_size <= 64:
            raise ValueError("semantic batch_size must be between 1 and 64")

    @classmethod
    def from_env(cls) -> SemanticConfig:
        cache = os.environ.get("MEMOWEFT_SEMANTIC_CACHE_DIR")
        return cls(
            enabled=os.environ.get("MEMOWEFT_SEMANTIC_ENABLED", "1").lower() not in {"0", "false", "off"},
            threshold=float(os.environ.get("MEMOWEFT_SEMANTIC_THRESHOLD", "0.87")),
            cache_dir=Path(cache) if cache else None,
        )
