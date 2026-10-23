# ASEOS — Role 4 Execution Plan & Status Tracker

Execution, Git, worktrees, command running, sandboxing and testing.

- **Status snapshot date:** 2026-10-23
- **Branch audited:** `execution` (HEAD `a91605f`)
- **Test baseline:** `python -m pytest -q` → 764 passed, 6 skipped (symlink/POSIX-mode tests and the real-container test skip where unavailable), 0 failed (default `testpaths` now exclude the
  intentionally failing `fixtures/` repo). Before the first slices it was 202 passed + 1 fixture failure.
- **Progress log:** slices A1–A3, B1–B2, C1–C4 (Phase 2 filesystem) C5–C9 (Phase 3 patches) D1–D3, D9 (Phase 4 Git) D4–D8, D11 (Phase 5 worktrees) E1–E5 (Phase 6 commands) and H1–H6 (Phase 7 security) are done; B3/B4 are partly done (see [Slice backlog](#3-slice-backlog-suggested-order)).

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
| 8 | Test discovery & execution | 🟡 PARTIAL | Hardcoded `python -m pytest`; no discovery or report parsing |
| 9 | Deterministic verification evidence | 🟡 PARTIAL | Role 1 verifier exists; not tied to code version/artifacts |
| 10 | Artifacts, retention & audit | ⬜ PENDING | Only runtime events exist |
| 11 | Concurrency, interruption & recovery | ⬜ PENDING | — |
| 12 | Cross-role integration | 🟡 PARTIAL | Role 1 wired in; Role 2 and Role 3 pending |
| 13 | Security, testing & performance qualification | 🟡 PARTIAL | Unit/integration tests only |

**Role 4 completion checklist: 9 ✅ done · 12 🟡 partial · 5 ⬜ pending (of 26)** (an item flips only when its full acceptance criterion is met). See [Section 4](#4-role-4-completion-checklist).

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
- 🟡 All files are validated in memory first; writes are atomic per file; if a later step fails earlier files are restored (best-effort). `data.rolled_back` / `data.partial` / `data.unrestored_files` say exactly what happened, and the result's `applied_diff` can be fed back with `reverse=true` to undo a patch. Not done: persisted recovery artifacts (Phase 10) and true multi-file atomicity.
- ✅ Result is built from disk after writing: per-file action, sha256 before/after, additions/deletions, hunk offsets and an `applied_diff` regenerated from the real before/after bytes (≤1 MB, `applied_diff_truncated`). Post-write verification re-reads every file.
- 🟡 Result carries `patch_id` (sha256 of the patch text) and the originating `action_id`. Linking to a task id and persisting the record needs the event/artifact work (Phase 10) and a task id in the gateway context.
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
- 🟡 Not covered: pushing/fetching, checkout/switch, reset, stash, rebase (deliberately out of scope); staging cannot tell the agent's edits from the user's earlier edits *inside the same file* (use an isolated worktree, or pass `expected_sha256`); hooks and signing configured in the repo still run on `git.commit`; diff text is bounded after Git produces it, not while streaming; the 'artifact' for oversized diffs waits for Phase 10.

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
- 🟡 Not covered / honest limits: **the policy decides what may start, not what an allowed program does** — a test suite is arbitrary code and can still read files, use the network or spawn processes within the limits (that is Phase 7). `shell.network` gates *install-type commands* only; network access is not blocked. `HOME`/`USERPROFILE` are passed through, so tools can read user-level config (`.npmrc`, `.pypirc`). The POSIX code paths (process group, `prlimit`) are written and unit-tested for logic but were **not run on a POSIX machine** in this environment. POSIX has no memory or process-count limit yet (cgroups/containers are Phase 7). Command logs are plain files in the system temp directory with no retention until Phase 10. Streaming logs are not implemented (results are returned when the command ends). `runtime/verification/command.py` (Role 1) still uses `create_subprocess_shell` and has not been moved onto this runner.

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

`[x]` done · `[~]` partial · `[ ]` pending — **9 / 12 / 5** of 26.

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
