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
| `patch.apply` | `EDIT` | `fs.write` | no | `{file, old, new}`; unique-match replace; `data.changed_files`, `data.sha256`; `.git` denied |
| `repo.write` | `WRITE` | `fs.write` | no | `{path, content, expected_sha256?}`; see §2.1 |
| `test.run` | `RUN_TEST` | `test.run` | no | `python -m pytest` |
| `git.diff` | `GIT_DIFF` | `git.read` | yes | unstaged diff |
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

Default granted capabilities: `fs.read`, `fs.write`, `git.read`, `test.run`.

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
| `PERMISSION_DENIED` | Capability not granted; path escapes the workspace |
| `WORKSPACE_UNAVAILABLE` | Reserved (workspace is validated at gateway construction) |
| `RESOURCE_LIMIT` | File too large to read, write content too large |
| `TIMEOUT` | Gateway or command timeout |
| `CANCELLED` | Reserved (cancellation not yet implemented) |
| `PROCESS_FAILURE` | Command could not start or errored |
| `PATCH_CONFLICT` | Patch target missing or ambiguous; `repo.write` hash mismatch |
| `GIT_FAILURE` | Git command failed |
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
change. `LIST_DIR`, `FILE_INFO` and `WRITE` were added that way. They are **not yet** in Role 1's `ToolRegistry.default()`,
so the planner cannot offer them until Role 1 registers them.
