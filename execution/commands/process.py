"""Hardened process runner.

Guarantees (see docs/EXECUTION_CONTRACT.md §2.5):
- argv only, never a shell; the child gets stdin closed and a scrubbed,
  explicit environment with its own temp directory.
- cwd must exist (and sit inside `workspace` when one is given).
- Wall-clock timeout and cooperative cancellation both kill the *whole
  process tree*, and the result says whether anything survived.
  Windows: the child runs in a Job Object (kill-on-close, active-process
  and memory limits). POSIX: its own session / process group.
- Output is bounded in memory (head + tail) and spooled to a log file
  when larger; a process that fills the pipe cannot stall or exhaust us.
- Processes still running when the main process exits are treated as
  orphans and killed.
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from execution.commands.capture import BoundedCapture
from execution.commands.env import EnvironmentError_, build_environment, redact
from execution.recovery.liveness import identity_of

DEFAULT_LOG_DIR = Path(tempfile.gettempdir()) / "aseos-command-logs"
_BATCH_SUFFIXES = (".cmd", ".bat")
_SAFE_BATCH_ARG = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "_-.,:=+/\\@ "
)


@dataclass(frozen=True)
class ProcessLimits:
    timeout_seconds: float = 60.0
    max_memory_bytes: int | None = 4 * 1024**3  # per process (Windows job)
    max_processes: int | None = 256  # active processes in the tree (Windows job)
    head_bytes: int = 100_000
    tail_bytes: int = 100_000
    max_spool_bytes: int = 100 * 1024 * 1024
    kill_grace_seconds: float = 2.0


@dataclass
class ProcessResult:
    status: str  # PASS | FAIL | TIMEOUT | CANCELLED | ERROR
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error: str | None = None
    error_kind: str | None = None  # not_found | invalid_cwd | invalid_env | start_failed | ...
    stdout_total: int = 0
    stderr_total: int = 0
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    output_files: dict[str, str] = field(default_factory=dict)
    spool_truncated: bool = False
    tree: dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    argv: list[str] = field(default_factory=list)
    cwd: str = ""

    @property
    def truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated


# ------------------------------------------------------------- Windows job

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _k32.TerminateJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        wintypes.LPVOID,
    ]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL
    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

    _JOB_ACTIVE_PROCESS = 0x8
    _JOB_PROCESS_MEMORY = 0x100
    _JOB_DIE_ON_UNHANDLED_EXCEPTION = 0x400
    _JOB_KILL_ON_CLOSE = 0x2000
    _CREATE_SUSPENDED = 0x4
    _CREATE_NEW_PROCESS_GROUP = 0x200
    _CREATE_NO_WINDOW = 0x08000000

    class _WindowsJob:
        def __init__(self, limits: ProcessLimits) -> None:
            self.handle = _k32.CreateJobObjectW(None, None)

            if not self.handle:
                raise OSError("CreateJobObject failed")

            info = _ExtendedLimit()
            flags = _JOB_KILL_ON_CLOSE | _JOB_DIE_ON_UNHANDLED_EXCEPTION

            if limits.max_processes:
                flags |= _JOB_ACTIVE_PROCESS
                info.BasicLimitInformation.ActiveProcessLimit = limits.max_processes

            if limits.max_memory_bytes:
                flags |= _JOB_PROCESS_MEMORY
                info.ProcessMemoryLimit = limits.max_memory_bytes

            info.BasicLimitInformation.LimitFlags = flags

            if not _k32.SetInformationJobObject(
                self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                self.close()
                raise OSError("SetInformationJobObject failed")

        def assign(self, process_handle: int) -> bool:
            return bool(_k32.AssignProcessToJobObject(self.handle, process_handle))

        def active(self) -> int | None:
            info = _Accounting()

            if not _k32.QueryInformationJobObject(
                self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None
            ):
                return None

            return int(info.ActiveProcesses)

        def terminate(self) -> None:
            _k32.TerminateJobObject(self.handle, 1)

        def close(self) -> None:
            if self.handle:
                _k32.CloseHandle(self.handle)
                self.handle = None

    def _resume(process_handle: int) -> None:
        _ntdll.NtResumeProcess(process_handle)


# ---------------------------------------------------------------- helpers


def _fail(kind: str, message: str, request: dict, started: float) -> ProcessResult:
    return ProcessResult(
        status="ERROR",
        error=message,
        error_kind=kind,
        stderr=message,
        duration_ms=int((time.perf_counter() - started) * 1000),
        argv=request["argv"],
        cwd=request["cwd"],
        run_id=request["run_id"],
    )


def _resolve_executable(name: str, env: Mapping[str, str]) -> str | None:
    if os.sep in name or (os.altsep and os.altsep in name):
        path = Path(name)
        return str(path) if path.is_file() else None

    return shutil.which(name, path=env.get("PATH"))


def _unsafe_batch_arguments(argv: list[str]) -> bool:
    return any(not set(arg) <= _SAFE_BATCH_ARG for arg in argv[1:])


def _pump(stream, capture: BoundedCapture) -> None:
    try:
        while True:
            chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)

            if not chunk:
                break

            capture.feed(chunk)
    except (OSError, ValueError):
        pass


def _kill_tree(proc: subprocess.Popen, job, limits: ProcessLimits) -> dict[str, Any]:
    """Kill the child and everything it started; report what survived."""

    info: dict[str, Any] = {"killed": True, "mechanism": "none", "leftover": None}
    deadline = time.monotonic() + limits.kill_grace_seconds

    if os.name == "nt":
        if job is not None:
            info["mechanism"] = "job_object"
            job.terminate()

            while time.monotonic() < deadline:
                if job.active() == 0:
                    break

                time.sleep(0.02)

            info["leftover"] = job.active()
        else:
            info["mechanism"] = "taskkill"
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
            )

        try:
            proc.wait(timeout=limits.kill_grace_seconds)
        except subprocess.TimeoutExpired:
            pass

        return info

    info["mechanism"] = "process_group"
    group = proc.pid

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            break

        wait_until = time.monotonic() + (
            limits.kill_grace_seconds if sig == signal.SIGTERM else 2.0
        )

        while time.monotonic() < wait_until:
            try:
                proc.wait(timeout=0.02)
            except subprocess.TimeoutExpired:
                pass

            try:
                os.killpg(group, 0)
            except ProcessLookupError:
                break
        else:
            continue

        break

    try:
        os.killpg(group, 0)
        info["leftover"] = 1
    except ProcessLookupError:
        info["leftover"] = 0

    return info


def _orphan_check(proc, job, limits) -> dict[str, Any] | None:
    """After the main process exited: kill anything it left running."""

    if os.name == "nt":
        if job is None or not job.active():
            return None
    else:
        try:
            os.killpg(proc.pid, 0)
        except ProcessLookupError:
            return None

    info = _kill_tree(proc, job, limits)
    info["orphans"] = True
    return info


# ------------------------------------------------------------------ runner


def run_process(
    argv: list[str],
    cwd: str | Path,
    *,
    env_extra: Mapping[str, str] | None = None,
    limits: ProcessLimits | None = None,
    cancel: threading.Event | None = None,
    log_dir: Path | None = None,
    workspace: Path | None = None,
    sandbox: Any = None,
    network: bool = False,
    on_start: Callable[[int, int | None, str], None] | None = None,
) -> ProcessResult:
    limits = limits or ProcessLimits()
    started = time.perf_counter()
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    request = {
        "argv": [str(a) for a in argv] if isinstance(argv, list) else [],
        "cwd": str(cwd),
        "run_id": run_id,
    }

    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(a, str) and "\0" not in a for a in argv)
        or not argv[0].strip()
    ):
        return _fail("invalid_argv", "argv must be a non-empty list of strings", request, started)

    try:
        directory = Path(cwd).resolve()
    except (OSError, RuntimeError):
        return _fail("invalid_cwd", f"Invalid working directory: {cwd}", request, started)

    if not directory.is_dir():
        return _fail("invalid_cwd", f"Working directory does not exist: {cwd}", request, started)

    if workspace is not None and not directory.is_relative_to(Path(workspace).resolve()):
        return _fail("invalid_cwd", "Working directory is outside the workspace", request, started)

    temp_dir = Path(tempfile.mkdtemp(prefix="aseos-run-"))
    job = None
    prepared = None
    needs_cleanup = True  # until the sandboxed program ended by itself
    proc: subprocess.Popen | None = None
    captures: list[BoundedCapture] = []

    try:
        try:
            env = build_environment(temp_dir, env_extra)
        except EnvironmentError_ as exc:
            return _fail("invalid_env", str(exc), request, started)

        popen_env = env

        if sandbox is not None:
            usable, why = sandbox.availability()

            if not usable:
                return _fail(
                    "sandbox_unavailable", f"Sandbox unavailable: {why}", request, started
                )

            try:
                prepared = sandbox.prepare(
                    list(argv),
                    cwd=directory,
                    workspace=Path(workspace).resolve() if workspace else directory,
                    env=env,
                    limits=limits,
                    network=network,
                    run_id=run_id,
                )
            except Exception as exc:  # SandboxError or a backend bug: fail closed
                return _fail("sandbox_refused", f"Sandbox refused: {exc}", request, started)

            command = prepared.command
            popen_env = prepared.env
        else:
            executable = _resolve_executable(argv[0], env)

            if executable is None:
                return _fail("not_found", f"Executable not found: {argv[0]}", request, started)

            command = [executable, *argv[1:]]

            if executable.lower().endswith(_BATCH_SUFFIXES) and _unsafe_batch_arguments(command):
                return _fail(
                    "unsafe_argument",
                    "Arguments contain characters that are unsafe for a batch file",
                    request,
                    started,
                )

        popen_kwargs: dict[str, Any] = {}

        if os.name == "nt":
            flags = _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW

            try:
                job = _WindowsJob(limits)
                flags |= _CREATE_SUSPENDED
            except OSError:
                job = None

            popen_kwargs["creationflags"] = flags
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(
                command,
                cwd=str(directory),
                env=popen_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **popen_kwargs,
            )
        except FileNotFoundError:
            return _fail("not_found", f"Executable not found: {argv[0]}", request, started)
        except PermissionError as exc:
            return _fail("permission", f"Permission denied: {exc}", request, started)
        except OSError as exc:
            return _fail("start_failed", f"Could not start process: {exc}", request, started)

        if on_start is not None:
            try:
                identity = identity_of(proc.pid)
                on_start(proc.pid, identity.created if identity else None, run_id)
            except Exception:
                pass  # recording must never affect the run

        if os.name == "nt" and job is not None:
            handle = int(proc._handle)  # type: ignore[attr-defined]

            if not job.assign(handle):
                job.close()
                job = None

            _resume(handle)
        elif os.name != "nt":
            _apply_posix_limits(proc.pid)

        log_root = log_dir or DEFAULT_LOG_DIR
        captures = [
            BoundedCapture(
                limits.head_bytes, limits.tail_bytes,
                log_root / f"{run_id}.{name}.log", limits.max_spool_bytes,
            )
            for name in ("stdout", "stderr")
        ]
        readers = [
            threading.Thread(target=_pump, args=(stream, capture), daemon=True)
            for stream, capture in zip((proc.stdout, proc.stderr), captures)
        ]

        for reader in readers:
            reader.start()

        status, tree = _supervise(proc, job, limits, cancel)

        if prepared is not None and status != "EXITED":
            prepared.terminate()  # killing the client does not stop a container
        orphan = _orphan_check(proc, job, limits) if status == "EXITED" else None

        for reader in readers:
            reader.join(timeout=2.0)

        if any(reader.is_alive() for reader in readers):
            # A grandchild still holds a pipe: finish it off.
            orphan = orphan or _kill_tree(proc, job, limits) | {"orphans": True}

            for reader in readers:
                reader.join(timeout=2.0)

        tree = tree or orphan or {}

        if prepared is not None:
            tree = {**tree, "sandbox": prepared.description, **prepared.details}

        if orphan and tree is not orphan:
            tree = {**tree, "orphans_killed": True}

        exit_code = proc.poll()
        final = status

        if status == "EXITED":
            final = "PASS" if exit_code == 0 else "FAIL"

        for capture in captures:
            capture.close()

        out, err = captures
        needs_cleanup = final not in ("PASS", "FAIL")
        files = {
            name: str(capture.spool_path)
            for name, capture in zip(("stdout", "stderr"), captures)
            if capture.spooled and capture.spool_path is not None
        }
        return ProcessResult(
            status=final,
            exit_code=exit_code,
            stdout=redact(out.text()),
            stderr=redact(err.text()),
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout_total=out.total,
            stderr_total=err.total,
            stdout_truncated=out.truncated,
            stderr_truncated=err.truncated,
            output_files=files,
            spool_truncated=out.spool_truncated or err.spool_truncated,
            tree=tree,
            run_id=run_id,
            argv=request["argv"],
            cwd=str(directory),
        )
    finally:
        if prepared is not None and needs_cleanup:
            prepared.cleanup()

        for capture in captures:
            capture.close()

        if proc is not None:
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream:
                        stream.close()
                except OSError:
                    pass

        if job is not None:
            job.close()

        shutil.rmtree(temp_dir, ignore_errors=True)


def _supervise(
    proc: subprocess.Popen,
    job,
    limits: ProcessLimits,
    cancel: threading.Event | None,
) -> tuple[str, dict[str, Any] | None]:
    deadline = time.monotonic() + limits.timeout_seconds

    while True:
        try:
            proc.wait(timeout=0.05)
            return "EXITED", None
        except subprocess.TimeoutExpired:
            pass

        if cancel is not None and cancel.is_set():
            return "CANCELLED", _kill_tree(proc, job, limits)

        if time.monotonic() >= deadline:
            return "TIMEOUT", _kill_tree(proc, job, limits)


def _apply_posix_limits(pid: int) -> None:
    """Best effort: no core dumps, bounded file size (Linux prlimit)."""

    try:
        import resource

        prlimit = getattr(resource, "prlimit", None)

        if prlimit is not None:
            prlimit(pid, resource.RLIMIT_CORE, (0, 0))
            prlimit(pid, resource.RLIMIT_FSIZE, (1024**3, 1024**3))
    except (ImportError, OSError, ValueError):
        pass


def python_executable() -> str:
    return sys.executable
