"""Benchmarks for the execution layer: `python -m execution.bench [--quick] [--json FILE]`.

Each benchmark builds its own throwaway repository, runs the operation several
times through the real gateway (journal, artifact store and records on, as in
production) and reports milliseconds. Numbers depend on the machine, the disk
and antivirus; compare runs on the same machine, and read the `overhead` rows
(gateway minus the same work done directly) rather than the absolute times.

`execution/tests/test_performance.py` runs the same functions at small sizes
with generous budgets, to catch an order-of-magnitude regression, not to
measure.
"""

import argparse
import asyncio
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from execution.artifacts import ArtifactStore, Owner
from execution.commands.policy import CommandPolicy, CommandRule, Match
from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_GIT_WRITE, CAP_SHELL_RUN, CAP_WORKTREE_MANAGE, DEFAULT_CAPABILITIES
from execution.gateway.command_tools import RunCommandTool
from execution.gateway.tools import default_tools
from execution.tests import qualification_repos as repos
from execution.worktrees.manager import WorktreeManager
from runtime.schemas.action import Action

PY_POLICY = CommandPolicy(
    rules=[CommandRule("py", frozenset({"python"}), lambda args: Match("build"))]
)
CAPS = DEFAULT_CAPABILITIES | {CAP_SHELL_RUN, CAP_GIT_WRITE, CAP_WORKTREE_MANAGE}


# ------------------------------------------------------------------ helpers


def stats(samples_ms: list[float]) -> dict[str, float]:
    ordered = sorted(samples_ms)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {
        "n": len(ordered),
        "p50_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(p95, 2),
        "max_ms": round(ordered[-1], 2),
    }


def rss_bytes() -> int:
    """Resident memory of this process (0 if it cannot be read)."""

    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.c_void_p(handle), ctypes.byref(counters), counters.cb
            )
            return int(counters.WorkingSetSize)

        with open("/proc/self/statm") as handle:
            return int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except Exception:
        return 0


class PeakMemory:
    """Samples this process's memory while a block runs."""

    def __init__(self, interval: float = 0.02) -> None:
        self.interval = interval
        self.start = rss_bytes()
        self.peak = self.start
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak = max(self.peak, rss_bytes())
            self._stop.wait(self.interval)

    def __enter__(self) -> "PeakMemory":
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, rss_bytes())

    @property
    def growth_mb(self) -> float:
        return round((self.peak - self.start) / 1e6, 1)


def make_gateway(root: Path, work: Path, **kw: Any) -> ExecutionGateway:
    tools = [t for t in default_tools() if t.spec.name != "shell.run"] + [RunCommandTool(policy=PY_POLICY)]
    return ExecutionGateway(
        str(root), tools=tools, granted_capabilities=CAPS,
        artifact_store=ArtifactStore(work / "store"), state_dir=work / "state", **kw,
    )


async def repeat(gw: ExecutionGateway, n: int, make: Callable[[int], Action]) -> list[float]:
    samples = []

    for index in range(n):
        action = make(index)
        started = time.perf_counter()
        result = await gw.execute(action)
        samples.append((time.perf_counter() - started) * 1000)

        if not result.success:
            raise RuntimeError(f"{action.type} failed during a benchmark: {result.stderr[:200]}")

    return samples


def direct_ms(argv: list[str], cwd: Path, n: int) -> list[float]:
    samples = []

    for _ in range(n):
        started = time.perf_counter()
        subprocess.run(argv, cwd=cwd, capture_output=True, check=False)
        samples.append((time.perf_counter() - started) * 1000)

    return samples


# -------------------------------------------------------------- benchmarks


async def bench_commands(root: Path, work: Path, n: int) -> dict[str, Any]:
    gw = make_gateway(root, work)
    gate = await repeat(gw, n, lambda i: Action(id=f"c{i}", type="RUN_COMMAND", payload={"command": ["python", "-c", "pass"]}))
    direct = direct_ms([sys.executable, "-c", "pass"], root, n)
    return {
        "gateway": stats(gate),
        "direct_subprocess": stats(direct),
        "overhead_p50_ms": round(statistics.median(gate) - statistics.median(direct), 2),
    }


async def bench_reads_and_patches(root: Path, work: Path, n: int) -> dict[str, Any]:
    gw = make_gateway(root, work)
    (root / "small.txt").write_text("x" * 10_000, encoding="utf-8")
    (root / "large.txt").write_text(("y" * 79 + "\n") * 11_000, encoding="utf-8")  # ~880 KB
    big_lines = "".join(f"line {i}\n" for i in range(5000))
    (root / "patchme.txt").write_text(big_lines, encoding="utf-8")

    def patch(i: int) -> Action:
        # flip one line back and forth so every run applies cleanly
        old, new = ("line 2500", "line 2500 changed") if i % 2 == 0 else ("line 2500 changed", "line 2500")
        text = (
            "--- a/patchme.txt\n+++ b/patchme.txt\n@@ -2500,3 +2500,3 @@\n"
            f" line 2499\n-{old}\n+{new}\n line 2501\n"
        )
        return Action(id=f"p{i}", type="APPLY_PATCH", payload={"patch": text})

    return {
        "read_10kb": stats(await repeat(gw, n, lambda i: Action(id=f"r{i}", type="READ", payload={"path": "small.txt"}))),
        "read_880kb": stats(await repeat(gw, n, lambda i: Action(id=f"R{i}", type="READ", payload={"path": "large.txt"}))),
        "search_tree": stats(await repeat(gw, max(3, n // 5), lambda i: Action(id=f"s{i}", type="SEARCH", payload={"query": "line 4999"}))),
        "patch_5000_line_file": stats(await repeat(gw, n, patch)),
        "write_10kb": stats(await repeat(gw, n, lambda i: Action(id=f"w{i}", type="WRITE", payload={"path": f"out/w{i}.txt", "content": "z" * 10_000}))),
    }


async def bench_git(root: Path, work: Path, n: int, files: int) -> dict[str, Any]:
    gw = make_gateway(root, work)
    clean_status = stats(await repeat(gw, n, lambda i: Action(id=f"gs{i}", type="GIT_STATUS")))

    for index in range(min(50, files)):
        path = root / f"pkg{index % 40}" / f"mod{index}.txt"
        path.write_text(path.read_text(encoding="utf-8") + "edit\n", encoding="utf-8")

    return {
        "tracked_files": files,
        "status_clean": clean_status,
        "status_50_modified": stats(await repeat(gw, n, lambda i: Action(id=f"gs2{i}", type="GIT_STATUS"))),
        "diff_50_modified": stats(await repeat(gw, n, lambda i: Action(id=f"gd{i}", type="GIT_DIFF"))),
        "workspace_version": stats(await _timed_sync(n, gw.workspace_version)),
    }


async def _timed_sync(n: int, fn: Callable[[], Any]) -> list[float]:
    samples = []

    for _ in range(n):
        started = time.perf_counter()
        await asyncio.to_thread(fn)
        samples.append((time.perf_counter() - started) * 1000)

    return samples


async def bench_worktrees(root: Path, work: Path, n: int, files: int) -> dict[str, Any]:
    manager = WorktreeManager(root)
    created, removed = [], []

    for index in range(n):
        started = time.perf_counter()
        record = manager.create(f"bench-{index}")
        created.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        manager.remove(record.worktree_id)
        removed.append((time.perf_counter() - started) * 1000)

    return {"tracked_files": files, "create": stats(created), "remove": stats(removed)}


async def bench_tests(root: Path, work: Path, n: int) -> dict[str, Any]:
    gw = make_gateway(root, work)
    gate = await repeat(gw, n, lambda i: Action(id=f"t{i}", type="RUN_TEST"))
    direct = direct_ms([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], root, n)
    return {
        "gateway_test_run": stats(gate),
        "direct_pytest": stats(direct),
        "overhead_p50_ms": round(statistics.median(gate) - statistics.median(direct), 2),
    }


async def bench_memory_under_output_flood(root: Path, work: Path, megabytes: int) -> dict[str, Any]:
    gw = make_gateway(root, work)
    code = f"import sys; [sys.stdout.write('x' * 99999 + chr(10)) for _ in range({megabytes * 10})]"
    started = time.perf_counter()

    with PeakMemory() as memory:
        result = await gw.execute(Action(id="flood", type="RUN_COMMAND", payload={"command": ["python", "-c", code], "timeout_seconds": 300}))

    return {
        "printed_mb": megabytes,
        "success": result.success,
        "result_stdout_chars": len(result.stdout),
        "result_truncated": result.truncated,
        "seconds": round(time.perf_counter() - started, 2),
        "memory_growth_mb": memory.growth_mb,
    }


async def bench_concurrency(root: Path, work: Path, width: int) -> dict[str, Any]:
    gw = make_gateway(root, work)
    (root / "small.txt").write_text("x" * 10_000, encoding="utf-8")

    def reads(prefix: str) -> list[Awaitable[Any]]:
        return [gw.execute(Action(id=f"{prefix}{i}", type="READ", payload={"path": "small.txt"})) for i in range(width * 4)]

    started = time.perf_counter()
    out = await asyncio.gather(*reads("cr"))
    read_ms = (time.perf_counter() - started) * 1000

    def commands(prefix: str) -> list[Awaitable[Any]]:
        return [
            gw.execute(Action(id=f"{prefix}{i}", type="RUN_COMMAND", payload={"command": ["python", "-c", "import time; time.sleep(0.5)"]}))
            for i in range(width)
        ]

    started = time.perf_counter()
    ran = await asyncio.gather(*commands("cc"))
    command_s = time.perf_counter() - started

    def writes() -> list[Awaitable[Any]]:
        return [gw.execute(Action(id=f"cw{i}", type="WRITE", payload={"path": f"cw/{i}.txt", "content": "x\n"})) for i in range(width * 2)]

    started = time.perf_counter()
    wrote = await asyncio.gather(*writes())
    write_ms = (time.perf_counter() - started) * 1000

    return {
        "width": width,
        "parallel_reads": {"count": len(out), "ok": all(r.success for r in out), "total_ms": round(read_ms, 1)},
        "parallel_commands_0_5s_each": {"count": width, "ok": all(r.success for r in ran), "total_s": round(command_s, 2)},
        "serialized_writes": {"count": len(wrote), "ok": all(r.success for r in wrote), "total_ms": round(write_ms, 1)},
    }


def bench_artifacts(work: Path, count: int) -> dict[str, Any]:
    store = ArtifactStore(work / "artifacts")
    owner = Owner("bench-workspace", "task", "action", "bench")
    payload = ("log line with some text\n" * 40)[:1000]
    started = time.perf_counter()
    ids = [store.put(payload, kind="command_log", owner=owner).id for _ in range(count)]
    put_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    found = store.find(workspace="bench-workspace", limit=count)
    find_cold_ms = (time.perf_counter() - started) * 1000  # files were just written
    started = time.perf_counter()
    store.find(workspace="bench-workspace", limit=count)
    find_warm_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    store.read(ids[count // 2], workspace="bench-workspace")
    read_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    audit = store.audit()
    audit_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    store.gc()
    gc_ms = (time.perf_counter() - started) * 1000
    disk = sum(p.stat().st_size for p in (work / "artifacts").rglob("*") if p.is_file())
    return {
        "artifacts": count,
        "put_each_ms": round(put_ms / count, 2),
        "find_all_cold_ms": round(find_cold_ms, 1),
        "find_all_warm_ms": round(find_warm_ms, 1),
        "found": len(found),
        "read_one_ms": round(read_ms, 2),
        "audit_ms": round(audit_ms, 1),
        "audit_clean": audit.get("clean"),
        "gc_ms": round(gc_ms, 1),
        "disk_mb": round(disk / 1e6, 2),
    }


# --------------------------------------------------------------- the runner


def run_all(quick: bool = False) -> dict[str, Any]:
    n = 5 if quick else 20
    files = 300 if quick else 2000
    flood = 20 if quick else 100
    results: dict[str, Any] = {
        "machine": {"os": platform.platform(), "python": platform.python_version(), "quick": quick},
    }

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        def section(name: str, fn: Callable[[], Any]) -> None:
            try:
                results[name] = fn()
            except Exception as exc:
                results[name] = {"error": f"{type(exc).__name__}: {exc}"}

        project = repos.python_project(base / "py")
        section("commands", lambda: asyncio.run(bench_commands(project, base / "w1", n)))
        section("tests", lambda: asyncio.run(bench_tests(project, base / "w2", max(3, n // 2))))
        section("files", lambda: asyncio.run(bench_reads_and_patches(repos.python_project(base / "py2"), base / "w3", n)))
        big = repos.big_repo(base / "big", files=files)
        section("git", lambda: asyncio.run(bench_git(big, base / "w4", max(3, n // 4), files)))
        section("worktrees", lambda: asyncio.run(bench_worktrees(big, base / "w5", 3 if quick else 5, files)))
        section("memory_output_flood", lambda: asyncio.run(bench_memory_under_output_flood(repos.python_project(base / "py3"), base / "w6", flood)))
        section("concurrency", lambda: asyncio.run(bench_concurrency(repos.python_project(base / "py4"), base / "w7", 8)))
        section("artifacts", lambda: bench_artifacts(base / "w8", 500 if quick else 3000))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark the execution layer")
    parser.add_argument("--quick", action="store_true", help="smaller sizes, fewer repetitions")
    parser.add_argument("--json", metavar="FILE", help="also write the results as JSON")
    args = parser.parse_args()
    results = run_all(quick=args.quick)
    text = json.dumps(results, indent=2)
    print(text)

    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
