# Role 4 — what is and is not guaranteed

Read this before relying on the execution layer. It states what was demonstrated by a passing test, what was only
written, and what was measured. Everything here was last checked on **Windows 11, Python 3.13.6, Git 2.47, Node 22** with
the test suite described in [EXECUTION_PLAN.md](EXECUTION_PLAN.md) (nothing was run on Linux or macOS).

Companion documents: [SECURITY_MODEL.md](SECURITY_MODEL.md) (threat model), [EXECUTION_CONTRACT.md](EXECUTION_CONTRACT.md)
(tools and result fields), [EXECUTION_PLAN.md](EXECUTION_PLAN.md) (status and phases).

## 1. The isolation boundary

| What | Confined? |
| ---- | --------- |
| A file path given to a **tool** (read, list, search, write, edit, patch, diff, stage, commit, discard, test targets, command `cwd`) | **Yes.** Resolved against the workspace; traversal, absolute paths, UNC, Windows device names, alternate data streams, protected areas (`.git`, hooks, managed worktrees) and links that leave the workspace are refused. Attacked in a sweep of every tool against 13–18 hostile paths. |
| One task writing into another task's worktree **through a tool** | **No** (refused), tested. |
| The **Git operations** the tools offer | Limited to named paths; the main checkout and the user's unfinished work are never touched by a task working in a worktree (tested on a dirty tree). |
| A **program** the command policy allows (`shell.run`, a test runner, a build) | **No.** Without a sandbox it runs as the current user: it can read and write any file the user can, including other tasks' worktrees (pinned by a test), use the network, and read `HOME`/`USERPROFILE` configuration. The environment it receives is scrubbed and its output is masked, but it is not contained. |
| The **container sandbox** that would contain such a program | **Unproven.** Built and tested against a stand-in engine. The Docker client is installed on the development machine but its daemon was not running, so the one test that proves network and host-path isolation with a real container has **never run**. |
| Network access and filesystem limits for programs | Cannot be shown without a sandbox. Memory/process-count limits for native programs exist on Windows only. |

Consequence: treat a worktree as the unit of **review**, not as a security boundary, until the container backend has been
run against a real engine. `git.commit` still runs the repository's hooks and signing configuration.

## 2. Where it was verified

| Area | Windows (here) | Linux / macOS |
| ---- | -------------- | ------------- |
| Everything in the test suite | passes | **never run** |
| Process-tree kill, job-object limits | verified | process groups and `prlimit` written, not run |
| Orphan cleanup after a crash | verified, creation token | Linux token written, not run; **macOS has no token, so orphans are reported but never killed** |
| Symlink-file tests, POSIX permission-bit tests | skipped (6 tests) | not run |
| Directory junction escape | verified | symlink variant written, not run |

Test toolchains:

| Framework | Proven against the real tool? |
| --------- | ----------------------------- |
| pytest, unittest | yes |
| Node (`npm test` running `node --test`, TAP output) | yes (Phase 13). Jest/Vitest/Mocha summaries are parsed from sample text only |
| Maven, Gradle, Cargo, Go | **no** — parsers and commands written from the tools' documented output; the tools are not installed here |

## 3. Guarantees and non-guarantees by area

**Files and patches.** Reads are size-bounded (1 MB) and refuse binary files; writes are atomic and conflict-checked by
hash; a multi-file patch is applied all-or-nothing with rollback (read-only target, conflict and crash tested). A small
diff re-applied to an already-changed file can apply **twice**, which is why `recover()` returns the pre-change hashes for a
guarded retry. A temporary file is removed on failure. Not covered: files being changed by something else while a patch
is applied (checked right before the write, not locked), very large files beyond the read cap, non-UTF-8 text encodings in
patches.

**Git and worktrees.** A task works in a worktree created from an explicit base commit; removal refuses while there is
unreviewed work, a live lease or a live operation, and backs work up first when a person confirms the discard. A failed
removal (folder in use) leaves a state a retry can finish. Not covered: submodules in worktrees, Git hooks that misbehave,
a worktree on a removed drive, retention/automatic cleanup (nothing is ever deleted unless a trusted caller asks), applying
a task's result to the main checkout (**not built**).

**Commands.** Argv only, default-deny policy, scrubbed environment, output capped and spooled, timeouts, cancellation and
whole-tree kill. The policy decides what may *start*; it cannot constrain what an allowed program then does (section 1).
Masking is by environment values and recognisable shapes; **a secret with no recognisable shape that the command prints is
stored as-is**. Secret-file detection is by name (`.env`, `id_rsa`, `*.pem`…); a secret in `config.py` is not hidden.

**Tests and verification.** A test run is tied to a content fingerprint of the workspace and is reported stale or unstable
when the code changed during or after the run. A pass in a repository with several test frameworks covers **only the one
that ran** (`other_frameworks` lists the rest). Targeted runs say so and do not stand in for the full suite. Not covered:
flaky-test detection, coverage, tests that need network or services.

**Artifacts and records.** Outputs live in a per-repository store outside the working tree with ownership scoping,
retention classes and a size quota (enforced by an estimate that is re-counted near the limit and every 30 s). Files are
plain, unencrypted and readable by the same OS user; `gc()` must be scheduled by the host (it is not).

**Recovery.** File and Git changes in one workspace are serialized within a gateway; managed worktrees take an exclusive
lease across gateways. After a crash `recover()` reports what happened to each interrupted action and never re-runs,
rolls back or deletes anything. Only `patch.apply`, `repo.write` and `git.commit` write file/commit checkpoints; other
state-changing tools report `unknown`. The runtime reports the result but does not decide to resume.

**Runtime integration.** The runtime's own defaults (six-tool registry, a policy that asks only for `RUN_COMMAND`) are
unchanged; `RealActionExecutor.runtime_options()` is how a runtime uses the gateway's tools and approval rules. The
runtime has **no approval flow**: an action that needs approval ends the task. Change notifications are in-process, best
effort and not persisted; nothing consumes them yet. There is no desktop code in this repository.

## 4. Measured performance

`python -m execution.bench` (journal, artifact store and records on). One run on the development machine — a laptop with
real-time antivirus, which makes the **first** read of any newly written file cost about 10 ms; treat absolute numbers
as an upper-ish bound and compare runs on the same machine. Repository: 2000 tracked files unless stated.

| Operation | p50 | p95 | Notes |
| --------- | --- | --- | ----- |
| `shell.run python -c pass` through the gateway | 133 ms | 196 ms | bare subprocess 67 ms → **≈ 66 ms overhead** |
| `test.run` (one trivial pytest) | 1.60 s | 1.72 s | bare pytest 1.23 s → **≈ 370 ms overhead** (fingerprint twice, report parsing) |
| `repo.read` 10 KB / 880 KB | 1.0 ms / 6.9 ms | 4.2 ms / 15 ms | |
| `repo.search` (whole 2000-file tree) | 18 ms | 32 ms | |
| `patch.apply` on a 5000-line file | 72 ms | 84 ms | journal + audit artifact included |
| `repo.write` 10 KB | 52 ms | 69 ms | atomic write + verification + journal + record |
| `git.status` clean / 50 modified | 103 ms / 81 ms | 132 ms / 100 ms | |
| `git.diff` with 50 modified files | 305 ms | 323 ms | |
| `workspace_version()` | **2.4 s** | 16 s (first, cold) | ≈ 1.2 ms per file warm; runs before and after every test run |
| worktree create / remove | 2.7 s / 1.2 s | 5.1 s / 1.6 s | 2000 files |
| 100 MB of command output | 5.1 s | | result excerpt 200 KB, truncated; **memory grew 85 MB**, not 100 MB+ |
| 32 parallel reads | 58 ms total | | |
| 8 parallel commands of 0.5 s each | 0.97 s total | | they do run side by side |
| 16 writes issued at once | 0.71 s total | | serialized by design |
| 3000 artifacts: store each | 6.7 ms | | was 54 ms and growing (quadratic) before Phase 13 |
| 3000 artifacts: list all | 0.7 s warm | 33 s **cold** | cold = files just written, antivirus scanning each |
| 3000 artifacts: `audit()` / `gc()` | 0.85 s / 0.78 s | | |

What the numbers say:

* A command or a read is cheap; what costs time is **content fingerprinting** (`workspace_version`) and **worktree
  creation** on large trees. Both grow linearly with the number of files. At 2000 files they are seconds; at 100 000 they
  would be minutes, and nothing has been measured at that size.
* Fingerprinting could be made incremental by caching hashes per (size, modification time), but that would let a program
  that resets a file's timestamp hide a change from the integrity check, so it was **not** done.
* Memory is bounded by the output caps, not by what a program prints.
* Guard tests (`execution/tests/test_performance.py`) fail on an order-of-magnitude regression; they do not prove
  these numbers on another machine.

## 5. Never tested

Hundreds of thousands of files; runs longer than a few minutes; low disk space and full-disk behaviour; several machines
sharing one repository; a repository on a network drive; Git LFS, sparse checkout, submodules; antivirus products other
than the development machine's; non-English locales for tool output; Python versions other than 3.13; running as a
service or under another user account; anything on Linux or macOS.
