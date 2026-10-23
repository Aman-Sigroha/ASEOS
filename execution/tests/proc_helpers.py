"""Helpers for tests that start, watch and kill real processes."""

import os
import sys
import threading
import time
from pathlib import Path

PY = sys.executable

SPAWN_CHILD_AND_SLEEP = (
    "import subprocess, sys, time, pathlib\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "pathlib.Path('child.pid').write_text(str(child.pid))\n"
    "time.sleep(120)\n"
)

SPAWN_ORPHAN_AND_EXIT = (
    "import subprocess, sys, pathlib\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "pathlib.Path('child.pid').write_text(str(child.pid))\n"
)


def pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)

        if not handle:
            return False

        try:
            code = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

    # A zombie still answers kill(0); treat it as dead.
    try:
        status = Path(f"/proc/{pid}/stat").read_text().split()[2]
        return status != "Z"
    except OSError:
        return True


def wait_for_file(path: Path, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return True

        time.sleep(0.05)

    return False


def cancel_when_file_appears(path: Path, event: threading.Event) -> threading.Thread:
    def watch():
        wait_for_file(path)
        event.set()

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def wait_until_dead(pid: int, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True

        time.sleep(0.05)

    return not pid_alive(pid)
