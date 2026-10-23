"""A stand-in container engine for testing the sandbox *plumbing*.

It is NOT a container: it parses the arguments ContainerSandbox builds,
records them, and runs the requested program directly with only the `-e`
environment. That lets the tests verify what we ask the engine to do and
that termination/cleanup are requested, without needing a Docker daemon.

usage: fake_engine.py <log file> <engine args...>
"""

import json
import os
import subprocess
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
args = sys.argv[2:]


def log(entry):
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


command = args[0] if args else ""

if command == "info":
    if Path(str(log_path) + ".down").exists():
        print("Cannot connect to the daemon", file=sys.stderr)
        sys.exit(1)

    print("99.0.0")
    sys.exit(0)

if command in ("kill", "rm"):
    log({"cmd": command, "args": args})
    sys.exit(0)

if command == "run":
    i = 1
    values = {}
    env = {}

    while args[i].startswith("-"):
        if args[i] in ("--rm", "--read-only"):
            i += 1
        elif args[i] == "-e":
            key, _, value = args[i + 1].partition("=")
            env[key] = value
            i += 2
        else:
            values[args[i]] = args[i + 1]
            i += 2

    image, program = args[i], args[i + 1 :]
    mount = dict(
        part.split("=", 1) for part in values["--mount"].split(",") if "=" in part
    )
    workdir = values["--workdir"].removeprefix("/workspace").lstrip("/")
    host_cwd = Path(mount["source"]) / workdir
    log({"cmd": "run", "args": args, "env": env, "image": image, "program": program})
    child_env = dict(env)

    for keep in ("PATH", "SYSTEMROOT"):  # what the "image" itself provides
        if keep in os.environ:
            child_env[keep] = os.environ[keep]

    sys.exit(subprocess.call(program, cwd=str(host_cwd), env=child_env))

sys.exit(2)
