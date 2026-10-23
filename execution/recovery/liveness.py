"""Is a process (still) the one we recorded?

A bare pid is not an identity: pids are reused. We therefore also record a
creation token (Windows creation time, Linux start ticks) and treat a pid as
"the same process" only when the token matches. Where no token is available
(macOS) a live pid is reported but flagged as unverified, and nothing is ever
terminated on that basis.
"""

import ctypes
import os
import signal
import subprocess
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    pid: int
    created: int | None  # opaque token; equal tokens = same process


def _windows_info(pid: int) -> tuple[bool, int | None]:
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION

    if not handle:
        # Access denied still means it exists; anything else means it does not.
        return ctypes.get_last_error() == 5, None

    try:
        code = wintypes.DWORD()

        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
            return False, None

        creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
        ok = k32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_),
            ctypes.byref(kernel), ctypes.byref(user),
        )
        token = (creation.dwHighDateTime << 32) | creation.dwLowDateTime if ok else None
        return True, token
    finally:
        k32.CloseHandle(handle)


def _posix_info(pid: int) -> tuple[bool, int | None]:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        pass
    except OSError:
        return False, None

    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            fields = handle.read().rsplit(b")", 1)[1].split()

        if fields[0] == b"Z":
            return False, None  # zombie: already dead

        return True, int(fields[19])  # starttime in clock ticks
    except (OSError, IndexError, ValueError):
        return True, None


def identity_of(pid: int) -> Identity | None:
    """The identity of a live process, or None if no such process exists."""

    if not isinstance(pid, int) or pid <= 0:
        return None

    alive, token = _windows_info(pid) if os.name == "nt" else _posix_info(pid)
    return Identity(pid, token) if alive else None


def is_same_process(pid: int, created: int | None) -> bool:
    """Alive, and (when both tokens exist) the very process we recorded."""

    current = identity_of(pid)

    if current is None:
        return False

    if created is None or current.created is None:
        return True  # alive but unverifiable; callers must not act destructively

    return current.created == created


def is_verified(pid: int, created: int | None) -> bool:
    """Alive and positively identified as the recorded process."""

    current = identity_of(pid)
    return bool(current and created is not None and current.created == created)


def terminate_tree(pid: int, created: int | None, grace: float = 2.0) -> bool:
    """Kill a verified process tree; returns True when it is gone afterwards.

    Refuses (returns False) unless the identity token matches, so a recycled
    pid is never killed.
    """

    if not is_verified(pid, created):
        return not is_same_process(pid, created)

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, stdin=subprocess.DEVNULL, timeout=30,
        )
    else:
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

        deadline = time.monotonic() + grace

        while time.monotonic() < deadline and is_same_process(pid, created):
            time.sleep(0.05)

        if is_verified(pid, created):
            try:
                os.killpg(pid, signal.SIGKILL)
            except OSError:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    deadline = time.monotonic() + max(grace, 2.0)

    while time.monotonic() < deadline:
        if not is_same_process(pid, created):
            return True

        time.sleep(0.05)

    return not is_same_process(pid, created)
