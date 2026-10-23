# ASEOS Architecture & Project Overview

ASEOS — the **Autonomous Software Engineering Operating System** — is a Python 3.12 system in which an AI agent
plans and carries out software-engineering tasks (read code, edit it, run tests, inspect diffs) against real
repositories, with a strict boundary between *deciding what to do* and *performing side effects*.

> Status: this document describes the code as it exists on the `execution` branch. For what is built vs. still
> planned, see [EXECUTION_PLAN.md](EXECUTION_PLAN.md). For the exact runtime ↔ execution contract see
> [API_CONTRACTS.md](API_CONTRACTS.md).

## 1. Guiding principle

> Never trust an LLM-generated action as executable input without validation, authorization and controlled execution.

The agent proposes; trusted code validates and executes. Filesystem boundaries, command restrictions, timeouts
and workspace isolation must be enforced by the execution layer itself, independently of what the model, the
planner or the UI request (defense in depth).

## 2. Roles and ownership

The project is split across four workstreams ("roles"). This repository currently contains Roles 1 and 4; Roles 2
and 3 are placeholders.

| Role | Responsibility | Where it lives | State |
| ---- | -------------- | -------------- | ----- |
| 1 — AI / agent runtime | Task understanding, planning, decisions, policy, verification, events, LLM access | `runtime/` | Implemented, covered by the bulk of the test suite |
| 2 — Desktop UI | File explorer, editor, diff/test/terminal presentation, approvals | not in this repo | Not started here |
| 3 — Codebase intelligence | Scanning, parsing, symbols, search, context retrieval, indexing | `intelligence/` | Empty placeholder modules |
| 4 — Execution | Filesystem, patches, Git, worktrees, commands, tests, sandboxing | `execution/` + `runtime/agent/real_executor.py` | Baseline implemented; see plan |

## 3. System context

```
                 ┌──────────────────────────────┐
                 │   Role 2: Desktop UI (TBD)   │   shows tasks, diffs, tests, approvals
                 └───────────────┬──────────────┘
                                 │ events / APIs (planned)
┌────────────────────────────────▼───────────────────────────────────────┐
│ Role 1 — runtime/                                                      │
│  Task → Understanding → Planner → ActionGenerator → Actions            │
│  DecisionEngine (deterministic | LLM) → Validator → Policy             │
│  AgentRuntime loop ── Verifier ── Replanner ── EventEmitter/Store      │
└───────────────┬──────────────────────────────┬─────────────────────────┘
                │ ActionExecutor.execute(action)│ RepositoryContext
                ▼                              ▼
┌───────────────────────────────┐   ┌────────────────────────────────────┐
│ Role 4 — execution boundary   │   │ Role 3 — intelligence/ (TBD)       │
│  RealActionExecutor           │◄──┤  scanner, parser, symbols, search, │
│  → patches, git, worktrees,   │   │  context                           │
│    commands, test runner      │──►│  (index refresh after edits)       │
└───────────────┬───────────────┘   └────────────────────────────────────┘
                ▼
     Real repository / isolated Git worktree
```

## 4. Repository layout

```
ASEOS/
├── runtime/                  Role 1 — the agent
│   ├── agent/                AgentRuntime loop; ActionExecutor interface; Mock + Real executors
│   ├── decision/             DecisionEngine (deterministic, LLM), context builder, validator, policy
│   ├── planner/              Planner, ActionGenerator (plan steps → Actions), Replanner
│   ├── task/                 Task understanding service
│   ├── state/                AgentState
│   ├── schemas/              Pydantic models: Action, ExecutionResult, Plan, Task, RepositoryContext, …
│   ├── tools/                ToolRegistry (which action types the agent may use)
│   ├── verification/         Verifier ABC, CommandVerifier, VerificationResult
│   ├── events/               AgentEvent, EventEmitter, EventStore, replay
│   └── llm/                  Provider-agnostic LLM client: base, api, mock, router, structured output
├── execution/                Role 4 — side effects
│   ├── executor.py           execute_task(): worktree → patch → tests → diff → cleanup (+ events)
│   ├── gateway/              ExecutionGateway (dispatcher), Tool/ToolSpec protocol, capabilities,
│   │                         fs_tools.py (read/list/info/search/replace/write), patch_tools.py (patch.validate/apply),
│   │                         git_tools.py (git.status/diff/info/stage/commit/branch_create/discard),
│   │                         command_tools.py (shell.run, test.run), tools.py (default_tools)
│   ├── patches/              unified.py (diff parser), engine.py (plan/validate/apply/rollback/report),
│   │                         fileio.py (atomic write), apply.py (legacy exact-text replace used by execute_task)
│   ├── git/                  operations.py (status, diff), worktree.py (create/remove);
│   │                         status.py / diff.py are empty placeholders
│   ├── security/             paths.py (path/secret rules), levels.py (execution levels + profiles)
│   ├── sandbox/              base.py (Sandbox interface), container.py (Docker/Podman backend)
│   ├── commands/             process.py (hardened runner: tree kill, limits, cancel), env.py, capture.py,
│   │                         policy.py (default-deny command policy), runner.py (trusted-caller wrapper)
│   ├── testing/runner.py     run_tests(worktree) → python -m pytest
│   ├── tests/                unit tests for the execution modules (+ empty runner.py placeholder)
│   ├── worktrees/            manager.py (lifecycle), store.py (registry + repo lock), record.py, ids.py
│   └── demo.py               `python -m execution.demo`
├── intelligence/             Role 3 — empty placeholders (context, parser, scanner, search, symbols)
├── tests/                    Role 1 + real-executor tests (agent, decision, events, llm, planner, …)
├── fixtures/
│   ├── repositories/calculator   tiny repo with a deliberately failing test (embedded git repo)
│   ├── contexts/, mock-data/     empty
├── docs/                     ARCHITECTURE, API_CONTRACTS, CONTRIBUTING, EXECUTION_PLAN
├── pyproject.toml            Python >=3.12,<3.13; pydantic, pydantic-settings, httpx, python-dotenv
└── docker-compose.yml, .env.example
```

## 5. Role 1 — runtime in brief

- **Schemas** (`runtime/schemas/`): `Action` (`id`, `type`, `payload`), `ExecutionResult`, `Plan`, `Task`,
  `RepositoryContext`, `TestRunResult`, `TaskResult`.
- **Action types:** `SEARCH`, `READ`, `EDIT`, `RUN_TEST`, `RUN_COMMAND`, `GIT_DIFF`, plus `LIST_DIR`, `FILE_INFO`, `WRITE`,
  `VALIDATE_PATCH`, `APPLY_PATCH`, `GIT_STATUS`, `GIT_INFO`, `GIT_STAGE`, `GIT_COMMIT`, `GIT_BRANCH`, `GIT_DISCARD`,
  `WORKTREE_CREATE`, `WORKTREE_LIST`, `WORKTREE_REMOVE`, `WORKTREE_RECONCILE`
  (all implemented by the gateway but not yet in `ToolRegistry.default()`).
- **Planning:** `Planner` produces a `Plan` of steps; `ActionGenerator` maps each step to an `Action`.
- **Decisions:** a `DecisionEngine` (deterministic or LLM-backed, routed by `runtime/llm/router.py`) proposes the
  next `Decision`; `DecisionValidator` checks it structurally; `DecisionPolicy` returns `ALLOW` or
  `REQUEST_APPROVAL` (e.g. `RUN_COMMAND` and low-confidence decisions need human approval).
- **Loop:** `AgentRuntime.execute` picks the next decision, runs the action through the `ActionExecutor`, and
  after the actions calls the `Verifier`. On a failed verification the `Replanner` produces a new plan, bounded by
  `max_replans`.
- **Events:** every lifecycle step (`TASK_STARTED`, `PLAN_CREATED`, `ACTION_*`, `VERIFICATION_*`, `TASK_*`) is an
  `AgentEvent` sent through `EventEmitter` into an `EventStore`; events can be replayed to rebuild state.
- **Tool registry:** `ToolRegistry.default()` lists the capabilities the agent may use; one tool per action type.

## 6. Role 4 — execution in brief

### 6.1 The execution boundary

`runtime/agent/executor.py` defines the single seam between the agent and the world:

```python
class ActionExecutor(ABC):
    async def execute(self, action: Action) -> ExecutionResult: ...
```

Two implementations exist:

- `MockActionExecutor` — deterministic stand-in for runtime tests; no side effects.
- `RealActionExecutor(workspace_path)` — thin adapter that sends every action through the `ExecutionGateway`
  (`execution/gateway/`): resolve registered tool → check capabilities → validate args → run with timeout → normalize.
  See [EXECUTION_CONTRACT.md](EXECUTION_CONTRACT.md).

### 6.2 What the registered tools do per action

| Action (tool id) | Behaviour | Safety |
| ------ | --------- | ------ |
| `READ` (`repo.read`) | Reads a UTF-8 file (optional line range); ≤1 MB returned, `sha256` for versioning | Containment; binary/oversize files rejected |
| `LIST_DIR` (`repo.list_dir`) | Lists one directory, non-recursive | Containment; escaping links hidden |
| `FILE_INFO` (`repo.file_info`) | Exists/type/size/mtime/sha256/binary | Containment |
| `WRITE` (`repo.write`) | Create a file, or overwrite only when `expected_sha256` matches; atomic + verified | Containment; no write through symlinks; `.git` denied; ≤1 MB |
| `SEARCH` (`repo.search`) | Case-insensitive text search; ≤100 matches; skips `.git`, `.venv`, `venv`, `__pycache__`, `node_modules` | Same containment; undecodable files skipped |
| `EDIT` (`patch.replace`) | Legacy `apply_patch(workspace, payload)` with `{file, old, new}` | Path containment; target must be non-empty and match exactly once; protected paths denied |
| `VALIDATE_PATCH` (`patch.validate`) | Checks a unified diff against the workspace; writes nothing | Containment, protected paths, size limits, base hashes, hunk context |
| `APPLY_PATCH` (`patch.apply`) | Applies a unified diff (create/modify/delete, multi-file); validates all files first, atomic per file, rolls back on failure, reports the diff actually produced; `reverse=true` undoes one | As above; deleting needs the `fs.delete` capability (not granted by default) |
| `RUN_TEST` (`test.run`) | `python -m pytest` through the hardened runner, 120 s | Fixed command, no caller-supplied argv; scrubbed env, tree kill, bounded output |
| `GIT_DIFF` (`git.diff`) | `git diff --` via the command runner, 10 s | Fixed argv |
| `RUN_COMMAND` (`shell.run`) | One allowed program (argv, never a shell) under the command policy | Needs `shell.run` (not granted by default); default-deny policy, path arguments confined to the workspace, scrubbed env, bounded output, tree kill on timeout/cancel |

Each tool runs in `asyncio.to_thread` under a per-tool timeout. Failures never raise to the runtime: they become a failed
`ExecutionResult` with an `error_category` (e.g. `INVALID_ARGUMENTS`, `PERMISSION_DENIED`, `PATCH_CONFLICT`,
`TEST_FAILURE`, `TIMEOUT`, `INTERNAL_ERROR`), the diagnostic in `stderr`, the canonical `tool` id and `duration_ms`.

### 6.3 Task-level flow (`execution/executor.py::execute_task`)

```
create_worktree(repo, task_id)      → <repo>/.aseos_worktrees/<task_id>, branch aseos/<task_id>
apply_patch(worktree, patch)        → PASS, or ACTION_FAILED + return ERROR
emit ACTION_COMPLETED, VERIFICATION_STARTED
run_tests(worktree)                 → PASS | FAIL | TIMEOUT | ERROR
git_diff(worktree)                  → actual unstaged diff
emit VERIFICATION_COMPLETED | VERIFICATION_FAILED
return {status, patch, tests, diff}
finally: remove_worktree(...)       → worktree removed, branch deleted
```

`python -m execution.demo` runs this against `fixtures/repositories/calculator`, fixing a `divide` bug in an
isolated worktree and showing the passing tests and diff.

> Current limitation: cleanup runs unconditionally with force-removal, so the agent’s change is discarded as soon
> as the run finishes. Preserving the worktree until review is planned (Phase 5).

### 6.4 Execution modules

| Module | Function | Notes |
| ------ | -------- | ----- |
| `execution/patches/apply.py` | `apply_patch(worktree, patch) -> dict` | `status` `PASS`/`ERROR` + message |
| `execution/git/operations.py` | `git_status`, `git_diff` | Status: branch/clean/modified; diff: unstaged only |
| `execution/git/worktree.py` | `create_worktree`, `remove_worktree` | `WORKTREE_ROOT = ".aseos_worktrees"` |
| `execution/commands/process.py` | `run_process(argv, cwd, ...)` | Hardened runner: `PASS`/`FAIL`/`TIMEOUT`/`CANCELLED`/`ERROR`, process-tree kill, bounded output, Windows job limits |
| `execution/commands/runner.py` | `run_command(argv, cwd, timeout=30)` | Trusted-caller wrapper over `run_process` (no command policy) |
| `execution/testing/runner.py` | `run_tests(worktree)` | Wraps the command runner |

## 7. Safety boundary (today)

What is enforced:

- Path containment for `READ`, `SEARCH` and `EDIT` (resolved path must be inside the workspace).
- Capability checks in the gateway before arguments are validated; `shell.run` is never granted by default.
- Worktree `task_id` is validated and worktree paths are confined to `.aseos_worktrees`.
- Patches must match exactly once; ambiguous or missing targets are rejected.
- Commands are invoked as argv lists (no shell) with a scrubbed environment, bounded output, a timeout and cancellation; the whole process tree is killed and verified. Agent commands pass a default-deny policy and need the `shell.run` capability.
- Source changes in `execute_task` happen in a separate Git worktree, not the main checkout.

What is **not** provided (do not assume it):

- A *proven* sandbox. Worktrees isolate source, not processes. A container backend exists and is enforced fail-closed by the `dev`/`elevated` profiles, but it has only been tested against a stand-in engine, not a real daemon (see [SECURITY_MODEL.md](SECURITY_MODEL.md) §6).
- Anything beyond *starting* only allowed programs: what a test suite or build script then does (network, files outside the workspace, user-level config via `HOME`) is not restricted; POSIX process handling is untested here; no memory/process limits off Windows.
- Patch recovery, crash recovery, file locking, and notification of other subsystems after writes (`repo.write` does detect stale-version conflicts).
- Redaction, artifact retention or an execution audit trail beyond runtime events.
- `CommandVerifier` (Role 1) runs shell command strings directly and does not go through the execution boundary.

These gaps and their planned fixes are tracked in [EXECUTION_PLAN.md](EXECUTION_PLAN.md#2-known-issues-found-during-the-audit).

## 8. Data contracts

`ExecutionResult` (`runtime/schemas/execution.py`) — the only result type crossing the boundary today:

| Field | Meaning |
| ----- | ------- |
| `action_id` | The action the result belongs to |
| `success` | Whether the operation succeeded |
| `stdout` / `stderr` | Textual output / error output |
| `exit_code` | Process exit status when applicable |
| `duration_ms` | Execution time in milliseconds |

Result extensions (all optional, backward compatible): `tool`, `error_category`, `truncated`, `artifacts`, `data`.

Also defined: `TestRunResult` (`status`, `passed`, `failed` — not yet populated by the runner) and `TaskResult`.
Still planned: cancellation state, an artifact store, and workspace revision on results.

## 9. Verification flow

1. The runtime finishes (or reaches a checkpoint in) its action list.
2. The `Verifier` — currently `CommandVerifier`, which runs configured commands in `task.workspace_path` — returns
   a `VerificationResult` with per-command `VerificationCheck`s (`PASS`/`FAIL`/`ERROR`/`TIMEOUT`).
3. On failure, the `Replanner` receives the verification evidence and produces a revised plan, up to `max_replans`.
4. Verification events are written to the event store and can be replayed.

Planned: verification evidence produced by Role 4 and tied to the exact workspace revision, with staleness
detection after later edits.

## 10. Design direction (target architecture)

The roadmap converges on a single **execution gateway** with specialized tools behind it:

```
Validated action (Role 1)
        │
        ▼
Execution Gateway: resolve tool → validate args → check capabilities/workspace
                   → apply policy → run with limits → normalize result → emit events
        │
 ┌──────┼───────────┬───────────────┬──────────────┐
 ▼      ▼           ▼               ▼              ▼
repo.* patch.*     git.*          shell.*        test.*
files  apply       status/diff/   bounded        discovery,
       validate    worktrees      processes      runner, reports
        │
        ▼
Normalized result: success/failure, exit status, duration, bounded output, artifact refs, error category
```

The gateway core now exists (`execution/gateway/`). Remaining work — events, cancellation, output limits,
artifacts, command policy and sandboxing — will be added in slices so the runtime keeps working throughout.

## 11. Testing

- Role 4 unit tests: `execution/tests/` (gateway, patches, git operations, worktrees, runners, `execute_task` + events).
- Executor integration with the runtime: `tests/agent/test_real_executor.py`,
  `tests/agent/test_real_executor_integration.py`.
- Role 1 suites: `tests/agent`, `decision`, `events`, `llm`, `planner`, `schemas`, `state`, `task`, `tools`,
  `verification`.
- Baseline at time of writing: **764 passed, 6 skipped** (symlink-file and POSIX-mode tests skip when the OS does not allow
  them; a Windows directory-junction test covers the same escape). `pyproject.toml` `testpaths` limits default collection to `tests/`
  and `execution/tests/`; the deliberately broken `fixtures/repositories/calculator` test (which the demo fixes)
  is not collected.
- Async tests use `pytest-asyncio` in strict mode; `pythonpath = ["."]`.

Suggested commands (from [CONTRIBUTING.md](CONTRIBUTING.md)):

```powershell
python -m pytest execution/tests -q
python -m pytest tests/agent/test_real_executor.py tests/agent/test_real_executor_integration.py -q
python -m execution.demo
```

## 12. How to extend

- **New agent capability:** add the action type + `ToolDefinition`, implement a `Tool` in `execution/gateway/tools.py`
  (args schema, capabilities, `ToolError` categories), register it in `default_tools()`, add gateway tests, then an
  integration test against a temporary fixture repo. See "Adding a tool" in [EXECUTION_CONTRACT.md](EXECUTION_CONTRACT.md).
- **Large changes:** build in slices — contract first, then one tool/consumer at a time — keeping the demo and
  existing tests green at every step. See the delivery rule in [EXECUTION_PLAN.md](EXECUTION_PLAN.md).
- **Anything with side effects:** treat task text, model output, file contents and command arguments as untrusted;
  never weaken a safety check to make a test pass.
