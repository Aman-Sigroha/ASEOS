# ASEOS — Role 4 Execution Plan & Status Tracker

Execution, Git, worktrees, command running, sandboxing and testing.

- **Status snapshot date:** 2026-10-23
- **Branch audited:** `execution` (HEAD `a91605f`)
- **Test baseline:** `python -m pytest -q` → 240 passed, 0 failed (default `testpaths` now exclude the
  intentionally failing `fixtures/` repo). Before the first slices it was 202 passed + 1 fixture failure.
- **Progress log:** slices A1, A2, A3, B1, B2 are done and B3/B4 are partly done (see [Slice backlog](#3-slice-backlog-suggested-order)).

Companion documents: [ARCHITECTURE.md](ARCHITECTURE.md) (how the system fits together),
[API_CONTRACTS.md](API_CONTRACTS.md) (baseline runtime ↔ execution contract),
[EXECUTION_CONTRACT.md](EXECUTION_CONTRACT.md) (gateway pipeline, tools, result fields, error categories),
[CONTRIBUTING.md](CONTRIBUTING.md) (verification commands and safety rules).

---

## How to read this document

| Marker | Meaning |
| ------ | ------- |
| ✅ **DONE** | Implemented *and* covered by passing tests. |
| 🟡 **PARTIAL** | A working baseline exists but it does not meet the roadmap's acceptance criterion. The gap is listed. |
| ⬜ **PENDING** | Not started (or only an empty placeholder file exists). |

A phase is only called complete when its **completion criterion** is met, not when some code exists.
Never mark an item ✅ unless its acceptance tests pass and were actually run.

## Delivery rule: build big changes in slices

Anything larger than a small, self-contained change **must be built in slices**. A slice is the thinnest
vertical piece that is useful, tested and safe to merge on its own.

1. **Slice, don't big-bang.** If a task touches more than one module, adds a new contract, or changes
   behaviour the runtime already depends on, split it. Examples: ship the `ToolResult` schema first, then
   migrate one tool onto it, then the rest — not all in one commit.
2. **Contract first, implementation second.** Land the schema/interface (plus a mock or adapter) in its own
   slice so Role 1 can code against it before the real implementation exists.
3. **Each slice ends green.** Code + unit tests + (where it has side effects) an integration test against a
   temporary fixture repository, with `python -m pytest` passing except for the known fixture failure.
4. **Read-only before mutating.** Within a phase, slice order is: read-only → validated write in an isolated
   worktree → anything touching the user's main checkout. Destructive operations come last and need explicit scope.
5. **Keep existing behaviour working.** `RealActionExecutor`, `execute_task` and `python -m execution.demo`
   must keep working at every slice boundary. Migrate callers in a later slice, then delete the old path.
6. **One slice = one reviewable diff** (target: one concern, a few files, one commit/PR) with a stated
   acceptance test. Update this tracker in the same PR.
7. **Security-relevant slices get their own tests first** (path escape, injection, timeout, cleanup scope) —
   write the failing security test, then the fix.

Suggested slice sequences for the biggest pending items are listed in [Slice backlog](#slice-backlog-suggested-order).

---

## Progress summary

| Phase | Title | Status | Notes |
| ----- | ----- | ------ | ----- |
| 0 | Audit scaffold & finalize contracts | 🟡 PARTIAL | Audit, mock adapter, result/error contract + `EXECUTION_CONTRACT.md` done; worktree/security docs, fixtures missing |
| 1 | Execution gateway & tool adapters | 🟡 PARTIAL | `ExecutionGateway` + 6 typed tools, capabilities, error taxonomy done; events, cancellation, registry alignment pending |
| 2 | Safe filesystem tools | 🟡 PARTIAL | READ/SEARCH inside workspace; no limits, metadata, listing, safe writes |
| 3 | Patch validation & application | 🟡 PARTIAL | Single-file exact-text replace; no unified diff, multi-file, recovery |
| 4 | Git status, diff & operations | 🟡 PARTIAL | Basic status + unstaged diff; no staged/base/stats/mutations |
| 5 | Worktrees & task isolation | 🟡 PARTIAL | Create/remove works; no metadata, locking, recovery, safe cleanup |
| 6 | Controlled command execution | 🟡 PARTIAL | `run_command` with list args + timeout; no env policy, caps, tree-kill, cancel |
| 7 | Sandboxing & execution security | ⬜ PENDING | Only worktree isolation + `RUN_COMMAND` disabled |
| 8 | Test discovery & execution | 🟡 PARTIAL | Hardcoded `python -m pytest`; no discovery or report parsing |
| 9 | Deterministic verification evidence | 🟡 PARTIAL | Role 1 verifier exists; not tied to code version/artifacts |
| 10 | Artifacts, retention & audit | ⬜ PENDING | Only runtime events exist |
| 11 | Concurrency, interruption & recovery | ⬜ PENDING | — |
| 12 | Cross-role integration | 🟡 PARTIAL | Role 1 wired in; Role 2 and Role 3 pending |
| 13 | Security, testing & performance qualification | 🟡 PARTIAL | Unit/integration tests only |

**Role 4 completion checklist: 1 ✅ done · 12 🟡 partial · 13 ⬜ pending (of 26)** (an item flips only when its full acceptance criterion is met). See [Section 4](#4-role-4-completion-checklist).

---

## 1. Phase-by-phase tracker

### Phase 0 — Audit the scaffold and finalize contracts 🟡 PARTIAL

*Completion criterion: Role 1 can invoke a mock execution adapter using the agreed contract before the real implementations are complete.*

- ✅ Inspect directory structure, dependency setup and test conventions (pytest, `pytest-asyncio` strict mode, `pythonpath = ["."]`).
- ✅ Read Role 1's `Action`, `ExecutionResult` and `ToolRegistry` interfaces.
- ✅ Understand how `AgentRuntime` calls `ActionExecutor.execute(action)` (`runtime/agent/runtime.py:442`).
- ✅ Mock execution adapter exists (`runtime/agent/mock_executor.py`) and Role 1 tests use it.
- ✅ Document current contracts — `docs/API_CONTRACTS.md` (baseline) and `docs/EXECUTION_CONTRACT.md` (gateway, tools, result, errors).
- 🟡 Canonical tool ids (`repo.read`, `repo.search`, `patch.apply`, `test.run`, `git.diff`, `shell.run`) are defined in the gateway and reported in `ExecutionResult.tool`; **still to agree with Role 1**, whose `ToolRegistry` keeps the short names (`read`, `edit`, …).
- 🟡 Extended result contract done: `tool`, `error_category`, `truncated`, `artifacts`, `data` (backward compatible). Cancellation state and a typed request model still pending.
- ⬜ Define command timeout, cancellation and output-limit behaviour.
- 🟡 Errors (12 categories) and artifact refs defined; partial-result representation pending.
- ⬜ Agree workspace identity and path conventions with Role 3.
- ⬜ Agree worktree lifecycle and patch-application rules.
- 🟡 `docs/EXECUTION_CONTRACT.md` created; `docs/WORKTREE_LIFECYCLE.md` and `docs/SECURITY_MODEL.md` pending.
- 🟡 Shared fixtures — `fixtures/repositories/calculator` exists; no failure/conflict/dirty-repo fixtures, `fixtures/contexts` and `fixtures/mock-data` are empty.
- 🟡 Initial integration tests — `tests/agent/test_real_executor_integration.py`, `execution/tests/test_executor*.py`.

### Phase 1 — Execution gateway and tool adapters 🟡 PARTIAL

*Completion criterion: every tool invocation passes through one predictable validate → authorize → execute → normalize path; adding a tool does not require rewriting `AgentRuntime`.*

- ✅ `ActionExecutor` async interface (`runtime/agent/executor.py`).
- ✅ `RealActionExecutor` handles `READ`, `SEARCH`, `EDIT`, `RUN_TEST`, `GIT_DIFF`; `RUN_COMMAND` explicitly refuses (`runtime/agent/real_executor.py`).
- ✅ Sync work runs off the event loop via `asyncio.to_thread`; exceptions are converted to a failed `ExecutionResult` with duration.
- ✅ Common tool protocol: `Tool`/`ToolSpec` with canonical id, pydantic args schema, capabilities, read-only, idempotent, timeout (`execution/gateway/tool.py`). Cancellation support and risk-level metadata still pending.
- ✅ `ExecutionGateway` resolves only *registered* tools: resolve → capabilities → validate → run with timeout → normalize. `RealActionExecutor` is now a thin adapter over it.
- 🟡 Namespaced tool ids exist in the gateway; aligning Role 1's `ToolRegistry` pending.
- ✅ Parameter validation per tool via pydantic schemas (`INVALID_ARGUMENTS`).
- ✅ Capability enforcement at the execution boundary (`fs.read`, `fs.write`, `git.read`, `test.run` granted by default; `shell.run` withheld → `RUN_COMMAND` denied).
- ✅ Standard error taxonomy (`ErrorCategory`, 12 values). `WORKSPACE_UNAVAILABLE`, `RESOURCE_LIMIT`, `CANCELLED` are defined but not yet produced.
- 🟡 Per-tool gateway timeout done (cannot interrupt the worker thread). Cancellation interface pending.
- ⬜ Emit execution events from the dispatcher (only `execution/executor.py::execute_task` emits events today).
- ✅ Dispatcher tests (`execution/tests/test_gateway.py`, 19 tests).

### Phase 2 — Safe filesystem tools 🟡 PARTIAL

*Completion criterion: the runtime can reliably read and write permitted files; invalid paths and conflicting modifications fail safely.*

- ✅ Read a file by workspace-relative path with `resolve()` + `is_relative_to` containment (blocks `..` and symlink targets that resolve outside).
- ✅ Content search with ignore list (`.git`, `.venv`, `venv`, `__pycache__`, `node_modules`) and a 100-match cap.
- ✅ Missing-file and non-string path errors reported as failed results.
- ⬜ Directory listing, file metadata, path-exists check.
- ⬜ Line-range reads and bounded file content (READ currently returns the whole file, no size cap).
- 🟡 Undecodable files now return a categorized `INVALID_ARGUMENTS` error instead of crashing; no binary sniffing or encoding options yet.
- ⬜ Explicit symlink policy (document and test; containment currently only a side effect of `resolve()`).
- ⬜ File-size and output-size limits.
- ⬜ Controlled write tool (create/overwrite) with exact target, payload limits, expected-version/hash check, checksum confirmation, structured result.
- ⬜ Concurrent-modification / conflict detection (return conflict instead of overwriting).
- ⬜ Change notification to subsystems (Role 3 index) after writes.
- ⬜ Filesystem unit tests for traversal, symlink, size and conflict cases.

### Phase 3 — Patch validation and application 🟡 PARTIAL

*Completion criterion: valid patches produce expected changes, conflicting patches fail visibly, and applied changes are reviewable and traceable to the originating action.*

- ✅ Exact-text replace patch `{file, old, new}` (`execution/patches/apply.py`).
- ✅ Path containment check, file-exists check, empty-target rejection, not-found and ambiguous (multiple-occurrence) rejection; failures carry an error `category` (`PATCH_CONFLICT`, `PERMISSION_DENIED`, …).
- ✅ Tests in `execution/tests/test_patch.py`.
- ⬜ Canonical patch format (unified diff and/or structured edit operations) with a schema and parser.
- ⬜ New-file, delete-file (policy-gated) and multi-file patches.
- ⬜ Base/content-hash validation; refuse to overwrite newer content.
- ⬜ Sensitive-path restrictions; patch size and affected-file limits.
- ⬜ Controlled application with recovery artifact / rollback; honest reporting of partial application.
- ⬜ Result built from the *actual* resulting files/Git diff (currently only returns a message).
- ⬜ Patch provenance: link patch → action id → task → workspace.
- ⬜ Conflict details preserved for Role 1 replanning.
- 🟡 Encoding/newlines — uses default `read_text()`/`write_text()` (platform encoding and newline translation); make explicit UTF-8 and preserve line endings.

### Phase 4 — Git status, diff and repository operations 🟡 PARTIAL

*Completion criterion: the agent can inspect real repository changes and perform only authorized Git operations against the intended repo or worktree.*

- ✅ `git_status` → branch, clean flag, modified files; `git_diff` → unstaged diff (`execution/git/operations.py`). Tests in `test_git_operations.py`.
- ✅ `GIT_DIFF` action wired through `RealActionExecutor`.
- 🟡 `execution/git/status.py` and `execution/git/diff.py` are empty placeholders; real logic lives in `operations.py`. Consolidate.
- ⬜ Repository root/identity, staged vs unstaged separation, untracked files, rename/add/delete status, merge/rebase state.
- ⬜ Explicit diff base (worktree vs base commit), diff statistics, changed-file list, bounded diff output + artifact.
- 🟡 `GIT_DIFF` via the gateway maps failures to `GIT_FAILURE`; the `git_status`/`git_diff` helpers still raise `CalledProcessError` (`check=True`). Git output is now decoded as UTF-8 (was the locale codepage, which crashed on non-cp1252 characters).
- ⬜ Read-only extras: commit metadata, branch list, default branch.
- ⬜ Mutating ops (agent branch, selective staging, commit, scoped discard) with path/scope validation.
- ⬜ Preserve pre-existing user changes: check dirty state, refuse unsafe ops, record before/after evidence.
- ⬜ Tests with dirty trees and pre-existing user changes.

### Phase 5 — Git worktrees and task isolation 🟡 PARTIAL

*Completion criterion: each code-writing task runs in a known workspace, the main checkout is preserved, and cleanup/recovery never loses unreviewed changes.*

- ✅ `create_worktree(repo, task_id)` → `<repo>/.aseos_worktrees/<task_id>` on new branch `aseos/<task_id>`.
- ✅ `remove_worktree` removes worktree → prunes → deletes the temporary branch; tolerant of already-removed state.
- ✅ Tests in `execution/tests/test_worktrees.py`; used by `execute_task` and the demo.
- ✅ `task_id` validated (`validate_task_id`) and worktree paths confined to `.aseos_worktrees` for create and remove, with tests.
- ⬜ Select a base revision explicitly; record base commit.
- ⬜ Worktree metadata record (id, task id, repo identity, path, branch, base commit, created, status, cleanup eligibility) with a registry/store; do not trust caller-supplied paths.
- ⬜ Dirty/unreviewed-change check before removal (currently `--force` + `branch -D`, and `execute_task` always removes in `finally`).
- ⬜ Prevent two tasks sharing a worktree; locking around shared Git admin data.
- ⬜ Ensure cleanup can only delete inside the managed worktree area.
- ⬜ Restart reconciliation: detect orphaned/missing worktrees, never auto-delete ambiguous ones.
- ⬜ Concurrency and recovery tests.
- ⬜ Surface the managed `.aseos_worktrees` directory so it can't be committed/indexed by accident.

### Phase 6 — Controlled command execution 🟡 PARTIAL

*Completion criterion: approved commands run against the correct workspace; output and errors are captured; timeouts/cancellations leave no uncontrolled processes.*

- ✅ `run_command(argv: list[str], cwd, timeout)` — direct invocation (no shell), UTF-8 decoding with replacement, captures stdout/stderr/exit code, statuses `PASS`/`FAIL`/`TIMEOUT`/`ERROR` (`execution/commands/runner.py`). Tests in `test_runner.py`.
- ✅ General `RUN_COMMAND` is refused by `RealActionExecutor` until a policy exists.
- ⬜ Validate cwd is inside an approved workspace/worktree (currently any `cwd` is accepted).
- ⬜ Command policy (allow-list / categories / risk) enforced in trusted code.
- ⬜ Controlled environment variables (currently inherits the full parent environment).
- ⬜ Output caps + truncation metadata + full-output artifact reference.
- ⬜ Process-tree termination on timeout (`subprocess.run` kills only the direct child); verify no orphans.
- ⬜ Cancellation and runtime-shutdown handling.
- ⬜ `duration_ms` in the runner result; distinguish missing executable vs invalid cwd vs non-zero exit.
- ⬜ Streaming logs (nice-to-have; keep distinct from completion).
- ⬜ Cross-platform (Windows/POSIX) process tests.
- 🟡 `runtime/verification/command.py` (Role 1) runs `create_subprocess_shell` with string commands and `process.kill()` — shell injection and tree-kill concerns; coordinate with Role 1 to route it through the command runner/policy.

### Phase 7 — Sandboxing and execution security ⬜ PENDING

*Completion criterion: a defined, tested security boundary; high-risk operations cannot gain permissions because the model asks.*

- ✅ (Baseline) Worktree isolation of source changes; `RUN_COMMAND` disabled; limitations documented in `API_CONTRACTS.md` and `ARCHITECTURE.md`.
- ⬜ `docs/SECURITY_MODEL.md` with threat model (unsafe model commands, prompt injection in repo files/output, malicious build/test scripts, out-of-workspace writes, secret exposure, runaway/fork-bomb processes, network access, cross-task interference, user-change loss).
- ⬜ Decide the isolation strategy (layers: path enforcement, worktrees, temp env dirs, container/OS isolation, network restriction, CPU/memory/time limits, env scrubbing).
- ⬜ Risk-based execution levels agreed with Role 1 (read-only / low-risk mutation / dev execution / elevated).
- ⬜ Per-tool permissions and resource limits.
- ⬜ Sandbox tests: workspace/symlink escape, host-path exposure, secret leakage, resource limits, cross-workspace access.
- ⬜ Known-limitations document kept current.

### Phase 8 — Test discovery and execution 🟡 PARTIAL

*Completion criterion: appropriate tests run; genuine failures are distinguished from infrastructure errors; results are tied to the code version tested.*

- ✅ `run_tests(worktree)` runs `python -m pytest` with a 30 s timeout via the command runner (`execution/testing/runner.py`); tests in `test_test_runner.py`.
- ✅ `TestRunResult` schema (`status`, `passed`, `failed`) exists in `runtime/schemas/execution.py` (not yet populated by the runner).
- ⬜ Test discovery (pytest/unittest, npm scripts, Maven/Gradle) from repo config, not a hardcoded command.
- ⬜ Use the workspace's own interpreter/venv rather than whatever `python` is on PATH.
- ⬜ Machine-readable reports (JUnit XML / JSON) parsed into a normalized model; keep framework details.
- ⬜ Counts for passed / failed / skipped / deselected; populate `TestRunResult`.
- ⬜ Distinguish test failure vs collection/setup error vs timeout vs "could not start".
- ⬜ Targeted tests vs full suite; report exactly which checks ran.
- ⬜ Record command, workspace, source revision, start time, duration, artifact refs.
- ⬜ Configurable timeout (30 s is fixed).
- ⬜ Tests for setup failure, failing tests, skipped tests, timeouts, corrupted report.
- 🟡 `execution/tests/runner.py` is an empty placeholder (and `execution/tests/` doubles as the test directory) — resolve the naming overlap with `execution/testing/`.

### Phase 9 — Deterministic verification and recovery evidence 🟡 PARTIAL

*Completion criterion: verification status comes from actual, traceable check results — never from "the plan finished".*

- ✅ Role 1 verification abstraction: `Verifier`, `CommandVerifier`, `VerificationResult`/`VerificationCheck` (`PASS/FAIL/ERROR/TIMEOUT`), runtime integration, replanning with verification evidence, event replay (`runtime/verification/`, `runtime/planner/replanner.py`).
- ✅ `execute_task` produces real patch + test + diff evidence and emits `VERIFICATION_*` events.
- ⬜ Supported check kinds: tests, build, lint, type-check, file exists, content condition, diff contains change, no prohibited paths modified.
- ⬜ Evidence includes the underlying command result and artifact references.
- ⬜ Associate each result with the workspace revision/tree hash; mark results **stale** after later edits.
- ⬜ Agree one authoritative verification status with Role 1 (avoid two competing systems).
- ⬜ Failure evidence sufficient for inspect / retrieve more output / targeted retest / modify patch / replan / stop.
- ⬜ Tests for stale and inconsistent outcomes.

### Phase 10 — Execution artifacts, output retention and auditability ⬜ PENDING

*Completion criterion: every meaningful execution is traceable to inputs, workspace, outputs and evidence, without exposing unnecessary or sensitive data.*

- ✅ (Baseline) Runtime `EventEmitter`/`EventStore` and replay exist (Role 1); `execute_task` emits `ACTION_*`/`VERIFICATION_*` events.
- ⬜ Artifact store: full logs, test reports, diffs, patches, build reports, traces; stable references; bounded excerpt in the result.
- ⬜ Task/action/workspace ownership and retention policy (temporary vs audit).
- ⬜ Secret redaction before display/persistence.
- ⬜ Evidence consistency checks (exit code, diff, summary, artifact ref match the real operation).
- ⬜ Evidence-to-action association via the runtime event system (no second task history).
- ⬜ Artifact-store failure handling and tests.

### Phase 11 — Concurrency, interruption and crash recovery ⬜ PENDING

*Completion criterion: an interrupted execution can be reconciled, resumed when safe or stopped with state preserved, without duplicating mutations or deleting valuable work.*

- ⬜ Prevent conflicting writes to the same managed worktree.
- ⬜ Task/process/execution-id association and a record of running operations.
- ⬜ Safe cancellation and process cleanup.
- ⬜ Checkpoints and post-restart reconciliation (inspect actual FS/process state before resuming; never blindly re-run a mutation).
- ⬜ Detect missing worktrees and orphaned artifacts.
- ⬜ Git lock failure / concurrent Git handling.
- ⬜ Behaviour when a result cannot be persisted.
- ⬜ Block auto-cleanup when unreviewed changes may be lost.
- ⬜ Failure-injection tests for interrupted operations.

### Phase 12 — Integration with the other roles 🟡 PARTIAL

*Completion criterion: a task started from the desktop can inspect and modify a real isolated repo, run tests, return real evidence and recover from a controlled failure.*

**Role 1 (runtime)**
- ✅ `RealActionExecutor` plugs into `AgentRuntime` (`tests/agent/test_real_executor_integration.py`).
- ✅ End-to-end demo: worktree → patch → tests → diff → cleanup (`python -m execution.demo`, `execute_task`).
- ⬜ Route runtime actions through the gateway with canonical tool names and normalized results/events.
- ⬜ Agree approval / elevated-risk representation, retry vs replan semantics, cancellation path.

**Role 2 (desktop UI)** — nothing in this repo yet
- ⬜ Stable APIs for git status/diff, worktree metadata, running commands + output retrieval, patch/conflict reporting, test results, execution state/errors.
- ⬜ Define semantics of review / apply / discard / commit.

**Role 3 (codebase intelligence)** — `intelligence/` modules are currently empty placeholders
- ⬜ Canonical workspace/worktree identity and version identifier.
- ⬜ File-change and worktree create/remove notifications; index invalidation/refresh interface.
- ⬜ Integration test: worktree → search symbol → modify via Role 4 → refresh → new content visible, main checkout untouched.

### Phase 13 — Security, testing and performance qualification 🟡 PARTIAL

*Completion criterion: correctness, security, recovery and performance criteria are met on representative workloads, not just the happy path.*

- ✅ Unit/integration tests: patching, runner, test runner, git operations, worktrees, executor events, `RealActionExecutor`, plus full Role 1 suites (202 passing).
- ⬜ Tests against richer fixture repos (dirty tree, conflicting patch, failing build, multiple languages).
- ⬜ Failure-injection suite (invalid/stale patch, missing executable, timeout, orphan child, permission failure, git lock, cleanup failure, test setup error, corrupted report, artifact-store failure, restart mid-run).
- ⬜ Security suite (traversal, symlink, blocked commands, network/FS limits, secret leakage, permission bypass, cross-task writes, cleanup scope).
- ⬜ Performance benchmarks (command overhead, read/patch latency, git status/diff, worktree create/remove, test overhead, memory, concurrency, artifact volume).
- ⬜ Documented execution limitations (started in `API_CONTRACTS.md`).

---

## 2. Known issues found during the audit

Fix these as early slices; they are small and risk-reducing.

| # | Issue | Location | Why it matters |
| - | ----- | -------- | -------------- |
| 1 | ~~`task_id` not validated before being joined into a path and branch name~~ **Fixed (slice A1)** | `execution/git/worktree.py` | Was: `../` could place a worktree (and a later `--force` removal) outside `.aseos_worktrees` |
| 2 | `execute_task` always calls `remove_worktree` in `finally`, with `--force` and `branch -D` | `execution/executor.py`, `worktree.py` | Deletes the agent's changes (including a passing result) before anyone reviews them |
| 3 | `run_command` accepts any `cwd` and inherits the full environment | `execution/commands/runner.py` | No workspace enforcement; secrets leak into child processes |
| 4 | Timeout kills only the direct child; output is unbounded | `execution/commands/runner.py` | Orphaned grandchildren; memory blow-up on noisy commands |
| 5 | `CommandVerifier` uses `create_subprocess_shell` with string commands | `runtime/verification/command.py` | Shell injection surface; bypasses execution boundary (Role 1 owns, needs coordination) |
| 6 | `apply_patch` uses default text encoding / newline handling | `execution/patches/apply.py` | Can corrupt CRLF files or non-UTF-8 platform defaults on Windows |
| 7 | `READ` has no size limit; `SEARCH` skips undecodable files silently | `runtime/agent/real_executor.py` | Large files flood the model; silent partial results |
| 8 | `git_diff`/`git_status` use `check=True` and raise (gateway path categorizes; the helpers do not) | `execution/git/operations.py` | Non-repo or git failure surfaces as exceptions, not a categorized result |
| 9 | Empty placeholder modules: `execution/git/status.py`, `execution/git/diff.py`, `execution/tests/runner.py`, `execution/worktrees/__init__.py`, `execution/patches/__init__.py` | `execution/` | Two layouts (`git/`, `worktrees/`, `testing/`, `tests/`) cause confusion; pick one |
| 10 | ~~Root `pytest` collected the intentionally failing calculator fixture~~ **Fixed (slice A2)**: `testpaths = ["tests", "execution/tests"]` | `pyproject.toml` | Default suite is green |
| 11 | `fixtures/repositories/calculator` is tracked as an embedded Git repository (gitlink `160000`, no `.gitmodules`), not plain files | repo root | Fixture contents are not part of this repo's history and a fresh clone gets an empty directory; vendor the files or use a proper submodule |
| 12 | `TestRunResult` exists but `run_tests` returns an untyped dict with no counts | `runtime/schemas/execution.py`, `execution/testing/runner.py` | Pass/fail counts never reach the runtime |

---

## 3. Slice backlog (suggested order)

Follows the roadmap's order: contracts → dispatcher → filesystem → patches → git → worktrees → commands →
sandbox → tests → verification → recovery/artifacts → integration/security. Each bullet is one slice.

**Slice group A — Contracts & quick safety fixes**
- ✅ A1. Validate `task_id` in worktree create/remove + tests (issue 1).
- ✅ A2. `pyproject.toml` `testpaths` so the default suite is green (issue 10). Also fixed git/command output decoding and made the git-operations tests use a temp repo instead of the live checkout.
- ✅ A3. `ErrorCategory`, `ArtifactRef` and backward-compatible `ExecutionResult` extensions + `docs/EXECUTION_CONTRACT.md`.
- ⬜ A4. Agree canonical tool names with Role 1 (needs Role 1 input); add an alias table (old → new).

**Slice group B — Gateway**
- ✅ B1. `Tool` protocol + registry-backed `ExecutionGateway`; `RealActionExecutor` delegates to it.
- ✅ B2. All six action types migrated (`RUN_COMMAND` registered but capability-gated).
- 🟡 B3. Central error mapping and capability checks done; **dispatcher events** pending.
- 🟡 B4. Gateway timeout done; **cancellation** plumbing and process-tree handling pending.

**Slice group C — Filesystem & patches**
- C1. Read limits (size, line range, binary detect) → C2. list/metadata/exists → C3. symlink policy tests.
- C4. Safe write tool with expected-hash conflict check (worktree only).
- C5. Patch schema + parser (unified diff) → C6. validate-only → C7. apply single file with recovery artifact → C8. multi-file/create/delete → C9. provenance.

**Slice group D — Git & worktrees**
- D1. Consolidate git status/diff modules; error mapping. D2. Staged/untracked/rename/stat/base-explicit diff. D3. Repo identity.
- D4. Worktree metadata registry. D5. Dirty-check before removal; stop auto-removing in `execute_task`. D6. Managed-area-only cleanup. D7. Locking. D8. Restart reconciliation.
- D9. Mutating git ops (branch → stage selected → commit → scoped discard), each separately.

**Slice group E — Commands & sandbox**
- E1. cwd enforcement + env policy. E2. Output caps + truncation metadata. E3. Process-tree kill + orphan test (Windows and POSIX). E4. Cancellation. E5. Command policy + enable `RUN_COMMAND` for an allow-list only.
- E6. `SECURITY_MODEL.md` + threat model. E7. Isolation strategy decision (ADR). E8. First isolation layer (limits/env/network) + sandbox tests.

**Slice group F — Tests & verification**
- F1. Detect pytest/unittest from config; use workspace interpreter. F2. JUnit XML parse → populate `TestRunResult`. F3. Targeted runs. F4. npm/Maven/Gradle adapters (one per slice).
- F5. Record workspace revision on each test run; F6. staleness check; F7. check kinds (build/lint/typecheck/file/diff/prohibited-path); F8. hand evidence to Role 1's verifier; route `CommandVerifier` through the runner.

**Slice group G — Artifacts, recovery, integration**
- G1. Artifact store + bounded results. G2. Redaction. G3. Execution records + running-ops table. G4. Reconcile on restart. G5. Failure-injection suite.
- G6. Role 1 end-to-end through the gateway. G7. Role 2 API surface. G8. Role 3 change notifications + index-refresh test. G9. Security + performance suites.

---

## 4. Role 4 completion checklist

Mark ✅ only after implementation **and** acceptance tests pass.

**A. Execution foundations**
- [x] Audit the execution scaffold and current runtime contracts
- [~] Implement the execution gateway and typed tool adapters — executor exists, no gateway/typed adapters
- [~] Normalize errors, output and execution metadata — `ExecutionResult` only; no taxonomy/extensions

**B. Files and patches**
- [~] Implement secure filesystem read operations — READ/SEARCH inside workspace; no limits/metadata/listing
- [ ] Implement validated filesystem writes and conflict detection
- [~] Implement patch validation and controlled application — single-file text replace
- [ ] Preserve patch provenance and failure evidence

**C. Git and isolation**
- [~] Implement Git status and diff — basic status, unstaged diff
- [ ] Preserve existing user changes during operations
- [~] Implement worktree creation and lifecycle management — create/remove only
- [ ] Add safe cleanup, recovery and task isolation

**D. Commands and sandbox**
- [~] Implement bounded process execution — list args, timeout; no output/resource bounds
- [~] Add timeouts, cancellation and child-process cleanup — timeout only
- [ ] Define command/environment permissions
- [ ] Implement and test the selected isolation strategy

**E. Testing and verification**
- [~] Implement test discovery and runner adapters — hardcoded pytest
- [ ] Normalize machine-readable test results
- [ ] Associate results with workspace versions
- [~] Provide actual verification evidence to Role 1 — raw results via `execute_task` / verifier; no versioning

**F. Artifacts and recovery**
- [ ] Add bounded output and artifact references
- [ ] Implement redaction and execution audit records
- [ ] Handle interruption, concurrency and crash recovery

**G. Integration and quality**
- [~] Connect real tools to the Role 1 runtime — `RealActionExecutor` wired; no gateway
- [ ] Integrate output, diffs and tests with Role 2
- [ ] Coordinate index refreshes and worktrees with Role 3
- [~] Complete security, failure-injection and performance tests — unit/integration only

`[x]` done · `[~]` partial · `[ ]` pending — **1 / 12 / 13** of 26.

---

## 5. Definition of "Role 4 complete"

The defining end-to-end demonstration, in an isolated worktree, on a real repository:

1. Runtime requests a registered tool with validated arguments.
2. Gateway checks permissions, workspace and resource limits.
3. The tool operates on the intended repo/worktree.
4. Filesystem, Git, patch and command ops return normalized, traceable results.
5. Tests run under the defined policy.
6. Verification receives evidence tied to the code version tested.
7. The desktop can inspect logs, changed files, diffs and test reports.
8. Repository intelligence refreshes after source changes.
9. Cancellation, timeouts, conflicts and process failures are handled safely.
10. Interrupted tasks reconcile without repeating mutations.
11. Security tests demonstrate the boundary holds.
12. End-to-end tests show a real modification, real verification and safe failure handling.

**Where we are today:** items 1, 3 and 4 work in baseline form (`python -m execution.demo` shows worktree →
patch → tests → diff → cleanup). Items 2, 5–12 are partial or pending, and cleanup currently destroys the result
rather than preserving it for review.

## 6. Cross-role agreements still to finalize

| Shared contract | Roles | Status |
| --------------- | ----- | ------ |
| Tool names, schemas, execution results | 1 & 4 | ⬜ names not aligned; result schema is baseline only |
| Task / action / event lifecycle | 1 & 4 (2 presents) | 🟡 events exist; execution-side events limited to `execute_task` |
| Approval & elevated-risk operations | 1 & 4 (2 UI) | 🟡 Role 1 policy treats `RUN_COMMAND` as high-risk; Role 4 refuses it outright |
| Workspace identity & path conventions | 2, 3, 4 (+1) | ⬜ |
| Repo-context refresh after edits | 3 & 4 | ⬜ |
| Worktree ownership & lifecycle | 1 & 4 | ⬜ → `WORKTREE_LIFECYCLE.md` |
| Diff / patch / finalization semantics | 2 & 4 | ⬜ |
| Test results & verification status | 1 & 4 (2 presents) | 🟡 |
| Artifact references & error formats | 1, 2, 4 (3 sources) | ⬜ |
| Failure recovery & cancellation | 1 & 4 (2 controls) | ⬜ |

### Integration rules (unchanged)

1. Don't duplicate security policy in prompts — enforce it in trusted, testable code at the execution boundary.
2. Every significant command, patch and Git op is traceable: task, action id, workspace, normalized result.
3. Preserve user work by default — isolated worktrees, explicit paths, scoped cleanup.
4. Test real side effects — integration tests inspect the real filesystem and Git state; a mock `"success": true` proves nothing.
