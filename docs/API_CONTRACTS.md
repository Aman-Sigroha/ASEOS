# ASEOS API Contracts

## Purpose
This document describes the current contracts between the agent runtime and
the execution layer.

## Action contract
The runtime represents an operation as an Action with an identifier, an action
type, and a payload.

Currently recognized action types:
- `READ`: read a workspace file.
- `SEARCH`: search workspace files for text.
- `EDIT`: apply a validated text patch.
- `RUN_TEST`: run the configured test runner.
- `GIT_DIFF`: retrieve the Git diff for the workspace.
- `RUN_COMMAND`: general command execution is disabled in RealActionExecutor.

The action schema is defined in `runtime/schemas/action.py`. Consult that file
for the authoritative field definitions.

## Executor contract
`runtime/agent/executor.py` defines the asynchronous ActionExecutor interface.
An executor accepts an action and returns an ExecutionResult.

Implementations:
- `MockExecutor`: supports runtime testing without real side effects.
- `RealActionExecutor`: performs supported operations against a configured
  workspace and delegates patching and testing to the execution modules.

## Result contract
Execution results contain:
- `action_id`: identifier of the action being executed.
- `success`: whether the operation succeeded.
- `stdout`: textual operation output.
- `stderr`: textual error output.
- `exit_code`: operation exit status when available.
- `duration_ms`: execution duration in milliseconds.

See `runtime/schemas/execution.py` for the authoritative schema.

## Workspace and safety expectations
- File operations must remain inside the configured workspace.
- File edits must pass patch validation.
- General command execution must not be assumed to be available.
- A Git worktree separates a task's working directory from the main checkout,
  but does not isolate operating-system processes or provide a full sandbox.
- Callers must inspect the result's success status before treating an action
  as successful.

## Known limitations
The current implementation is not a production-hardened sandbox. Output-size
limits, robust process-tree termination, cancellation behavior, crash recovery,
and comprehensive concurrency protection require additional validation or work.

This document describes the current implementation, not a guarantee that every
planned execution feature is complete.
