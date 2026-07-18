"""从包内生成的共享资产载入语言中立 JSON。

TS 是唯一真相源。``npm run shared:update`` 将其生成结果同步到仓库
``shared/``（供跨语言开发）和本包的 ``_shared_data/``（供已安装发行版）。
``npm run shared:check`` 验证两处均未漂移。
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import PurePosixPath
from typing import Any


@lru_cache(maxsize=1)
def shared_dir() -> Traversable:
    """返回随 ``memoweft`` 分发的生成共享资产目录。"""
    return resources.files("memoweft").joinpath("_shared_data")


def _resource_path(relpath: str) -> Traversable:
    """解析一个包内共享 JSON 相对路径，拒绝越界路径。"""
    path = PurePosixPath(relpath)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".json":
        raise ValueError(f"共享资产路径必须是相对 JSON 路径: {relpath!r}")
    return shared_dir().joinpath(*path.parts)


def load_shared(relpath: str) -> Any:
    """读包内 shared/<relpath> JSON（如 config 常量或 parity 夹具）。"""
    resource = _resource_path(relpath)
    if not resource.is_file():
        raise FileNotFoundError(f"找不到打包的共享资产: {relpath}")
    with resource.open(encoding="utf-8") as f:
        return json.load(f)
