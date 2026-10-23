"""Shared by the crash-injection tests: a gateway builder and a child process
that dies at an exact point (os._exit skips every cleanup handler, like a
power cut or a kill -9 would)."""

import os
import subprocess
import sys
import time
from pathlib import Path

from execution.artifacts import ArtifactStore
from execution.commands.policy import CommandPolicy, CommandRule, Match
from execution.gateway import ExecutionGateway
from execution.gateway.capabilities import CAP_GIT_WRITE, CAP_SHELL_RUN, DEFAULT_CAPABILITIES
from execution.gateway.command_tools import RunCommandTool
from execution.gateway.tools import default_tools

ROOT = Path(__file__).resolve().parents[2]
CAPS = DEFAULT_CAPABILITIES | {CAP_SHELL_RUN, CAP_GIT_WRITE}
PY_POLICY = CommandPolicy(
    rules=[CommandRule("py", frozenset({"python"}), lambda args: Match("build"))]
)


def tools():
    return [t for t in default_tools() if t.spec.name != "shell.run"] + [
        RunCommandTool(policy=PY_POLICY)
    ]


def make_gateway(workspace, state_dir, store_dir, **kw):
    return ExecutionGateway(
        str(workspace),
        tools=tools(),
        granted_capabilities=CAPS,
        artifact_store=ArtifactStore(store_dir),
        state_dir=state_dir,
        **kw,
    )


CHILD = r'''
import asyncio, os, sys
sys.path.insert(0, {root!r})
from execution.tests.crash_helpers import make_gateway
from runtime.schemas.action import Action

workspace, state_dir, store_dir, mode = sys.argv[1:5]
gateway = make_gateway(workspace, state_dir, store_dir, lease=True, owner_id="crashing-owner")


def die():
    os._exit(7)


if mode == "patch_none":
    import execution.gateway.patch_tools as pt
    pt.apply_plan = lambda plan, action_id="": die()
elif mode == "patch_partial":
    import execution.gateway.patch_tools as pt
    from execution.patches.fileio import atomic_write_bytes

    def partial(plan, action_id=""):
        first = plan.files[0]
        atomic_write_bytes(first.target, first.after)
        die()

    pt.apply_plan = partial
elif mode == "patch_all":
    import execution.gateway.patch_tools as pt
    real = pt.apply_plan

    def finished(plan, action_id=""):
        real(plan, action_id=action_id)
        die()

    pt.apply_plan = finished
elif mode == "write_all":
    import execution.gateway.fs_tools as ft
    real_write = ft.WriteTool._atomic_write

    def after_write(self, target, payload, previous):
        real_write(self, target, payload, previous)
        die()

    ft.WriteTool._atomic_write = after_write
elif mode == "commit_none":
    import execution.gateway.git_tools as gt
    gt.commit_paths = lambda *a, **k: die()
elif mode == "commit_done":
    import execution.gateway.git_tools as gt
    real_commit = gt.commit_paths

    def committed(*a, **k):
        real_commit(*a, **k)
        die()

    gt.commit_paths = committed

_patch_file = os.path.join(workspace, "..", "change.patch")
PATCH = open(_patch_file).read() if os.path.exists(_patch_file) else ""
ACTIONS = {{
    "patch_none": ("APPLY_PATCH", {{"patch": PATCH}}),
    "patch_partial": ("APPLY_PATCH", {{"patch": PATCH}}),
    "patch_all": ("APPLY_PATCH", {{"patch": PATCH}}),
    "write_all": ("WRITE", {{"path": "new.txt", "content": "fresh\n"}}),
    "commit_none": ("GIT_COMMIT", {{"message": "intended commit", "paths": ["a.txt"]}}),
    "commit_done": ("GIT_COMMIT", {{"message": "intended commit", "paths": ["a.txt"]}}),
    "sleep": ("RUN_COMMAND", {{"command": ["python", "-c", "import time; print('up', flush=True); time.sleep(300)"]}}),
}}
kind, payload = ACTIONS[mode]
asyncio.run(gateway.execute(Action(id="doomed-1", type=kind, payload=payload)))
sys.exit(0)  # reaching here means the injected crash did not happen
'''


def run_crashing_child(workspace, state_dir, store_dir, mode, *, timeout=90):
    """Run a gateway in a separate process that dies mid-action; returns its exit code."""

    script = Path(workspace).parent / "crash_child.py"
    script.write_text(CHILD.format(root=str(ROOT)), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(script), str(workspace), str(state_dir), str(store_dir), mode],
        capture_output=True, text=True, timeout=timeout,
    )
    return done


def start_child(workspace, state_dir, store_dir, mode):
    script = Path(workspace).parent / "crash_child.py"
    script.write_text(CHILD.format(root=str(ROOT)), encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, str(script), str(workspace), str(state_dir), str(store_dir), mode],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def wait_for(predicate, timeout=60.0, interval=0.1):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        value = predicate()

        if value:
            return value

        time.sleep(interval)

    raise AssertionError("condition not reached in time")


def dead_pid() -> int:
    """The pid of a process that has already exited."""

    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid
