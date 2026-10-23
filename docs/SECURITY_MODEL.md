# ASEOS Execution Security Model

Threat model, trust boundaries, the layered controls that exist today, and — just as important — what they do **not** protect
against. Companion to [EXECUTION_CONTRACT.md](EXECUTION_CONTRACT.md) and [EXECUTION_PLAN.md](EXECUTION_PLAN.md).

> Principle: an LLM-generated action is untrusted input. Every restriction below is enforced in trusted code at the point
> where the operation executes. Nothing in an action payload — a field, a path, a "risk" label, a claimed capability — can
> widen what the gateway allows.

## 1. Trust boundaries

| Party | Trusted? | Notes |
| ----- | -------- | ----- |
| Gateway configuration (profile, capabilities, sandbox, workspace) | **Yes** | Chosen by the host application (Role 1 runtime / Role 2 UI), never by the model |
| Action payload (tool args, paths, commands, patches) | **No** | Validated by schema, then by tool-specific rules |
| File contents, command output, commit messages, test names | **No** | Data, never instructions. May contain prompt injection |
| The repository's own code and scripts | **No** | Tests and build scripts are arbitrary code (see §4) |
| The user's main checkout | Protected | Tasks work in a separate worktree from a base commit |
| The host machine | Protected | Only as far as the sandbox backend actually isolates it |

## 2. Assets

Source in the main checkout and in other tasks' worktrees · uncommitted user changes · secrets (env files, keys, tokens,
user-level config) · the host file system and OS · availability (CPU, memory, processes, disk) · the integrity of Git
history and hooks.

## 3. Threats and controls

Status: ✅ enforced and tested here · 🟡 partly mitigated · ⬜ not mitigated (see §6).

| # | Threat | Controls | Status |
| - | ------ | -------- | ------ |
| 1 | **Unsafe or malformed commands** from the model | No shell ever (argv only); default-deny command policy (allow-list of test/lint/typecheck/build forms; shells, destructive tools, network clients, `git`, `-c`, scripts refused); path-like arguments confined to the workspace; `shell.run` not granted by default | ✅ |
| 2 | **Prompt injection** in repo files or command output steering the agent | Cannot be prevented in the execution layer. Limits the blast radius instead: payloads cannot raise level/capabilities/sandbox; secrets are unreadable; destructive operations need a trusted caller; work happens in a worktree that is reviewed before it is merged. Role 1 must treat tool output as untrusted data | 🟡 |
| 3 | **Malicious build/test scripts** | With a sandbox: container with no network, read-only root, only the workspace mounted, capabilities dropped, memory/pid limits. Without one: scrubbed environment, tree kill, Windows job limits — but the script runs as the user | 🟡 (container path not run against a real daemon here, see §5) |
| 4 | **Writes outside the workspace** | Resolved-path containment; symlink/junction escape denied; writes never go through a symlink; Windows aliases handled (case, trailing dots/spaces, short names, alternate data streams, device names); `.git`, `.env*` and the managed worktree area are write-protected | ✅ for tools; programs: see 3 |
| 5 | **Secret exposure** (to the model, logs, artifacts) | Secret-bearing files (`.env*`, keys, `.npmrc`, `.ssh/`, …) cannot be read, searched, hashed, patched, staged or diffed without `fs.read_secrets`; environment scrubbed (secret-looking names never pass) and secret values redacted from output; container gets only `-e` values | 🟡 |
| 6 | **Runaway processes / fork bombs / disk floods** | Wall-clock timeout; verified process-tree kill; bounded output (spooled, capped); Windows job object (processes, memory); container `--pids-limit`/`--memory` | 🟡 (no native limits off Windows) |
| 7 | **Unintended network access** | Container: `--network none` unless the install capability applies. Native: not blocked. `shell.network` gates install-type commands only | 🟡 |
| 8 | **Cross-task interference** | One worktree and branch per task; a gateway is bound to exactly one workspace; `../sibling`, absolute paths, `--rootdir=`, `cwd` into another worktree all denied; managed area unreadable and unwritable from the main checkout's file tools; worktree tools refuse to run from inside a task | ✅ |
| 9 | **Loss of the user's existing work** | Worktrees start from a base commit and never modify the main checkout; removal refuses unreviewed work and backs up when forced; Git mutations name exact files and refuse to sweep in user-staged changes | ✅ |
| 10 | **Privilege escalation through the tool surface** | Execution levels + capability profiles checked by the gateway; unclassified tools default to the highest level; high-risk commands need ELEVATED; profile is a ceiling for capabilities | ✅ |
| 11 | **Hostile Git configuration** (e.g. `core.fsmonitor`, `GIT_DIR`) | fsmonitor disabled, redirecting environment variables removed, `--no-ext-diff/--no-textconv`, literal pathspecs, refs validated | ✅ |

## 4. What the policy can and cannot do

The command policy decides what may be **started**. It cannot see what an allowed program then does. `python -m pytest`
executes the repository's test code; `npm test` executes its scripts. Only the sandbox (§5) limits that code's access to
the network, the file system and the host. Treat any command that runs repository code as arbitrary code execution unless
a sandbox is in place — which is why the `dev` and `elevated` profiles **require** one and refuse to run commands without
it.

## 5. Isolation strategy (decision)

Layers, from always-on to optional:

1. **Always on (gateway):** schema validation → execution level → capability check → path/secret rules → per-tool limits.
2. **Task isolation:** one Git worktree per task from a base commit; registry, locking, safe removal.
3. **Process containment (native):** scrubbed environment, per-run temp dir, argv only, stdin closed, bounded output,
   timeout/cancel with verified tree kill, Windows Job Object limits.
4. **Sandbox (required by the `dev` and `elevated` profiles):** a container backend (Docker or Podman).

Why a container: it is the only mechanism available on all three desktop platforms that can remove network access and
hide the rest of the file system from a program. Native Windows has no unprivileged "no network" switch, and Linux
namespaces/seccomp are not portable. Alternatives considered: AppContainer/restricted tokens (Windows only, complex, no file
mount model), bubblewrap/nsjail (Linux only), VMs (strongest, heavy). The `Sandbox` interface (`execution/sandbox/base.py`)
keeps the choice replaceable.

What `ContainerSandbox` builds (`docker run …`):

- `--network none` (or `bridge` only for install-type commands that were granted `shell.network`)
- `--read-only` root, `/tmp` as a small `noexec` tmpfs
- `--cap-drop ALL`, `--security-opt no-new-privileges`, non-root `--user`
- `--pids-limit`, `--memory` (= `--memory-swap`), `--cpus`, `--ulimit core=0 nofile=1024`
- exactly one `--mount`: the workspace at `/workspace`; no other host path, no Docker socket, no home directory
- environment: only explicit `-e` values (allow-listed names); the host environment is never forwarded
- never `--privileged`, host namespaces, `--device`, published ports, `--env-file`, extra volumes (asserted by tests)
- stop: the container is named `aseos-<run id>`; on timeout/cancel the engine is told to `kill`, then `rm -f`

Fail closed: if a profile requires a sandbox and none is configured, or the configured one is unavailable (engine
missing, daemon down), the command is **refused** with `PERMISSION_DENIED`. It is never run on the host instead.

**Operating requirements:** the image must already contain the project's toolchain and dependencies (nothing is installed
on the fly); a container shares the host kernel, so for hostile code use a VM-backed runtime; in a linked worktree the
`.git` file points outside `/workspace`, so Git does not work *inside* the container — Git operations run on the host
through the `git.*` tools.

## 6. Known limitations (read before relying on this)

- **The container backend has not been run against a real daemon in the development environment** (Docker was installed but
  its daemon was not running). What is verified: the exact engine command line and every forbidden flag, environment
  filtering, fail-closed behaviour, and that termination/cleanup are requested, using a stand-in engine. The one test that
  proves network and host-path isolation with a real container is written and skips itself when no daemon/image is present.
  Run it (`pytest execution/tests/test_sandbox.py`) on a machine with Docker and the `python:3.12-slim` image before trusting
  the boundary.
- Without a sandbox, allowed programs run as the current user: they can reach the network and any file the user can read,
  including `HOME`/`USERPROFILE` config (`.npmrc`, `.pypirc`, cloud credentials). The default gateway (no profile) is in this
  mode.
- Secret detection is name-based. A secret in a file that does not look like one (`config.py`, `notes.txt`) is not hidden.
- POSIX process handling (process groups, `prlimit`) is implemented but was not run on a POSIX machine. Memory and
  process-count limits for native processes exist only on Windows.
- A `fs.write` grant allows editing any non-protected file, including CI definitions and scripts that will later be
  executed. Mitigation is review of the worktree diff before merge, not prevention.
- Git hooks and signing settings of the repository still run on `git.commit`.
- Command logs are plain files in the system temp directory with no retention policy until Phase 10.
- Time-of-check/time-of-use gaps remain between a path check and the file operation (tiny, but not zero) because no
  file-system locking is used.
- Disk usage inside a workspace is not limited.

## 7. Execution levels and profiles

| Level | Meaning | Tools |
| ----- | ------- | ----- |
| `READ_ONLY` | Inspect | `repo.read/list_dir/file_info/search`, `patch.validate`, `git.status/diff/info`, `git.worktree.list` |
| `LOW_RISK_MUTATION` | Validated, auditable writes | `repo.write`, `patch.replace`, `patch.apply`, `git.stage/commit/branch_create`, `git.worktree.create/reconcile` |
| `DEV_EXECUTION` | Run approved programs | `test.run`, `shell.run` |
| `ELEVATED` | Destructive / wide-reaching | `git.discard`, `git.worktree.remove`; high-risk commands (installs) |

| Profile | Max level | Capabilities | Sandbox required |
| ------- | --------- | ------------ | ---------------- |
| `read_only` | READ_ONLY | `fs.read`, `git.read` | no |
| `worktree_edit` | LOW_RISK_MUTATION | + `fs.write`, `git.write`, `worktree.manage` | no |
| `dev` | DEV_EXECUTION | + `test.run`, `shell.run` | **yes** |
| `elevated` | ELEVATED | + `fs.delete`, `git.discard`, `worktree.discard`, `shell.network` | **yes** |

Never granted by any profile except `elevated`: `fs.delete`, `git.discard`, `worktree.discard`, `shell.network`.
`fs.read_secrets` is in **no** profile; it must be granted explicitly by trusted code.

A profile is a ceiling: `ExecutionGateway(profile=…, granted_capabilities=…)` cannot grant more than the profile contains.
Selecting a profile is the host's decision — Role 1 decides *which* profile a task gets (and whether the user must approve
`elevated`); Role 4 enforces it. `gateway.describe_tools()` tells planners what is available under the current profile.

```python
gateway = ExecutionGateway(
    str(worktree_path),
    tools=default_tools(),
    profile="dev",                       # level + capability ceiling
    sandbox=ContainerSandbox(ContainerConfig(image="my-project-ci:latest")),
)
```

## 8. Findings from the Phase 7 audit (all fixed, each has a regression test)

| Finding | Fix |
| ------- | --- |
| Files named like Windows devices (`CON`, `aux.txt`, `COM1`) could be created, and could not be removed by normal tools | Reserved names rejected on Windows |
| `name:stream` created NTFS alternate data streams and left stray `..env`-style files behind from the temp-file step | `:` rejected in paths on Windows; malformed paths give `INVALID_ARGUMENTS`, never `INTERNAL_ERROR` |
| Secret files (`.env`, keys) could be read, searched, hashed and patched, and their content leaked through `git.diff` and patch-conflict details | Secret-path policy across read/search/info/patch/diff/stage; `fs.read_secrets` to override |
| Other tasks' worktrees (under `.aseos_worktrees`) were readable and writable through the main checkout's file tools | Managed area denied for reads and writes; hidden from listings and searches |
| Case-variant and alias spellings of protected names | Verified closed (`.GIT`, `.git.`, `.env `, `GIT~1`, …) with tests; comparisons made case-insensitive |
| Search skipped every file when the *workspace itself* lived under an ignored directory name | Ignored-directory test now uses workspace-relative paths |
| Commands could be started without any isolation even for risky categories | `dev`/`elevated` profiles require a sandbox and fail closed |

## 9. Security test map

| Concern | Tests |
| ------- | ----- |
| Traversal, symlink/junction escape | `test_fs_tools.py`, `test_path_security.py`, `test_patch_tools.py`, `test_command_policy.py` |
| Windows path aliases, device names, ADS | `test_path_security.py` |
| Secret reads and diff redaction | `test_path_security.py` |
| Env scrubbing, redaction | `test_process_runner.py`, `test_sandbox.py` |
| Process limits, tree kill, timeout/cancel | `test_process_runner.py`, `test_shell_tool.py` |
| Command policy | `test_command_policy.py`, `test_shell_tool.py` |
| Sandbox command line, fail-closed | `test_sandbox.py` |
| Levels, profiles, no privilege escalation by payload | `test_profiles_and_isolation.py` |
| Cross-task / cross-workspace | `test_profiles_and_isolation.py`, `test_path_security.py` |
| Cleanup scope, user-work preservation | `test_worktree_manager.py`, `test_git_mutations.py` |
