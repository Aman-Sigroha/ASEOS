# ASEOS Execution Contract

How an `Action` becomes an `ExecutionResult`. Authoritative code: `execution/gateway/` and
`runtime/schemas/execution.py`. Companion to [API_CONTRACTS.md](API_CONTRACTS.md) (baseline fields) and
[SECURITY_MODEL notes in ARCHITECTURE.md](ARCHITECTURE.md#7-safety-boundary-today).

## 1. Pipeline

`RealActionExecutor.execute(action)` → `ExecutionGateway.execute(action)`:

1. **Resolve** the registered tool for `action.type` (no registered tool → `UNKNOWN_TOOL`).
2. **Check capabilities** — every capability in `tool.spec.capabilities` must be in the gateway's granted set
   (else `PERMISSION_DENIED`). Done *before* argument validation so a denied tool reveals nothing about its schema.
3. **Validate arguments** against the tool's pydantic `args_model` (else `INVALID_ARGUMENTS`).
4. **Run** the tool in a worker thread under `spec.timeout_seconds` (else `TIMEOUT`).
5. **Normalize** into `ExecutionResult`. Tool bugs become `INTERNAL_ERROR`; the gateway never raises to the runtime.

The workspace is trusted gateway context (`ToolContext`); it is never taken from the action payload.

## 2. Registered tools

| Tool id | Action type | Capability | Read-only | Notes |
| ------- | ----------- | ---------- | --------- | ----- |
| `repo.read` | `READ` | `fs.read` | yes | `{path, start_line?, end_line?, max_bytes?}`; UTF-8 text; ≤1 MB returned (`truncated`); `data.sha256` identifies the version |
| `repo.list_dir` | `LIST_DIR` | `fs.read` | yes | `{path='.', max_entries≤1000}`; non-recursive; hides links that escape the workspace |
| `repo.file_info` | `FILE_INFO` | `fs.read` | yes | `{path}`; missing path → success with `exists=false`; type, size, mtime, sha256, binary |
| `repo.search` | `SEARCH` | `fs.read` | yes | ≤100 matches; sets `truncated` |
| `patch.replace` | `EDIT` | `fs.write` | no | Legacy `{file, old, new}` unique-match replace; `data.changed_files`, `data.sha256`; protected paths denied |
| `patch.validate` | `VALIDATE_PATCH` | `fs.read` | yes | `{patch, base?, reverse?}`; checks a unified diff against the workspace, writes nothing; see §2.2 |
| `patch.apply` | `APPLY_PATCH` | `fs.write` (+ `fs.delete` to delete files) | no | `{patch, base?, reverse?}`; see §2.2 |
| `repo.write` | `WRITE` | `fs.write` | no | `{path, content, expected_sha256?}`; see §2.1 |
| `test.run` | `RUN_TEST` | `test.run` | no | `python -m pytest` |
| `git.status` | `GIT_STATUS` | `git.read` | yes | `{max_entries?}`; structured branch/staged/unstaged/untracked/conflicted/state |
| `git.diff` | `GIT_DIFF` | `git.read` | yes | `{mode='unstaged'\|'staged'\|'base', base?, paths?, include_untracked=true, max_bytes?, context?}`; stdout is the diff text, `data` has base, files, stats; see §2.3 |
| `git.info` | `GIT_INFO` | `git.read` | yes | `{max_branches?, max_commits?}`; identity, HEAD, branches, recent commits, relevant config |
| `git.stage` | `GIT_STAGE` | `git.write` | no | `{paths, expected_sha256?}` |
| `git.commit` | `GIT_COMMIT` | `git.write` | no | `{message, paths}` |
| `git.branch_create` | `GIT_BRANCH` | `git.write` | no | `{name, start_point?}`; does not check out |
| `git.discard` | `GIT_DISCARD` | `git.discard` | no | `{paths}`; returns the discarded diff |
| `shell.run` | `RUN_COMMAND` | `shell.run` | no | **Not granted by default; always denied** until a command policy exists |

Tool ids use the planned namespaces (`repo.*`, `git.*`, `shell.*`, `test.*`, `patch.*`). The runtime's
`ToolRegistry` still uses the short names (`read`, `edit`, …) keyed by action type; `ExecutionResult.tool` reports the
canonical id. Aligning Role 1's registry names is an open item.

### 2.1 Filesystem semantics

- **Paths** are workspace-relative and resolved before use; anything resolving outside the workspace is `PERMISSION_DENIED`.
- **Symlinks:** reads follow links whose target is inside the workspace; links/junctions pointing outside are denied; `repo.write` refuses to write through a symlink; `repo.list_dir` omits escaping links.
- **Limits:** read refuses files >10 MB (`RESOURCE_LIMIT`) and returns ≤1 MB; write content ≤1 MB; binary files (NUL in the first 8 KB) and non-UTF-8 files are `INVALID_ARGUMENTS`.
- **Versions:** `sha256` is the hex SHA-256 of the raw file bytes. Line endings are preserved byte-for-byte.
- **`repo.write`:** a new file must omit `expected_sha256`; overwriting requires `expected_sha256` equal to the current hash. Missing hash on an existing file → `INVALID_ARGUMENTS`; mismatch, or a hash for a missing file → `PATCH_CONFLICT` (with `expected_sha256`/`actual_sha256` in `data`). The write is atomic and verified by re-reading. Paths under `.git` are denied (also for `patch.apply`).
- **Not covered:** an OS-level race between the final hash re-check and `os.replace`; no file locking.

### 2.2 Patch semantics (`patch.validate`, `patch.apply`)

- **Format:** unified diff, plain or `git diff` style, modified/created (`--- /dev/null`)/deleted (`+++ /dev/null`) text files, several files per patch, `\ No newline at end of file`. A surrounding markdown code fence is tolerated. Renames, copies, mode changes, binary patches and quoted paths are rejected (`INVALID_ARGUMENTS`).
- **Matching:** each hunk must match the file exactly (whitespace-sensitive, line endings ignored). The stated line number is tried first; otherwise a single unique match later in the file is used (reported in `hunk_offsets`). No match, or several matches → `PATCH_CONFLICT` with details; there is no fuzzy matching.
- **Validation first:** every file is checked in memory before any write: path containment, protected paths (`.git/**`, `.env`, `.env.*` except `.example/.sample/.template`), symlinks, size limits (200 KB patch, 20 files, 10 MB per file), `base` hashes, create-vs-exists, delete coverage.
- **`base`:** optional `{path: sha256}`. A mismatch (or a hash for a missing file) → `PATCH_CONFLICT`.
- **Deleting** a file requires the `fs.delete` capability, which is not granted by default (`PERMISSION_DENIED` otherwise). The delete hunks must cover the entire file.
- **Apply:** files are re-compared with what was validated, then written atomically one by one. If a step fails, files already written are restored and created files/directories removed. `data.rolled_back=true` means everything was undone; `data.partial=true` with `data.unrestored_files` means rollback itself failed. This is a best-effort rollback, not a transaction.
- **Result (from disk, not from the patch):** `files[]` (`action` created/modified/deleted, `sha256_before`, `sha256_after`, `additions`, `deletions`, `hunk_offsets`), `changed_files`, `applied_diff` (regenerated from the real before/after bytes; ≤1 MB, `applied_diff_truncated`), `patch_id` (sha256 of the patch text), `action_id`, `reverse`.
- **Undo:** pass the previous `applied_diff` with `reverse=true`. Reversing a create is a delete and so needs `fs.delete`.
- **Encoding/line endings:** UTF-8; added lines use the file's dominant line ending; permission bits of existing files are preserved on POSIX.

### 2.3 Git semantics

- **Scope:** every git tool requires the workspace to be the repository (or linked worktree) root; otherwise `GIT_FAILURE`. Paths are workspace-relative, literal (no globs), and must not be absolute or contain `..`.
- **`git.diff` bases:** `unstaged` = working tree vs index; `staged` = index vs HEAD; `base` = working tree vs the commit `base` resolves to (`data.base_commit`). `data.base` says in words what the comparison is. Untracked files are included as added files unless `include_untracked=false` (not for `staged`).
- **`git.stage`:** named files only (no directories, `.`, globs, duplicates; `.git`/`.env` refused). `expected_sha256` ({path: hash, null = deleted}) refuses to stage a file that changed since you produced it (`PATCH_CONFLICT`). Refused during merge/rebase or with conflicts.
- **`git.commit`:** the index must contain *exactly* `paths`, otherwise `GIT_FAILURE` with `unexpected_staged` / `missing_staged`. A staged rename needs both the old and new path. Refused on detached HEAD, during merge/rebase, or with conflicts. Message ≤4000 chars; passed as an argument, never through a shell. The repository's hooks and signing settings still apply.
- **`git.branch_create`:** validates the name with `git check-ref-format`, refuses an existing branch, never checks out or moves anything.
- **`git.discard`:** tracked files with unstaged changes only; refuses untracked, unchanged, staged or conflicted files, and refuses if the discarded diff exceeds 1 MB (it could not be returned). `data.discarded_diff` can be re-applied with `patch.apply`.
- **Evidence on every mutation:** `data.other_changes` (dirty paths outside the operation's scope, before it ran) and `data.status_after` (counts).
- **Locks:** a Git index lock held by another process gives `GIT_FAILURE` with `data.lock_conflict=true`; retry later.

Default granted capabilities: `fs.read`, `fs.write`, `git.read`, `test.run`. Not granted by default: `fs.delete`, `git.write`, `git.discard`, `shell.run`.

## 3. `ExecutionResult`

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `action_id` | str | Action this result belongs to |
| `success` | bool | Operation succeeded |
| `stdout`, `stderr` | str | Output / error text. On failure `stderr` carries the diagnostic message |
| `exit_code` | int \| None | Process/operation exit status when applicable |
| `duration_ms` | int \| None | Wall time in the gateway |
| `tool` | str \| None | Canonical tool id; `None` if no tool was resolved |
| `error_category` | enum \| None | Set only when `success` is false (enforced by the schema) |
| `truncated` | bool | Output was cut short by a tool limit |
| `artifacts` | list[ArtifactRef] | References to large outputs (reserved; no artifact store yet) |
| `data` | dict | Structured, tool-specific output (e.g. `changed_files`, `match_count`) |

The first six fields are the original baseline; the rest default to empty values, so existing producers and consumers
are unaffected.

## 4. Error categories

| Category | Used when |
| -------- | --------- |
| `INVALID_ARGUMENTS` | Payload fails schema; missing file; binary/undecodable file; empty patch target; overwrite without `expected_sha256` |
| `UNKNOWN_TOOL` | No tool registered for the action type |
| `PERMISSION_DENIED` | Capability not granted (incl. `fs.delete`); path escapes the workspace; protected path or symlink |
| `WORKSPACE_UNAVAILABLE` | Reserved (workspace is validated at gateway construction) |
| `RESOURCE_LIMIT` | File too large to read, write content too large |
| `TIMEOUT` | Gateway or command timeout |
| `CANCELLED` | Reserved (cancellation not yet implemented) |
| `PROCESS_FAILURE` | Command could not start or errored |
| `PATCH_CONFLICT` | Patch target missing/ambiguous/changed; hunk context mismatch; base hash mismatch; `repo.write` hash mismatch |
| `GIT_FAILURE` | Git command failed; not a repository / not the root; unsafe repository state (merge, detached HEAD); index does not match the commit scope |
| `TEST_FAILURE` | Test command ran and tests failed |
| `INTERNAL_ERROR` | Unexpected exception in a tool |

`TEST_FAILURE` (tests ran and failed) is deliberately distinct from `PROCESS_FAILURE` (could not run).

## 5. Known limitations

- The gateway timeout cannot stop a worker thread; tools that start processes must enforce their own timeouts.
  Process-tree termination and cancellation are not implemented yet.
- No output-size limits, artifact store, redaction or execution events from the gateway yet.
- Role 1's `ToolRegistry` names and policy risk labels are not yet aligned with the capability model.

See [EXECUTION_PLAN.md](EXECUTION_PLAN.md) for the scheduled work.

## 6. Adding a tool

1. Subclass `Tool` with a `ToolSpec` (canonical id, `args_model`, capabilities, `read_only`, timeout).
2. In `run`, use `resolve_in_workspace` for paths and raise `ToolError(category, message)` for failures.
3. Register it in `default_tools()` (and add a capability to `DEFAULT_CAPABILITIES` only if it is safe by default).
4. Add gateway-level tests for success, invalid arguments, denied capability and each error category.

`ActionType` in `runtime/schemas/action.py` gates which action types exist; new ones need a (backward-compatible) schema
change. `LIST_DIR`, `FILE_INFO`, `WRITE`, `VALIDATE_PATCH`, `APPLY_PATCH`, `GIT_STATUS`, `GIT_INFO`, `GIT_STAGE`, `GIT_COMMIT`, `GIT_BRANCH` and `GIT_DISCARD` were added that way. They are **not yet** in Role 1's `ToolRegistry.default()`,
so the planner cannot offer them until Role 1 registers them.
