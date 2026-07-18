"""定位并载入 ../shared 下的语言中立资产(1.3 · D-0042 · Phase 0 产物)。

TS 是唯一真相源;Python 载入同一份 shared/ JSON(而非手抄),由 shared 的守门测试保证不漂移。
本模块只管「找到 repo/shared 并读 JSON」。打包分发时如何随包携带 shared 资产是后续(Phase 2+)的事。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def shared_dir() -> Path:
    """从本文件向上找含 `shared/config-constants.json` 的目录(= 仓库根的 shared/)。找不到即报错。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "shared" / "config-constants.json"
        if candidate.is_file():
            return parent / "shared"
    raise FileNotFoundError(
        "找不到 shared/config-constants.json —— Python parity 内核需与 TS 仓的 shared/ 同源"
        "(见 D-0042 Phase 0)。运行 `npm run shared:update` 生成。"
    )


def load_shared(relpath: str) -> Any:
    """读 shared/<relpath> 的 JSON(如 'config-constants.json' / 'parity/confidence.json')。"""
    with (shared_dir() / relpath).open(encoding="utf-8") as f:
        return json.load(f)
