"""Local worker lifetimes, independent of the renewable job lease.

A unique owner takes its OS lock before claiming anything. The kernel drops
the lock on process death, including forced termination. Probing a different
owner never steals a live lock and does not rely on reusable process IDs.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import sys
from typing import BinaryIO


PREFIX = "memoweft-world-v2:"


def _lock(file: BinaryIO) -> None:
    file.seek(0)
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _path(db_path: Path, owner: str) -> Path:
    return db_path.with_name(db_path.name + ".workers") / (owner[len(PREFIX):] + ".lock")


def acquire_lifetime(db_path: Path, owner: str) -> BinaryIO:
    path = _path(db_path, owner)
    path.parent.mkdir(parents=True, exist_ok=True)
    file = path.open("a+b")
    try:
        if path.stat().st_size == 0:
            file.write(b"\0")
            file.flush()
        _lock(file)
        return file
    except BaseException:
        file.close()
        raise


def owner_is_gone(db_path: Path, owner: str) -> bool:
    if re.fullmatch(re.escape(PREFIX) + r"[0-9a-f]{32}", owner):
        # No lock file means a gracefully released lifetime. IDs are never
        # reused and the lock is always established before the first claim.
        path = _path(db_path, owner)
        try:
            with path.open("r+b") as file:
                _lock(file)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False  # Held, or unreadable: retain the ordinary lease.
    legacy = re.fullmatch(r"memoweft-world:(\d+):[0-9a-f]{32}", owner)
    if legacy is None:
        return False  # Custom/external owners retain their existing contract.
    pid = int(legacy[1])
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE, read-only
        if not handle:
            return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no PID
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass
    return False
