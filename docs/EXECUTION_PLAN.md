# ASEOS — Role 4 Execution Plan & Status Tracker

Execution, Git, worktrees, command running, sandboxing and testing.

- **Status snapshot date:** 2026-10-23
- **Branch audited:** `execution` (HEAD `a91605f`)
- **Test baseline:** `python -m pytest -q` → 1082 passed, 6 skipped (symlink/POSIX-mode tests and the real-container test skip where unavailable), 0 failed (default `testpaths` now exclude the
  intentionally failing `fixtures/` repo). Before the first slices it was 202 passed + 1 fixture failure.
- **Progress log:** slices A1–A3, B1–B2, C1–C4 (Phase 2 filesystem) C5–C9 (Phase 3 patches) D1–D3, D9 (Phase 4 Git) D4–D8, D11 (Phase 5 worktrees) E1–E5 (Phase 6 commands) H1–H6 (Phase 7 security) F1–F6 (Phase 8 tests) and F7–F8 (Phase 9 verification) and G1–G3 (Phase 10 artifacts, redaction, records) are done; B3/B4 are partly done (see [Slice backlog](#3-slice-backlog-suggested-order)).

Companion documents: [ARCHITECTURE.md](ARCHITECTURE.md) (how the system fits together),
[API_CONTRACTS.md](API_CONTRACTS.md) (baseline runtime ↔ execution contract),
[EXECUTION_CONTRACT.md](EXECUTION_CONTRACT.md) (gateway pipeline, tools, result fields, error categories),
[SECURITY_MODEL.md](SECURITY_MODEL.md) (threat model, isolation, limits),
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
| 2 | Safe filesystem tools | 🟡 PARTIAL | Bounded reads, line ranges, list/info, hash-checked atomic writes, symlink policy done; change notifications (Role 3) pending |
| 3 | Patch validation & application | 🟡 PARTIAL | Unified-diff validate/apply (create/modify/delete, multi-file, base hashes, rollback, reverse) done; persisted recovery artifacts + task-level provenance pending |
| 4 | Git status, diff & operations | 🟡 PARTIAL | Structured status/diff/info and scoped stage/commit/branch/discard done; worktree-lifecycle protection of user work (Phase 5) pending |
| 5 | Worktrees & task isolation | 🟡 PARTIAL | Registry, base commits, safe/backed-up removal, locking, reconciliation, gateway tools done; retention policy and live-owner leases pending |
| 6 | Controlled command execution | 🟡 PARTIAL | Hardened runner (env policy, output bounds, verified tree kill, cancel, limits), command policy and `shell.run` done; POSIX paths unverified, no sandbox yet |
| 7 | Sandboxing & execution security | 🟡 PARTIAL | Threat model, path/secret hardening, levels & profiles, container backend with fail-closed enforcement done; **real-container proof pending** |
| 8 | Test discovery & execution | 🟡 PARTIAL | Discovery, 7 adapters, JUnit/JSON/console parsing, normalized statuses, targeted runs, version fingerprint + freshness done; only Python adapters run against real tools |
| 9 | Deterministic verification evidence | 🟡 PARTIAL | 8 check kinds with traceable evidence, stale/unstable detection, next-step hints and a Role 1 `Verifier` adapter done; Role 1 sign-off, artifact refs (Phase 10) and a `verify.run` action pending |
| 10 | Artifacts, retention & audit | 🟡 PARTIAL | Artifact store, retention/quota, redaction, execution records, evidence consistency checks, `artifact.read`/`list` and store-failure handling done; Role 1 event forwarding, gc scheduler and report/trace artifacts pending |
| 11 | Concurrency, interruption & recovery | ⬜ PENDING | — |
| 12 | Cross-role integration | 🟡 PARTIAL | Role 1 wired in; Role 2 and Role 3 pending |
| 13 | Security, testing & performance qualification | 🟡 PARTIAL | Unit/integration tests only |

**Role 4 completion checklist: 12 ✅ done · 13 🟡 partial · 1 ⬜ pending (of 26)** (an item flips only when its full acceptance criterion is met). See [Section 4](#4-role-4-completion-checklist).

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

- ✅ Read a file by workspace-relative path with `resolve()` + `is_relative_to` containment (blocks `..` and symlink/junction targets that resolve outside).
- ✅ Content search with ignore list (`.git`, `.venv`, `venv`, `__pycache__`, `node_modules`) and a 100-match cap.
- ✅ Missing-file and non-string path errors reported as failed results.
- ✅ Directory listing (`repo.list_dir`), file metadata + path-exists (`repo.file_info`, missing path is success with `exists=false`).
- ✅ Line-range reads (1-based inclusive, end clamped) and bounded content (`max_bytes` ≤ 1 MB, `truncated` flag, `data.sha256/total_bytes/total_lines`).
- ✅ Binary detection (NUL in first 8 KB) and strict UTF-8 decoding return categorized `INVALID_ARGUMENTS`. Other encodings are not supported.
- ✅ Explicit symlink policy (documented in `fs_tools.py`): resolved path must stay in the workspace; reads follow in-workspace links; writes refuse to go through a symlink; `list_dir` hides escaping links; `.git` is write-protected. Tested with a real directory junction on Windows; the 4 symlink-file tests skip when symlinks are unavailable.
- ✅ File-size and output-size limits: reads refuse files >10 MB (`RESOURCE_LIMIT`), return ≤1 MB; writes ≤1 MB; list ≤1000 entries.
- ✅ Controlled write tool `repo.write`: create new files, or overwrite only with a matching `expected_sha256`; atomic temp-file + `os.replace`; post-write checksum verification; structured result (`sha256`, `previous_sha256`, `created`, `changed_files`).
- ✅ Conflict detection: stale or missing-file hash → `PATCH_CONFLICT` with expected/actual hashes; re-checked just before replace. A tiny race window between the re-check and `os.replace` remains (no OS-level file lock).
- ⬜ Change notification to subsystems (Role 3 index) after writes.
- ✅ Filesystem tests: `execution/tests/test_fs_tools.py` (32 tests).

### Phase 3 — Patch validation and application 🟡 PARTIAL

*Completion criterion: valid patches produce expected changes, conflicting patches fail visibly, and applied changes are reviewable and traceable to the originating action.*

- ✅ Exact-text replace patch `{file, old, new}` (`execution/patches/apply.py`).
- ✅ Path containment check, file-exists check, empty-target rejection, not-found and ambiguous (multiple-occurrence) rejection; failures carry an error `category` (`PATCH_CONFLICT`, `PERMISSION_DENIED`, …).
- ✅ Tests in `execution/tests/test_patch.py`.
- ✅ Canonical patch format: unified diff (plain `diff -u` and `git diff`), parsed by `execution/patches/unified.py`. Rejects renames/copies, mode changes, binary patches, quoted paths with a clear error. The legacy `{file, old, new}` replace remains as `patch.replace` (`EDIT`).
- ✅ New-file, delete-file and multi-file patches. Deletion needs the `fs.delete` capability, which is **not** granted by default.
- ✅ Optional per-file `base` sha256 map; mismatch → `PATCH_CONFLICT` with expected/actual hashes. Files are re-compared immediately before writing (and just before each replace), so a file that changed after validation is not overwritten.
- ✅ Protected paths (`.git/**`, `.env`, `.env.*` except `.example/.sample/.template`; symlinks refused) enforced by a shared `ensure_writable_path` also used by `repo.write` and `patch.replace`. Limits: 200 KB patch, 20 files, 10 MB per file.
- 🟡 All files are validated in memory first; writes are atomic per file; if a later step fails earlier files are restored (best-effort). `data.rolled_back` / `data.partial` / `data.unrestored_files` say exactly what happened, and the result's `applied_diff` can be fed back with `reverse=true` to undo a patch. Every applied or rejected patch is now kept as an audit artifact (Phase 10); not done: automatic recovery from those artifacts and true multi-file atomicity.
- ✅ Result is built from disk after writing: per-file action, sha256 before/after, additions/deletions, hunk offsets and an `applied_diff` regenerated from the real before/after bytes (≤1 MB, `applied_diff_truncated`). Post-write verification re-reads every file.
- 🟡 Result carries `patch_id` (sha256 of the patch text) and the originating `action_id`. The patch text is persisted as an audit artifact and the execution record links `patch_id`, `action_id` and (when the gateway is given one) `task_id`.
- ✅ Conflicts report the path, hunk number, expected vs found lines, ambiguous candidate line numbers, or expected/actual hashes in `data`.
- ✅ Patch engine: explicit UTF-8, CRLF/LF preserved (added lines use the file's dominant line ending), missing-final-newline handled, file permission bits preserved on POSIX. 🟡 The legacy `apply_patch` text replace (`patch.replace`, used by `execute_task`) still uses platform default encoding.

### Phase 4 — Git status, diff and repository operations 🟡 PARTIAL

*Completion criterion: the agent can inspect real repository changes and perform only authorized Git operations against the intended repo or worktree.*

- ✅ Consolidated: `execution/git/repo.py` (hardened runner), `status.py`, `diff.py`, `info.py`, `mutate.py`. `operations.py` is now a thin wrapper kept for `execute_task` and the demo.
- ✅ `git.status` (`GIT_STATUS`): branch, upstream, ahead/behind, detached flag, staged / unstaged / untracked / conflicted paths, per-entry add/modify/delete/rename status, in-progress merge/rebase/cherry-pick/revert/bisect state; unborn repos work.
- ✅ `git.diff` (`GIT_DIFF`) with an explicit base: `unstaged` (working tree vs index), `staged` (index vs HEAD), `base` (working tree vs a commit/branch, resolved to a full hash). Per-file status, rename similarity, additions/deletions, binary flag, changed-file list, path filter, `max_bytes` bound with `truncated`/`total_bytes`. Untracked files appear as added files (≤50 files, ≤200 KB each; skipped ones are listed).
- ✅ `git.info` (`GIT_INFO`): repository root, git dir, linked-worktree flag, HEAD, current/default branch, branch list, recent commits, and the config relevant to committing (user set?, gpgsign).
- ✅ Failures are categorized (`GIT_FAILURE`, `TIMEOUT`, `PROCESS_FAILURE`, `INVALID_ARGUMENTS`), including `data.lock_conflict` for index-lock clashes.
- ✅ Hardening: argv only, `--literal-pathspecs` (no globs), `core.fsmonitor` disabled (a repo can use it to run programs), `--no-ext-diff`/`--no-textconv`, `GIT_DIR`-style environment redirection removed, prompts disabled, refs checked with `--end-of-options`, `--no-optional-locks` for reads. The workspace must *be* the repository/worktree root, so a nested workspace can never read or change an outer repository.
- ✅ Mutating tools, each needing a capability that is **not granted by default**: `git.stage`, `git.commit`, `git.branch_create` (`git.write`) and `git.discard` (`git.discard`). See EXECUTION_CONTRACT §2.3 for the exact rules.
- ✅ Preserving user work: named files only (no `.`/dirs/globs, `.env` and `.git` refused); `commit` refuses unless the index holds *exactly* the named paths; refuses detached HEAD and merge/rebase/conflict states; `branch_create` never checks out or moves a branch; `discard` only touches tracked, unstaged files and returns the discarded diff (re-applicable via `patch.apply`); every mutation reports `other_changes` (dirty paths outside its scope, observed before it ran) and the status counts after.
- ✅ Tests with dirty trees and pre-existing user changes: `test_git_read_tools.py`, `test_git_mutations.py` (81 tests, real repos incl. linked worktree, merge conflict, upstream, lock file, fsmonitor).
- 🟡 Not covered: pushing/fetching, checkout/switch, reset, stash, rebase (deliberately out of scope); staging cannot tell the agent's edits from the user's earlier edits *inside the same file* (use an isolated worktree, or pass `expected_sha256`); hooks and signing configured in the repo still run on `git.commit`; diff text is bounded after Git produces it, not while streaming; the complete diff of an oversized result is kept as an artifact (Phase 10).

### Phase 5 — Git worktrees and task isolation 🟡 PARTIAL

*Completion criterion: each code-writing task runs in a known workspace, the main checkout is preserved, and cleanup/recovery never loses unreviewed changes.*

- ✅ `WorktreeManager` (`execution/worktrees/`): `create(task_id, base, owner)`, `get`, `list`, `find_live`, `assess`, `mark`, `remove`, `reconcile`, `prune_missing`. The old `create_worktree` / `remove_worktree` helpers are thin wrappers over it.
- ✅ Layout: `<repo>/.aseos_worktrees/<task_id>` on branch `aseos/<task_id>`, created from an explicit **base commit** (`base` ref resolved to a hash; default `HEAD`). The main checkout is never modified; its uncommitted edits are neither visible to nor touched by the task. `.aseos_worktrees/` is added to `.git/info/exclude`, so it does not show up in the main checkout's `git status`.
- ✅ Metadata record per worktree (`worktree_id`, `task_id`, `repo_id`, `repo_root`, `path`, `branch`, `base_ref`, `base_commit`, `owner`, `status`, timestamps, note, backups) stored as JSON in `<git common dir>/aseos/worktrees/` (not tracked, shared by all worktrees). The record is the authority; a caller-supplied path is never trusted.
- ✅ Statuses: `creating` → `active` → `completed` / `failed` / `abandoned`; `missing`; `removing` → `removed`. `creating` and `removing` are persisted *before* the risky step.
- ✅ `assess()` observes real state (disk + Git): exists, registered in Git, locked, branch exists, staged/unstaged/untracked/conflicted paths, in-progress operation, commits ahead of the base, whether those commits also exist on another ref, and a list of `cleanup_blockers`.
- ✅ `task_id` validated; one live worktree per task; an existing directory or branch without a matching record is refused, never reused or deleted.
- ✅ Safe removal: only the recorded path, which must equal the managed layout, is eligible. Refused for uncommitted changes, commits that exist nowhere else, locked worktrees, directories Git does not know, and tampered records. Uses `git worktree remove` (no `--force` unless the caller confirmed discarding).
- ✅ Confirmed discard (`discard_confirmed=True`, trusted callers only) backs up first: a patch of uncommitted changes (including untracked text files) under `<common>/aseos/removed/`, and `refs/aseos/removed/<id>` pointing at unmerged commits. The result says if the patch could not capture something (binary / oversized untracked files). The branch is deleted only when nothing is lost.
- ✅ `execute_task` no longer destroys its own work: the worktree is kept (`completed`/`failed`) whenever it holds changes; it is removed automatically only if clean. `keep_worktree=False` still refuses to lose work. The reported diff is now computed against the base commit and includes new files.
- ✅ Concurrency: registry and Git-administration changes are serialized by a reentrant in-process lock plus an exclusive lock file (stale after 300 s); Git lock clashes are retried briefly. Tested with 6 threads, 5 racing creates of the same task (exactly one wins) and 3 separate processes.
- ✅ Restart reconciliation (`reconcile`): compares records, `git worktree list` and the disk; recovers interrupted create/remove; marks vanished directories `missing`; **reports** orphan directories, unrecorded Git worktrees, detached directories and corrupt records. It changes record statuses only and deletes nothing. Stale Git entries are dropped only by an explicit `prune_missing`, which keeps the branch.
- ✅ Gateway tools (all need `worktree.manage`, not granted by default; `git.worktree.list` needs `git.read`): `git.worktree.create`, `.list`, `.remove`, `.reconcile`. `discard_unreviewed` on remove works only if the *trusted caller* granted `worktree.discard` — the model cannot authorize its own discard. The tools refuse to run from inside a worktree.
- ✅ Tests: `test_worktree_manager.py` (53), `test_worktree_tools_and_executor.py` (19), plus the legacy `test_worktrees.py`.
- 🟡 Not covered: automatic cleanup policies/retention (nothing is ever deleted unless a trusted caller asks); `git worktree lock` is only *respected*, not set by us; no lease/heartbeat to detect a *live* owner process (two callers that both know a worktree id can still use it concurrently — the gateway does not stop that); a worktree on a removed drive looks `missing`; the stale-lock timeout is time-based because process liveness cannot be checked portably; submodules inside worktrees are not handled.

### Phase 6 — Controlled command execution 🟡 PARTIAL

*Completion criterion: approved commands run against the correct workspace; output and errors are captured; timeouts/cancellations leave no uncontrolled processes.*

- ✅ One hardened runner, `execution/commands/process.py::run_process`, behind everything that starts a process (`shell.run`, `test.run`, and the trusted `run_command` helper): argv only (never a shell), stdin closed, explicit executable resolution against the *controlled* `PATH`, UTF-8 decoding with replacement.
- ✅ Working directory must exist and sit inside the workspace (`invalid_cwd` otherwise); symlink/junction escapes are resolved first.
- ✅ Environment policy (`commands/env.py`): nothing is inherited by default. A small allow-list of platform variables passes (`PATH`, `SYSTEMROOT`, `HOME`, ...) minus anything that looks like a secret (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSW*`, ...); code-loading variables (`PYTHONPATH`, `NODE_OPTIONS`, `LD_PRELOAD`) never pass. Callers may add only `CI`, `NODE_ENV`, `PYTHONHASHSEED`, `PYTHONDONTWRITEBYTECODE`, `TZ`, `LANG`, `LC_ALL`, `FORCE_COLOR`, `NO_COLOR`. Each run gets its own `TEMP`/`TMP`/`TMPDIR`, deleted afterwards. Secret values from the parent environment are redacted from captured output.
- ✅ Output is bounded in memory (first 100 KB + last 100 KB per stream, with an omitted-bytes marker) and spooled in full to a log file when larger (capped at 100 MB, `spool_truncated` says if hit). The result carries exact byte totals, truncation flags and `ArtifactRef`s (`command_output`) pointing at the logs. A process that floods stdout and stderr cannot stall or exhaust the runner.
- ✅ Process-tree termination, verified: on timeout, cancellation or orphaned children the whole tree is killed and the result reports `process_tree` (`mechanism`, `leftover`). Windows: Job Object (created suspended, assigned, then resumed — no race), kill-on-close, `leftover` is the job's real active-process count. POSIX: own session / process group, SIGTERM then SIGKILL, `leftover` probed with `killpg(0)`. Processes still running after the main process exits are treated as orphans and killed.
- ✅ Resource limits where the OS gives them: Windows Job Object caps the active-process count (default 256) and per-process memory (default 4 GiB); POSIX (Linux) disables core dumps and caps file size via `prlimit`. See limitations below.
- ✅ Cancellation and shutdown end to end: `ToolContext.cancel` is set by the gateway on timeout, on `gateway.cancel(action_id)`, `cancel_all()`, `await gateway.shutdown()` and when the awaiting asyncio task is cancelled. The gateway timeout no longer orphans the worker: it asks the tool to stop, waits for it, and returns `TIMEOUT` with the partial output (`CANCELLED` for explicit cancels).
- ✅ Failure kinds are distinguished: missing executable, invalid cwd, invalid environment, unsafe batch-file argument, start failure, non-zero exit, timeout, cancellation, output overflow. Windows `.cmd`/`.bat` targets reject arguments containing shell metacharacters.
- ✅ Command policy (`commands/policy.py`), default-deny, enforced in trusted code inside `shell.run`: bare command names only; shells, destructive utilities, network clients, container tools and `git` (use `git.*`) are refused with a reason; allowed forms exist for `python -m pytest|unittest|mypy|ruff|flake8|black|compileall`, `pytest`, `ruff`/`black` (no fixing/formatting in place), `mypy`, `flake8`, `npm|pnpm|yarn` (`test`, `run test|lint|build|typecheck|check`), `node --version`, `tsc --noEmit`, `eslint` (no `--fix`), `mvn` lifecycle goals, `cargo test|check|clippy|build|doc|fmt --check`, `go test|vet|build`. Inline code (`-c`), arbitrary scripts, pytest plugins (`-p`, `-o`) are refused. Every path-like argument must resolve inside the workspace; URLs are refused. Installs (`pip install`, `npm install`) are classified `install`/high risk and need `shell.network`, which is not granted by default.
- ✅ `shell.run` is now implemented: `{command: argv list | plain string, cwd, timeout_seconds ≤ 600, env}`. Strings are split like POSIX quoting but never run by a shell; pipes, redirects and `&&` are rejected. Needs `shell.run` (not granted by default). Results carry `data.policy`, `data.process_tree`, output byte counts and artifact refs.
- ✅ `test.run` now goes through the same runner, so it also gets the scrubbed environment, tree kill, output bounds and cancellation.
- ✅ Tests: `test_process_runner.py` (38), `test_command_policy.py` (84), `test_shell_tool.py` (28) — real processes, real grandchildren, verified dead afterwards.
- 🟡 Not covered / honest limits: **the policy decides what may start, not what an allowed program does** — a test suite is arbitrary code and can still read files, use the network or spawn processes within the limits (that is Phase 7). `shell.network` gates *install-type commands* only; network access is not blocked. `HOME`/`USERPROFILE` are passed through, so tools can read user-level config (`.npmrc`, `.pypirc`). The POSIX code paths (process group, `prlimit`) are written and unit-tested for logic but were **not run on a POSIX machine** in this environment. POSIX has no memory or process-count limit yet (cgroups/containers are Phase 7). Command output beyond the inline buffers is now moved into the artifact store (redacted, owned, retained, capped) and the raw spool file is deleted (Phase 10). Streaming logs are not implemented (results are returned when the command ends). `runtime/verification/command.py` (Role 1) still uses `create_subprocess_shell` and has not been moved onto this runner.

### Phase 7 — Sandboxing and execution security 🟡 PARTIAL

*Completion criterion: a defined, tested security boundary; high-risk operations cannot gain permissions because the model asks.*

- ✅ `docs/SECURITY_MODEL.md`: trust boundaries, threat model (11 threats with controls and honest status), isolation decision and why, execution levels/profiles, known limitations, audit findings, test map.
- ✅ Audit of existing code found and fixed real holes (each with a regression test): Windows device names and NTFS alternate data streams in paths; secret files readable/searchable/hashable/patchable and leaking through `git.diff` and patch-conflict details; other tasks' worktrees readable and writable through the main checkout's file tools; malformed paths surfacing as `INTERNAL_ERROR`. Case-variant and alias spellings of `.git`/`.env` were checked and were already closed.
- ✅ Shared path rules (`execution/security/paths.py`) used by every tool: raw-path validation, write protection (`.git`, `.env*`, managed worktree area), secret-bearing file detection (`.env*`, keys, `.npmrc`, `.ssh/`, …) with the `fs.read_secrets` override, template files (`.example`, `.sample`) exempt.
- ✅ Risk-based execution levels (`READ_ONLY`, `LOW_RISK_MUTATION`, `DEV_EXECUTION`, `ELEVATED`) declared by every tool (unclassified tools default to the highest) and capability profiles (`read_only`, `worktree_edit`, `dev`, `elevated`) enforced by the gateway as a ceiling, in addition to capabilities. Payload fields cannot change either. `gateway.describe_tools()` reports level, capabilities, timeout and availability. *(The level names are proposed; they still need Role 1's agreement.)*
- ✅ Per-tool permissions and resource limits: capabilities + level + timeout per tool (`describe_tools`), command limits (timeout ≤600 s, output caps, Windows job limits, container pid/memory limits).
- ✅ Isolation strategy decided: layered, with a container sandbox (Docker/Podman) behind a replaceable `Sandbox` interface. `ContainerSandbox` builds a locked-down `run` command (no network, read-only root, only the workspace mounted, caps dropped, non-root, pid/memory/cpu limits, explicit `-e` environment only) and terminates/cleans up the named container on timeout/cancel. `shell.run` and `test.run` use it when configured.
- ✅ Fail closed: profiles `dev`/`elevated` require a sandbox; with none configured, or the configured one unavailable, commands are refused (`PERMISSION_DENIED`) and never run on the host. A broken sandbox backend is also treated as a refusal.
- ✅ Sandbox/security tests: path & secret tests (`test_path_security.py`, 79), profiles/levels/cross-task isolation (`test_profiles_and_isolation.py`, 19), sandbox command line, environment, fail-closed, plumbing (`test_sandbox.py`, 33 + 1 opt-in).
- 🟡 **The container boundary itself is not yet proven.** No Docker daemon was running in the development environment, so the real-container test (network blocked, host paths invisible, read-only root) was written but skipped. The command line, environment filtering and lifecycle are verified against a stand-in engine, not a container. Run `execution/tests/test_sandbox.py` on a machine with Docker and the `python:3.12-slim` image, and fix whatever it shows, before relying on the boundary.
- 🟡 Without a sandbox (the default gateway, or `read_only`/`worktree_edit` profiles, which run no programs), allowed programs run as the user with network and file access; secrets are name-detected only; POSIX native limits and process handling are untested here. All listed in `SECURITY_MODEL.md` §6.
- ⬜ Image provisioning (building an image with the project's toolchain), a VM-backed backend for hostile code, native Linux (`bubblewrap`) and Windows (AppContainer) backends, disk quotas.

### Phase 8 — Test discovery and execution 🟡 PARTIAL

*Completion criterion: appropriate tests run; genuine failures are distinguished from infrastructure errors; results are tied to the code version tested.*

- ✅ `test.run` is now a real test service (`execution/testing/service.py`): detect → build command → check it against the command policy → run in the hardened runner (and the sandbox when configured) → read results → normalized `RunReport`. The old `run_tests(worktree)` helper remains only for `execute_task`.
- ✅ Discovery from what the repository documents, with the evidence recorded in `report.evidence`: pytest (`pytest.ini`, `[tool.pytest]`, `tox.ini`, `setup.cfg`, `conftest.py`, test files importing pytest, pytest in requirements), unittest-style tests, npm (`scripts.test`, runner guessed from it), Maven (`pom.xml`), Gradle (`build.gradle[.kts]`), Cargo (`Cargo.toml`), Go (`go.mod`). Several frameworks → the first in a fixed order runs and the others are listed in `report.notes`; `framework=` picks one. Vendored directories are not scanned.
- ✅ Machine-readable results: JUnit XML (pytest `--junitxml`, Maven Surefire, Gradle — only files newer than the run, so a stale report is never read) and `go test -json`. Counts come from the individual cases, not from totals the file claims; a report that disagrees with itself cannot produce a PASS. DOCTYPE/ENTITY declarations are refused (no entity-expansion attacks) and size is bounded. Console summaries (pytest, unittest, Jest/Vitest/Mocha, Cargo) are the fallback and cross-check (deselected counts only exist there).
- ✅ Normalized model (`RunReport`): framework, command, scope (`full`/`targeted`) and targets, status, exit code, start/finish/duration, counts (`total/passed/failed/errors/skipped/deselected`), up to 200 cases (failures first), report source/files/problems, evidence/notes, process info and output-file references. `to_runtime_result()` fills Role 1's existing `TestRunResult`.
- ✅ Statuses distinguish what happened: `PASSED`, `FAILED` (tests failed), `ERROR` (collection/setup errors, crashes, a non-zero exit although every reported test passed, an inconsistent report), `NO_TESTS` (nothing collected — or every test skipped, since nothing was verified), `UNVERIFIED` (exit 0 but no readable results), `TIMEOUT`, `CANCELLED`, `NOT_RUN` (no framework, refused by policy, sandbox missing, program not installed, could not start). Exit 0 alone is never a PASS.
- ✅ Targeted vs full: `targets` (pytest paths/node ids, unittest dotted names, Maven `Class#method`, Gradle `--tests` patterns, Cargo filters, Go packages, npm paths) and pytest `keyword`, all validated (no options, nothing outside the workspace, nonexistent paths refused). Each report says `scope`, so a passing target can never be mistaken for a passing suite.
- ✅ Version association: `workspace_version` = content fingerprint of the workspace (committed, modified and untracked files; generated clutter such as bytecode, caches, `node_modules` and our own report files excluded) plus `head`. Recorded before the run; `modified_during_run` flags tests that change the tree. New tool `test.freshness` (`TEST_FRESHNESS`, read-only) answers whether a result is still current.
- ✅ Configurable timeout (`timeout_seconds`, ≤600).
- ✅ Tests: `test_test_parsers.py` (106) and `test_test_service.py` (45) — real pytest and unittest runs for pass, fail, skip, collection error, syntax error, no tests, all skipped, targeted/keyword, timeout, cancellation, corrupted and missing reports (via a conftest that sabotages the file), workspace-modifying tests, stale/fresh results, plus fixtures for every other parser.
- 🟡 **Only the Python adapters were exercised against real tools** (this machine has no Node, Maven, Gradle, Cargo or Go). Their commands, target validation, policy compatibility and parsers are tested against sample output only; expect to adjust them on first contact with real projects. Maven/Gradle need dependencies, which the network-less sandbox cannot download.
- 🟡 Not covered: using the *project's* interpreter/venv (the controlled `PATH`'s `python` runs the tests, so a project that needs its own virtualenv must provide it on `PATH`), Jest/Vitest JUnit output (console summary only), the Gradle wrapper, parallel/sharded runs, flaky-test retries, coverage, raw JUnit/report files as retained artifacts (only the normalized report and console output are kept).

### Phase 9 — Deterministic verification and recovery evidence 🟡 PARTIAL

*Completion criterion: verification status comes from actual, traceable check results — never from "the plan finished".*

- ✅ Role 1 verification abstraction: `Verifier`, `CommandVerifier`, `VerificationResult`/`VerificationCheck` (`PASS/FAIL/ERROR/TIMEOUT`), runtime integration, replanning with verification evidence, event replay (`runtime/verification/`, `runtime/planner/replanner.py`).
- ✅ `execute_task` produces real patch + test + diff evidence and emits `VERIFICATION_*` events.
- ✅ Supported check kinds (`execution/verification/checks.py`, validated declarative models): `tests`, `build`, `lint`, `typecheck`, `file_exists`, `file_content` (contains / not_contains / sha256), `diff_contains` (added/removed lines, min files changed), `no_prohibited_paths` (prohibited globs and/or an allow-list; staged, unstaged, untracked, conflicted, renamed and — with `base` — committed changes). Every check is established by gateway tool calls, so capabilities, the command policy, path/secret rules and the sandbox all apply. Build/lint/typecheck commands must be of the matching policy category (a "lint" check that actually runs the test suite is an ERROR); a tool that is not installed is an ERROR, not a code failure.
- ✅ Evidence includes the underlying result: every check records its gateway calls (`tool`, unique `action_id`, success, exit code, error category, truncation, artifact refs, output excerpts), the observed facts (counts, failing cases, matched/missing text, violating paths) and a message with the first failing cases. Traces now carry real artifact references (id, size, sha256) retrievable with `artifact.read`, and a PASS is withheld when the evidence is inconsistent (Phase 10).
- ✅ Freshness: test results carry `workspace_version`; a verification run fingerprints the workspace before, after each program-running check and at the end. A PASS is downgraded to ERROR if the tree changed while it ran (`require_stable`, default on), and `report_is_current()` reports staleness later.
- 🟡 One authoritative status: **Role 1's `VerificationStatus` (`PASS/FAIL/ERROR/TIMEOUT`) is the only verdict.** `ExecutionVerifier` implements Role 1's `Verifier` and folds the evidence deterministically (TIMEOUT > ERROR > FAIL > PASS; no checks, a bound-to-another-workspace verifier or a crashed check is never a PASS). `ExecutionResult.success` and `TestRunResult` are inputs, not verdicts. Two optional fields (`kind`, `evidence`) were added to Role 1's `VerificationCheck`/`VerificationResult`; old producers are unaffected. **Role 1 sign-off, and replacing `CommandVerifier` in the real composition, are still open.**
- ✅ Failure evidence suggests the next move: each non-passing check lists `next_steps` from `inspect`, `retrieve_output`, `retest_targeted`, `modify_patch`, `replan`, `stop` (`replan`/`stop` only when the check was blocked, e.g. permission or policy, because retrying cannot help).
- ✅ Tests: `test_verification.py` (80: globs, parsing/validation, every check kind against real repos, stale and unstable workspaces, timeouts, capability/policy blocks) and `test_execution_verifier.py` (10: the Role 1 adapter and the runtime completing or failing on the evidence).
- ⬜ Left for later: a `verify.run` gateway action so a planner can request verification, check specs in Role 1's task/plan schema, verification events from the execution side, regex content conditions (deliberately omitted: unbounded patterns), and seeing changes to git-ignored files (e.g. a modified `.env`) in `no_prohibited_paths`.

### Phase 10 — Execution artifacts, output retention and auditability 🟡 PARTIAL

*Completion criterion: every meaningful execution is traceable to inputs, workspace, outputs and evidence, without exposing unnecessary or sensitive data.*

- ✅ (Baseline) Runtime `EventEmitter`/`EventStore` and replay exist (Role 1); `execute_task` emits `ACTION_*`/`VERIFICATION_*` events.
- ✅ Artifact store (`execution/artifacts/`): an integrity hash (sha256) per artifact, stable opaque ids (`art_<24 hex>`), kept **outside the working tree** (`<git common dir>/aseos/artifacts`, or a per-workspace temp folder for a plain directory) so artifacts never appear in diffs, fingerprints or file tools. Stored: the complete output of any command whose output outgrew the inline buffers, a `test_report` (JSON of the normalized `RunReport`) for every test run, the complete diff when `git.diff` was cut, every applied **and every rejected** patch, and an `execution_record` per action. Results keep a bounded excerpt plus `ArtifactRef`s (`id`, `kind`, `size`, `sha256`, `retention`, `truncated`, `redactions`). The model retrieves content with `artifact.read` (paged by `offset`/`limit`, or `from_end`) and `artifact.list`.
- 🟡 Ownership and retention: every artifact records workspace, action, tool and (when the gateway is given a `task_id`) task. Retention classes: `temporary` (7 days) and `audit` (90 days); per-artifact cap 20 MB (head and tail kept) and a 500 MB store quota that evicts the oldest *temporary* artifacts first and refuses new writes rather than delete audit records. Expired artifacts are unreadable at once; physical cleanup (`store.gc()`) runs when the quota is hit but **there is no background scheduler yet**. Reads are scoped to the owning workspace: another task's artifact is indistinguishable from a missing one.
- ✅ Secret redaction before display and persistence (`redact.py`): environment secret values plus private keys, AWS/GitHub/Slack/Google/`sk-` tokens, JWTs, bearer/basic headers, URL credentials and `password=`/`token:`-style assignments, applied to the output of `shell.run`/`test.run` (inline and stored). Patterns are bounded and tested against hostile input. Raw spool files (previously unredacted files in the temp folder) are moved into the store and deleted. File-reading tools and patches are deliberately *not* rewritten (that would corrupt edits); secret files stay blocked by path rules. Redaction is best-effort: a secret with no recognizable shape and no environment entry is not found.
- ✅ Evidence consistency checks (`execution/artifacts/consistency.py`, `gateway.check_evidence`): exit code vs success vs process status, test counts vs status, patch id vs file list, and for every artifact reference: it exists, belongs to this workspace and action, its stored bytes still match the recorded hash and size, and its size agrees with the byte count the process reported. Verification (Phase 9) turns a would-be PASS into ERROR when any of these disagree.
- 🟡 Evidence-to-action association: the `execution_record` (args digest and bounded redacted summary, level, capabilities, outcome, artifact ids, redaction count) is keyed by the runtime's `action_id`, so the runtime's event stream stays the one task history. `gateway.add_observer()` hands each record to the host. **Role 1 does not yet forward records into its `EventEmitter`.** Reads are not recorded unless `record_reads=True`.
- ✅ Artifact-store failure handling: a missing, unwritable, full or corrupt store never fails or changes an action. The excerpt stays in the result, `data.artifact_errors` says what could not be stored (including a failed audit record), raw spool files are still deleted, and the gateway runs with artifacts disabled when the store cannot be opened (`store_error`).
- ⬜ Left for later: raw JUnit/other report files and build reports as artifacts, execution traces/timelines, a gc scheduler, per-task retention overrides, encryption at rest, cross-process locking of the quota, and tying artifact access to a user/session rather than a workspace.

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
| 2 | ~~`execute_task` always removed its worktree with `--force` and `branch -D`~~ **Fixed (Phase 5)**: work is preserved; removal refuses to lose it | `execution/executor.py`, `worktrees/manager.py` | Was: deleted the agent's changes before anyone reviewed them |
| 3 | ~~`run_command` accepted any `cwd` and inherited the full environment~~ **Fixed (Phase 6)**: scrubbed environment, cwd validation available via `workspace=` | `execution/commands/` | Was: secrets leaked into child processes |
| 4 | ~~Timeout killed only the direct child; output was unbounded~~ **Fixed (Phase 6)**: whole tree killed and verified; output bounded and spooled | `execution/commands/process.py` | — |
| 5 | `CommandVerifier` uses `create_subprocess_shell` with string commands | `runtime/verification/command.py` | Shell injection surface; bypasses execution boundary (Role 1 owns, needs coordination) |
| 6 | `apply_patch` (legacy replace used by `execute_task` and `patch.replace`) uses default text encoding / newline handling; the new unified-diff engine does not have this problem | `execution/patches/apply.py` | Can corrupt CRLF files or non-UTF-8 platform defaults on Windows |
| 7 | ~~`READ` has no size limit~~ **Fixed (slice C1)**; `SEARCH` still skips undecodable files silently and does not report skipped counts | `execution/gateway/fs_tools.py` | Silent partial search results |
| 8 | ~~`git_diff`/`git_status` raised raw `CalledProcessError`~~ **Fixed (slice D1)**: they now raise categorized `GitError`; gateway tools return categorized results | `execution/git/` | — |
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
- ✅ C1. Read limits (size, line range, binary detect) → ✅ C2. list/metadata/exists → ✅ C3. symlink policy tests.
- ✅ C4. Safe write tool with expected-hash conflict check. (Not restricted to worktrees: it works in whatever workspace the gateway is bound to.)
- ⬜ C4b. Notify Role 3 of changed files (needs the Role 3 interface); register `LIST_DIR`/`FILE_INFO`/`WRITE` in Role 1's `ToolRegistry` (needs Role 1 decision).
- ✅ C5. Unified-diff parser (+ reverse) → ✅ C6. validate-only (`patch.validate`) → ✅ C7. apply with rollback and actual-diff reporting (`patch.apply`) → ✅ C8. multi-file/create/delete (`fs.delete` gated) → 🟡 C9. provenance (`patch_id` + `action_id`; persistence pending).
- ⬜ C10. Migrate `execute_task` and the legacy replace onto the unified engine, then retire `apply_patch`.
- ⬜ C11. Register `VALIDATE_PATCH` / `APPLY_PATCH` (and the Phase 2 types) in Role 1's `ToolRegistry`.

**Slice group D — Git & worktrees**
- ✅ D1. Consolidate git status/diff modules; error mapping. ✅ D2. Staged/untracked/rename/stat/base-explicit diff. ✅ D3. Repo identity.
- ✅ D4. Worktree metadata registry. ✅ D5. Dirty-check before removal; `execute_task` no longer auto-destroys work. ✅ D6. Managed-area-only cleanup. ✅ D7. Locking. ✅ D8. Restart reconciliation.
- ⬜ D12. Register the `WORKTREE_*` action types in Role 1's `ToolRegistry`/policy and decide who grants `worktree.manage` / `worktree.discard` (runtime or UI after user approval).
- ⬜ D13. Tell Role 3 about worktree create/remove and give each worktree its own index key (needs the Role 3 interface).
- ⬜ D14. Lease/heartbeat so a second live owner of the same worktree can be detected.
- ✅ D9. Mutating git ops (branch → stage selected → commit → scoped discard), each behind its own capability.
- ⬜ D10. Register `GIT_STATUS`, `GIT_INFO`, `GIT_STAGE`, `GIT_COMMIT`, `GIT_BRANCH`, `GIT_DISCARD` in Role 1's `ToolRegistry` and risk policy (the mutating ones should require approval).
- ✅ D11. `execute_task` uses the structured diff (`mode=base`, untracked included).

**Slice group E — Commands & sandbox**
- ✅ E1. cwd enforcement + env policy. ✅ E2. Output caps + truncation metadata. 🟡 E3. Process-tree kill + orphan test (done and verified on Windows; POSIX written but **not run** here). ✅ E4. Cancellation (+ gateway shutdown). ✅ E5. Command policy + `RUN_COMMAND` for an allow-list only.
- ⬜ E3b. Run the process tests on Linux/macOS (CI) and fix whatever they reveal.
- ✅ H1. Threat model + `SECURITY_MODEL.md`. ✅ H2. Path hardening (Windows names/ADS, protected and managed areas). ✅ H3. Secret-file read policy incl. `git.diff` redaction. ✅ H4. Execution levels and profiles. ✅ H5. Sandbox interface + container backend + fail-closed. ✅ H6. Cross-task isolation tests.
- ⬜ H7. **Run the sandbox tests against a real Docker/Podman daemon** (and add that run to CI); fix what they reveal. Until then the container boundary is unproven.
- ⬜ H8. Agree level and profile names with Role 1; have the runtime pick a profile per task and ask the user before `elevated`.
- ⬜ H9. Image provisioning: build/select an image containing the project's toolchain; decide how dependencies get in without network.
- ⬜ H10. Native fallbacks (bubblewrap on Linux, AppContainer on Windows) and a VM-backed backend for hostile code.
- ⬜ E9. Route Role 1's `CommandVerifier` through `run_process` / the policy (needs Role 1).
- ⬜ E10. Streaming logs for long commands (live output separate from completion).
- E6. `SECURITY_MODEL.md` + threat model. E7. Isolation strategy decision (ADR). E8. First isolation layer (limits/env/network) + sandbox tests.

**Slice group F — Tests & verification**
- ✅ F1. Detect pytest/unittest from config (project interpreter/venv still ⬜). ✅ F2. JUnit XML parse → `RunReport` (+ `to_runtime_result`). ✅ F3. Targeted runs. ✅ F4. npm/Maven/Gradle/Cargo/Go adapters (unverified against real tools).
- ⬜ F4b. Run the non-Python adapters against real projects and fix what breaks. ⬜ F4c. Use the project's own interpreter/venv.
- ✅ F5. Record workspace revision on each test run; ✅ F6. staleness check (`test.freshness`); ✅ F7. check kinds (tests/build/lint/typecheck/file exists/file content/diff contains/prohibited paths); ✅ F8. `ExecutionVerifier` hands the evidence to Role 1 as a `VerificationResult`.
- ⬜ F9. Replace `CommandVerifier` with `ExecutionVerifier` in the real composition (needs Role 1) — same as E9. ⬜ F10. `verify.run` gateway action + check specs in Role 1's task/plan schema. ⬜ F11. Include git-ignored files in the prohibited-path check.

**Slice group G — Artifacts, recovery, integration**
- ✅ G1. Artifact store + bounded results (+ `artifact.read`/`artifact.list`). ✅ G2. Redaction. 🟡 G3. Execution records (done; running-ops table waits for Phase 11). G4. Reconcile on restart. G5. Failure-injection suite.
- ⬜ G1b. Forward execution records into Role 1's `EventEmitter` (needs Role 1). ⬜ G1c. Schedule `store.gc()`; retain raw JUnit/build reports and traces; register `READ_ARTIFACT`/`LIST_ARTIFACTS` in Role 1's `ToolRegistry`.
- G6. Role 1 end-to-end through the gateway. G7. Role 2 API surface. G8. Role 3 change notifications + index-refresh test. G9. Security + performance suites.

---

## 4. Role 4 completion checklist

Mark ✅ only after implementation **and** acceptance tests pass.

**A. Execution foundations**
- [x] Audit the execution scaffold and current runtime contracts
- [~] Implement the execution gateway and typed tool adapters — executor exists, no gateway/typed adapters
- [~] Normalize errors, output and execution metadata — `ExecutionResult` only; no taxonomy/extensions

**B. Files and patches**
- [x] Implement secure filesystem read operations — bounded READ with line ranges, list, file info, SEARCH, symlink policy
- [~] Implement validated filesystem writes and conflict detection — done in the gateway; change notification to Role 3 pending
- [x] Implement patch validation and controlled application — unified-diff validate/apply, multi-file, base hashes, rollback, reverse
- [~] Preserve patch provenance and failure evidence — `patch_id`/`action_id`/conflict details in results; nothing persisted yet

**C. Git and isolation**
- [x] Implement Git status and diff — structured status, staged/unstaged/base diffs with stats, repo info
- [x] Preserve existing user changes during operations — scoped Git mutations, base-commit worktrees that never touch the main checkout, removal that refuses (or backs up) unreviewed work
- [x] Implement worktree creation and lifecycle management — registry, explicit base, statuses, assessment, gateway tools
- [~] Add safe cleanup, recovery and task isolation — safe removal, backups, reconcile and locking done; retention policy and live-owner leases pending

**D. Commands and sandbox**
- [x] Implement bounded process execution — argv only, scrubbed env, bounded + spooled output, Windows job limits
- [x] Add timeouts, cancellation and child-process cleanup — tree kill verified on Windows; gateway cancel/shutdown
- [x] Define command/environment permissions — default-deny command policy, env allow-list, capability-gated installs
- [~] Implement and test the selected isolation strategy — container backend, profiles and fail-closed enforcement built and tested against a stand-in engine; not yet run against a real daemon

**E. Testing and verification**
- [~] Implement test discovery and runner adapters — 7 adapters built; only pytest/unittest proven against real tools
- [x] Normalize machine-readable test results — JUnit XML / `go test -json` / console into `RunReport` and Role 1's `TestRunResult`
- [x] Associate results with workspace versions — content fingerprint + `test.freshness`
- [~] Provide actual verification evidence to Role 1 — 8 check kinds, versioned traceable evidence and an `ExecutionVerifier` that works in the runtime are done; Role 1 has not adopted it yet and artifact references wait for Phase 10

**F. Artifacts and recovery**
- [x] Add bounded output and artifact references — bounded excerpts + retrievable artifacts with hashes, ownership and retention
- [~] Implement redaction and execution audit records — pattern + environment redaction and per-action audit records done; records are not yet forwarded into Role 1's event stream, redaction is best-effort
- [ ] Handle interruption, concurrency and crash recovery

**G. Integration and quality**
- [~] Connect real tools to the Role 1 runtime — `RealActionExecutor` wired; no gateway
- [ ] Integrate output, diffs and tests with Role 2
- [ ] Coordinate index refreshes and worktrees with Role 3
- [~] Complete security, failure-injection and performance tests — unit/integration only

`[x]` done · `[~]` partial · `[ ]` pending — **12 / 13 / 1** of 26.

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
| Test results & verification status | 1 & 4 (2 presents) | 🟡 proposal built: Role 1's `VerificationStatus` is the single verdict, Role 4 supplies evidence + `next_steps` via optional fields; **Role 1 sign-off pending** |
| Artifact references & error formats | 1, 2, 4 (3 sources) | 🟡 `ArtifactRef` extended (size, sha256, retention, truncated, redactions); ids are opaque `art_…`; Role 2/3 consumption and Role 1 registry pending |
| Failure recovery & cancellation | 1 & 4 (2 controls) | ⬜ |

### Integration rules (unchanged)

1. Don't duplicate security policy in prompts — enforce it in trusted, testable code at the execution boundary.
2. Every significant command, patch and Git op is traceable: task, action id, workspace, normalized result.
3. Preserve user work by default — isolated worktrees, explicit paths, scoped cleanup.
4. Test real side effects — integration tests inspect the real filesystem and Git state; a mock `"success": true` proves nothing.
