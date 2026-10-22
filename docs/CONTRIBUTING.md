# Contributing to ASEOS

## Development setup
1. Clone the repository and switch to the branch you intend to work on.
2. Use the project's documented Python environment and dependencies.
3. Run focused tests for the component you change.
4. Run the broader test suite when practical.

## Before submitting changes
- Keep changes scoped to the relevant component.
- Add or update tests for changed behavior.
- Check path validation, failure handling, and cleanup when changing execution
  or worktree code.
- Do not weaken safety checks merely to make a test pass.
- Inspect the final diff before committing.

## Suggested verification commands
Run these from the repository root:

```powershell
python -m pytest execution/tests -q
python -m pytest tests/agent/test_real_executor.py tests/agent/test_real_executor_integration.py -q
python -m execution.demo
git diff --check
git status
```

Some repositories may contain intentionally failing fixture tests. Investigate
failures and report them accurately instead of changing fixture behavior
without understanding the test scenario.

## Execution safety
Treat task instructions, model-generated actions, file contents, and command
arguments as potentially untrusted. Validate workspace paths and patches.
Do not enable unrestricted command execution without an explicit permission
model, appropriate isolation, resource limits, and tests.

## Pull request expectations
Describe the behavior changed, the tests run, known limitations, and any
follow-up work. Never claim a test passed unless it was actually run.
