# ASEOS Architecture

## Overview
ASEOS separates agent reasoning and workflow orchestration from the code that
performs execution side effects.

## Main flow
1. The agent runtime prepares and processes a task.
2. The runtime produces or receives an Action.
3. The runtime delegates the action to an ActionExecutor implementation.
4. The executor validates the operation and calls the relevant execution
   module.
5. The executor returns an ExecutionResult.
6. The runtime uses the result to continue the workflow and report progress.

## Important components

### Runtime
`runtime/agent/runtime.py` orchestrates task execution and lifecycle events.

### Executor interface
`runtime/agent/executor.py` defines the asynchronous executor contract.
`runtime/agent/mock_executor.py` provides a mock implementation for tests.
`runtime/agent/real_executor.py` performs supported real workspace operations.

### Execution modules
- `execution/patches/apply.py`: validates and applies text replacements.
- `execution/git/`: Git status, diff, operations, and worktree management.
- `execution/testing/runner.py`: runs tests in a workspace.
- `execution/commands/runner.py`: provides command-running functionality
  with a timeout and configured environment handling.
- `execution/executor.py`: coordinates the task-level execution demo flow.

### Tests
Tests under `execution/tests/` cover execution, patching, Git operations,
worktrees, and runners. Agent executor tests cover real-executor behavior
and runtime integration.

## Workspace isolation
The task-level executor uses a Git worktree so changes can be made and tested
in a separate working directory. This reduces interference with the primary
checkout, but is not equivalent to a container, VM, or operating-system
security sandbox.

## Current safety boundary
`RealActionExecutor` disables the general `RUN_COMMAND` action. Do not assume
that every command runner or verification helper is automatically safe for
untrusted input; callers and command policies must be reviewed separately.

## Verification flow
The demo creates an isolated worktree, applies a patch, runs tests, collects
the diff, and attempts cleanup. The demo is useful integration evidence, but
does not establish that every roadmap requirement is complete.
