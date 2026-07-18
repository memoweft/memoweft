"""Generated package resources stay byte-identical to the TS-owned shared assets."""
from __future__ import annotations

from importlib import resources
from pathlib import Path

from memoweft._shared import load_shared


REPO_SHARED = Path(__file__).resolve().parents[2] / "shared"
PACKAGE_SHARED = Path(__file__).resolve().parents[1] / "src" / "memoweft" / "_shared_data"


def test_package_resources_match_generated_shared_assets() -> None:
    package_root = resources.files("memoweft").joinpath("_shared_data")
    source_files = sorted(REPO_SHARED.rglob("*.json"))
    packaged_files = sorted(
        path.relative_to(PACKAGE_SHARED).as_posix()
        for path in PACKAGE_SHARED.rglob("*.json")
    )

    assert source_files
    assert packaged_files == [path.relative_to(REPO_SHARED).as_posix() for path in source_files]
    for source in source_files:
        relpath = source.relative_to(REPO_SHARED)
        packaged = package_root.joinpath(*relpath.parts)
        assert packaged.is_file(), relpath.as_posix()
        assert packaged.read_bytes() == source.read_bytes(), relpath.as_posix()


def test_runtime_loads_config_and_prompts_from_package_resources() -> None:
    assert "consolidation" in load_shared("config-constants.json")
    assert len(load_shared("prompts.json")["prompts"]) == 8
