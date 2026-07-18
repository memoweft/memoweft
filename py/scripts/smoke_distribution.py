"""Build and verify wheel/sdist without relying on the MemoWeft monorepo layout.

Run with ``uv run python scripts/smoke_distribution.py`` from ``py/``.
"""
from __future__ import annotations

import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(args, check=True, cwd=cwd or PROJECT_ROOT)


def artifact_names(path: Path) -> set[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())
    with tarfile.open(path) as archive:
        return {member.name for member in archive.getmembers() if member.isfile()}


def assert_artifact_contents(wheel: Path, sdist: Path) -> None:
    wheel_names = artifact_names(wheel)
    sdist_names = artifact_names(sdist)
    required_wheel = {
        "memoweft/_shared_data/config-constants.json",
        "memoweft/_shared_data/prompts.json",
    }
    assert required_wheel <= wheel_names, wheel_names
    assert any(name.endswith(".dist-info/licenses/LICENSE") for name in wheel_names)
    assert any(name.endswith("/LICENSE") for name in sdist_names)
    assert any(name.endswith("/src/memoweft/_shared_data/parity/confidence.json") for name in sdist_names)
    assert not any(name.endswith("uv.lock") for name in sdist_names)


def smoke_installed_wheel(wheel: Path, temp_dir: Path) -> None:
    environment = temp_dir / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    run(str(python), "-m", "pip", "install", str(wheel), cwd=temp_dir)
    smoke = (
        "import memoweft; "
        "from memoweft.llm.prompts import prompt_versions; "
        "assert memoweft.CONFIG.consolidation.support_step > 0; "
        "assert len(prompt_versions()) == 8"
    )
    run(str(python), "-c", smoke, cwd=temp_dir)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="memoweft-dist-") as temp:
        temp_dir = Path(temp)
        output = temp_dir / "dist"
        run("uv", "build", "--out-dir", str(output))
        wheel = next(output.glob("*.whl"))
        sdist = next(output.glob("*.tar.gz"))
        assert_artifact_contents(wheel, sdist)
        smoke_installed_wheel(wheel, temp_dir)


if __name__ == "__main__":
    main()
