"""Container sandbox (Docker or Podman).

What the container boundary gives, by construction of the `run` command:
- Filesystem: only the task workspace is mounted (at /workspace). No other
  host path, no Docker socket, no home directory, no `.git` of the main
  repository. The root filesystem is read-only; /tmp is a small tmpfs.
- Network: `--network none` unless the caller was granted network access.
- Privileges: all capabilities dropped, `no-new-privileges`, non-root user,
  never `--privileged`, never host PID/IPC/network namespaces.
- Resources: memory (no swap), process count, CPUs, open files, no core dumps.
- Environment: *only* the values passed with `-e` below. The host
  environment is never forwarded, so host secrets cannot reach the program.

What it does not give: the image must already contain the project's
toolchain and dependencies (nothing is installed on the fly), and a
container shares the host kernel (use a VM-based runtime for hostile code).
"""

import os
import re
import shutil
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from execution.commands.env import CALLER_ALLOWED, FORCED
from execution.sandbox.base import PreparedCommand, Sandbox, SandboxError

_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@\-]{0,254}$")
_USER = re.compile(r"^\d{1,10}(:\d{1,10})?$")
_HOST_ENV_FOR_CLIENT = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_RUNTIME_DIR",
    "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY", "CONTAINER_HOST",
)
_AVAILABILITY_TTL = 30.0


@dataclass(frozen=True)
class ContainerConfig:
    engine: str = "docker"  # or "podman"
    image: str = "python:3.12-slim"
    user: str = "65532:65532"
    cpus: float | None = 2.0
    tmpfs_size: str = "256m"
    tmpfs_noexec: bool = True
    read_only_workspace: bool = False
    default_memory_bytes: int = 2 * 1024**3
    default_pids: int = 256
    stop_timeout_seconds: int = 2
    # Explicit command for the engine client (e.g. a full path). Defaults to
    # `engine` looked up on PATH.
    engine_command: tuple[str, ...] | None = None


class ContainerSandbox(Sandbox):
    name = "container"

    def __init__(self, config: ContainerConfig | None = None) -> None:
        self.config = config or ContainerConfig()

        if self.config.engine not in ("docker", "podman"):
            raise ValueError("engine must be 'docker' or 'podman'")

        if not _IMAGE.match(self.config.image):
            raise ValueError(f"Invalid image reference: {self.config.image!r}")

        if not _USER.match(self.config.user):
            raise ValueError("user must be numeric 'uid[:gid]'")

        if self.config.engine_command is not None and not (
            self.config.engine_command
            and all(isinstance(p, str) and p for p in self.config.engine_command)
        ):
            raise ValueError("engine_command must be a non-empty tuple of strings")

        self._checked_at = 0.0
        self._status: tuple[bool, str] = (False, "not checked")

    def _engine(self) -> list[str] | None:
        if self.config.engine_command is not None:
            return list(self.config.engine_command)

        found = shutil.which(self.config.engine, path=os.environ.get("PATH"))
        return [found] if found else None

    # ----------------------------------------------------- availability

    def _host_env(self) -> dict[str, str]:
        return {k: v for k, v in os.environ.items() if k.upper() in _HOST_ENV_FOR_CLIENT}

    def availability(self) -> tuple[bool, str]:
        now = time.monotonic()

        if now - self._checked_at < _AVAILABILITY_TTL:
            return self._status

        engine = self._engine()

        if engine is None:
            status = (False, f"'{self.config.engine}' was not found on PATH")
        else:
            try:
                done = subprocess.run(
                    [*engine, "info", "--format", "{{.ServerVersion}}"],
                    capture_output=True,
                    timeout=15,
                    env=self._host_env(),
                    stdin=subprocess.DEVNULL,
                )
                if done.returncode == 0:
                    status = (True, f"{self.config.engine} {done.stdout.decode().strip()}")
                else:
                    detail = done.stderr.decode(errors="replace").strip()[:200]
                    status = (False, f"{self.config.engine} daemon is not reachable: {detail}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                status = (False, f"{self.config.engine} did not respond: {exc}")

        self._checked_at, self._status = now, status
        return status

    def refresh(self) -> None:
        self._checked_at = 0.0

    # ------------------------------------------------------------ prepare

    def container_env(self, env: Mapping[str, str]) -> dict[str, str]:
        """Only variables that are meant to cross into the container."""

        allowed = {n.upper() for n in CALLER_ALLOWED} | {n.upper() for n in FORCED}
        passed = {k: v for k, v in env.items() if k.upper() in allowed}
        passed.setdefault("HOME", "/tmp")
        return passed

    def run_args(
        self,
        argv: list[str],
        *,
        cwd: Path,
        workspace: Path,
        env: Mapping[str, str],
        limits: Any,
        network: bool,
        name: str,
    ) -> list[str]:
        cfg = self.config
        workspace = workspace.resolve()
        cwd = cwd.resolve()

        if not cwd.is_relative_to(workspace):
            raise SandboxError("working directory is outside the workspace")

        source = str(workspace)

        if any(c in source for c in ',"\n\r\0'):
            raise SandboxError(
                "the workspace path contains characters that cannot be "
                "mounted safely"
            )

        relative = PurePosixPath(cwd.relative_to(workspace).as_posix())
        workdir = str(PurePosixPath("/workspace") / relative)
        memory = getattr(limits, "max_memory_bytes", None) or cfg.default_memory_bytes
        pids = getattr(limits, "max_processes", None) or cfg.default_pids
        mount = f"type=bind,source={source},target=/workspace"

        if cfg.read_only_workspace:
            mount += ",readonly"

        tmpfs = f"/tmp:rw,nosuid,size={cfg.tmpfs_size}"

        if cfg.tmpfs_noexec:
            tmpfs += ",noexec"

        args = [
            cfg.engine, "run", "--rm", "--name", name,
            "--network", "bridge" if network else "none",
            "--read-only",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(int(pids)),
            "--memory", str(int(memory)),
            "--memory-swap", str(int(memory)),
            "--ulimit", "core=0",
            "--ulimit", "nofile=1024:1024",
            "--user", cfg.user,
            "--tmpfs", tmpfs,
            "--mount", mount,
            "--workdir", workdir,
            "--stop-timeout", str(cfg.stop_timeout_seconds),
        ]

        if cfg.cpus:
            args += ["--cpus", str(cfg.cpus)]

        for key, value in sorted(self.container_env(env).items()):
            if "\0" in key or "\0" in value or "=" in key:
                raise SandboxError("invalid environment entry")

            args += ["-e", f"{key}={value}"]

        # `--` is not accepted before the image, so the image comes first and
        # the program's argv follows verbatim.
        return [*args, cfg.image, *argv]

    def prepare(
        self,
        argv: list[str],
        *,
        cwd: Path,
        workspace: Path,
        env: Mapping[str, str],
        limits: Any,
        network: bool,
        run_id: str,
    ) -> PreparedCommand:
        name = f"aseos-{run_id}"
        args = self.run_args(
            argv, cwd=cwd, workspace=workspace, env=env, limits=limits,
            network=network, name=name,
        )
        host_env = self._host_env()
        engine = self._engine() or [self.config.engine]
        args[:1] = engine

        def run_engine(*sub: str) -> None:
            try:
                subprocess.run(
                    [*engine, *sub, name],
                    capture_output=True,
                    timeout=30,
                    env=host_env,
                    stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

        return PreparedCommand(
            command=args,
            env=host_env,
            description=f"{self.config.engine}:{self.config.image}",
            terminate=lambda: run_engine("kill"),
            cleanup=lambda: run_engine("rm", "-f"),
            details={
                "container": name,
                "network": "bridge" if network else "none",
                "image": self.config.image,
            },
        )
